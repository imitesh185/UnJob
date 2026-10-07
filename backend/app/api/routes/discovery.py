import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import and_, case, func, or_, select

from app.api.dependencies import SessionDep, SettingsDep
from app.discovery.jsonld import TIMEZONE_SLACK_HOURS
from app.discovery.scoring import confidence_band
from app.discovery.settings_store import (
    ExplorationBudgets,
    RuntimeSettings,
    RuntimeSettingsPatch,
    load_runtime_settings,
    provider_statuses,
    save_runtime_settings,
)
from app.intelligence.llm import LLMRewriter
from app.models import Company, CompanyDiscoveryEvent, DiscoveryCandidate, DiscoveryRun, Job
from app.schemas import (
    DiscoverySummaryRead,
    EventPage,
    EventRead,
    FrontierPage,
    FrontierRead,
    ReviewRequest,
    RunCreate,
    RunPage,
    RunRead,
)
from app.services.candidates import get_candidate
from app.services.companies import compute_stats, upsert_candidate
from app.services.intelligence import sync_s_tier
from app.services.queue import ACTIVE_STATUSES, enqueue_run

router = APIRouter(prefix="/discovery", tags=["discovery"])


def _settings_payload(settings, runtime: RuntimeSettings) -> dict:
    llm = LLMRewriter(settings)
    return {
        **runtime.model_dump(),
        "provider_status": provider_statuses(settings, runtime),
        "llm_status": {
            "configured": llm.configured,
            "provider": settings.llm_provider,
            "model": settings.llm_model if llm.configured else None,
            "enabled": runtime.llm_rewrite_enabled and llm.configured,
            "detail": (
                "AI rewording is configured; every rewrite is verified against your facts and "
                "discarded if it adds anything."
                if llm.configured
                else "Not configured (set LLM_PROVIDER, LLM_API_KEY and LLM_MODEL). Tailoring is "
                "deterministic: it selects, orders and emphasizes your own wording."
            ),
        },
    }


# Settings whose change requires re-ranking or re-tailoring.
_ANALYSIS_KEYS = {
    "s_tier_companies", "company_registry", "tailoring_policy", "tailoring_require_location_fit",
    "target_score_weights", "fit_weights", "application_priority_weights",
    "application_priority_thresholds", "tier_values", "rusty_after_years", "positioning_profiles",
    "archetype_signals", "freshness_hours", "secondary_freshness_hours", "tiers",
}  # fmt: skip


@router.get("/settings")
async def get_settings_view(session: SessionDep, settings: SettingsDep) -> dict:
    return _settings_payload(settings, await load_runtime_settings(session, settings))


@router.patch("/settings")
async def update_settings(
    body: RuntimeSettingsPatch, session: SessionDep, settings: SettingsDep
) -> dict:
    try:
        runtime = await save_runtime_settings(session, settings, body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {"loc": ["body", *map(str, error["loc"])], "msg": error["msg"]}
                for error in exc.errors()
            ],
        ) from exc
    changed = set(body.model_dump(exclude_unset=True))
    payload = _settings_payload(settings, runtime)
    if changed & _ANALYSIS_KEYS:
        await sync_s_tier(session, runtime)
        if await get_candidate(session) is not None:
            run, _created = await enqueue_run(
                session,
                "candidate_analysis",
                params={},
                trigger="settings_change",
                dedupe_key="candidate_analysis:full",
            )
            payload["analysis_run_id"] = str(run.id)
    return payload


@router.post("/runs", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def create_run(body: RunCreate, session: SessionDep, settings: SettingsDep) -> RunRead:
    runtime = await load_runtime_settings(session, settings)
    overrides = body.budgets.model_dump(exclude_none=True) if body.budgets else {}
    budgets = ExplorationBudgets.model_validate({**runtime.budgets.model_dump(), **overrides})
    run, _created = await enqueue_run(
        session, body.kind, budgets=budgets.model_dump(), dedupe_key=f"kind:{body.kind}"
    )
    return RunRead.model_validate(run)


@router.get("/runs", response_model=RunPage)
async def list_runs(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    kind: str | None = None,
) -> RunPage:
    filters = [DiscoveryRun.kind == kind] if kind else []
    total = await session.scalar(select(func.count(DiscoveryRun.id)).where(*filters))
    runs = (
        await session.scalars(
            select(DiscoveryRun)
            .where(*filters)
            .order_by(DiscoveryRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return RunPage(items=[RunRead.model_validate(run) for run in runs], total=total or 0)


@router.get("/runs/{run_id}", response_model=RunRead)
async def get_run(run_id: uuid.UUID, session: SessionDep) -> RunRead:
    run = await session.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return RunRead.model_validate(run)


def _event_read(event: CompanyDiscoveryEvent, company: Company) -> EventRead:
    return EventRead(
        id=event.id,
        company_id=event.company_id,
        company_name=company.name,
        source=event.source,
        source_url=event.source_url,
        reason=event.reason,
        evidence=event.evidence,
        evidence_data=event.evidence_data or {},
        confidence=event.confidence,
        discovery_score=company.discovery_score,
        status=event.status,
        observation_count=event.observation_count,
        discovered_at=event.discovered_at,
        last_seen_at=event.last_seen_at,
    )


@router.get("/feed", response_model=EventPage)
async def discovery_feed(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    reason: str | None = None,
    include_seeds: bool = False,
) -> EventPage:
    filters = []
    if not include_seeds:
        filters.append(CompanyDiscoveryEvent.reason != "MANUAL_SEED")
    if status_filter:
        filters.append(CompanyDiscoveryEvent.status == status_filter.upper())
    if reason:
        filters.append(CompanyDiscoveryEvent.reason == reason.upper())
    base = (
        select(CompanyDiscoveryEvent, Company)
        .join(Company, Company.id == CompanyDiscoveryEvent.company_id)
        .where(*filters)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await session.execute(
            base.order_by(
                case((CompanyDiscoveryEvent.status == "PENDING", 0), else_=1),
                Company.discovery_score.desc(),
                CompanyDiscoveryEvent.last_seen_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return EventPage(
        items=[_event_read(event, company) for event, company in rows], total=total or 0
    )


@router.post("/feed/{event_id}/review", response_model=EventRead)
async def review_event(
    event_id: uuid.UUID, body: ReviewRequest, session: SessionDep, settings: SettingsDep
) -> EventRead:
    event = await session.get(CompanyDiscoveryEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Discovery event not found.")
    if event.status != "PENDING":
        raise HTTPException(status_code=409, detail=f"Event was already reviewed ({event.status}).")
    company = await session.get(Company, event.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")
    now = datetime.now(UTC)
    runtime = await load_runtime_settings(session, settings)
    pending = (
        await session.scalars(
            select(CompanyDiscoveryEvent).where(
                CompanyDiscoveryEvent.company_id == company.id,
                CompanyDiscoveryEvent.status == "PENDING",
            )
        )
    ).all()
    if body.action == "accept":
        for item in pending:
            item.status, item.reviewed_at = "ACCEPTED", now
        company.review_status = "ACCEPTED"
        if company.status in {"DISCOVERED", "UNVERIFIED"}:
            stats = await compute_stats(session, company, runtime, now)
            await upsert_candidate(
                session,
                company,
                reason="Accepted from the discovery feed",
                depth=1,
                run_id=None,
                stats=stats,
                runtime=runtime,
            )
    else:
        targets = [event] if company.is_seed else pending
        for item in targets:
            item.status, item.reviewed_at = "IGNORED", now
        if not company.is_seed:
            company.review_status = "IGNORED"
            company.status = "INACTIVE"
            candidates = (
                await session.scalars(
                    select(DiscoveryCandidate).where(
                        DiscoveryCandidate.company_id == company.id,
                        DiscoveryCandidate.status == "PENDING",
                    )
                )
            ).all()
            for candidate in candidates:
                candidate.status = "SKIPPED"
    await session.commit()
    await session.refresh(event)
    return _event_read(event, company)


@router.get("/frontier", response_model=FrontierPage)
async def frontier(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
) -> FrontierPage:
    filters = [DiscoveryCandidate.status == status_filter.upper()] if status_filter else []
    base = (
        select(DiscoveryCandidate, Company.name)
        .join(Company, Company.id == DiscoveryCandidate.company_id)
        .where(*filters)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await session.execute(
            base.order_by(
                case(
                    (DiscoveryCandidate.status == "PENDING", 0),
                    (DiscoveryCandidate.status == "IN_PROGRESS", 1),
                    else_=2,
                ),
                DiscoveryCandidate.priority.desc(),
                DiscoveryCandidate.created_at,
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return FrontierPage(
        items=[
            FrontierRead(
                id=candidate.id,
                company_id=candidate.company_id,
                company_name=name,
                reason=candidate.reason,
                priority=candidate.priority,
                priority_breakdown=candidate.priority_breakdown or {},
                status=candidate.status,
                depth=candidate.depth,
                error=candidate.error,
                created_at=candidate.created_at,
            )
            for candidate, name in rows
        ],
        total=total or 0,
    )


@router.post(
    "/frontier/{candidate_id}/explore", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED
)
async def explore_candidate(
    candidate_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> RunRead:
    candidate = await session.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Frontier entry not found.")
    if candidate.status != "PENDING":
        raise HTTPException(
            status_code=409, detail=f"Frontier entry is {candidate.status}, not PENDING."
        )
    runtime = await load_runtime_settings(session, settings)
    budgets = {
        **runtime.budgets.model_dump(),
        "max_company_expansion": max(1, runtime.budgets.max_company_expansion),
    }
    run, _ = await enqueue_run(
        session,
        "frontier_explore",
        params={"candidate_id": str(candidate.id)},
        budgets=budgets,
        dedupe_key=f"frontier:{candidate.id}",
    )
    return RunRead.model_validate(run)


@router.get("/summary", response_model=DiscoverySummaryRead)
async def summary(session: SessionDep, settings: SettingsDep) -> DiscoverySummaryRead:
    runtime = await load_runtime_settings(session, settings)
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    discovered_today = (
        await session.scalars(
            select(Company.confidence).where(
                Company.created_at >= today, Company.is_seed.is_(False)
            )
        )
    ).all()
    bands = [confidence_band(value) for value in discovered_today]
    fresh_cut = now - timedelta(hours=runtime.freshness_hours)
    imprecise_cut = now - timedelta(hours=runtime.freshness_hours - TIMEZONE_SLACK_HOURS)
    fresh_jobs = await session.scalar(
        select(func.count(Job.id)).where(
            Job.listing_status == "OPEN",
            Job.role_category == "data_engineering",
            Job.role_relevance >= 0.5,
            or_(
                and_(Job.posted_at_precision == "datetime", Job.posted_at >= fresh_cut),
                and_(
                    Job.posted_at_precision.in_(("date", "local_datetime")),
                    Job.posted_at >= imprecise_cut,
                ),
            ),
        )
    )
    return DiscoverySummaryRead(
        companies_total=await session.scalar(
            select(func.count(Company.id)).where(Company.review_status != "IGNORED")
        )
        or 0,
        companies_discovered_today=len(discovered_today),
        high_confidence=bands.count("high"),
        medium_confidence=bands.count("medium"),
        low_confidence=bands.count("low"),
        frontier_pending=await session.scalar(
            select(func.count(DiscoveryCandidate.id)).where(DiscoveryCandidate.status == "PENDING")
        )
        or 0,
        active_runs=await session.scalar(
            select(func.count(DiscoveryRun.id)).where(DiscoveryRun.status.in_(ACTIVE_STATUSES))
        )
        or 0,
        fresh_data_jobs=fresh_jobs or 0,
        pending_reviews=await session.scalar(
            select(func.count(CompanyDiscoveryEvent.id)).where(
                CompanyDiscoveryEvent.status == "PENDING"
            )
        )
        or 0,
    )
