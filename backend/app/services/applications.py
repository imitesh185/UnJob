"""Application workflow (human approval before submission), events and outcome analytics."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Application,
    ApplicationEvent,
    Company,
    Job,
    JobAnalysis,
    ResumeVariant,
)

PREPARATION = ["DISCOVERED", "ANALYZED", "RECOMMENDED", "RESUME_GENERATED", "USER_REVIEW"]
APPROVAL = ["APPROVED", "READY_TO_APPLY", "APPLICATION_STARTED"]
OUTCOMES = ["APPLIED", "OA", "RECRUITER_SCREEN", "TECHNICAL", "FINAL", "OFFER"]
CLOSED = ["REJECTED", "WITHDRAWN"]
STATUSES = PREPARATION + APPROVAL + OUTCOMES + CLOSED
INTERVIEW_STAGES = {"RECRUITER_SCREEN", "TECHNICAL", "FINAL", "OFFER"}
RESPONSE_STAGES = {"OA", *INTERVIEW_STAGES}


class WorkflowError(ValueError):
    pass


def _approved(application: Application, events: list[ApplicationEvent]) -> bool:
    if application.status in APPROVAL + OUTCOMES:
        return True
    # The latest approval counts unless a newer resume version sent it back for review.
    approved = False
    for event in sorted(events, key=lambda item: item.created_at):
        if event.to_status == "APPROVED":
            approved = True
        elif event.to_status == "USER_REVIEW" and event.from_status in APPROVAL:
            approved = False
    return approved


async def transition(
    session: AsyncSession,
    application: Application,
    status: str,
    *,
    note: str | None = None,
    actor: str = "user",
) -> ApplicationEvent:
    """Move an application to ``status``. Submission-related statuses require approval first;
    UnJob never submits anything itself."""
    if status not in STATUSES:
        raise WorkflowError(f"Unknown status '{status}'.")
    events = list(
        (
            await session.scalars(
                select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
            )
        ).all()
    )
    if status in APPROVAL[1:] + OUTCOMES and not _approved(application, events):
        raise WorkflowError(
            "Approve the application (and its resume) before marking it ready or applied."
        )
    if status == "APPROVED" and application.resume_variant_id:
        variant = await session.get(ResumeVariant, application.resume_variant_id)
        if variant is not None and not (variant.truth_validation or {}).get("passed"):
            raise WorkflowError(
                "The selected resume has unsupported claims; fix or exclude them before approving."
            )
    event = ApplicationEvent(
        application_id=application.id,
        from_status=application.status,
        to_status=status,
        note=note,
        actor=actor,
    )
    session.add(event)
    application.status = status
    if status == "APPLIED" and application.applied_at is None:
        application.applied_at = datetime.now(UTC)
    return event


def _rate(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 1) if denominator else None


async def analytics(
    session: AsyncSession, candidate_id: uuid.UUID, minimum_sample: int = 5
) -> dict[str, Any]:
    """Deterministic outcome analytics; no model is trained. Small samples are flagged."""
    rows = (
        await session.execute(
            select(Application, Job, Company, ResumeVariant, JobAnalysis)
            .join(Job, Job.id == Application.job_id)
            .outerjoin(Company, Company.id == Application.company_id)
            .outerjoin(ResumeVariant, ResumeVariant.id == Application.resume_variant_id)
            .outerjoin(JobAnalysis, JobAnalysis.job_id == Job.id)
            .where(Application.candidate_id == candidate_id)
        )
    ).all()
    events = (
        await session.scalars(
            select(ApplicationEvent)
            .join(Application, Application.id == ApplicationEvent.application_id)
            .where(Application.candidate_id == candidate_id)
        )
    ).all()
    reached: dict[uuid.UUID, set[str]] = defaultdict(set)
    for event in events:
        reached[event.application_id].add(event.to_status)
    status_counts = Counter(application.status for application, *_rest in rows)
    applied_rows = [
        row for row in rows if reached[row[0].id] & set(OUTCOMES) or row[0].status in OUTCOMES
    ]

    def outcome(row: Any) -> tuple[bool, bool]:
        stages = reached[row[0].id] | {row[0].status}
        return bool(stages & RESPONSE_STAGES), bool(stages & INTERVIEW_STAGES)

    def breakdown(key: Any) -> list[dict[str, Any]]:
        groups: dict[str, list[Any]] = defaultdict(list)
        for row in applied_rows:
            for value in key(row):
                groups[value or "UNKNOWN"].append(row)
        result = []
        for value, items in groups.items():
            responses = sum(1 for item in items if outcome(item)[0])
            interviews = sum(1 for item in items if outcome(item)[1])
            result.append(
                {
                    "value": value,
                    "applications": len(items),
                    "responses": responses,
                    "interviews": interviews,
                    "interview_rate": _rate(interviews, len(items)),
                    "low_sample": len(items) < minimum_sample,
                }
            )
        return sorted(result, key=lambda item: (-item["applications"], item["value"]))

    total_applied = len(applied_rows)
    total_interviews = sum(1 for row in applied_rows if outcome(row)[1])
    return {
        "totals": {
            "tracked": len(rows),
            "applied": total_applied,
            "responses": sum(1 for row in applied_rows if outcome(row)[0]),
            "interviews": total_interviews,
            "offers": sum(
                1 for row in applied_rows if "OFFER" in reached[row[0].id] | {row[0].status}
            ),
            "interview_rate": _rate(total_interviews, total_applied),
            "resume_versions": len({row[3].id for row in rows if row[3] is not None}),
            "companies": len({row[2].id for row in rows if row[2] is not None}),
        },
        "status_counts": dict(status_counts),
        "by_company": breakdown(lambda row: [row[2].name if row[2] else row[1].company]),
        "by_tier": breakdown(lambda row: [row[2].tier if row[2] else "UNCATEGORIZED"]),
        "by_role": breakdown(lambda row: [row[1].role_category]),
        "by_technology": breakdown(
            lambda row: [
                skill["name"] for skill in ((row[4].required_skills if row[4] else []) or [])[:5]
            ]
        ),
        "by_positioning": breakdown(
            lambda row: [
                (row[3].positioning or {}).get("archetype") or "generic"
                if row[3]
                else "master resume"
            ]
        ),
        "by_industry": breakdown(lambda row: [row[2].industry if row[2] else None]),
        "by_company_size": breakdown(lambda row: [row[2].employee_range if row[2] else None]),
        "by_source": breakdown(lambda row: [row[0].source or row[1].ats]),
        "minimum_sample": minimum_sample,
        "note": (
            "Rates are descriptive counts from your tracked outcomes, not predictions. Groups with "
            f"fewer than {minimum_sample} applications are marked as low-sample."
        ),
    }
