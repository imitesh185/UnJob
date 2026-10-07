"""Durable run queue backed by the ``discovery_runs`` table.

Claims use compare-and-set updates so several workers can share one database. A running
run holds a lease that the worker renews; runs whose lease expires (for example after a
crash) are retried with backoff until ``max_attempts`` is reached.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.discovery.settings_store import RuntimeSettings
from app.models import Company, DiscoveryCandidate, DiscoveryRun, Job

ACTIVE_STATUSES = ("QUEUED", "RUNNING")
TERMINAL_STATUSES = ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED")
RUN_KINDS = (
    "exploration",
    "scan",
    "reconciliation",
    "frontier",
    "frontier_explore",
    "company_scan",
    "seed_resolution",
    "ingestion",
    "candidate_analysis",
    "resume_tailoring",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def enqueue_run(
    session: AsyncSession,
    kind: str,
    *,
    params: dict[str, Any] | None = None,
    budgets: dict[str, Any] | None = None,
    trigger: str = "manual",
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> tuple[DiscoveryRun, bool]:
    if kind not in RUN_KINDS:
        raise ValueError(f"Unknown run kind: {kind}")
    if dedupe_key:
        existing = await session.scalar(
            select(DiscoveryRun).where(
                DiscoveryRun.dedupe_key == dedupe_key, DiscoveryRun.status.in_(ACTIVE_STATUSES)
            )
        )
        if existing is not None:
            return existing, False
    now = utcnow()
    run = DiscoveryRun(
        kind=kind,
        status="QUEUED",
        trigger=trigger,
        params=params or {},
        budgets=budgets or {},
        progress={},
        errors=[],
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
        available_at=now,
        created_at=now,
    )
    session.add(run)
    await session.commit()
    return run, True


async def claim_next_run(
    session_factory: async_sessionmaker[AsyncSession], worker_id: str, lease_seconds: int
) -> DiscoveryRun | None:
    now = utcnow()
    async with session_factory() as session:
        candidates = (
            await session.scalars(
                select(DiscoveryRun.id)
                .where(DiscoveryRun.status == "QUEUED", DiscoveryRun.available_at <= now)
                .order_by(DiscoveryRun.created_at)
                .limit(5)
            )
        ).all()
        for run_id in candidates:
            result = await session.execute(
                update(DiscoveryRun)
                .where(DiscoveryRun.id == run_id, DiscoveryRun.status == "QUEUED")
                .values(
                    status="RUNNING",
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    started_at=func.coalesce(DiscoveryRun.started_at, now),
                    attempts=DiscoveryRun.attempts + 1,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            if result.rowcount == 1:
                return await session.get(DiscoveryRun, run_id, populate_existing=True)
    return None


async def heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
    snapshot: dict[str, Any],
) -> bool:
    now = utcnow()
    async with session_factory() as session:
        result = await session.execute(
            update(DiscoveryRun)
            .where(
                DiscoveryRun.id == run_id,
                DiscoveryRun.lease_owner == worker_id,
                DiscoveryRun.status == "RUNNING",
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now, **snapshot
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1


async def finish_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    worker_id: str,
    status: str,
    snapshot: dict[str, Any],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        await session.execute(
            update(DiscoveryRun)
            .where(DiscoveryRun.id == run_id, DiscoveryRun.lease_owner == worker_id)
            .values(
                status=status,
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
                **snapshot,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def retry_or_fail(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    worker_id: str,
    message: str,
    snapshot: dict[str, Any] | None = None,
) -> str:
    now = utcnow()
    async with session_factory() as session:
        run = await session.get(DiscoveryRun, run_id)
        if run is None:
            return "MISSING"
        if snapshot and snapshot.get("progress"):
            run.progress = snapshot["progress"]
        errors = [*(run.errors or []), message][-50:]
        if run.attempts < run.max_attempts:
            run.status = "QUEUED"
            run.available_at = now + timedelta(seconds=min(900, 30 * 2**run.attempts))
        else:
            run.status = "FAILED"
            run.finished_at = now
            run.error = message
        run.errors = errors
        run.lease_owner = None
        run.lease_expires_at = None
        await session.commit()
        return run.status


async def recover_expired_runs(session_factory: async_sessionmaker[AsyncSession]) -> int:
    now = utcnow()
    recovered = 0
    async with session_factory() as session:
        expired = (
            await session.scalars(
                select(DiscoveryRun).where(
                    DiscoveryRun.status == "RUNNING", DiscoveryRun.lease_expires_at < now
                )
            )
        ).all()
        for run in expired:
            message = f"Worker lease expired during attempt {run.attempts}."
            run.errors = [*(run.errors or []), message][-50:]
            run.lease_owner = None
            run.lease_expires_at = None
            if run.attempts < run.max_attempts:
                run.status = "QUEUED"
                run.available_at = now + timedelta(seconds=min(900, 30 * 2**run.attempts))
            else:
                run.status = "FAILED"
                run.error = f"{message} No attempts remain."
                run.finished_at = now
            recovered += 1
        await session.commit()
    return recovered


async def schedule_due_runs(
    session_factory: async_sessionmaker[AsyncSession], runtime: RuntimeSettings
) -> list[str]:
    """Enqueue scheduled work that is due. Every scheduled run uses the saved budgets."""
    now = utcnow()
    scheduled: list[str] = []
    async with session_factory() as session:
        plans = [
            ("exploration", runtime.exploration_interval_hours, True),
            (
                "scan",
                runtime.scan_interval_hours,
                bool(
                    await session.scalar(
                        select(func.count(Company.id)).where(Company.review_status != "IGNORED")
                    )
                ),
            ),
            (
                "frontier",
                runtime.frontier_interval_hours,
                bool(
                    await session.scalar(
                        select(func.count(DiscoveryCandidate.id)).where(
                            DiscoveryCandidate.status == "PENDING"
                        )
                    )
                ),
            ),
            (
                "reconciliation",
                runtime.reconciliation_interval_hours,
                bool(
                    await session.scalar(
                        select(func.count(Job.id)).where(Job.listing_status == "OPEN")
                    )
                ),
            ),
        ]
        for kind, interval_hours, has_work in plans:
            if not has_work:
                continue
            active = await session.scalar(
                select(func.count(DiscoveryRun.id)).where(
                    DiscoveryRun.kind == kind, DiscoveryRun.status.in_(ACTIVE_STATUSES)
                )
            )
            if active:
                continue
            last = await session.scalar(
                select(func.max(DiscoveryRun.created_at)).where(DiscoveryRun.kind == kind)
            )
            if last is not None:
                last = last.replace(tzinfo=UTC) if last.tzinfo is None else last
                if now - last < timedelta(hours=interval_hours):
                    continue
            await enqueue_run(
                session,
                kind,
                budgets=runtime.budgets.model_dump(),
                trigger="schedule",
                dedupe_key=f"schedule:{kind}",
            )
            scheduled.append(kind)
    return scheduled
