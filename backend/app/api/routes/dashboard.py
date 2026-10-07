from datetime import UTC, datetime, time

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.dependencies import SessionDep, SettingsDep
from app.models import Application, ApplicationScore, Job, OutreachDraft
from app.schemas import DashboardSummary
from app.services.candidates import get_candidate

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    session: SessionDep,
    settings: SettingsDep,
) -> DashboardSummary:
    today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    discovered_today = await session.scalar(
        select(func.count(Job.id)).where(Job.discovered_at >= today)
    )
    candidate = await get_candidate(session)
    if candidate is None:
        high_fit = await session.scalar(
            select(func.count(Job.id)).where(Job.overall_score >= settings.high_fit_threshold)
        )
        return DashboardSummary(
            jobs_discovered_today=discovered_today or 0,
            high_fit_jobs=high_fit or 0,
            applications_prepared=0,
            applications_submitted=0,
            blocked_applications=0,
            human_reviews=0,
            outreach_opportunities=0,
            interviews=0,
        )
    # With a profile, "high fit" means P0/P1 priority on open listings.
    high_fit = await session.scalar(
        select(func.count(ApplicationScore.id))
        .join(Job, Job.id == ApplicationScore.job_id)
        .where(
            ApplicationScore.candidate_id == candidate.id,
            ApplicationScore.priority_class.in_(("P0", "P1")),
            Job.listing_status == "OPEN",
        )
    )
    counts = dict(
        (
            await session.execute(
                select(Application.status, func.count(Application.id))
                .where(Application.candidate_id == candidate.id)
                .group_by(Application.status)
            )
        ).all()
    )
    drafts = await session.scalar(
        select(func.count(OutreachDraft.id))
        .join(Application, Application.id == OutreachDraft.application_id)
        .where(Application.candidate_id == candidate.id, OutreachDraft.status == "DRAFT")
    )
    return DashboardSummary(
        jobs_discovered_today=discovered_today or 0,
        high_fit_jobs=high_fit or 0,
        applications_prepared=sum(
            counts.get(status, 0)
            for status in ("RESUME_GENERATED", "USER_REVIEW", "APPROVED", "READY_TO_APPLY")
        ),
        applications_submitted=sum(
            counts.get(status, 0)
            for status in ("APPLIED", "OA", "RECRUITER_SCREEN", "TECHNICAL", "FINAL", "OFFER")
        ),
        blocked_applications=0,
        human_reviews=counts.get("USER_REVIEW", 0),
        outreach_opportunities=drafts or 0,
        interviews=sum(
            counts.get(status, 0) for status in ("RECRUITER_SCREEN", "TECHNICAL", "FINAL", "OFFER")
        ),
    )
