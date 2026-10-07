from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.connectors.base import JobConnector
from app.discovery.jsonld import age_bounds_hours
from app.discovery.roles import RoleClassification, classify_role
from app.models import CareerSource, Company, Job, JobSource
from app.schemas import IngestedJob, IngestionResult
from app.services.deduplication import DeduplicationEngine
from app.services.normalization import NormalizedJob, normalize_job
from app.services.ranking import OpportunityScorer

logger = structlog.get_logger()


class DiscoveryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.deduplication = DeduplicationEngine()
        self.scorer = OpportunityScorer(settings)

    async def ingest(
        self, session: AsyncSession, connector: JobConnector, identifier: str
    ) -> IngestionResult:
        discovered_jobs = await connector.fetch(identifier)
        return await self.ingest_jobs(session, discovered_jobs)

    async def ingest_jobs(
        self,
        session: AsyncSession,
        jobs: list[IngestedJob],
        *,
        company: Company | None = None,
        career_source: CareerSource | None = None,
        complete: bool = False,
        relevant_only: bool = False,
        scan_started_at: datetime | None = None,
    ) -> IngestionResult:
        """Persist jobs through normalization, deduplication, and ranking.

        Jobs already known from the same source identity are refreshed and marked as seen
        even when they are stale or irrelevant, so a complete scan never closes a job that
        is still listed. Only a complete scan of ``career_source`` closes unseen jobs.
        """
        now = scan_started_at or datetime.now(UTC)
        created = deduplicated = stale_skipped = updated = irrelevant_skipped = 0
        company_score = company.discovery_score if company and company.score_breakdown else None
        for raw_job in jobs:
            if company is not None:
                raw_job = raw_job.model_copy(update={"company": company.name})
            classification = classify_role(raw_job.title, raw_job.description, raw_job.company)
            normalized = normalize_job(raw_job)
            existing_source = await session.scalar(
                select(JobSource).where(
                    JobSource.source == raw_job.source,
                    JobSource.external_id == raw_job.external_id,
                )
            )
            if existing_source is not None:
                job = await session.get(Job, existing_source.job_id)
                if job is not None and self._refresh(
                    job,
                    existing_source,
                    raw_job,
                    normalized,
                    classification,
                    now,
                    company,
                    career_source,
                    company_score,
                ):
                    updated += 1
                deduplicated += 1
                continue

            if relevant_only and not classification.is_target:
                irrelevant_skipped += 1
                continue
            bounds = age_bounds_hours(raw_job.posted_at, raw_job.posted_at_precision, now)
            max_age = max(self.settings.max_job_age_hours, self.settings.secondary_freshness_hours)
            if bounds and bounds[0] > max_age:
                stale_skipped += 1
                continue

            match, _source_exists = await self.deduplication.find_match(session, normalized)
            if match is None:
                score = self.scorer.score(
                    normalized, classification, company_score, company.name if company else None
                )
                match = Job(
                    company=raw_job.company,
                    title=raw_job.title,
                    normalized_title=normalized.normalized_title,
                    location=raw_job.location,
                    remote_status=raw_job.remote_status,
                    salary=raw_job.salary,
                    employment_type=raw_job.employment_type,
                    ats=raw_job.ats,
                    posted_at=raw_job.posted_at,
                    posted_at_precision=raw_job.posted_at_precision,
                    source_updated_at=raw_job.source_updated_at,
                    discovered_at=now,
                    last_seen_at=now,
                    listing_status="OPEN",
                    description=raw_job.description,
                    requirements=normalized.requirements,
                    preferred_requirements=normalized.preferred_requirements,
                    technology_stack=normalized.technology_stack,
                    experience_requirement=normalized.experience_requirement,
                    application_url=raw_job.application_url,
                    overall_score=score.overall,
                    score_breakdown=score.breakdown,
                    score_reasons=score.reasons,
                    company_id=company.id if company else None,
                    role_category=classification.category,
                    role_relevance=classification.relevance,
                )
                session.add(match)
                await session.flush()
                created += 1
                await logger.ainfo(
                    "JOB_DISCOVERED",
                    job_id=str(match.id),
                    source=raw_job.source,
                    external_id=raw_job.external_id,
                )
            else:
                deduplicated += 1
                match.last_seen_at = now
                if match.listing_status != "OPEN":
                    match.listing_status, match.closed_at = "OPEN", None
                if match.company_id is None and company is not None:
                    match.company_id = company.id
                await logger.ainfo(
                    "JOB_DEDUPLICATED",
                    job_id=str(match.id),
                    source=raw_job.source,
                    external_id=raw_job.external_id,
                )
            session.add(
                JobSource(
                    job_id=match.id,
                    source=raw_job.source,
                    external_id=raw_job.external_id,
                    source_url=raw_job.source_url,
                    raw_payload=raw_job.raw_payload,
                    discovered_at=now,
                    career_source_id=career_source.id if career_source else None,
                    last_seen_at=now,
                    listing_status="OPEN",
                )
            )
        await session.flush()
        closed = (
            await self.close_unseen(session, career_source, now)
            if complete and career_source is not None
            else 0
        )
        await session.commit()
        return IngestionResult(
            discovered=len(jobs),
            created=created,
            deduplicated=deduplicated,
            stale_skipped=stale_skipped,
            updated=updated,
            irrelevant_skipped=irrelevant_skipped,
            closed=closed,
        )

    def _refresh(
        self,
        job: Job,
        source: JobSource,
        raw: IngestedJob,
        normalized: NormalizedJob,
        classification: RoleClassification,
        now: datetime,
        company: Company | None,
        career_source: CareerSource | None,
        company_score: float | None,
    ) -> bool:
        source.last_seen_at = now
        source.listing_status, source.closed_at = "OPEN", None
        source.source_url = raw.source_url
        source.raw_payload = raw.raw_payload
        if career_source is not None and source.career_source_id is None:
            source.career_source_id = career_source.id
        changed = False
        if job.listing_status != "OPEN":
            job.listing_status, job.closed_at = "OPEN", None
            changed = True
        job.last_seen_at = now
        updates = {
            "title": raw.title,
            "description": raw.description,
            "location": raw.location,
            "application_url": raw.application_url,
            "salary": raw.salary or job.salary,
            "employment_type": raw.employment_type or job.employment_type,
            "remote_status": raw.remote_status
            if raw.remote_status != "unknown"
            else job.remote_status,
        }
        for field, value in updates.items():
            if value is not None and getattr(job, field) != value:
                setattr(job, field, value)
                changed = True
        if raw.posted_at is not None and job.posted_at is None:
            job.posted_at, job.posted_at_precision = raw.posted_at, raw.posted_at_precision
            changed = True
        if raw.source_updated_at is not None:
            job.source_updated_at = raw.source_updated_at
        if company is not None and job.company_id is None:
            job.company_id = company.id
        if changed:
            job.normalized_title = normalized.normalized_title
            job.requirements = normalized.requirements
            job.preferred_requirements = normalized.preferred_requirements
            job.technology_stack = normalized.technology_stack
            job.experience_requirement = normalized.experience_requirement
        job.role_category, job.role_relevance = classification.category, classification.relevance
        score = self.scorer.score(
            normalized, classification, company_score, company.name if company else None
        )
        job.overall_score, job.score_breakdown, job.score_reasons = (
            score.overall,
            score.breakdown,
            score.reasons,
        )
        return changed

    async def close_unseen(
        self, session: AsyncSession, career_source: CareerSource, scan_started_at: datetime
    ) -> int:
        unseen = (
            await session.scalars(
                select(JobSource).where(
                    JobSource.career_source_id == career_source.id,
                    JobSource.listing_status == "OPEN",
                    or_(JobSource.last_seen_at.is_(None), JobSource.last_seen_at < scan_started_at),
                )
            )
        ).all()
        closed = 0
        for source in unseen:
            source.listing_status, source.closed_at = "CLOSED", scan_started_at
            job = await session.get(Job, source.job_id)
            if job is None or job.listing_status == "CLOSED":
                continue
            await session.refresh(job, ["sources"])
            if all(item.listing_status == "CLOSED" for item in job.sources):
                job.listing_status, job.closed_at = "CLOSED", scan_started_at
                closed += 1
        return closed
