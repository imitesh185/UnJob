import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import SessionDep, SettingsDep
from app.discovery.http import FetchError, validate_public_url
from app.discovery.names import clean_display_name, company_key
from app.discovery.settings_store import RuntimeSettings, load_runtime_settings
from app.models import (
    CareerSource,
    Company,
    CompanyDiscoveryEvent,
    CompanyRelationship,
    Job,
    TargetScore,
)
from app.schemas import (
    CareerSourceCreated,
    CareerSourceRead,
    CompanyDetail,
    CompanyPage,
    CompanyPatch,
    CompanyRead,
    EventRead,
    RelationshipRead,
    RunRead,
    SeedRequest,
    SeedResponse,
    SourceCreate,
)
from app.services.candidates import get_candidate
from app.services.companies import (
    compute_stats,
    ensure_career_source,
    job_counts_subquery,
    normalized_seed_names,
    upsert_candidate,
    upsert_seed,
)
from app.services.intelligence import sync_s_tier
from app.services.queue import enqueue_run

router = APIRouter(prefix="/companies", tags=["companies"])
_HOSTNAME = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _with_counts(company: Company, counts: dict[str, Any] | None) -> CompanyRead:
    read = CompanyRead.model_validate(company)
    values = counts or {}
    update: dict[str, Any] = {
        key: int(value or 0) for key, value in values.items() if key.endswith("_count")
    }
    for key in ("target_score", "target_breakdown", "target_reasons"):
        if key in values:
            update[key] = values[key]
    return read.model_copy(update=update)


async def _counts_for(
    session: AsyncSession, ids: list[uuid.UUID], runtime: RuntimeSettings
) -> dict[uuid.UUID, dict[str, Any]]:
    if not ids:
        return {}
    counts = job_counts_subquery(runtime, datetime.now(UTC))
    rows = (await session.execute(select(counts).where(counts.c.company_id.in_(ids)))).mappings()
    result: dict[uuid.UUID, dict[str, Any]] = {
        row["company_id"]: {key: row[key] for key in row if key != "company_id"} for row in rows
    }
    candidate = await get_candidate(session)
    if candidate is not None:
        targets = (
            await session.scalars(
                select(TargetScore).where(
                    TargetScore.candidate_id == candidate.id, TargetScore.company_id.in_(ids)
                )
            )
        ).all()
        for target in targets:
            entry = result.setdefault(target.company_id, {})
            entry["target_score"] = target.score
            entry["target_breakdown"] = target.breakdown or {}
            entry["target_reasons"] = target.reasons or []
    return result


async def _company_or_404(session: AsyncSession, company_id: uuid.UUID) -> Company:
    company = await session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found.")
    return company


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


def _boolean(value: str | None, name: str) -> bool | None:
    if value in (None, ""):
        return None
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise HTTPException(status_code=422, detail=f"{name} must be true or false.")


@router.get("", response_model=CompanyPage)
async def list_companies(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tier: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    min_score: float | None = Query(default=None, ge=0, le=100),
    fresh_hiring: str | None = None,
    industry: str | None = None,
    stage: str | None = None,
    location: str | None = None,
    remote: str | None = None,
    data_hiring: str | None = None,
    source: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="discovery", pattern="^(discovery|target)$"),
) -> CompanyPage:
    runtime = await load_runtime_settings(session, settings)
    counts = job_counts_subquery(runtime, datetime.now(UTC))
    data_count = func.coalesce(counts.c.data_roles_count, 0)
    fresh_count = func.coalesce(counts.c.fresh_data_roles_count, 0)
    candidate = await get_candidate(session)
    targets = (
        select(TargetScore.company_id, TargetScore.score)
        .where(TargetScore.candidate_id == candidate.id)
        .subquery()
        if candidate is not None
        else None
    )
    filters = []
    if tier:
        filters.append(Company.tier == tier)
    if status_filter:
        filters.append(Company.status == status_filter.upper())
    if min_score is not None:
        filters.append(Company.discovery_score >= min_score)
    if industry:
        filters.append(Company.industry.ilike(f"%{industry}%"))
    if stage:
        filters.append(Company.company_stage.ilike(f"%{stage}%"))
    if location:
        filters.append(
            Company.headquarters.ilike(f"%{location}%")
            if location.lower() != "india"
            else or_(Company.india_presence.is_(True), Company.headquarters.ilike("%india%"))
        )
    if source:
        filters.append(Company.discovery_source == source)
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(or_(Company.name.ilike(pattern), Company.domain.ilike(pattern)))
    remote_value = _boolean(remote, "remote")
    if remote_value is not None:
        filters.append(
            Company.remote_presence.is_(True)
            if remote_value
            else Company.remote_presence.is_(False)
        )
    for value, column, name in (
        (fresh_hiring, fresh_count, "fresh_hiring"),
        (data_hiring, data_count, "data_hiring"),
    ):
        parsed = _boolean(value, name)
        if parsed is not None:
            filters.append(column > 0 if parsed else column == 0)
    base = (
        select(Company).outerjoin(counts, counts.c.company_id == Company.id).where(and_(*filters))
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    order = [Company.discovery_score.desc(), fresh_count.desc(), Company.name]
    if sort == "target" and targets is not None:
        base = base.outerjoin(targets, targets.c.company_id == Company.id)
        order = [func.coalesce(targets.c.score, -1).desc(), *order]
    companies = (await session.scalars(base.order_by(*order).offset(offset).limit(limit))).all()
    by_id = await _counts_for(session, [company.id for company in companies], runtime)
    return CompanyPage(
        items=[_with_counts(company, by_id.get(company.id)) for company in companies],
        total=total or 0,
    )


@router.get("/{company_id}", response_model=CompanyDetail)
async def get_company(
    company_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> CompanyDetail:
    company = await _company_or_404(session, company_id)
    runtime = await load_runtime_settings(session, settings)
    counts = (await _counts_for(session, [company.id], runtime)).get(company.id)
    sources = (
        await session.scalars(
            select(CareerSource)
            .where(CareerSource.company_id == company.id)
            .order_by(CareerSource.active.desc(), CareerSource.created_at)
        )
    ).all()
    events = (
        await session.scalars(
            select(CompanyDiscoveryEvent)
            .where(CompanyDiscoveryEvent.company_id == company.id)
            .order_by(
                CompanyDiscoveryEvent.confidence.desc(), CompanyDiscoveryEvent.last_seen_at.desc()
            )
            .limit(50)
        )
    ).all()
    relationships = (
        await session.scalars(
            select(CompanyRelationship)
            .where(
                or_(
                    CompanyRelationship.source_company_id == company.id,
                    CompanyRelationship.target_company_id == company.id,
                )
            )
            .limit(50)
        )
    ).all()
    related_ids = {rel.source_company_id for rel in relationships} | {
        rel.target_company_id for rel in relationships
    }
    names = (
        dict(
            (
                await session.execute(
                    select(Company.id, Company.name).where(Company.id.in_(related_ids))
                )
            ).all()
        )
        if related_ids
        else {}
    )
    base = _with_counts(company, counts)
    return CompanyDetail(
        **base.model_dump(),
        sources=[CareerSourceRead.model_validate(source) for source in sources],
        events=[_event_read(event, company) for event in events],
        relationships=[
            RelationshipRead(
                id=rel.id,
                source_company_id=rel.source_company_id,
                source_company_name=names.get(rel.source_company_id, "UNKNOWN"),
                target_company_id=rel.target_company_id,
                target_company_name=names.get(rel.target_company_id, "UNKNOWN"),
                relationship_type=rel.relationship_type,
                confidence=rel.confidence,
                evidence=rel.evidence,
                evidence_url=rel.evidence_url,
            )
            for rel in relationships
        ],
    )


@router.post("/seeds", response_model=SeedResponse, status_code=status.HTTP_202_ACCEPTED)
async def add_seeds(body: SeedRequest, session: SessionDep, settings: SettingsDep) -> SeedResponse:
    runtime = await load_runtime_settings(session, settings)
    if body.tier and body.tier not in runtime.tiers:
        raise HTTPException(
            status_code=422, detail=f"Unknown tier '{body.tier}'. Configure tiers first."
        )
    names = normalized_seed_names(body.names)
    if not names:
        raise HTTPException(status_code=422, detail="No valid company names were provided.")
    companies: list[Company] = []
    to_explore: list[uuid.UUID] = []
    now = datetime.now(UTC)
    for name in names:
        company, _created = await upsert_seed(session, name, body.tier, runtime)
        companies.append(company)
        if company.last_explored_at is None or company.status in {"DISCOVERED", "UNVERIFIED"}:
            stats = await compute_stats(session, company, runtime, now)
            candidate = await upsert_candidate(
                session,
                company,
                reason="Manual seed",
                depth=0,
                run_id=None,
                stats=stats,
                runtime=runtime,
            )
            if candidate is not None:
                to_explore.append(company.id)
    await session.commit()
    # Seeds named on the configurable S-tier list become S-tier unless a tier was chosen.
    await sync_s_tier(session, runtime)
    for company in companies:
        await session.refresh(company)
    run_id = None
    if to_explore:
        run, _ = await enqueue_run(
            session,
            "seed_resolution",
            params={"company_ids": [str(company_id) for company_id in to_explore]},
            budgets=runtime.budgets.model_dump(),
        )
        run_id = run.id
    by_id = await _counts_for(session, [company.id for company in companies], runtime)
    return SeedResponse(
        companies=[_with_counts(company, by_id.get(company.id)) for company in companies],
        run_id=run_id,
    )


@router.patch("/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: uuid.UUID, body: CompanyPatch, session: SessionDep, settings: SettingsDep
) -> CompanyRead:
    company = await _company_or_404(session, company_id)
    runtime = await load_runtime_settings(session, settings)
    if body.name is not None:
        name = clean_display_name(body.name)
        key = company_key(name)
        if not key:
            raise HTTPException(status_code=422, detail="Name must contain letters or digits.")
        if key != company.name_key:
            conflict = await session.scalar(
                select(Company.name).where(Company.name_key == key, Company.id != company.id)
            )
            if conflict:
                raise HTTPException(
                    status_code=409, detail=f"Another company is already named {conflict}."
                )
            # The old name stays as an alias so later signals under it merge here.
            company.aliases = list(dict.fromkeys([*(company.aliases or []), company.name]))[-20:]
            company.name_key = key
        company.name = name
        await session.execute(update(Job).where(Job.company_id == company.id).values(company=name))
    if body.tier is not None:
        if body.tier not in runtime.tiers:
            raise HTTPException(status_code=422, detail=f"Unknown tier '{body.tier}'.")
        company.tier = body.tier
        company.tier_source = "user"
    if body.status is not None:
        company.status = body.status
    if body.domain is not None:
        domain = body.domain.strip().lower().rstrip(".")
        domain = domain[4:] if domain.startswith("www.") else domain
        if not _HOSTNAME.match(domain):
            raise HTTPException(
                status_code=422, detail="Domain must be a hostname such as example.com."
            )
        conflict = await session.scalar(
            select(Company.name).where(Company.domain == domain, Company.id != company.id)
        )
        if conflict:
            raise HTTPException(
                status_code=409, detail=f"Domain is already assigned to {conflict}."
            )
        company.domain = domain
    if body.careers_url is not None:
        try:
            url = validate_public_url(body.careers_url)
        except FetchError as exc:
            raise HTTPException(status_code=422, detail=f"careers_url: {exc.args[0]}") from exc
        company.careers_url = url
        await ensure_career_source(
            session, company, url, discovered_via="manual", evidence="Career URL entered manually."
        )
    await session.commit()
    if body.name is not None:
        # A renamed company may now match the configurable S-tier list.
        await sync_s_tier(session, runtime)
        await session.refresh(company)
    counts = (await _counts_for(session, [company.id], runtime)).get(company.id)
    return _with_counts(company, counts)


@router.post(
    "/{company_id}/sources",
    response_model=CareerSourceCreated,
    status_code=status.HTTP_201_CREATED,
)
async def add_source(
    company_id: uuid.UUID, body: SourceCreate, session: SessionDep, settings: SettingsDep
) -> CareerSourceCreated:
    company = await _company_or_404(session, company_id)
    try:
        url = validate_public_url(body.url)
    except FetchError as exc:
        raise HTTPException(status_code=422, detail=f"url: {exc.args[0]}") from exc
    source = await ensure_career_source(
        session, company, url, discovered_via="manual", evidence="Career source added manually."
    )
    source.active = True
    await session.commit()
    read = CareerSourceRead.model_validate(source)
    # The worker fetches the source; an UNSUPPORTED or BLOCKED result is recorded on it.
    runtime = await load_runtime_settings(session, settings)
    run, _ = await enqueue_run(
        session,
        "company_scan",
        params={"company_id": str(company.id)},
        budgets=runtime.budgets.model_dump(),
        dedupe_key=f"company_scan:{company.id}",
    )
    return CareerSourceCreated(**read.model_dump(), scan_run_id=run.id)


@router.post("/{company_id}/scan", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def scan_company(
    company_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> RunRead:
    company = await _company_or_404(session, company_id)
    runtime = await load_runtime_settings(session, settings)
    run, _ = await enqueue_run(
        session,
        "company_scan",
        params={"company_id": str(company.id)},
        budgets=runtime.budgets.model_dump(),
        dedupe_key=f"company_scan:{company.id}",
    )
    return RunRead.model_validate(run)
