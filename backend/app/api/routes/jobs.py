import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, not_, or_, select

from app.api.dependencies import SessionDep, SettingsDep
from app.discovery.jsonld import TIMEZONE_SLACK_HOURS, freshness_status
from app.discovery.settings_store import load_runtime_settings
from app.models import Job
from app.schemas import JobPage, JobRead
from app.services.companies import as_utc

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _within(hours: float, now: datetime):
    """Jobs whose oldest possible posting age is within ``hours`` (see freshness_status)."""
    return or_(
        and_(Job.posted_at_precision == "datetime", Job.posted_at >= now - timedelta(hours=hours)),
        and_(
            Job.posted_at_precision.in_(("date", "local_datetime")),
            Job.posted_at >= now - timedelta(hours=hours - TIMEZONE_SLACK_HOURS),
        ),
    )


@router.get("", response_model=JobPage)
async def list_jobs(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    min_score: float | None = Query(default=None, ge=0, le=100),
    freshness: Literal["fresh", "recent", "stale", "unknown"] | None = None,
    role_category: str | None = None,
    company_id: uuid.UUID | None = None,
    include_closed: bool = False,
) -> JobPage:
    runtime = await load_runtime_settings(session, settings)
    now = datetime.now(UTC)
    filters = [Job.overall_score >= min_score] if min_score is not None else []
    if not include_closed:
        filters.append(Job.listing_status == "OPEN")
    if role_category:
        filters.append(Job.role_category == role_category)
    if company_id:
        filters.append(Job.company_id == company_id)
    fresh = _within(runtime.freshness_hours, now)
    recent = _within(runtime.secondary_freshness_hours, now)
    if freshness == "fresh":
        filters.append(fresh)
    elif freshness == "recent":
        filters.extend([recent, not_(fresh)])
    elif freshness == "stale":
        filters.extend([Job.posted_at.is_not(None), not_(recent)])
    elif freshness == "unknown":
        filters.append(Job.posted_at.is_(None))
    total = await session.scalar(select(func.count(Job.id)).where(*filters))
    jobs = (
        await session.scalars(
            select(Job)
            .where(*filters)
            .order_by(Job.overall_score.desc(), Job.discovered_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = [
        JobRead.model_validate(job).model_copy(
            update={
                "freshness_status": freshness_status(
                    as_utc(job.posted_at),
                    job.posted_at_precision,
                    now,
                    runtime.freshness_hours,
                    runtime.secondary_freshness_hours,
                )
            }
        )
        for job in jobs
    ]
    return JobPage(items=items, total=total or 0)
