"""Bounded background exploration.

Company discovery (providers -> evidence -> companies -> frontier) and job discovery
(career sources -> adapters -> existing ingestion pipeline) run here, inside the worker,
never inside an HTTP request. Every run is limited by explicit budgets.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.connectors.base import ScanRequest, SourceScanResult, run_adapter
from app.connectors.generic import HTML_ACCEPT, adapter_for
from app.discovery.ats import API_PLATFORMS, SUPPORTED_PLATFORMS, PlatformMatch, detect_from_url
from app.discovery.extraction import CandidateSignal, career_root
from app.discovery.http import BudgetExhausted, FetchError, SafeFetcher
from app.discovery.jsonld import find_job_postings, page_title, parse_datetime, posting_fields
from app.discovery.names import (
    company_key,
    hostname,
    is_aggregator,
    is_india_location,
    is_platform_host,
    name_matches_domain,
    normalize_company_name,
    registrable_domain,
)
from app.discovery.providers import BaseProvider, ProviderContext, default_providers
from app.discovery.roles import TARGET_CATEGORIES, classify_role
from app.discovery.search import SearchService
from app.discovery.settings_store import (
    ExplorationBudgets,
    RuntimeSettings,
    cache_get,
    cache_set,
    effective_settings,
    load_runtime_settings,
)
from app.models import (
    AppSetting,
    CareerSource,
    Company,
    DiscoveryCandidate,
    DiscoveryRun,
    Job,
    JobSource,
)
from app.schemas import IngestedJob
from app.services.candidates import get_candidate
from app.services.companies import (
    REASON_LABELS,
    apply_signal_to_company,
    as_utc,
    ensure_career_source,
    find_company,
    new_company,
    record_event,
    refresh_company,
    signal_evidence_data,
    upsert_candidate,
    upsert_relationship,
)
from app.services.ingestion import DiscoveryService
from app.services.intelligence import IntelligenceService, registry_entry, summarize_outcome
from app.services.normalization import normalize_job
from app.services.queue import enqueue_run
from app.services.ranking import OpportunityScorer
from app.services.search_store import SearchStore

KEYWORD_SCOPED_SOURCES = {
    "workday",
    "oracle",
    "amazon_jobs",
    "generic_html",
    "schema_org",
    "smartrecruiters",
    "icims",
    "jobvite",
    "teamtailor",
}
_SENIORITY = re.compile(r"\b(senior|sr\.?|lead|staff|principal|junior|jr\.?)\b", re.I)


class RunFailure(Exception):
    """A run-level failure that retrying will not fix (for example an unknown board)."""


_COUNTRY_CODES = {
    "india": ("IND", "India"),
    "united states": ("USA", "United States"),
    "usa": ("USA", "United States"),
    "united kingdom": ("GBR", "United Kingdom"),
    "uk": ("GBR", "United Kingdom"),
    "canada": ("CAN", "Canada"),
    "germany": ("DEU", "Germany"),
    "singapore": ("SGP", "Singapore"),
}


def location_hints(preferred_locations: list[str] | None) -> tuple[list[str], list[str]]:
    """Country codes and search terms derived from the candidate's location preferences."""
    codes: list[str] = []
    terms: list[str] = []
    for place in preferred_locations or []:
        lower = place.lower()
        match = next((value for key, value in _COUNTRY_CODES.items() if key in lower), None)
        if match is None and is_india_location(place):
            match = _COUNTRY_CODES["india"]
        if match and match[0] not in codes:
            codes.append(match[0])
            terms.append(match[1])
    return codes, terms


@dataclass
class RunContext:
    run_id: uuid.UUID
    kind: str
    trigger: str
    budgets: ExplorationBudgets
    runtime: RuntimeSettings
    limits: dict[str, int]
    used: Counter[str] = field(default_factory=Counter)
    counters: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    exhausted: set[str] = field(default_factory=set)
    queries: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    # Companies whose jobs changed in this run; candidate analysis is queued for them.
    touched_companies: set[str] = field(default_factory=set)

    def take(self, budget: str) -> bool:
        if self.used[budget] >= self.limits.get(budget, 0):
            self.exhausted.add(budget)
            return False
        self.used[budget] += 1
        return True

    def remaining(self, budget: str) -> int:
        return max(0, self.limits.get(budget, 0) - self.used[budget])

    def page_hook(self, _url: str) -> None:
        if not self.take("max_pages"):
            raise BudgetExhausted("max_pages")

    def error(self, message: str) -> None:
        message = message[:500]
        if message not in self.errors and len(self.errors) < 100:
            self.errors.append(message)

    def note(self, message: str) -> None:
        if message not in self.notes and len(self.notes) < 200:
            self.notes.append(message[:500])

    def snapshot(self) -> dict[str, Any]:
        return {
            "companies_discovered": self.counters["companies_discovered"],
            "jobs_inspected": self.counters["jobs_inspected"],
            "jobs_created": self.counters["jobs_created"],
            "errors": list(self.errors),
            "progress": {
                "used": dict(self.used),
                "limits": dict(self.limits),
                "counters": dict(self.counters),
                "budget_exhausted": sorted(self.exhausted),
                "notes": self.notes[-40:],
                "queries": self.queries[-60:],
                **self.details,
            },
        }


def utcnow() -> datetime:
    return datetime.now(UTC)


class Explorer:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        fetcher_factory: Callable[[RunContext], SafeFetcher] | None = None,
        search: SearchService | None = None,
        providers: dict[str, BaseProvider] | None = None,
        intelligence: IntelligenceService | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.fetcher_factory = fetcher_factory or self._default_fetcher
        self.search = search or SearchService(settings)
        self.providers = providers if providers is not None else default_providers(self.search)
        self.intelligence = intelligence or IntelligenceService(settings, session_factory)

    def _default_fetcher(self, ctx: RunContext) -> SafeFetcher:
        trusted = (
            {SafeFetcher.origin_of(self.settings.searxng_url)}
            if self.settings.searxng_url
            else set()
        )
        return SafeFetcher(self.settings, on_request=ctx.page_hook, trusted_origins=trusted)

    async def build_context(self, run: DiscoveryRun) -> RunContext:
        async with self.session_factory() as session:
            runtime = await load_runtime_settings(session, self.settings)
            start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            used_today: Counter[str] = Counter()
            for progress in await session.scalars(
                select(DiscoveryRun.progress).where(
                    DiscoveryRun.created_at >= start, DiscoveryRun.id != run.id
                )
            ):
                used_today.update(
                    {k: int(v) for k, v in ((progress or {}).get("used") or {}).items()}
                )
        merged = {
            **runtime.budgets.model_dump(),
            **{k: v for k, v in (run.budgets or {}).items() if v is not None},
        }
        budgets = ExplorationBudgets.model_validate(merged)
        daily = runtime.daily_limits
        limits = {
            "max_search_queries": max(
                0,
                min(
                    budgets.max_search_queries,
                    daily.search_queries - used_today["max_search_queries"],
                ),
            ),
            "max_pages": max(0, min(budgets.max_pages, daily.pages - used_today["max_pages"])),
            "max_new_companies": max(
                0,
                min(
                    budgets.max_new_companies, daily.new_companies - used_today["max_new_companies"]
                ),
            ),
            "max_company_expansion": budgets.max_company_expansion,
        }
        ctx = RunContext(run.id, run.kind, run.trigger, budgets, runtime, limits)
        for name, value in limits.items():
            if value < getattr(budgets, name):
                ctx.note(
                    f"Daily limit reduced {name} for this run "
                    f"from {getattr(budgets, name)} to {value}."
                )
        return ctx

    async def execute(self, run: DiscoveryRun, ctx: RunContext) -> str:
        handlers = {
            "exploration": self._exploration,
            "scan": self._scan_known,
            "reconciliation": self._reconcile,
            "frontier": self._frontier_run,
            "frontier_explore": self._frontier_run,
            "company_scan": self._company_scan,
            "seed_resolution": self._seed_resolution,
            "ingestion": self._legacy_ingestion,
            "candidate_analysis": self._candidate_analysis,
            "resume_tailoring": self._resume_tailoring,
        }
        fetcher = self.fetcher_factory(ctx)
        self.search.observer = SearchStore(self.session_factory, ctx.run_id)
        try:
            await handlers[run.kind](ctx, fetcher, run.params or {})
        except BudgetExhausted as exc:
            ctx.exhausted.add(exc.budget)
        finally:
            await fetcher.aclose()
        if ctx.exhausted:
            ctx.note("Stopped within budget limits: " + ", ".join(sorted(ctx.exhausted)) + ".")
        if ctx.touched_companies:
            await self._queue_candidate_analysis(ctx)
        return "PARTIAL" if ctx.errors else "COMPLETED"

    async def _queue_candidate_analysis(self, ctx: RunContext) -> None:
        """New or changed jobs are analysed against the candidate profile in a follow-up run."""
        async with self.session_factory() as session:
            if await get_candidate(session) is None:
                return
            existing = await session.scalar(
                select(DiscoveryRun).where(
                    DiscoveryRun.kind == "candidate_analysis",
                    DiscoveryRun.status == "QUEUED",
                    DiscoveryRun.dedupe_key == "candidate_analysis:auto",
                )
            )
            if existing is not None:
                merged = set((existing.params or {}).get("company_ids", [])) | ctx.touched_companies
                existing.params = {**(existing.params or {}), "company_ids": sorted(merged)}
                await session.commit()
                ctx.details["follow_up_analysis_run_id"] = str(existing.id)
                return
            run, _created = await enqueue_run(
                session,
                "candidate_analysis",
                params={"company_ids": sorted(ctx.touched_companies)},
                trigger="after_scan",
                dedupe_key="candidate_analysis:auto",
            )
        ctx.details["follow_up_analysis_run_id"] = str(run.id)
        ctx.note(
            f"Queued candidate analysis run {run.id} for {len(ctx.touched_companies)} company(ies)."
        )

    async def _candidate_analysis(
        self, ctx: RunContext, _fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        outcome = await self.intelligence.run_analysis(
            job_ids=[uuid.UUID(value) for value in params.get("job_ids", [])] or None,
            company_ids=[uuid.UUID(value) for value in params.get("company_ids", [])] or None,
            trigger=ctx.trigger,
            tailor=params.get("tailor", True),
        )
        ctx.details["intelligence"] = summarize_outcome(outcome)
        for note in outcome.notes:
            ctx.note(note)
        for error in outcome.errors:
            ctx.error(error)
        ctx.note(
            f"Analysed {outcome.jobs_analyzed} job(s), scored {outcome.companies_scored} "
            f"company(ies), {outcome.recommended} recommended, {outcome.variants_generated} "
            "tailored resume(s) generated."
        )

    async def _resume_tailoring(
        self, ctx: RunContext, _fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        try:
            variant, created = await self.intelligence.tailor(
                uuid.UUID(params["job_id"]), trigger="manual", force=bool(params.get("force"))
            )
        except ValueError as exc:
            raise RunFailure(str(exc)) from exc
        ctx.details["variant_id"] = str(variant.id)
        ctx.note(
            f"Generated tailored resume v{variant.version}."
            if created
            else f"An up-to-date tailored resume (v{variant.version}) already exists."
        )

    # ----------------------------------------------------------------------------------
    # Company discovery
    # ----------------------------------------------------------------------------------

    async def _exploration(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        await self._market_search(ctx, fetcher)
        await self._process_frontier(ctx, fetcher)

    def _provider_context(
        self, ctx: RunContext, fetcher: SafeFetcher, errors: list[str], **kwargs: Any
    ) -> ProviderContext:
        async def get_cached(key: str, ttl: float) -> Any:
            async with self.session_factory() as session:
                return await cache_get(session, key, ttl)

        async def set_cached(key: str, value: Any) -> None:
            async with self.session_factory() as session:
                await cache_set(session, key, value)

        return ProviderContext(
            fetcher=fetcher,
            runtime=ctx.runtime,
            search=self.search,
            results_per_query=ctx.budgets.max_results_per_query,
            errors=errors,
            take_query=kwargs.pop("take_query", lambda: ctx.take("max_search_queries")),
            cache_get=get_cached,
            cache_set=set_cached,
            **kwargs,
        )

    async def _market_search(self, ctx: RunContext, fetcher: SafeFetcher) -> None:
        async with self.session_factory() as session:
            known = set((await session.scalars(select(Company.name_key))).all())
            cursor = await session.get(AppSetting, "discovery_query_cursor")
            rotation = int((cursor.value or {}).get("offset", 0)) if cursor else 0
        total = ctx.limits["max_search_queries"]
        reserved = total // 5
        market = total - reserved
        job_queries = math.ceil(market * 0.6)
        linkedin_queries = market * 25 // 100
        allocation = {
            "job": job_queries,
            "linkedin": linkedin_queries,
            "x": max(0, market - job_queries - linkedin_queries),
        }
        ctx.details["query_allocation"] = {**allocation, "similar_and_resolution": reserved}
        if total and not self.search.available(ctx.runtime):
            ctx.note(
                "No web search backend is enabled; only structured no-key providers were used."
            )
        errors: list[str] = []
        provider_ctx = self._provider_context(
            ctx,
            fetcher,
            errors,
            take_query=lambda: (
                ctx.used["max_search_queries"] < market and ctx.take("max_search_queries")
            ),
            known_keys=known,
            query_allocation=allocation,
            rotation=rotation,
        )
        signals: list[CandidateSignal] = []
        steps = (
            ("hackernews", "search_jobs"),
            ("remotive", "search_jobs"),
            ("web_search", "search_jobs"),
            ("web_search", "search_hiring_signals"),
            ("yc_oss", "search_companies"),
        )
        for name, method in steps:
            provider = self.providers.get(name)
            if provider is None:
                continue
            try:
                signals.extend(await getattr(provider, method)(provider_ctx))
            except BudgetExhausted as exc:
                ctx.exhausted.add(exc.budget)
                break
            except FetchError as exc:
                ctx.error(f"{'SOURCE_BLOCKED' if exc.blocked else 'SOURCE_ERROR'} {name}: {exc}")
        for message in errors:
            ctx.error(message)
        ctx.queries.extend(provider_ctx.queries_run)
        async with self.session_factory() as session:
            cursor = await session.get(AppSetting, "discovery_query_cursor")
            offset = rotation + len(provider_ctx.queries_run)
            if cursor is None:
                session.add(AppSetting(key="discovery_query_cursor", value={"offset": offset}))
            else:
                cursor.value = {"offset": offset}
            await session.commit()
        ctx.counters["signals_observed"] += len(signals)
        signals.sort(
            key=lambda signal: (signal.is_hiring_evidence, signal.confidence), reverse=True
        )
        for signal in signals:
            await self._apply_signal(ctx, signal, depth=1)

    def _allow_new_company(self, ctx: RunContext) -> bool:
        if ctx.take("max_new_companies"):
            return True
        ctx.note("max_new_companies reached; further candidates were not added.")
        return False

    async def _apply_signal(
        self, ctx: RunContext, signal: CandidateSignal, depth: int
    ) -> Company | None:
        async with self.session_factory() as session:
            company, created = await apply_signal_to_company(
                session, signal, ctx.runtime, allow_create=lambda: self._allow_new_company(ctx)
            )
            if company is None:
                ctx.counters["signals_rejected"] += 1
                return None
            if created:
                ctx.counters["companies_discovered"] += 1
            await record_event(
                session,
                company,
                reason=signal.reason,
                source=signal.source,
                source_url=signal.source_url,
                evidence=signal.evidence,
                confidence=signal.confidence,
                evidence_data=signal_evidence_data(signal),
                run_id=ctx.run_id,
                key=signal.role_title,
            )
            ctx.counters["events_recorded"] += 1
            if signal.career_url:
                await ensure_career_source(
                    session,
                    company,
                    signal.career_url,
                    discovered_via=signal.source,
                    evidence=signal.evidence,
                )
            if signal.related_company and signal.relationship_type:
                related = await find_company(session, signal.related_company)
                if related is not None:
                    await upsert_relationship(
                        session,
                        related,
                        company,
                        signal.relationship_type,
                        signal.confidence,
                        signal.evidence,
                        signal.source_url,
                    )
            stats = await refresh_company(session, company, ctx.runtime)
            explorable = company.status in {"DISCOVERED", "UNVERIFIED"} and (
                company.last_explored_at is None or signal.career_url
            )
            if explorable and depth <= ctx.budgets.max_depth:
                role = f": {signal.role_title}" if signal.role_title else ""
                label = REASON_LABELS.get(signal.reason, signal.reason)
                await upsert_candidate(
                    session,
                    company,
                    reason=f"{label} via {signal.source}{role}",
                    depth=depth,
                    run_id=ctx.run_id,
                    stats=stats,
                    runtime=ctx.runtime,
                )
            await session.commit()
            return company

    # ----------------------------------------------------------------------------------
    # Frontier and company exploration
    # ----------------------------------------------------------------------------------

    async def _set_candidate(
        self,
        candidate_id: uuid.UUID,
        status: str,
        *,
        run_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> None:
        async with self.session_factory() as session:
            candidate = await session.get(DiscoveryCandidate, candidate_id)
            if candidate is None:
                return
            candidate.status = status
            if run_id is not None:
                candidate.explored_run_id = run_id
            candidate.error = error
            await session.commit()

    async def _process_frontier(
        self,
        ctx: RunContext,
        fetcher: SafeFetcher,
        *,
        candidate_ids: list[uuid.UUID] | None = None,
        company_ids: list[uuid.UUID] | None = None,
    ) -> None:
        async with self.session_factory() as session:
            query = select(DiscoveryCandidate).where(
                DiscoveryCandidate.status == "PENDING",
                DiscoveryCandidate.depth <= ctx.budgets.max_depth,
            )
            if candidate_ids:
                query = query.where(DiscoveryCandidate.id.in_(candidate_ids))
            if company_ids:
                query = query.where(DiscoveryCandidate.company_id.in_(company_ids))
            candidates = (
                await session.scalars(
                    query.order_by(
                        DiscoveryCandidate.priority.desc(), DiscoveryCandidate.created_at
                    ).limit(max(ctx.limits["max_company_expansion"], 0))
                )
            ).all()
            work = [
                (candidate.id, candidate.company_id, candidate.depth) for candidate in candidates
            ]
        if not work and (candidate_ids or company_ids):
            ctx.note("No pending frontier entries matched this run.")
        for candidate_id, company_id, depth in work:
            if not ctx.take("max_company_expansion"):
                break
            await self._set_candidate(candidate_id, "IN_PROGRESS")
            try:
                await self.explore_company(ctx, fetcher, company_id, depth)
            except BudgetExhausted as exc:
                ctx.exhausted.add(exc.budget)
                await self._set_candidate(candidate_id, "PENDING")
                break
            except FetchError as exc:
                ctx.error(f"SOURCE_ERROR exploring candidate: {exc}")
                await self._set_candidate(candidate_id, "FAILED", error=str(exc))
                continue
            await self._set_candidate(candidate_id, "EXPLORED", run_id=ctx.run_id)
            ctx.counters["companies_explored"] += 1

    async def explore_company(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID, depth: int
    ) -> int:
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                return 0
            sources = (
                await session.scalars(
                    select(CareerSource).where(CareerSource.company_id == company_id)
                )
            ).all()
            has_scannable = any(
                source.active and source.platform in SUPPORTED_PLATFORMS for source in sources
            )
        results: list[SourceScanResult] = []
        try:
            if not has_scannable:
                await self._resolve_sources(ctx, fetcher, company_id)
            source_ids = await self._scannable_source_ids(company_id)
            for source_id in source_ids:
                result = await self._scan_source(ctx, fetcher, company_id, source_id)
                if result is None:
                    continue
                results.append(result)
                if result.redirect is not None:
                    redirected = await self._source_from_redirect(
                        company_id, source_id, result.redirect
                    )
                    if redirected and redirected not in source_ids:
                        follow_up = await self._scan_source(ctx, fetcher, company_id, redirected)
                        if follow_up is not None:
                            results.append(follow_up)
        finally:
            await self._finalize_company(ctx, company_id, scanned=bool(results))
        if depth < ctx.budgets.max_depth:
            await self._similar_expansion(ctx, fetcher, company_id, depth)
        return len(results)

    async def _scannable_source_ids(self, company_id: uuid.UUID) -> list[uuid.UUID]:
        async with self.session_factory() as session:
            sources = (
                await session.scalars(
                    select(CareerSource).where(
                        CareerSource.company_id == company_id, CareerSource.active.is_(True)
                    )
                )
            ).all()
        ordered = sorted(
            sources,
            key=lambda source: (source.platform not in API_PLATFORMS, -source.platform_confidence),
        )
        return [source.id for source in ordered[:3]]

    @staticmethod
    def _platform_matches(company: Company, match: PlatformMatch, title: str) -> bool:
        key = company.name_key
        for value in (
            match.identifier.get(name) for name in ("token", "slug", "company", "tenant")
        ):
            candidate = company_key(value or "")
            if candidate and key and (candidate == key or (len(key) >= 4 and key in candidate)):
                return True
        name = normalize_company_name(company.name)
        return (
            bool(name)
            and re.search(rf"\b{re.escape(name)}\b", normalize_company_name(title)) is not None
        )

    async def _resolve_sources(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID
    ) -> None:
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                return
            if company.careers_url:
                await ensure_career_source(
                    session,
                    company,
                    company.careers_url,
                    discovered_via="company_record",
                    evidence="Career URL stored on the company record.",
                )
                await session.commit()
            entry = registry_entry(ctx.runtime, company)
            if entry is not None and entry.career_urls:
                for url in entry.career_urls:
                    await ensure_career_source(
                        session,
                        company,
                        url,
                        discovered_via="registry",
                        evidence=(
                            f"Career portal listed for {entry.name} in the company registry "
                            "(settings); confirmed only when a scan succeeds."
                        ),
                    )
                await session.commit()
                ctx.note(f"{company.name}: career portal(s) from the company registry.")
                return
            name = company.name
        if not self.search.ready(fetcher, ctx.runtime, ctx.errors):
            ctx.note(f"Web search unavailable; resolving {name} through direct probes only.")
        elif ctx.take("max_search_queries"):
            query = f'"{name}" careers jobs'
            errors: list[str] = []
            results = await self.search.search(
                fetcher,
                ctx.runtime,
                query,
                max(5, min(ctx.budgets.max_results_per_query, 10)),
                errors,
            )
            for message in errors:
                ctx.error(message)
            ctx.queries.append(
                {
                    "query": query,
                    "intent": "resolve",
                    "results": len(results),
                    "provider": results[0].provider if results else None,
                }
            )
            async with self.session_factory() as session:
                company = await session.get(Company, company_id)
                found = 0
                for result in results:
                    if company is None or found >= 3:
                        break
                    match = detect_from_url(result.url)
                    evidence = f'Search result "{result.title}" ({result.url}) for query "{query}".'
                    if match and match.platform not in {"generic_html", "schema_org", "unknown"}:
                        if self._platform_matches(company, match, result.title):
                            await ensure_career_source(
                                session,
                                company,
                                match.canonical_url,
                                discovered_via=f"search:{result.provider}",
                                evidence=evidence,
                                match=match,
                            )
                            found += 1
                        continue
                    host = hostname(result.url)
                    domain = registrable_domain(host) if host else None
                    if not domain or is_aggregator(host) or is_platform_host(host):
                        continue
                    if name_matches_domain(company.name, domain) < 0.7:
                        continue
                    if company.domain is None:
                        taken = await session.scalar(
                            select(Company.id).where(
                                Company.domain == domain, Company.id != company.id
                            )
                        )
                        if taken is None:
                            company.domain = domain
                    path = urlsplit(result.url).path
                    if host.startswith(("careers.", "jobs.", "career.")) or domain.endswith(
                        ".jobs"
                    ):
                        url = f"https://{host}/"
                    elif re.search(r"/(?:careers?|jobs?)(?:/|$)", path, re.I):
                        url = career_root(result.url)
                    else:
                        continue
                    await ensure_career_source(
                        session,
                        company,
                        url,
                        discovered_via=f"search:{result.provider}",
                        evidence=evidence,
                    )
                    found += 1
                await session.commit()
        else:
            ctx.note(f"No search queries left to resolve career sources for {name}.")
        if not await self._has_source(company_id, api_only=True):
            await self._probe_ats_boards(ctx, fetcher, company_id)
        if not await self._has_source(company_id):
            await self._probe_homepage(ctx, fetcher, company_id)
            await self._probe_domain(ctx, fetcher, company_id)

    async def _has_source(self, company_id: uuid.UUID, *, api_only: bool = False) -> bool:
        async with self.session_factory() as session:
            query = select(func.count(CareerSource.id)).where(
                CareerSource.company_id == company_id, CareerSource.active.is_(True)
            )
            if api_only:
                query = query.where(CareerSource.platform.in_(API_PLATFORMS))
            return bool(await session.scalar(query))

    async def _probe_ats_boards(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID
    ) -> None:
        """Check public ATS boards at slugs equal to the company name; only corroborated or
        exact-slug boards are recorded, with the evidence stated."""
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                return
            name, key = company.name, company.name_key
        slugs = list(dict.fromkeys([key, normalize_company_name(name).replace(" ", "-")]))[:2]
        for slug in slugs:
            probes = (
                ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}"),
                ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"),
                ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"),
            )
            for platform, url in probes:
                try:
                    data = await fetcher.get_json(url)
                except FetchError as exc:
                    if exc.blocked:
                        ctx.error(f"SOURCE_BLOCKED {platform} board probe: {exc}")
                    continue
                if platform == "greenhouse":
                    board_name = (data or {}).get("name") if isinstance(data, dict) else None
                    if not board_name or company_key(board_name) != key:
                        continue
                    confidence, evidence = (
                        0.9,
                        f"Greenhouse board '{slug}' is published under the name '{board_name}'.",
                    )
                elif platform == "lever":
                    if not isinstance(data, list):
                        continue
                    confidence = 0.6
                    evidence = (
                        f"A Lever board exists at slug '{slug}', which equals the company name; "
                        "Lever does not publish a company name to corroborate."
                    )
                else:
                    if not isinstance(data, dict) or "jobs" not in data:
                        continue
                    confidence = 0.6
                    evidence = (
                        f"An Ashby board exists at slug '{slug}', which equals the company name; "
                        "Ashby does not publish a company name to corroborate."
                    )
                board_url = {
                    "greenhouse": f"https://boards.greenhouse.io/{slug}",
                    "lever": f"https://jobs.lever.co/{slug}",
                    "ashby": f"https://jobs.ashbyhq.com/{slug}",
                }[platform]
                match = detect_from_url(board_url)
                if match is None:
                    continue
                async with self.session_factory() as session:
                    company = await session.get(Company, company_id)
                    if company is not None:
                        await ensure_career_source(
                            session,
                            company,
                            board_url,
                            discovered_via="ats_probe",
                            evidence=evidence,
                            match=PlatformMatch(
                                match.platform, confidence, match.canonical_url, match.identifier
                            ),
                        )
                        await session.commit()
                return

    async def _probe_homepage(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID
    ) -> None:
        """Verify an official domain when none is known yet.

        ``www.<name>.com``, ``.in`` and ``.jobs`` are only checked, never assumed: the domain is
        recorded only if the page's final host matches the name and the page names the company.
        A verified ``.jobs`` site becomes a career source without being recorded as the domain.
        """
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None or company.domain or len(company.name_key) < 3:
                return
            name, key = company.name, company.name_key
        expected = normalize_company_name(name)
        for url in (
            f"https://www.{key}.com/",
            f"https://www.{key}.in/",
            f"https://www.{key}.jobs/",
        ):
            try:
                page = await fetcher.get(url, headers=HTML_ACCEPT, max_bytes=3_000_000)
            except FetchError as exc:
                if exc.blocked:
                    ctx.note(
                        f"{name}: homepage check {url} was refused ({exc.code}); not bypassed."
                    )
                continue
            final_host = hostname(page.url)
            domain = registrable_domain(final_host) if final_host else None
            if not domain or name_matches_domain(name, domain) < 0.8:
                continue
            site_name = re.search(
                r"<meta[^>]+property=[\"']og:site_name[\"'][^>]+content=[\"']([^\"']+)",
                page.text,
                re.I,
            )
            label = " ".join(
                part
                for part in (page_title(page.text), site_name.group(1) if site_name else None)
                if part
            )
            if not re.search(rf"\b{re.escape(expected)}\b", normalize_company_name(label)):
                continue
            # ``.jobs`` is a careers-only TLD: it proves the careers site, not the official domain.
            careers_only = domain.endswith(".jobs")
            async with self.session_factory() as session:
                company = await session.get(Company, company_id)
                taken = (
                    None
                    if careers_only
                    else await session.scalar(
                        select(Company.id).where(Company.domain == domain, Company.id != company_id)
                    )
                )
                if company is not None and taken is None:
                    evidence = f"{page.url} is titled '{label[:120]}'."
                    if not careers_only:
                        company.domain = domain
                    platform = detect_from_url(page.url)
                    if platform is not None or careers_only:
                        await ensure_career_source(
                            session,
                            company,
                            page.url,
                            discovered_via="homepage_probe",
                            evidence=f"Verified careers site for {name}: {evidence}",
                            match=platform,
                        )
                    await session.commit()
                    ctx.note(
                        f"Verified careers site for {name}: {evidence}"
                        if careers_only
                        else f"Verified domain {domain} for {name}: {evidence}"
                    )
            return

    async def _probe_domain(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID
    ) -> None:
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None or not company.domain:
                return
            domain, name = company.domain, company.name
        for url in (f"https://{domain}/careers", f"https://careers.{domain}/"):
            try:
                page = await fetcher.get(url, headers=HTML_ACCEPT)
            except FetchError as exc:
                if exc.blocked:
                    ctx.error(f"SOURCE_BLOCKED {name} careers probe: {exc}")
                continue
            text = page.text.lower()
            final = urlsplit(page.url)
            lands_on_careers = (
                detect_from_url(page.url) is not None
                or (final.hostname or "").startswith(("careers.", "jobs.", "career."))
                or bool(re.search(r"career|jobs?", final.path, re.I))
            )
            # A redirect to the homepage is not evidence of a careers page.
            if not lands_on_careers or not re.search(
                r"\b(careers?|jobs?|openings?|positions?)\b", text
            ):
                continue
            async with self.session_factory() as session:
                company = await session.get(Company, company_id)
                if company is not None:
                    await ensure_career_source(
                        session,
                        company,
                        page.url,
                        discovered_via="domain_probe",
                        evidence=(
                            f"{url} on the company's verified domain {domain} resolved to the "
                            f"careers page {page.url}."
                        ),
                    )
                    await session.commit()
            return

    @staticmethod
    def _keywords(runtime: RuntimeSettings) -> list[str]:
        simplified = [
            re.sub(r"\s+", " ", _SENIORITY.sub(" ", role)).strip().lower()
            for role in runtime.target_roles
        ]
        return [keyword for keyword in dict.fromkeys(simplified) if keyword][:2] or [
            "data engineer"
        ]

    async def _relevant_open_jobs(self, session: AsyncSession, source_id: uuid.UUID) -> list[Job]:
        return list(
            (
                await session.scalars(
                    select(Job)
                    .join(JobSource, JobSource.job_id == Job.id)
                    .where(
                        JobSource.career_source_id == source_id,
                        Job.listing_status == "OPEN",
                        Job.role_category.in_(TARGET_CATEGORIES),
                        Job.role_relevance >= 0.5,
                    )
                    .distinct()
                )
            ).all()
        )

    async def _scan_source(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID, source_id: uuid.UUID
    ) -> SourceScanResult | None:
        async with self.session_factory() as session:
            source = await session.get(CareerSource, source_id)
            company = await session.get(Company, company_id)
            if source is None or company is None or not source.active:
                return None
            person = await get_candidate(session)
            countries, location_terms = location_hints(person.preferred_locations if person else [])
            request = ScanRequest(
                url=source.url,
                platform=source.platform,
                identifier=dict(source.platform_identifier or {}),
                company_name=company.name,
                keywords=self._keywords(ctx.runtime),
                max_listing_pages=3,
                max_detail_pages=max(1, min(20, ctx.remaining("max_pages"))),
                max_age_hours=ctx.runtime.max_job_age_hours,
                relevant_only=True,
                countries=countries,
                location_terms=location_terms,
            )
        if ctx.remaining("max_pages") <= 0:
            raise BudgetExhausted("max_pages")
        scan_started = utcnow()
        result = await run_adapter(adapter_for(request.platform), fetcher, request)
        if result.budget_exhausted:
            ctx.exhausted.add(result.budget_exhausted)
        ctx.counters["jobs_inspected"] += result.listings_seen
        ctx.counters["sources_scanned"] += 1
        async with self.session_factory() as session:
            source = await session.get(CareerSource, source_id)
            company = await session.get(Company, company_id)
            if source is None or company is None:
                return result
            ingestion = await DiscoveryService(
                effective_settings(self.settings, ctx.runtime)
            ).ingest_jobs(
                session,
                result.jobs,
                company=company,
                career_source=source,
                complete=result.complete,
                relevant_only=True,
                scan_started_at=scan_started,
            )
            now = utcnow()
            source.scan_status = result.status
            source.last_scanned_at = now
            source.jobs_found = result.listings_seen
            source.relevant_jobs_found = len(await self._relevant_open_jobs(session, source.id))
            if result.status in {"OK", "PARTIAL"}:
                source.last_success_at = now
            if result.complete:
                source.last_complete_scan_at = now
            messages = [*result.errors]
            if result.detail and result.status in {"UNSUPPORTED", "BLOCKED", "NOT_FOUND", "OK"}:
                messages.append(result.detail)
            if result.budget_exhausted:
                messages.append(
                    f"Scan stopped when the run's {result.budget_exhausted} budget was exhausted."
                )
            if result.company_name and company_key(result.company_name) != company.name_key:
                messages.append(f"The source names its organization '{result.company_name}'.")
            source.error = "; ".join(messages)[:2000] or None
            if result.status == "NOT_FOUND":
                source.active = False
            await session.commit()
        ctx.counters["jobs_created"] += ingestion.created
        ctx.counters["jobs_updated"] += ingestion.updated
        ctx.counters["jobs_closed"] += ingestion.closed
        if ingestion.created or ingestion.updated or ingestion.closed:
            ctx.touched_companies.add(str(company_id))
        label = f"{company.name} ({request.platform} {request.url})"
        if result.status == "BLOCKED":
            ctx.error(
                f"SOURCE_BLOCKED {label}: {'; '.join(result.errors[:2]) or 'access restricted'}"
            )
        elif result.status in {"ERROR", "NOT_FOUND"}:
            ctx.error(f"SOURCE_{result.status} {label}: {'; '.join(result.errors[:2])}")
        elif result.status == "UNSUPPORTED":
            ctx.note(f"UNSUPPORTED {label}: {result.detail}")
        elif result.errors:
            ctx.error(f"SOURCE_PARTIAL {label}: {'; '.join(result.errors[:2])}")
        return result

    async def _source_from_redirect(
        self, company_id: uuid.UUID, source_id: uuid.UUID, match: PlatformMatch
    ) -> uuid.UUID | None:
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            original = await session.get(CareerSource, source_id)
            if company is None or original is None:
                return None
            created = await ensure_career_source(
                session,
                company,
                match.canonical_url,
                discovered_via="embedded_in_career_page",
                evidence=(
                    f"{original.url} embeds or links to {match.platform} ({match.canonical_url})."
                ),
                match=match,
            )
            if created.id != original.id:
                original.active = False
                original.error = (
                    f"Career page is served by {match.platform}; "
                    f"scanning {match.canonical_url} instead."
                )
            await session.commit()
            return created.id

    async def _finalize_company(
        self, ctx: RunContext, company_id: uuid.UUID, *, scanned: bool
    ) -> None:
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                return
            now = utcnow()
            sources = (
                await session.scalars(
                    select(CareerSource).where(CareerSource.company_id == company_id)
                )
            ).all()
            for source in sources:
                if not source.last_scanned_at or as_utc(source.last_scanned_at) < now - timedelta(
                    hours=1
                ):
                    continue
                jobs = await self._relevant_open_jobs(session, source.id)
                if not jobs:
                    continue
                dated = [as_utc(job.posted_at) for job in jobs if job.posted_at]
                titles = ", ".join(f"'{job.title}'" for job in jobs[:3])
                await record_event(
                    session,
                    company,
                    reason="CAREER_PAGE",
                    source=source.platform,
                    source_url=source.url,
                    evidence=(
                        f"{len(jobs)} open data/platform engineering role(s) found on the "
                        f"{source.platform} career source {source.url}, e.g. {titles}."
                    ),
                    confidence=0.95,
                    evidence_data={
                        "role_relevance": max(job.role_relevance for job in jobs),
                        "relevant_jobs": len(jobs),
                        "platform": source.platform,
                        "signal_date": max(dated).isoformat() if dated else None,
                    },
                    run_id=ctx.run_id,
                    key="career_page",
                )
            stats = await refresh_company(session, company, ctx.runtime, now)
            succeeded = [source for source in sources if source.last_success_at]
            if company.review_status == "IGNORED":
                company.status = "INACTIVE"
            elif stats.data_roles + stats.platform_roles > 0 and succeeded:
                company.status = "ACTIVE"
            elif succeeded:
                complete = any(source.last_complete_scan_at for source in succeeded)
                company.status = "INACTIVE" if complete else "VERIFIED"
            else:
                company.status = "UNVERIFIED"
            if scanned:
                company.last_scanned_at = now
            company.last_explored_at = now
            pending = (
                await session.scalars(
                    select(DiscoveryCandidate).where(
                        DiscoveryCandidate.company_id == company_id,
                        DiscoveryCandidate.status == "PENDING",
                    )
                )
            ).all()
            for candidate in pending:
                if ctx.kind in {"company_scan", "scan"}:
                    candidate.status, candidate.explored_run_id = "EXPLORED", ctx.run_id
            await session.commit()

    async def _similar_expansion(
        self, ctx: RunContext, fetcher: SafeFetcher, company_id: uuid.UUID, depth: int
    ) -> None:
        provider = self.providers.get("web_search")
        if provider is None or ctx.remaining("max_search_queries") <= 0:
            return
        async with self.session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                return
            relevant = await session.scalar(
                select(func.count(Job.id)).where(
                    Job.company_id == company_id,
                    Job.listing_status == "OPEN",
                    Job.role_category == "data_engineering",
                    Job.role_relevance >= 0.5,
                )
            )
            name = company.name
        if not relevant:
            return
        errors: list[str] = []
        provider_ctx = self._provider_context(
            ctx, fetcher, errors, query_allocation={"similar": 1}, similar_targets=[name]
        )
        try:
            signals = await provider.search_companies(provider_ctx)
        except FetchError as exc:
            ctx.error(f"SOURCE_ERROR similar-company search for {name}: {exc}")
            return
        for message in errors:
            ctx.error(message)
        ctx.queries.extend(provider_ctx.queries_run)
        for signal in signals:
            await self._apply_signal(ctx, signal, depth + 1)

    async def _frontier_run(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        candidate_id = params.get("candidate_id")
        await self._process_frontier(
            ctx, fetcher, candidate_ids=[uuid.UUID(candidate_id)] if candidate_id else None
        )

    async def _company_scan(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        company_id = uuid.UUID(params["company_id"])
        scanned = await self.explore_company(ctx, fetcher, company_id, depth=0)
        if not scanned:
            ctx.note("No scannable career source could be resolved for this company.")

    async def _seed_resolution(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        company_ids = [uuid.UUID(value) for value in params.get("company_ids", [])]
        if company_ids:
            await self._process_frontier(ctx, fetcher, company_ids=company_ids)
        async with self.session_factory() as session:
            remaining = (
                await session.scalar(
                    select(func.count(DiscoveryCandidate.id)).where(
                        DiscoveryCandidate.company_id.in_(company_ids),
                        DiscoveryCandidate.status == "PENDING",
                    )
                )
                if company_ids
                else 0
            )
            if remaining:
                ctx.note(
                    f"{remaining} seed(s) remain in the frontier for later runs (budget limit)."
                )
            follow_up, created = await enqueue_run(
                session,
                "exploration",
                budgets=ctx.runtime.budgets.model_dump(),
                trigger="seed_followup",
                dedupe_key="kind:exploration",
            )
        ctx.details["follow_up_run_id"] = str(follow_up.id)
        ctx.note(
            ("Queued" if created else "Exploration already queued:")
            + f" market exploration run {follow_up.id} to discover related companies."
        )

    async def _scan_known(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        now = utcnow()
        due_before = (
            now - timedelta(hours=ctx.runtime.scan_interval_hours)
            if ctx.trigger == "schedule"
            else now
        )
        async with self.session_factory() as session:
            ids = (
                await session.scalars(
                    select(Company.id)
                    .where(
                        Company.review_status != "IGNORED",
                        or_(
                            Company.status.in_(("VERIFIED", "ACTIVE", "INACTIVE")),
                            Company.is_seed.is_(True),
                        ),
                        or_(
                            Company.last_scanned_at.is_(None), Company.last_scanned_at < due_before
                        ),
                    )
                    .order_by(
                        # S-tier companies are scanned first; tiers never exclude anyone.
                        case((Company.tier == "S", 0), else_=1),
                        Company.last_scanned_at.asc().nulls_first(),
                        Company.discovery_score.desc(),
                    )
                    .limit(max(ctx.limits["max_company_expansion"], 0))
                )
            ).all()
        if not ids:
            ctx.note("No known companies are due for scanning.")
        for company_id in ids:
            if not ctx.take("max_company_expansion"):
                break
            await self.explore_company(ctx, fetcher, company_id, depth=ctx.budgets.max_depth)

    async def _reconcile(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        now = utcnow()
        closed = verified = rescored = unverifiable = 0
        async with self.session_factory() as session:
            sources = (
                await session.scalars(
                    select(JobSource).where(JobSource.listing_status == "OPEN").limit(5000)
                )
            ).all()
            for source in sources:
                payload = source.raw_payload or {}
                end, _ = parse_datetime(
                    payload.get("validThrough") or payload.get("ExternalPostedEndDate")
                )
                if end is not None and end < now:
                    closed += await self._close_source(session, source, now)
            await session.commit()

        stale_before = now - timedelta(hours=max(72.0, 3 * ctx.runtime.scan_interval_hours))
        async with self.session_factory() as session:
            stale = (
                await session.scalars(
                    select(JobSource)
                    .where(
                        JobSource.listing_status == "OPEN",
                        JobSource.source.in_(KEYWORD_SCOPED_SOURCES),
                        or_(
                            JobSource.last_seen_at.is_(None), JobSource.last_seen_at < stale_before
                        ),
                    )
                    .limit(max(ctx.remaining("max_pages"), 0))
                )
            ).all()
            for source in stale:
                try:
                    page = await fetcher.get(source.source_url, headers=HTML_ACCEPT)
                except BudgetExhausted:
                    ctx.exhausted.add("max_pages")
                    break
                except FetchError as exc:
                    if exc.code == "NOT_FOUND":
                        closed += await self._close_source(session, source, now)
                    else:
                        kind = "SOURCE_BLOCKED" if exc.blocked else "SOURCE_ERROR"
                        ctx.error(f"{kind} verifying {source.source_url}: {exc}")
                    continue
                postings = [posting_fields(item, page.url) for item in find_job_postings(page.text)]
                expired = [
                    item
                    for item in postings
                    if item["valid_through"] and item["valid_through"] < now
                ]
                if expired:
                    closed += await self._close_source(session, source, now)
                elif postings:
                    source.last_seen_at = now
                    verified += 1
                else:
                    unverifiable += 1
            await session.commit()

        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Company.id, Company.name, Company.discovery_score, Company.score_breakdown
                    )
                )
            ).all()
            scores = {row.id: row.discovery_score for row in rows if row.score_breakdown}
            names = {row.id: row.name for row in rows}
            scorer = OpportunityScorer(effective_settings(self.settings, ctx.runtime))
            jobs = (
                await session.scalars(select(Job).where(Job.listing_status == "OPEN").limit(5000))
            ).all()
            for job in jobs:
                raw = IngestedJob(
                    company=job.company,
                    title=job.title,
                    location=job.location,
                    remote_status=job.remote_status
                    if job.remote_status in {"remote", "hybrid", "onsite"}
                    else "unknown",
                    salary=job.salary,
                    employment_type=job.employment_type,
                    application_url=job.application_url,
                    source_url=job.application_url,
                    source=job.ats,
                    ats=job.ats,
                    external_id=str(job.id),
                    posted_at=as_utc(job.posted_at),
                    posted_at_precision=job.posted_at_precision,
                    description=job.description,
                    raw_payload={},
                )
                classification = classify_role(job.title, job.description, job.company)
                company_score = scores.get(job.company_id) if job.company_id else None
                score = scorer.score(
                    normalize_job(raw), classification, company_score, names.get(job.company_id)
                )
                job.role_category, job.role_relevance = (
                    classification.category,
                    classification.relevance,
                )
                job.overall_score, job.score_breakdown, job.score_reasons = (
                    score.overall,
                    score.breakdown,
                    score.reasons,
                )
                rescored += 1
            companies = (await session.scalars(select(Company).limit(2000))).all()
            for company in companies:
                await refresh_company(session, company, ctx.runtime, now)
            await session.commit()
        ctx.details["reconciliation"] = {
            "closed": closed,
            "verified_still_listed": verified,
            "unverifiable": unverifiable,
            "rescored": rescored,
        }
        ctx.counters["jobs_closed"] += closed

    @staticmethod
    async def _close_source(session: AsyncSession, source: JobSource, now: datetime) -> int:
        source.listing_status, source.closed_at = "CLOSED", now
        job = await session.get(Job, source.job_id)
        if job is None or job.listing_status == "CLOSED":
            return 0
        await session.refresh(job, ["sources"])
        if all(item.listing_status == "CLOSED" for item in job.sources):
            job.listing_status, job.closed_at = "CLOSED", now
            return 1
        return 0

    async def _legacy_ingestion(
        self, ctx: RunContext, fetcher: SafeFetcher, params: dict[str, Any]
    ) -> None:
        provider, identifier = params["provider"], params["identifier"]
        board_url = {
            "greenhouse": f"https://boards.greenhouse.io/{identifier}",
            "lever": f"https://jobs.lever.co/{identifier}",
        }[provider]
        match = detect_from_url(board_url)
        if match is None:
            raise RunFailure(f"Unsupported board identifier: {identifier}")
        request = ScanRequest(
            url=match.canonical_url,
            platform=provider,
            identifier=match.identifier,
            company_name=identifier.replace("-", " ").title(),
            relevant_only=False,
            max_age_hours=ctx.runtime.max_job_age_hours,
        )
        started = utcnow()
        result = await run_adapter(adapter_for(provider), fetcher, request)
        ctx.counters["jobs_inspected"] += result.listings_seen
        if result.status not in {"OK", "PARTIAL"}:
            detail = "; ".join(result.errors) or result.status
            if result.status == "NOT_FOUND":
                raise RunFailure(
                    f"{provider.title()} board '{identifier}' was not found (HTTP 404)."
                )
            raise RunFailure(f"Job source could not be read: {detail}")
        company_name = result.company_name or request.company_name
        async with self.session_factory() as session:
            company = await find_company(session, company_name)
            if company is None:
                company = new_company(
                    company_name,
                    tier=ctx.runtime.default_tier,
                    status="DISCOVERED",
                    review_status="ACCEPTED",
                    discovery_source="manual_ingestion",
                    discovery_reason=f"Imported directly from the {provider} board '{identifier}'.",
                    confidence=0.9,
                )
                session.add(company)
                await session.flush()
                ctx.counters["companies_discovered"] += 1
            source = await ensure_career_source(
                session,
                company,
                match.canonical_url,
                discovered_via="manual_ingestion",
                evidence=f"Direct {provider} import requested for '{identifier}'.",
                match=match,
            )
            ingestion = await DiscoveryService(
                effective_settings(self.settings, ctx.runtime)
            ).ingest_jobs(
                session,
                result.jobs,
                company=company,
                career_source=source,
                complete=result.complete,
                relevant_only=False,
                scan_started_at=started,
            )
            now = utcnow()
            source.scan_status = result.status
            source.last_scanned_at = source.last_success_at = now
            if result.complete:
                source.last_complete_scan_at = now
            source.jobs_found = result.listings_seen
            source.relevant_jobs_found = len(await self._relevant_open_jobs(session, source.id))
            source.error = "; ".join(result.errors)[:2000] or None
            company_id = company.id
            await session.commit()
        await self._finalize_company(ctx, company_id, scanned=True)
        ctx.counters["jobs_created"] += ingestion.created
        ctx.counters["jobs_updated"] += ingestion.updated
        ctx.counters["jobs_closed"] += ingestion.closed
        if ingestion.created or ingestion.updated or ingestion.closed:
            ctx.touched_companies.add(str(company_id))
        ctx.details["ingestion"] = ingestion.model_dump()
        for message in result.errors:
            ctx.error(f"SOURCE_PARTIAL {provider} {identifier}: {message}")
