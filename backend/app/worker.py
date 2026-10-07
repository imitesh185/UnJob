"""Background discovery worker.

Run with ``python -m app.worker``. The API only enqueues work; this process claims runs
from the database queue, executes them under their budgets, renews its lease while
working, and schedules periodic work when the scheduler is enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.discovery.settings_store import load_runtime_settings
from app.models import DiscoveryRun
from app.services.exploration import Explorer, RunContext, RunFailure
from app.services.queue import (
    claim_next_run,
    finish_run,
    heartbeat,
    recover_expired_runs,
    retry_or_fail,
    schedule_due_runs,
)

logger = structlog.get_logger()
SCHEDULER_CHECK_SECONDS = 60


async def process_run(
    explorer: Explorer,
    session_factory: async_sessionmaker[AsyncSession],
    run: DiscoveryRun,
    worker_id: str,
    lease_seconds: int,
) -> str:
    try:
        ctx: RunContext = await explorer.build_context(run)
    except Exception as exc:  # noqa: BLE001 - configuration errors must fail visibly
        return await retry_or_fail(
            session_factory, run.id, worker_id, f"{exc.__class__.__name__}: {exc}"
        )
    stop = asyncio.Event()

    async def renew_lease() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(5.0, lease_seconds / 3))
            except TimeoutError:
                await heartbeat(session_factory, run.id, worker_id, lease_seconds, ctx.snapshot())

    renewer = asyncio.create_task(renew_lease())
    await logger.ainfo("RUN_STARTED", run_id=str(run.id), kind=run.kind, attempt=run.attempts)
    try:
        status = await explorer.execute(run, ctx)
        await finish_run(session_factory, run.id, worker_id, status, ctx.snapshot())
    except RunFailure as exc:
        ctx.error(str(exc))
        status = "FAILED"
        await finish_run(
            session_factory, run.id, worker_id, status, {**ctx.snapshot(), "error": str(exc)}
        )
    except Exception as exc:  # noqa: BLE001 - unexpected failures are retried with backoff
        await logger.aexception("RUN_ERROR", run_id=str(run.id))
        status = await retry_or_fail(
            session_factory, run.id, worker_id, f"{exc.__class__.__name__}: {exc}", ctx.snapshot()
        )
    finally:
        stop.set()
        await renewer
    await logger.ainfo("RUN_FINISHED", run_id=str(run.id), kind=run.kind, status=status)
    return status


async def run_worker(
    *,
    once: bool = False,
    worker_id: str | None = None,
    explorer: Explorer | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    max_runs: int | None = None,
) -> int:
    settings = settings or get_settings()
    session_factory = session_factory or SessionLocal
    explorer = explorer or Explorer(settings, session_factory)
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    processed = 0
    last_schedule_check = 0.0
    await logger.ainfo("WORKER_STARTED", worker_id=worker_id, once=once)
    while True:
        await recover_expired_runs(session_factory)
        if not once and time.monotonic() - last_schedule_check >= SCHEDULER_CHECK_SECONDS:
            last_schedule_check = time.monotonic()
            async with session_factory() as session:
                runtime = await load_runtime_settings(session, settings)
            if runtime.scheduler_enabled:
                scheduled = await schedule_due_runs(session_factory, runtime)
                if scheduled:
                    await logger.ainfo("RUNS_SCHEDULED", kinds=scheduled)
        run = await claim_next_run(session_factory, worker_id, settings.worker_lease_seconds)
        if run is None:
            if once:
                return processed
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        await process_run(explorer, session_factory, run, worker_id, settings.worker_lease_seconds)
        processed += 1
        if max_runs is not None and processed >= max_runs:
            return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="UnJob discovery worker")
    parser.add_argument("--once", action="store_true", help="Process queued runs, then exit.")
    args = parser.parse_args()
    try:
        processed = asyncio.run(run_worker(once=args.once))
    except KeyboardInterrupt:
        print("Worker stopped. Interrupted runs are retried after their lease expires.")
        return
    print(f"Processed {processed} run(s).")


if __name__ == "__main__":
    main()
