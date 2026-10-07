"""Company universe persistence: matching, evidence, sources, frontier, and scoring."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.discovery.ats import PlatformMatch, detect_platform
from app.discovery.extraction import HIRING_REASONS, CandidateSignal
from app.discovery.jsonld import (
    TIMEZONE_SLACK_HOURS,
    canonical_url,
    freshness_status,
    parse_datetime,
)
from app.discovery.names import (
    clean_display_name,
    company_key,
    is_india_location,
    is_plausible_company_name,
    normalize_company_name,
)
from app.discovery.scoring import CompanyStats, company_confidence, frontier_priority, score_company
from app.discovery.settings_store import RuntimeSettings
from app.models import (
    CareerSource,
    Company,
    CompanyDiscoveryEvent,
    CompanyRelationship,
    DiscoveryCandidate,
    Job,
)
from app.services.normalization import TECH_ALIASES

COMPANY_STATUSES = ("DISCOVERED", "UNVERIFIED", "VERIFIED", "ACTIVE", "INACTIVE")
REASON_LABELS = {
    "JOB_SEARCH": "Job-market search",
    "LINKEDIN_HIRING_SIGNAL": "LinkedIn hiring signal",
    "X_HIRING_SIGNAL": "X hiring signal",
    "SEARCH_ENGINE": "Search engine",
    "SIMILAR_COMPANY": "Similar-company discovery",
    "STARTUP_DISCOVERY": "Startup discovery",
    "CAREER_PAGE": "Career page",
    "MANUAL_SEED": "Manual seed",
}
_METADATA_FIELDS = (
    "industry",
    "company_stage",
    "employee_range",
    "india_presence",
    "remote_presence",
    "company_type",
    "headquarters",
)
DATA_TECH = set(TECH_ALIASES) - {"Python", "SQL", "Kubernetes", "Terraform"}


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def fingerprint(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode()).hexdigest()


def event_status_for(company: Company) -> str:
    return {"IGNORED": "IGNORED", "ACCEPTED": "ACCEPTED"}.get(company.review_status, "PENDING")


async def find_company(
    session: AsyncSession, name: str, domain: str | None = None
) -> Company | None:
    key = company_key(name)
    if key:
        company = await session.scalar(select(Company).where(Company.name_key == key))
        if company is not None:
            return company
    if domain:
        return await session.scalar(select(Company).where(Company.domain == domain).limit(1))
    return None


def new_company(name: str, **fields: Any) -> Company:
    defaults: dict[str, Any] = {
        "aliases": [],
        "known_technologies": [],
        "data_infrastructure_signals": [],
        "engineering_signals": [],
        "score_breakdown": {},
        "score_reasons": [],
    }
    defaults.update(fields)
    return Company(name=clean_display_name(name), name_key=company_key(name), **defaults)


async def upsert_seed(
    session: AsyncSession, name: str, tier: str | None, runtime: RuntimeSettings
) -> tuple[Company, bool]:
    company = await find_company(session, name)
    created = company is None
    if company is None:
        company = new_company(
            name,
            tier=tier or runtime.default_tier,
            tier_source="user" if tier else "default",
            status="DISCOVERED",
            review_status="ACCEPTED",
            is_seed=True,
            discovery_source="manual",
            discovery_reason="Added manually as a seed company.",
            confidence=0.0,
        )
        session.add(company)
        await session.flush()
    else:
        was_ignored = company.review_status == "IGNORED"
        company.is_seed = True
        company.review_status = "ACCEPTED"
        if tier:
            company.tier = tier
            company.tier_source = "user"
        if was_ignored and company.status == "INACTIVE":
            company.status = "DISCOVERED"
    await record_event(
        session,
        company,
        reason="MANUAL_SEED",
        source="manual",
        source_url=None,
        evidence="Added manually as a seed company.",
        confidence=0.0,
        evidence_data={},
        status="ACCEPTED",
        key="seed",
    )
    return company, created


async def apply_signal_to_company(
    session: AsyncSession,
    signal: CandidateSignal,
    runtime: RuntimeSettings,
    *,
    allow_create: Any,
) -> tuple[Company | None, bool]:
    """Find or create the company named by ``signal``. ``allow_create`` enforces budgets."""
    name = clean_display_name(signal.company_name)
    if not company_key(name) or not is_plausible_company_name(name):
        return None, False
    company = await find_company(session, name, signal.domain)
    created = False
    if company is None:
        if not allow_create():
            return None, False
        company = new_company(
            name,
            tier=runtime.default_tier,
            status="DISCOVERED",
            review_status="PENDING",
            discovery_source=signal.source,
            discovery_reason=signal.evidence[:1000],
            confidence=signal.confidence,
            domain=signal.domain,
        )
        session.add(company)
        await session.flush()
        created = True
    else:
        if company.domain is None and signal.domain:
            other = await session.scalar(
                select(Company.id).where(Company.domain == signal.domain, Company.id != company.id)
            )
            if other is None:
                company.domain = signal.domain
        alias_key = company_key(signal.company_name)
        if alias_key != company.name_key and signal.company_name not in (company.aliases or []):
            company.aliases = [*(company.aliases or []), clean_display_name(signal.company_name)][
                :20
            ]
    for field in _METADATA_FIELDS:
        value = signal.metadata.get(field)
        if value is not None and getattr(company, field) is None:
            setattr(company, field, value)
    return company, created


async def record_event(
    session: AsyncSession,
    company: Company,
    *,
    reason: str,
    source: str,
    source_url: str | None,
    evidence: str,
    confidence: float,
    evidence_data: dict[str, Any],
    run_id: uuid.UUID | None = None,
    status: str | None = None,
    key: str | None = None,
) -> CompanyDiscoveryEvent:
    now = datetime.now(UTC)
    print_key = fingerprint(company.id, reason, source, source_url, key)
    event = await session.scalar(
        select(CompanyDiscoveryEvent).where(CompanyDiscoveryEvent.fingerprint == print_key)
    )
    if event is not None:
        event.observation_count += 1
        event.last_seen_at = now
        event.confidence = max(event.confidence, confidence)
        event.evidence = evidence[:4000]
        event.evidence_data = evidence_data
        return event
    event = CompanyDiscoveryEvent(
        company_id=company.id,
        run_id=run_id,
        fingerprint=print_key,
        source=source,
        source_url=source_url,
        reason=reason,
        evidence=evidence[:4000],
        evidence_data=evidence_data,
        confidence=confidence,
        status=status or event_status_for(company),
        discovered_at=now,
        last_seen_at=now,
    )
    session.add(event)
    await session.flush()
    return event


def signal_evidence_data(signal: CandidateSignal) -> dict[str, Any]:
    return {
        "role_title": signal.role_title,
        "role_relevance": signal.role_relevance,
        "location": signal.location,
        "signal_date": signal.signal_date.isoformat() if signal.signal_date else None,
        "signal_date_precision": signal.signal_date_precision,
        "domain": signal.domain,
        "career_url": signal.career_url,
        "job_url": signal.job_url,
        "query": signal.query,
        "related_company": signal.related_company,
        **{
            key: value
            for key, value in signal.metadata.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        },
    }


async def ensure_career_source(
    session: AsyncSession,
    company: Company,
    url: str,
    *,
    discovered_via: str,
    evidence: str | None = None,
    match: PlatformMatch | None = None,
) -> CareerSource:
    match = match or detect_platform(url)
    source_url = canonical_url(match.canonical_url if match.platform != "unknown" else url)
    source = await session.scalar(
        select(CareerSource).where(
            CareerSource.company_id == company.id, CareerSource.url == source_url
        )
    )
    if source is not None:
        return source
    platform = match.platform if match.platform != "unknown" else "generic_html"
    source = CareerSource(
        company_id=company.id,
        url=source_url,
        platform=platform,
        platform_confidence=match.confidence if match.platform != "unknown" else 0.3,
        platform_identifier=match.identifier,
        active=True,
        discovered_via=discovered_via[:60],
        evidence=(evidence or "")[:2000] or None,
        scan_status="PENDING",
    )
    session.add(source)
    await session.flush()
    if not company.careers_url:
        company.careers_url = source_url
    return source


async def upsert_relationship(
    session: AsyncSession,
    source_company: Company,
    target_company: Company,
    relationship_type: str,
    confidence: float,
    evidence: str,
    evidence_url: str | None,
) -> CompanyRelationship | None:
    if source_company.id == target_company.id:
        return None
    existing = await session.scalar(
        select(CompanyRelationship).where(
            CompanyRelationship.source_company_id == source_company.id,
            CompanyRelationship.target_company_id == target_company.id,
            CompanyRelationship.relationship_type == relationship_type,
        )
    )
    if existing is not None:
        existing.confidence = max(existing.confidence, confidence)
        existing.evidence = evidence[:2000]
        return existing
    relationship = CompanyRelationship(
        source_company_id=source_company.id,
        target_company_id=target_company.id,
        relationship_type=relationship_type,
        confidence=confidence,
        evidence=evidence[:2000],
        evidence_url=evidence_url,
    )
    session.add(relationship)
    await session.flush()
    return relationship


async def upsert_candidate(
    session: AsyncSession,
    company: Company,
    *,
    reason: str,
    depth: int,
    run_id: uuid.UUID | None,
    stats: CompanyStats,
    runtime: RuntimeSettings,
) -> DiscoveryCandidate | None:
    if company.review_status == "IGNORED":
        return None
    priority, breakdown = frontier_priority(stats, runtime.company_priority_weights)
    priority += round(company.discovery_score / 10, 1)
    candidate = await session.scalar(
        select(DiscoveryCandidate).where(
            DiscoveryCandidate.company_id == company.id,
            DiscoveryCandidate.status.in_(("PENDING", "IN_PROGRESS")),
        )
    )
    if candidate is not None:
        candidate.priority = max(candidate.priority, priority)
        candidate.priority_breakdown = breakdown
        candidate.depth = min(candidate.depth, depth)
        if reason not in candidate.reason:
            candidate.reason = f"{candidate.reason}; {reason}"[:2000]
        return candidate
    candidate = DiscoveryCandidate(
        company_id=company.id,
        reason=reason[:2000],
        priority=priority,
        priority_breakdown=breakdown,
        status="PENDING",
        depth=depth,
        source_run_id=run_id,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def compute_stats(
    session: AsyncSession, company: Company, runtime: RuntimeSettings, now: datetime
) -> CompanyStats:
    stats = CompanyStats(
        is_seed=company.is_seed,
        company_stage=company.company_stage,
        india_presence=company.india_presence,
        remote_presence=company.remote_presence,
        product_company="startup" in (company.company_type or "").lower(),
    )
    rows = (
        await session.execute(
            select(
                Job.role_category,
                Job.role_relevance,
                Job.posted_at,
                Job.posted_at_precision,
                Job.location,
                Job.remote_status,
                Job.technology_stack,
            ).where(Job.company_id == company.id, Job.listing_status == "OPEN")
        )
    ).all()
    technologies: Counter[str] = Counter()
    for row in rows:
        stats.open_jobs += 1
        target = (row.role_relevance or 0) >= 0.5
        if target and row.role_category == "data_engineering":
            stats.data_roles += 1
            status = freshness_status(
                as_utc(row.posted_at),
                row.posted_at_precision,
                now,
                runtime.freshness_hours,
                runtime.secondary_freshness_hours,
            )
            stats.fresh_data_roles += status == "fresh"
            stats.recent_data_roles += status == "recent"
        elif target and row.role_category == "platform_infrastructure":
            stats.platform_roles += 1
        if target:
            stats.job_relevance.append(float(row.role_relevance))
            technologies.update(row.technology_stack or [])
            if is_india_location(row.location):
                stats.india_presence = True
            if row.remote_status == "remote":
                stats.remote_presence = True
    stats.technologies = [name for name, _count in technologies.most_common(12)]

    best: dict[tuple[str, str], float] = {}
    hiring_sources: set[str] = set()
    events = (
        await session.scalars(
            select(CompanyDiscoveryEvent).where(CompanyDiscoveryEvent.company_id == company.id)
        )
    ).all()
    for event in events:
        if event.status == "IGNORED" or event.reason == "MANUAL_SEED":
            continue
        best[(event.reason, event.source)] = max(
            best.get((event.reason, event.source), 0.0), event.confidence
        )
        data = event.evidence_data or {}
        relevance = float(data.get("role_relevance") or 0)
        if event.reason in HIRING_REASONS and event.reason != "CAREER_PAGE" and relevance >= 0.5:
            stats.hiring_signals += 1
            hiring_sources.add(event.source)
            stats.evidence_relevance.append(relevance)
            if event.source == "hackernews" or event.reason in {
                "X_HIRING_SIGNAL",
                "LINKEDIN_HIRING_SIGNAL",
            }:
                stats.hiring_posts += 1
            signal_date, _precision = parse_datetime(data.get("signal_date"))
            if signal_date is not None:
                age = max(0.0, (now - signal_date).total_seconds() / 3600)
                if stats.latest_signal_age_hours is None or age < stats.latest_signal_age_hours:
                    stats.latest_signal_age_hours = age
        if data.get("product_company"):
            stats.product_company = True
        if data.get("india_presence") is True:
            stats.india_presence = True
        if data.get("remote_presence") is True:
            stats.remote_presence = True
        if event.reason == "SIMILAR_COMPANY" and data.get("related_company"):
            stats.similar_to.append(str(data["related_company"]))
    stats.hiring_sources = len(hiring_sources)
    stats.confidences = list(best.values())
    stats.verified_hiring = stats.data_roles + stats.platform_roles > 0
    return stats


def _summary(
    company: Company,
    stats: CompanyStats,
    events: list[CompanyDiscoveryEvent],
    runtime: RuntimeSettings,
) -> str:
    lines: list[str] = []
    if company.is_seed:
        lines.append("\u2713 Added manually as a seed company")
    if stats.data_roles:
        lines.append(
            f"\u2713 {stats.data_roles} data-engineering opening(s) found on career sources"
        )
    if stats.platform_roles:
        lines.append(f"\u2713 {stats.platform_roles} platform/infrastructure opening(s)")
    if stats.fresh_data_roles:
        lines.append(f"\u2713 Hiring activity within the last {runtime.freshness_hours:g} hours")
    elif stats.recent_data_roles or (
        stats.latest_signal_age_hours is not None and stats.latest_signal_age_hours <= 168
    ):
        lines.append("\u2713 Hiring activity within the last 7 days")
    if stats.india_presence:
        lines.append("\u2713 India presence evidenced")
    if stats.remote_presence:
        lines.append("\u2713 Remote roles evidenced")
    if stats.product_company:
        lines.append("\u2713 Product company (startup dataset)")
    if stats.similar_to:
        lines.append(f"\u2713 Listed as similar to {', '.join(sorted(set(stats.similar_to))[:3])}")
    external = sorted(
        (
            event
            for event in events
            if event.reason not in {"MANUAL_SEED", "CAREER_PAGE"} and event.status != "IGNORED"
        ),
        key=lambda event: -event.confidence,
    )
    for event in external[:2]:
        role = (event.evidence_data or {}).get("role_title")
        detail = f" for {role}" if role else ""
        lines.append(
            f"\u2713 {REASON_LABELS.get(event.reason, event.reason)} via {event.source}{detail}"
        )
    return "\n".join(lines) or "Evidence pending verification."


async def refresh_company(
    session: AsyncSession, company: Company, runtime: RuntimeSettings, now: datetime | None = None
) -> CompanyStats:
    now = now or datetime.now(UTC)
    stats = await compute_stats(session, company, runtime, now)
    score, breakdown, reasons = score_company(stats, runtime.company_score_weights)
    company.discovery_score, company.score_breakdown, company.score_reasons = (
        score,
        breakdown,
        reasons,
    )
    if stats.confidences or stats.verified_hiring:
        company.confidence = company_confidence(stats)
    if stats.technologies:
        company.known_technologies = stats.technologies
        company.data_infrastructure_signals = [
            name for name in stats.technologies if name in DATA_TECH
        ]
    company.engineering_signals = (
        [f"{stats.platform_roles} platform/infrastructure opening(s)"]
        if stats.platform_roles
        else []
    )
    if stats.india_presence:
        company.india_presence = True
    if stats.remote_presence:
        company.remote_presence = True
    events = (
        await session.scalars(
            select(CompanyDiscoveryEvent).where(CompanyDiscoveryEvent.company_id == company.id)
        )
    ).all()
    company.discovery_reason = _summary(company, stats, list(events), runtime)
    return stats


def job_counts_subquery(runtime: RuntimeSettings, now: datetime):
    fresh_cut = now - timedelta(hours=runtime.freshness_hours)
    imprecise_cut = now - timedelta(hours=runtime.freshness_hours - TIMEZONE_SLACK_HOURS)
    is_data = and_(Job.role_category == "data_engineering", Job.role_relevance >= 0.5)
    is_platform = and_(Job.role_category == "platform_infrastructure", Job.role_relevance >= 0.5)
    fresh = or_(
        and_(Job.posted_at_precision == "datetime", Job.posted_at >= fresh_cut),
        and_(
            Job.posted_at_precision.in_(("date", "local_datetime")), Job.posted_at >= imprecise_cut
        ),
    )
    return (
        select(
            Job.company_id.label("company_id"),
            func.count(Job.id).label("jobs_count"),
            func.sum(case((is_data, 1), else_=0)).label("data_roles_count"),
            func.sum(case((and_(is_data, fresh), 1), else_=0)).label("fresh_data_roles_count"),
            func.sum(case((is_platform, 1), else_=0)).label("platform_roles_count"),
        )
        .where(Job.company_id.is_not(None), Job.listing_status == "OPEN")
        .group_by(Job.company_id)
        .subquery()
    )


def normalized_seed_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = clean_display_name(raw or "")
        key = company_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


__all__ = [
    "COMPANY_STATUSES",
    "REASON_LABELS",
    "apply_signal_to_company",
    "as_utc",
    "compute_stats",
    "ensure_career_source",
    "event_status_for",
    "find_company",
    "job_counts_subquery",
    "normalize_company_name",
    "normalized_seed_names",
    "record_event",
    "refresh_company",
    "signal_evidence_data",
    "upsert_candidate",
    "upsert_relationship",
    "upsert_seed",
]
