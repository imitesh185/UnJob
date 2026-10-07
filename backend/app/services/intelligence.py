"""Candidate intelligence pipeline: JD analysis -> fit -> company target -> priority -> strategy
-> policy-driven resume tailoring -> application records. Runs in the worker, never in HTTP."""

from __future__ import annotations

import difflib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.discovery.jsonld import age_bounds_hours
from app.discovery.names import company_key
from app.discovery.roles import TARGET_CATEGORIES, classify_role
from app.discovery.settings_store import RuntimeSettings, load_runtime_settings
from app.intelligence.jd_analyzer import ANALYZER_VERSION, analyze_job, description_hash
from app.intelligence.llm import LLMError, LLMRewriter, RewriteRequest
from app.intelligence.matching import CandidateModel, MatchResult, build_candidate_model, match_job
from app.intelligence.positioning import positioning_for
from app.intelligence.quality import evaluate_resume
from app.intelligence.render import to_docx, to_html, to_markdown, to_text
from app.intelligence.tailoring import (
    GENERATOR,
    ClaimDraft,
    ResumeBase,
    build_resume_base,
    master_resume,
    tailor_resume,
)
from app.intelligence.targeting import (
    CompanyContext,
    application_priority,
    application_strategy,
    company_target_score,
    freshness_score,
    is_current_employer,
)
from app.intelligence.verification import verify_claim
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationScore,
    Candidate,
    CandidateFact,
    CandidateJobMatch,
    Company,
    Job,
    JobAnalysis,
    Resume,
    ResumeClaim,
    ResumeVariant,
    TargetScore,
)
from app.services.candidates import active_facts, get_candidate

RECOMMENDED_CLASSES = {"P0", "P1", "P2"}
STRATEGY_CLASSES = {"P0", "P1", "P2"}
# Application statuses the pipeline may advance automatically; anything later is the user's.
AUTOMATIC_STATUSES = {"DISCOVERED", "ANALYZED", "RECOMMENDED", "RESUME_GENERATED", "USER_REVIEW"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass
class AnalysisOutcome:
    jobs_analyzed: int = 0
    analyses_updated: int = 0
    matches: int = 0
    companies_scored: int = 0
    recommended: int = 0
    variants_generated: int = 0
    variants_skipped: int = 0
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CandidateContext:
    candidate: Candidate
    facts: list[CandidateFact]
    facts_by_id: dict[str, CandidateFact]
    model: CandidateModel
    base: ResumeBase
    resume: Resume | None


def s_tier_keys(runtime: RuntimeSettings) -> set[str]:
    keys = {company_key(name) for name in runtime.s_tier_companies}
    for entry in runtime.company_registry:
        names = [entry.name, *entry.aliases]
        if any(company_key(name) in keys for name in names):
            keys.update(company_key(name) for name in names)
    return {key for key in keys if key}


def registry_entry(runtime: RuntimeSettings, company: Company) -> Any | None:
    keys = {company.name_key, *(company_key(alias) for alias in company.aliases or [])}
    for entry in runtime.company_registry:
        if {company_key(name) for name in [entry.name, *entry.aliases]} & keys:
            return entry
    return None


async def sync_s_tier(session: AsyncSession, runtime: RuntimeSettings) -> int:
    """Apply the configurable S-tier list. Tiers the user set explicitly are never changed."""
    keys = s_tier_keys(runtime)
    changed = 0
    companies = (await session.scalars(select(Company))).all()
    for company in companies:
        names = {company.name_key, *(company_key(alias) for alias in company.aliases or [])}
        listed = bool(names & keys)
        if company.tier_source == "user":
            continue
        if listed and company.tier != "S" and "S" in runtime.tiers:
            company.tier, company.tier_source = "S", "s_tier_list"
            changed += 1
        elif not listed and company.tier_source == "s_tier_list":
            company.tier, company.tier_source = runtime.default_tier, "default"
            changed += 1
    if changed:
        await session.commit()
    return changed


def _age_hours(job: Job, now: datetime) -> float | None:
    bounds = age_bounds_hours(as_utc(job.posted_at), job.posted_at_precision, now)
    return bounds[1] if bounds else None


class IntelligenceService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMRewriter | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.llm = llm if llm is not None else LLMRewriter(settings)

    async def candidate_context(
        self, session: AsyncSession, runtime: RuntimeSettings
    ) -> CandidateContext | None:
        candidate = await get_candidate(session)
        if candidate is None:
            return None
        facts = await active_facts(session, candidate.id)
        if not facts:
            return None
        resume = (
            await session.get(Resume, candidate.active_resume_id)
            if candidate.active_resume_id
            else None
        )
        section_order = (resume.parsed or {}).get("section_order") if resume else None
        model = build_candidate_model(candidate, facts, utcnow().date(), runtime.rusty_after_years)
        base = build_resume_base(facts, section_order)
        return CandidateContext(
            candidate, facts, {str(f.id): f for f in facts}, model, base, resume
        )

    async def ensure_analysis(
        self, session: AsyncSession, job: Job, company_name: str
    ) -> tuple[JobAnalysis, bool]:
        digest = description_hash(job.title, job.description)
        analysis = await session.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
        if (
            analysis is not None
            and analysis.description_hash == digest
            and (analysis.analyzer_version == ANALYZER_VERSION)
        ):
            return analysis, False
        result = analyze_job(
            job.title,
            job.description,
            location=job.location,
            remote_status=job.remote_status,
            salary=job.salary,
            company_name=company_name,
        ).to_dict()
        if analysis is None:
            analysis = JobAnalysis(job_id=job.id)
            session.add(analysis)
        for key, value in result.items():
            setattr(analysis, key, value)
        await session.flush()
        return analysis, True

    @staticmethod
    def analysis_dict(analysis: JobAnalysis) -> dict[str, Any]:
        return {
            "seniority": analysis.seniority,
            "years_min": analysis.years_min,
            "years_max": analysis.years_max,
            "requirements": analysis.requirements or [],
            "required_skills": analysis.required_skills or [],
            "preferred_skills": analysis.preferred_skills or [],
            "responsibilities": analysis.responsibilities or [],
            "categories": analysis.categories or {},
            "domains": analysis.domains or [],
            "leadership_signals": analysis.leadership_signals or [],
            "keywords": analysis.keywords or [],
            "business_context": analysis.business_context,
            "location_requirements": analysis.location_requirements or {},
            "compensation": analysis.compensation,
            "education": analysis.education or [],
            "role_focus": analysis.role_focus,
        }

    def compute_match(
        self,
        context: CandidateContext,
        job: Job,
        analysis: dict[str, Any],
        runtime: RuntimeSettings,
    ) -> MatchResult:
        return match_job(
            context.model,
            analysis,
            title=job.title,
            location=job.location,
            role_category=job.role_category,
            role_relevance=job.role_relevance,
            weights=runtime.fit_weights,
        )

    async def _store_match(
        self,
        session: AsyncSession,
        context: CandidateContext,
        job: Job,
        analysis: JobAnalysis,
        match: MatchResult,
    ) -> CandidateJobMatch:
        row = await session.scalar(
            select(CandidateJobMatch).where(
                CandidateJobMatch.candidate_id == context.candidate.id,
                CandidateJobMatch.job_id == job.id,
            )
        )
        if row is None:
            row = CandidateJobMatch(candidate_id=context.candidate.id, job_id=job.id)
            session.add(row)
        row.analysis_id = analysis.id
        row.profile_version = context.candidate.profile_version
        row.description_hash = analysis.description_hash
        row.fit_score = match.fit_score
        row.breakdown = match.breakdown
        row.seniority_fit = match.seniority_fit
        row.requirement_matches = match.requirement_matches
        row.skill_assessments = match.skill_assessments
        row.strong_matches = match.strong_matches
        row.partial_matches = match.partial_matches
        row.gaps = match.gaps
        row.explanation = [
            *match.explanation,
            *[f"{k}: {v}" for k, v in match.component_notes.items()],
        ]
        row.location_fit = match.location_fit
        row.compensation_fit = match.compensation_fit
        row.computed_at = utcnow()
        return row

    def positioning(
        self, runtime: RuntimeSettings, company: Company | None, job: Job, analysis: dict[str, Any]
    ) -> dict[str, Any]:
        return positioning_for(
            company.name if company else job.company,
            profiles=runtime.positioning_profiles,
            archetypes=runtime.archetype_signals,
            industry=company.industry if company else None,
            company_type=company.company_type if company else None,
            stage=company.company_stage if company else None,
            jd_context=analysis.get("business_context"),
            jd_domains=analysis.get("domains"),
        )

    async def run_analysis(
        self,
        *,
        job_ids: list[uuid.UUID] | None = None,
        company_ids: list[uuid.UUID] | None = None,
        trigger: str = "manual",
        tailor: bool = True,
    ) -> AnalysisOutcome:
        outcome = AnalysisOutcome()
        now = utcnow()
        async with self.session_factory() as session:
            runtime = await load_runtime_settings(session, self.settings)
            await sync_s_tier(session, runtime)
            context = await self.candidate_context(session, runtime)
            if context is None:
                outcome.notes.append(
                    "No candidate profile yet: import your master resume in Profile."
                )
                return outcome
            query = select(Job).where(Job.listing_status == "OPEN")
            if job_ids:
                query = select(Job).where(Job.id.in_(job_ids))
            if company_ids:
                query = query.where(Job.company_id.in_(company_ids))
            candidates_jobs = list((await session.scalars(query)).all())
            scored_ids = set(
                (
                    await session.scalars(
                        select(ApplicationScore.job_id).where(
                            ApplicationScore.candidate_id == context.candidate.id
                        )
                    )
                ).all()
            )
            jobs = []
            for job in candidates_jobs:
                # Re-classify so classifier improvements apply to jobs ingested earlier.
                classification = classify_role(job.title, job.description, job.company)
                if (job.role_category, job.role_relevance) != (
                    classification.category,
                    classification.relevance,
                ):
                    job.role_category = classification.category
                    job.role_relevance = classification.relevance
                if job_ids or classification.is_target or job.id in scored_ids:
                    jobs.append(job)
            companies = {
                company.id: company
                for company in (
                    await session.scalars(
                        select(Company).where(
                            Company.id.in_({job.company_id for job in jobs if job.company_id})
                        )
                    )
                ).all()
            }
            per_job: dict[uuid.UUID, tuple[Job, JobAnalysis, MatchResult]] = {}
            for job in jobs:
                company = companies.get(job.company_id) if job.company_id else None
                analysis, changed = await self.ensure_analysis(
                    session, job, company.name if company else job.company
                )
                outcome.analyses_updated += changed
                match = self.compute_match(context, job, self.analysis_dict(analysis), runtime)
                await self._store_match(session, context, job, analysis, match)
                per_job[job.id] = (job, analysis, match)
                outcome.jobs_analyzed += 1
                outcome.matches += 1
            await session.flush()

            targets = await self._score_companies(
                session, context, runtime, list(companies.values()), now
            )
            outcome.companies_scored = len(targets)
            tailor_queue: list[tuple[Job, Company | None, str]] = []
            for job, analysis, match in per_job.values():
                company = companies.get(job.company_id) if job.company_id else None
                target = targets.get(company.id) if company else None
                priority, score_row = await self._prioritise(
                    session, context, runtime, job, company, analysis, match, target, now
                )
                if priority.priority_class in RECOMMENDED_CLASSES:
                    outcome.recommended += 1
                    await self.ensure_application(
                        session, context.candidate, job, company, priority.priority_class
                    )
                elif priority.priority_class == "REJECT":
                    await self._withdraw_recommendation(session, context.candidate, job, priority)
                reason = self._tailoring_reason(runtime, job, company, match, priority)
                if reason and tailor:
                    tailor_queue.append((job, company, reason))
            await session.commit()

        if not tailor:
            return outcome
        budget = runtime.max_variants_per_run
        for job, _company, reason in sorted(
            tailor_queue, key=lambda item: item[0].discovered_at, reverse=True
        ):
            if outcome.variants_generated >= budget:
                outcome.variants_skipped += 1
                continue
            try:
                variant, created = await self.tailor(job.id, trigger=f"auto:{reason}")
            except Exception as exc:  # noqa: BLE001 - one bad JD must not stop the run
                outcome.errors.append(f"Tailoring failed for {job.company} / {job.title}: {exc}")
                continue
            if created:
                outcome.variants_generated += 1
        if outcome.variants_skipped:
            outcome.notes.append(
                f"{outcome.variants_skipped} tailored resume(s) deferred by max_variants_per_run "
                f"({budget}); they will be generated on the next run."
            )
        return outcome

    async def _score_companies(
        self,
        session: AsyncSession,
        context: CandidateContext,
        runtime: RuntimeSettings,
        companies: list[Company],
        now: datetime,
    ) -> dict[uuid.UUID, TargetScore]:
        results: dict[uuid.UUID, TargetScore] = {}
        for company in companies:
            rows = (
                await session.execute(
                    select(CandidateJobMatch, Job, JobAnalysis)
                    .join(Job, Job.id == CandidateJobMatch.job_id)
                    .join(JobAnalysis, JobAnalysis.job_id == Job.id)
                    .where(
                        CandidateJobMatch.candidate_id == context.candidate.id,
                        Job.company_id == company.id,
                        Job.listing_status == "OPEN",
                    )
                )
            ).all()
            fits, locations, terms, compensation = [], [], [], []
            fresh = 0
            for match_row, job, analysis in rows:
                fits.append(match_row.fit_score)
                status = (match_row.location_fit or {}).get("status")
                if status in {"MATCH", "MISMATCH", "RELOCATION"}:
                    locations.append(status == "MATCH")
                terms.extend(skill["name"] for skill in (analysis.required_skills or [])[:6])
                if (match_row.compensation_fit or {}).get("status") in {"MATCH", "CLOSE", "BELOW"}:
                    compensation.append(match_row.compensation_fit["status"] in {"MATCH", "CLOSE"})
                age = _age_hours(job, now)
                fresh += age is not None and age <= runtime.freshness_hours
            company_context = CompanyContext(
                name=company.name,
                tier=company.tier,
                discovery_breakdown=company.score_breakdown or {},
                relevant_open_jobs=len(rows),
                fresh_relevant_jobs=fresh,
                india_presence=company.india_presence,
                remote_presence=company.remote_presence,
                company_type=company.company_type,
                industry=company.industry,
                job_fits=fits,
                job_locations_ok=locations,
                tech_terms=terms,
                compensation_matches=compensation,
                current_employer=is_current_employer(
                    context.candidate.current_company, company.name
                ),
            )
            score, breakdown, reasons = company_target_score(
                company_context,
                context.model,
                weights=runtime.target_score_weights,
                tier_values=runtime.tier_values,
            )
            row = await session.scalar(
                select(TargetScore).where(
                    TargetScore.candidate_id == context.candidate.id,
                    TargetScore.company_id == company.id,
                )
            )
            if row is None:
                row = TargetScore(candidate_id=context.candidate.id, company_id=company.id)
                session.add(row)
            row.score, row.breakdown, row.reasons, row.computed_at = score, breakdown, reasons, now
            results[company.id] = row
        await session.flush()
        return results

    async def _prioritise(
        self,
        session: AsyncSession,
        context: CandidateContext,
        runtime: RuntimeSettings,
        job: Job,
        company: Company | None,
        analysis: JobAnalysis,
        match: MatchResult,
        target: TargetScore | None,
        now: datetime,
    ) -> tuple[Any, ApplicationScore]:
        age = _age_hours(job, now)
        freshness, freshness_note = freshness_score(age)
        has_variant = bool(
            await session.scalar(
                select(func.count(ResumeVariant.id)).where(
                    ResumeVariant.job_id == job.id,
                    ResumeVariant.status.in_(("USER_REVIEW", "APPROVED")),
                )
            )
        )
        priority = application_priority(
            match=match,
            company_target=target.score if target else 50.0,
            company_career_growth=(target.breakdown or {}).get("career_growth", 50.0)
            if target
            else 50.0,
            freshness=freshness,
            freshness_note=freshness_note,
            age_hours=age,
            role_is_target=job.role_category in TARGET_CATEGORIES and job.role_relevance >= 0.5,
            listing_open=job.listing_status == "OPEN",
            ats=job.ats,
            tailored_resume_ready=has_variant,
            current_employer=is_current_employer(
                context.candidate.current_company, company.name if company else job.company
            ),
            weights=runtime.application_priority_weights,
            thresholds=runtime.application_priority_thresholds,
        )
        strategy = None
        analysis_values = self.analysis_dict(analysis)
        if priority.priority_class in STRATEGY_CLASSES:
            strategy = application_strategy(
                model=context.model,
                match=match,
                analysis=analysis_values,
                positioning=self.positioning(runtime, company, job, analysis_values),
                company_name=company.name if company else job.company,
            )
            strategy["effort_minutes"] = priority.effort_minutes
        row = await session.scalar(
            select(ApplicationScore).where(
                ApplicationScore.candidate_id == context.candidate.id,
                ApplicationScore.job_id == job.id,
            )
        )
        if row is None:
            row = ApplicationScore(candidate_id=context.candidate.id, job_id=job.id)
            session.add(row)
        row.company_id = company.id if company else None
        row.fit_score = match.fit_score
        row.company_target_score = target.score if target else 50.0
        row.freshness_score = freshness
        row.career_value_score = priority.career_value
        row.priority_score = priority.priority_score
        row.priority_class = priority.priority_class
        row.gates = priority.gates
        row.reasons = priority.reasons
        row.recommendation = priority.recommendation
        row.strategy = strategy
        row.computed_at = now
        return priority, row

    def _tailoring_reason(
        self,
        runtime: RuntimeSettings,
        job: Job,
        company: Company | None,
        match: MatchResult,
        priority: Any,
    ) -> str | None:
        tier = company.tier if company else runtime.default_tier
        rule = runtime.tailoring_policy.get(tier)
        if rule is None or rule.mode == "off":
            return None
        relevant = (
            job.listing_status == "OPEN"
            and job.role_category in TARGET_CATEGORIES
            and job.role_relevance >= 0.5
        )
        if (
            runtime.tailoring_require_location_fit
            and match.location_fit.get("status") == "MISMATCH"
        ):
            relevant = False
        if not relevant:
            return None
        if rule.mode == "all_relevant":
            return f"{tier}-tier: every relevant job"
        if rule.mode == "min_fit" and match.fit_score >= (rule.threshold or 0):
            return f"{tier}-tier: fit {match.fit_score:.0f} >= {rule.threshold:g}"
        if rule.mode == "priority" and priority.priority_class in rule.classes:
            return f"{tier}-tier: {priority.priority_class}"
        if rule.mode == "min_priority_score" and priority.priority_score >= (rule.threshold or 0):
            return f"{tier}-tier: priority {priority.priority_score:.0f} >= {rule.threshold:g}"
        return None

    async def _withdraw_recommendation(
        self, session: AsyncSession, candidate: Candidate, job: Job, priority: Any
    ) -> None:
        """A job that is no longer recommended leaves the queue; nothing the user did is undone."""
        application = await session.scalar(
            select(Application).where(
                Application.candidate_id == candidate.id, Application.job_id == job.id
            )
        )
        if application is None or application.status not in AUTOMATIC_STATUSES:
            return
        reason = priority.gates[0] if priority.gates else priority.recommendation
        session.add(
            ApplicationEvent(
                application_id=application.id,
                from_status=application.status,
                to_status="WITHDRAWN",
                note=f"No longer recommended: {reason}",
                actor="system",
            )
        )
        application.status = "WITHDRAWN"
        application.priority_class = "REJECT"

    async def ensure_application(
        self,
        session: AsyncSession,
        candidate: Candidate,
        job: Job,
        company: Company | None,
        priority_class: str | None,
        status: str = "RECOMMENDED",
        note: str | None = None,
    ) -> Application:
        application = await session.scalar(
            select(Application).where(
                Application.candidate_id == candidate.id, Application.job_id == job.id
            )
        )
        if application is None:
            application = Application(
                candidate_id=candidate.id,
                job_id=job.id,
                company_id=company.id if company else None,
                status=status,
                source=job.ats,
                priority_class=priority_class,
            )
            session.add(application)
            await session.flush()
            session.add(
                ApplicationEvent(
                    application_id=application.id,
                    from_status=None,
                    to_status=status,
                    note=note or f"Recommended as {priority_class}.",
                    actor="system",
                )
            )
            return application
        application.priority_class = priority_class or application.priority_class
        if application.status == "WITHDRAWN":
            last = await session.scalar(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == application.id)
                .order_by(ApplicationEvent.created_at.desc())
                .limit(1)
            )
            if last is not None and last.actor == "system":
                # Withdrawn by the pipeline, not by you: it is recommended again.
                session.add(
                    ApplicationEvent(
                        application_id=application.id,
                        from_status="WITHDRAWN",
                        to_status="RECOMMENDED",
                        note=f"Recommended again as {priority_class}.",
                        actor="system",
                    )
                )
                application.status = "RECOMMENDED"
        order = ["DISCOVERED", "ANALYZED", "RECOMMENDED", "RESUME_GENERATED", "USER_REVIEW"]
        if (
            application.status in AUTOMATIC_STATUSES
            and status in order
            and (order.index(status) > order.index(application.status))
        ):
            session.add(
                ApplicationEvent(
                    application_id=application.id,
                    from_status=application.status,
                    to_status=status,
                    note=note,
                    actor="system",
                )
            )
            application.status = status
        elif note and application.status in AUTOMATIC_STATUSES:
            # For example a regenerated resume version: recorded without a status change.
            session.add(
                ApplicationEvent(
                    application_id=application.id,
                    from_status=application.status,
                    to_status=application.status,
                    note=note,
                    actor="system",
                )
            )
        return application

    async def tailor(
        self, job_id: uuid.UUID, *, trigger: str = "manual", force: bool = False
    ) -> tuple[ResumeVariant, bool]:
        """Generate (or reuse) the JD-specific resume for ``job_id``."""
        async with self.session_factory() as session:
            runtime = await load_runtime_settings(session, self.settings)
            context = await self.candidate_context(session, runtime)
            if context is None:
                raise ValueError("Import your master resume before tailoring.")
            job = await session.get(Job, job_id)
            if job is None:
                raise ValueError("Job not found.")
            company = await session.get(Company, job.company_id) if job.company_id else None
            analysis_row, _changed = await self.ensure_analysis(
                session, job, company.name if company else job.company
            )
            analysis = self.analysis_dict(analysis_row)
            current = await session.scalar(
                select(ResumeVariant)
                .where(
                    ResumeVariant.candidate_id == context.candidate.id,
                    ResumeVariant.job_id == job.id,
                    ResumeVariant.status.in_(("USER_REVIEW", "APPROVED", "GENERATED")),
                )
                .order_by(ResumeVariant.version.desc())
                .limit(1)
            )
            if current is not None and not force:
                same_profile = current.profile_version == context.candidate.profile_version
                # An improved JD analysis renumbers requirements and an improved generator
                # writes better (still verified) text; unapproved resumes follow both.
                reanalysed = current.status != "APPROVED" and (
                    (current.generator or "").split("+")[0] != GENERATOR
                    or (
                        analysis_row.updated_at is not None
                        and current.created_at is not None
                        and as_utc(analysis_row.updated_at) > as_utc(current.created_at)
                    )
                )
                if current.jd_hash == analysis_row.description_hash and same_profile:
                    if not reanalysed:
                        return current, False
                # Word-level similarity: quick_ratio() only compares letter counts, which any two
                # English job descriptions of similar length share.
                similar = (
                    difflib.SequenceMatcher(
                        None,
                        (current.jd_snapshot or "").split(),
                        (job.description or "").split(),
                        autojunk=False,
                    ).ratio()
                    >= 0.9
                )
                if similar and same_profile and not reanalysed:
                    return current, False
                if current.status == "APPROVED" and similar:
                    return current, False
            match = self.compute_match(context, job, analysis, runtime)
            positioning = self.positioning(runtime, company, job, analysis)
            jd_text = f"{job.title}\n{job.description}"
            tailored = tailor_resume(
                candidate=context.candidate,
                model=context.model,
                base=context.base,
                facts_by_id=context.facts_by_id,
                analysis=analysis,
                match=match,
                positioning=positioning,
                jd_text=jd_text,
            )
            generator = GENERATOR
            if runtime.llm_rewrite_enabled and self.llm.configured:
                generator = f"{GENERATOR}+{self.llm.name}"
                await self._rewrite(
                    tailored.claims, context, analysis, company, job, jd_text, tailored.changes
                )
            claims = tailored.claims

            def resolve(ref: Any) -> ClaimDraft | None:
                return claims[ref] if isinstance(ref, int) and 0 <= ref < len(claims) else None

            master = master_resume(candidate=context.candidate, base=context.base)
            baseline = to_text(
                master.content, lambda ref: master.claims[ref] if isinstance(ref, int) else None
            )
            rendered = to_text(tailored.content, resolve)
            quality = evaluate_resume(
                claims,
                analysis=analysis,
                match=match,
                rendered_text=rendered,
                candidate_titles=context.model.titles,
                years_experience=context.model.years,
                baseline_text=baseline,
            )
            version = (
                await session.scalar(
                    select(func.max(ResumeVariant.version)).where(
                        ResumeVariant.candidate_id == context.candidate.id,
                        ResumeVariant.job_id == job.id,
                    )
                )
                or 0
            ) + 1
            replaced_approved = False
            for previous in (
                await session.scalars(
                    select(ResumeVariant).where(
                        ResumeVariant.candidate_id == context.candidate.id,
                        ResumeVariant.job_id == job.id,
                        ResumeVariant.status.in_(("USER_REVIEW", "GENERATED", "APPROVED")),
                    )
                )
            ).all():
                replaced_approved = replaced_approved or previous.status == "APPROVED"
                previous.status = "STALE" if previous.status == "APPROVED" else "SUPERSEDED"
            variant = ResumeVariant(
                candidate_id=context.candidate.id,
                job_id=job.id,
                company_id=company.id if company else None,
                master_resume_id=context.resume.id if context.resume else None,
                master_version=context.resume.version if context.resume else 1,
                profile_version=context.candidate.profile_version,
                version=version,
                jd_hash=analysis_row.description_hash,
                jd_snapshot=job.description,
                generator=generator,
                trigger=trigger,
                status="USER_REVIEW",
                positioning=positioning,
                content={},
                changes=tailored.changes,
                source_fact_ids=sorted(
                    {fid for claim in claims if claim.included for fid in claim.source_fact_ids}
                ),
                alignment_score=quality["alignment_score"],
                alignment_breakdown=quality["alignment_breakdown"],
                truth_validation=quality["truth_validation"],
                quality_checks=quality["quality_checks"],
                file_paths={},
            )
            session.add(variant)
            await session.flush()
            ids: list[str] = []
            for claim in claims:
                row = ResumeClaim(
                    variant_id=variant.id,
                    section=claim.section,
                    entry_key=claim.entry_key,
                    position=claim.position,
                    label=claim.label,
                    generated_text=claim.text,
                    original_text=claim.original_text,
                    source_fact_ids=claim.source_fact_ids,
                    jd_requirement_ids=claim.jd_requirement_ids,
                    tailoring_reason=claim.reason,
                    relevance=claim.relevance,
                    confidence=1.0 if claim.verified else 0.0,
                    verified=claim.verified,
                    verification_notes=claim.notes,
                    included=claim.included,
                    rewritten_by=claim.rewritten_by,
                )
                session.add(row)
                await session.flush()
                ids.append(str(row.id))
            variant.content = _replace_refs(tailored.content, ids, claims)
            variant.file_paths = self._write_files(variant, tailored.content, resolve, job, company)
            await self.ensure_application(
                session,
                context.candidate,
                job,
                company,
                None,
                status="USER_REVIEW",
                note=f"Tailored resume v{version} generated ({trigger}).",
            )
            application = await session.scalar(
                select(Application).where(
                    Application.candidate_id == context.candidate.id, Application.job_id == job.id
                )
            )
            if application is not None and application.status in AUTOMATIC_STATUSES:
                application.resume_variant_id = variant.id
            elif (
                application is not None
                and replaced_approved
                and application.status in {"APPROVED", "READY_TO_APPLY"}
            ):
                # Approval covered the previous version; nothing is applied with an unreviewed one.
                session.add(
                    ApplicationEvent(
                        application_id=application.id,
                        from_status=application.status,
                        to_status="USER_REVIEW",
                        note=(
                            f"The job description changed, so resume v{version} replaced the "
                            "version you approved. Review and approve it again before applying."
                        ),
                        actor="system",
                    )
                )
                application.status = "USER_REVIEW"
                application.resume_variant_id = variant.id
            await session.commit()
            return variant, True

    async def _rewrite(
        self,
        claims: list[ClaimDraft],
        context: CandidateContext,
        analysis: dict[str, Any],
        company: Company | None,
        job: Job,
        jd_text: str,
        changes: dict[str, Any],
    ) -> None:
        candidates = sorted(
            (
                index
                for index, claim in enumerate(claims)
                if claim.included and claim.section in {"experience", "project"}
            ),
            key=lambda index: -claims[index].relevance,
        )[:6]
        requests = [
            RewriteRequest(
                str(index), claims[index].text, [name for name in analysis.get("keywords", [])][:6]
            )
            for index in candidates
        ]
        try:
            proposals = await self.llm.rewrite(
                requests,
                jd_terms=analysis.get("keywords", []),
                company=company.name if company else job.company,
                role=job.title,
            )
        except LLMError as exc:
            changes["rewrites"].append({"accepted": False, "notes": [str(exc)]})
            return
        for key, text in proposals.items():
            claim = claims[int(key)]
            if text.strip() == claim.text.strip():
                continue
            sources = [
                context.facts_by_id[fid]
                for fid in claim.source_fact_ids
                if fid in context.facts_by_id
            ]
            result = verify_claim(
                text,
                sources,
                kind="generated",
                candidate_titles=context.model.titles,
                jd_text=jd_text,
                max_length_ratio=1.35,
            )
            changes["rewrites"].append(
                {
                    "label": claim.label,
                    "before": claim.text,
                    "after": text,
                    "accepted": result.ok,
                    "notes": result.notes,
                }
            )
            if result.ok:
                claim.text = text
                claim.kind = "generated"
                claim.rewritten_by = self.llm.name
                claim.notes = ["Reworded by AI and verified against the cited fact."]

    def _write_files(
        self,
        variant: ResumeVariant,
        content: dict[str, Any],
        resolve: Any,
        job: Job,
        company: Company | None,
    ) -> dict[str, str]:
        folder = Path(self.settings.data_dir) / "resumes" / "variants"
        folder.mkdir(parents=True, exist_ok=True)
        stem = folder / str(variant.id)
        title = f"{company.name if company else job.company} - {job.title}"
        paths = {
            "md": (stem.with_suffix(".md"), to_markdown(content, resolve).encode()),
            "txt": (stem.with_suffix(".txt"), to_text(content, resolve).encode()),
            "html": (stem.with_suffix(".html"), to_html(content, resolve, title).encode()),
            "docx": (stem.with_suffix(".docx"), to_docx(content, resolve)),
        }
        for path, data in paths.values():
            path.write_bytes(data)
        return {name: str(path) for name, (path, _data) in paths.items()}


def _replace_refs(
    content: dict[str, Any], ids: list[str], claims: list[ClaimDraft]
) -> dict[str, Any]:
    """Swap claim list indexes in ``content`` for persisted claim ids."""

    def ref(value: Any) -> Any:
        return ids[value] if isinstance(value, int) and 0 <= value < len(ids) else value

    result = dict(content)
    for key in ("headline",):
        if key in result:
            result[key] = ref(result[key])
    for key in ("summary", "skills", "education", "certifications"):
        result[key] = [ref(value) for value in result.get(key, [])]
    for section in ("experience", "projects"):
        entries = []
        for entry in result.get(section, []):
            entry = dict(entry)
            entry["head"] = ref(entry.get("head"))
            entry["bullets"] = [ref(value) for value in entry.get("bullets", [])]
            entry["stack"] = ref(entry.get("stack"))
            entries.append(entry)
        result[section] = entries
    return result


async def claim_resolver(session: AsyncSession, variant: ResumeVariant) -> dict[str, ResumeClaim]:
    rows = (
        await session.scalars(select(ResumeClaim).where(ResumeClaim.variant_id == variant.id))
    ).all()
    return {str(row.id): row for row in rows}


def resolve_claims(claims: dict[str, ResumeClaim]) -> Any:
    def resolve(ref: Any) -> Any:
        return claims.get(str(ref)) if ref is not None else None

    return resolve


def summarize_outcome(outcome: AnalysisOutcome) -> dict[str, Any]:
    return {
        "jobs_analyzed": outcome.jobs_analyzed,
        "analyses_updated": outcome.analyses_updated,
        "companies_scored": outcome.companies_scored,
        "recommended": outcome.recommended,
        "variants_generated": outcome.variants_generated,
        "variants_deferred": outcome.variants_skipped,
    }


def group_by(items: list[Any], key: Any) -> dict[Any, list[Any]]:
    grouped: dict[Any, list[Any]] = defaultdict(list)
    for item in items:
        grouped[key(item)].append(item)
    return grouped
