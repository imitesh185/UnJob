"""Pluggable company-discovery providers.

Each provider turns observed public data into ``CandidateSignal`` evidence. Providers do
not create companies themselves; the exploration service validates, deduplicates, and
records every signal with its source URL.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.discovery.ats import detect_from_url
from app.discovery.extraction import (
    CandidateSignal,
    career_root,
    clean_company_candidate,
    extract_signal,
    extract_similar,
)
from app.discovery.http import SafeFetcher
from app.discovery.jsonld import html_to_text, parse_datetime
from app.discovery.names import (
    company_key,
    employee_range,
    is_aggregator,
    is_india_location,
    is_location_text,
    name_matches_domain,
    registrable_domain,
)
from app.discovery.roles import classify_role, find_role_phrase
from app.discovery.search import SearchService
from app.discovery.settings_store import RuntimeSettings


@dataclass
class ProviderContext:
    fetcher: SafeFetcher
    runtime: RuntimeSettings
    search: SearchService
    results_per_query: int
    errors: list[str]
    take_query: Callable[[], bool]
    cache_get: Callable[[str, float], Awaitable[Any]]
    cache_set: Callable[[str, Any], Awaitable[None]]
    known_keys: set[str] = field(default_factory=set)
    query_allocation: dict[str, int] = field(default_factory=dict)
    rotation: int = 0
    similar_targets: list[str] = field(default_factory=list)
    queries_run: list[dict[str, Any]] = field(default_factory=list)


class DiscoveryProvider(Protocol):
    name: str

    async def search_companies(self, ctx: ProviderContext) -> list[CandidateSignal]: ...

    async def search_jobs(self, ctx: ProviderContext) -> list[CandidateSignal]: ...

    async def search_hiring_signals(self, ctx: ProviderContext) -> list[CandidateSignal]: ...


class BaseProvider:
    name = "base"

    async def search_companies(self, ctx: ProviderContext) -> list[CandidateSignal]:
        return []

    async def search_jobs(self, ctx: ProviderContext) -> list[CandidateSignal]:
        return []

    async def search_hiring_signals(self, ctx: ProviderContext) -> list[CandidateSignal]:
        return []


class WebSearchProvider(BaseProvider):
    name = "web_search"
    job_templates = (
        '"{role}" {location} careers',
        'site:boards.greenhouse.io "{role}" {location}',
        'site:jobs.lever.co "{role}" {location}',
        '"{role}" jobs {location} hiring',
        'site:jobs.ashbyhq.com "{role}"',
        'site:myworkdayjobs.com "{role}" {location}',
    )
    linkedin_template = 'site:linkedin.com/jobs/view "{role}" {location}'
    x_template = 'site:x.com "hiring" "{role}"'

    @staticmethod
    def _plan(
        templates: tuple[str, ...], runtime: RuntimeSettings, count: int, rotation: int
    ) -> list[str]:
        queries: list[str] = []
        for location in runtime.target_locations:
            for role in runtime.target_roles:
                for template in templates:
                    query = template.format(role=role, location=location).strip()
                    if query not in queries:
                        queries.append(query)
        if not queries or count <= 0:
            return []
        start = rotation % len(queries)
        ordered = queries[start:] + queries[:start]
        return ordered[:count]

    async def _run(
        self, ctx: ProviderContext, queries: list[str], intent: str
    ) -> list[CandidateSignal]:
        signals: list[CandidateSignal] = []
        for query in queries:
            if not ctx.search.ready(ctx.fetcher, ctx.runtime, ctx.errors):
                break
            if not ctx.take_query():
                break
            results = await ctx.search.search(
                ctx.fetcher, ctx.runtime, query, ctx.results_per_query, ctx.errors
            )
            extracted = [signal for result in results if (signal := extract_signal(result, intent))]
            ctx.queries_run.append(
                {
                    "query": query,
                    "intent": intent,
                    "provider": results[0].provider if results else None,
                    "results": len(results),
                    "candidates": len(extracted),
                }
            )
            signals.extend(extracted)
        return signals

    async def search_jobs(self, ctx: ProviderContext) -> list[CandidateSignal]:
        count = ctx.query_allocation.get("job", 0)
        return await self._run(
            ctx, self._plan(self.job_templates, ctx.runtime, count, ctx.rotation), "job"
        )

    async def search_hiring_signals(self, ctx: ProviderContext) -> list[CandidateSignal]:
        signals: list[CandidateSignal] = []
        if ctx.runtime.providers.get("linkedin_via_search", True):
            queries = self._plan(
                (self.linkedin_template,),
                ctx.runtime,
                ctx.query_allocation.get("linkedin", 0),
                ctx.rotation,
            )
            signals += await self._run(ctx, queries, "linkedin")
        if ctx.runtime.providers.get("x_via_search", True):
            queries = self._plan(
                (self.x_template,), ctx.runtime, ctx.query_allocation.get("x", 0), ctx.rotation
            )
            signals += await self._run(ctx, queries, "x")
        return signals

    async def search_companies(self, ctx: ProviderContext) -> list[CandidateSignal]:
        signals: list[CandidateSignal] = []
        for name in ctx.similar_targets[: ctx.query_allocation.get("similar", 0)]:
            if not ctx.search.ready(ctx.fetcher, ctx.runtime, ctx.errors):
                break
            if not ctx.take_query():
                break
            query = f'"{name}" competitors'
            results = await ctx.search.search(
                ctx.fetcher, ctx.runtime, query, ctx.results_per_query, ctx.errors
            )
            found = [signal for result in results for signal in extract_similar(result, name)]
            ctx.queries_run.append(
                {
                    "query": query,
                    "intent": "similar",
                    "results": len(results),
                    "candidates": len(found),
                    "provider": results[0].provider if results else None,
                }
            )
            signals.extend(found)
        return signals


HN_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_BY_DATE = "https://hn.algolia.com/api/v1/search_by_date"
_HREF = re.compile(r'href="([^"]+)"')


def parse_hn_comment(hit: dict[str, Any], story: dict[str, Any]) -> CandidateSignal | None:
    raw = hit.get("comment_text") or ""
    first_line = html_to_text(re.split(r"<p>", raw, maxsplit=1)[0], 400)
    segments = [segment.strip() for segment in first_line.split("|") if segment.strip()]
    if len(segments) < 2:
        return None
    company = clean_company_candidate(segments[0])
    if not company:
        return None
    role = next(
        (
            segment
            for segment in segments[1:]
            if find_role_phrase(segment) or classify_role(segment).is_target
        ),
        None,
    )
    if role is None:
        phrase = find_role_phrase(html_to_text(raw, 4000))
        role = phrase.group(0) if phrase else None
    if not role or not classify_role(role).is_target:
        return None
    location = next(
        (
            segment
            for segment in segments[1:]
            if is_location_text(segment) or is_india_location(segment)
        ),
        None,
    )
    domain = career_url = None
    for link in (html_lib.unescape(href) for href in _HREF.findall(raw)):
        platform = detect_from_url(link)
        if platform and platform.platform not in {"generic_html", "schema_org", "unknown"}:
            career_url = career_url or platform.canonical_url
        elif (
            not is_aggregator(link)
            and name_matches_domain(company, registrable_domain(link)) >= 0.7
        ):
            domain = domain or registrable_domain(link)
            if re.search(r"/(?:careers?|jobs?)", link, re.I):
                career_url = career_url or career_root(link)
    posted, precision = parse_datetime(hit.get("created_at"))
    day = posted.date().isoformat() if posted else "UNKNOWN date"
    return CandidateSignal(
        company_name=company,
        reason="JOB_SEARCH",
        source="hackernews",
        source_url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
        evidence=(
            f'Hacker News "{story.get("title")}" post on {day}: "{first_line[:240]}". '
            f'Target role mentioned: "{role[:120]}".'
        ),
        confidence=0.75,
        role_title=role[:200],
        role_relevance=classify_role(role).relevance,
        location=location,
        signal_date=posted,
        signal_date_precision=precision,
        domain=domain,
        career_url=career_url,
        metadata={
            "thread": story.get("title"),
            "india_presence": True if is_india_location(first_line) else None,
            "remote_presence": True if re.search(r"\bremote\b", first_line, re.I) else None,
        },
    )


class HackerNewsProvider(BaseProvider):
    name = "hackernews"
    queries = ("data engineer", "data platform", "data infrastructure")

    async def search_jobs(self, ctx: ProviderContext) -> list[CandidateSignal]:
        if not ctx.runtime.providers.get("hackernews", True) or ctx.results_per_query <= 0:
            return []
        stories = await ctx.cache_get("hn:whoishiring", 6 * 3600)
        if stories is None:
            data = await ctx.fetcher.get_json(
                HN_BY_DATE, params={"tags": "story,author_whoishiring", "hitsPerPage": 10}
            )
            stories = [
                {
                    "id": hit["objectID"],
                    "title": hit.get("title"),
                    "created_at": hit.get("created_at"),
                }
                for hit in data.get("hits", [])
                if (hit.get("title") or "").lower().startswith("ask hn: who is hiring")
            ]
            await ctx.cache_set("hn:whoishiring", stories)
        if not stories:
            return []
        story = stories[0]
        created, _ = parse_datetime(story.get("created_at"))
        if created and (datetime.now(UTC) - created).days > 45:
            ctx.errors.append("hackernews: no 'Who is hiring?' thread from the last 45 days.")
            return []
        signals: list[CandidateSignal] = []
        seen: set[str] = set()
        for query in self.queries:
            data = await ctx.fetcher.get_json(
                HN_SEARCH,
                params={
                    "tags": f"comment,story_{story['id']}",
                    "query": query,
                    "hitsPerPage": ctx.results_per_query,
                },
            )
            for hit in data.get("hits", []):
                if str(hit.get("parent_id")) != str(story["id"]) or hit["objectID"] in seen:
                    continue
                seen.add(hit["objectID"])
                signal = parse_hn_comment(hit, story)
                if signal:
                    signals.append(signal)
        return signals


class RemotiveProvider(BaseProvider):
    name = "remotive"
    endpoint = "https://remotive.com/api/remote-jobs"

    async def search_jobs(self, ctx: ProviderContext) -> list[CandidateSignal]:
        if not ctx.runtime.providers.get("remotive", True) or ctx.results_per_query <= 0:
            return []
        jobs = await ctx.cache_get("remotive:data-engineer", 6 * 3600)
        if jobs is None:
            data = await ctx.fetcher.get_json(
                self.endpoint, params={"search": "data engineer", "limit": 100}
            )
            jobs = [
                {
                    "company_name": item.get("company_name"),
                    "title": item.get("title"),
                    "publication_date": item.get("publication_date"),
                    "candidate_required_location": item.get("candidate_required_location"),
                    "url": item.get("url"),
                    "description": html_to_text(item.get("description"), 3000),
                }
                for item in data.get("jobs", [])
            ]
            await ctx.cache_set("remotive:data-engineer", jobs)
        signals: list[CandidateSignal] = []
        for job in jobs:
            role_class = classify_role(
                job.get("title"), job.get("description"), job.get("company_name")
            )
            company = clean_company_candidate(job.get("company_name"))
            if not company or not role_class.is_target:
                continue
            posted, precision = parse_datetime(job.get("publication_date"))
            location = job.get("candidate_required_location") or "UNKNOWN"
            india_ok = bool(
                re.search(r"worldwide|anywhere|apac|asia", location, re.I)
                or is_india_location(location)
            )
            signals.append(
                CandidateSignal(
                    company_name=company,
                    reason="JOB_SEARCH",
                    source="remotive",
                    source_url=job.get("url"),
                    evidence=(
                        f'Remotive remote listing "{job.get("title")}" published '
                        f"{posted.date().isoformat() if posted else 'UNKNOWN date'}; "
                        f"candidate location: {location}."
                    ),
                    confidence=0.7,
                    role_title=job.get("title"),
                    role_relevance=role_class.relevance,
                    location=location,
                    signal_date=posted,
                    signal_date_precision=precision,
                    metadata={
                        "remote_presence": True,
                        "india_presence": True if is_india_location(location) else None,
                        "india_eligible_remote": india_ok,
                    },
                )
            )
            if len(signals) >= ctx.results_per_query:
                break
        return signals


_YC_TAGS = {
    "data engineering",
    "databases",
    "analytics",
    "infrastructure",
    "developer tools",
    "fintech",
    "payments",
    "machine learning",
    "artificial intelligence",
    "ai",
    "b2b",
    "saas",
    "big data",
    "cloud computing",
    "open source",
    "security",
    "observability",
    "devops",
    "data visualization",
    "enterprise software",
    "banking and exchange",
    "insurance",
    "credit and lending",
    "enterprise",
}
_YC_TEXT = re.compile(
    r"\b(data|analytics|database|warehouse|pipelines?|streaming|infrastructure|platform|api|"
    r"payments?|lending|fintech|machine learning|ml|ai)\b",
    re.I,
)


class YCStartupProvider(BaseProvider):
    name = "yc_oss"
    endpoint = "https://yc-oss.github.io/api/companies/hiring.json"

    async def search_companies(self, ctx: ProviderContext) -> list[CandidateSignal]:
        if not ctx.runtime.providers.get("yc_oss", True) or ctx.results_per_query <= 0:
            return []
        companies = await ctx.cache_get("yc_oss:hiring", 24 * 3600)
        if companies is None:
            data = await ctx.fetcher.get_json(self.endpoint)
            regions = {region.lower() for region in ctx.runtime.startup_regions}
            companies = [
                {
                    key: item.get(key)
                    for key in (
                        "name",
                        "slug",
                        "website",
                        "batch",
                        "industries",
                        "tags",
                        "regions",
                        "team_size",
                        "stage",
                        "status",
                        "one_liner",
                        "url",
                        "isHiring",
                    )
                }
                for item in data
                if item.get("isHiring")
                and (item.get("status") or "Active") == "Active"
                and regions & {str(region).lower() for region in item.get("regions") or []}
            ]
            await ctx.cache_set("yc_oss:hiring", companies)
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in companies:
            key = company_key(item.get("name") or "")
            if not key or key in ctx.known_keys:
                continue
            labels = {
                str(value).lower()
                for value in (item.get("industries") or []) + (item.get("tags") or [])
            }
            tag_matches = len(labels & _YC_TAGS)
            text_match = bool(_YC_TEXT.search(item.get("one_liner") or ""))
            team = item.get("team_size") or 0
            if not (tag_matches or text_match) or (team and team < 15):
                continue
            regions = [str(region) for region in item.get("regions") or []]
            score = 3 * ("India" in regions) + ("Remote" in regions) + min(tag_matches, 4)
            score += 2 * text_match + 2 * (team >= 50) + (team >= 200)
            scored.append((score, item))
        scored.sort(key=lambda pair: -pair[0])
        signals: list[CandidateSignal] = []
        for _score, item in scored[: ctx.results_per_query]:
            name = clean_company_candidate(item.get("name"))
            if not name:
                continue
            regions = [str(region) for region in item.get("regions") or []]
            industries = [str(value) for value in item.get("industries") or []]
            signals.append(
                CandidateSignal(
                    company_name=name,
                    reason="STARTUP_DISCOVERY",
                    source="yc_oss",
                    source_url=item.get("url"),
                    evidence=(
                        f"YC company ({item.get('batch') or 'batch UNKNOWN'}) marked as hiring "
                        f"in the open yc-oss dataset. Regions: {', '.join(regions) or 'UNKNOWN'}; "
                        f"industries: {', '.join(industries) or 'UNKNOWN'}; "
                        f"team size: {item.get('team_size') or 'UNKNOWN'}. "
                        "Data-engineering hiring is not yet verified."
                    ),
                    confidence=0.45,
                    domain=registrable_domain(item["website"]) if item.get("website") else None,
                    metadata={
                        "industry": ", ".join(industries) or None,
                        "company_stage": item.get("stage"),
                        "employee_range": employee_range(item.get("team_size")),
                        "india_presence": True if "India" in regions else None,
                        "remote_presence": True if "Remote" in regions else None,
                        "company_type": f"YC startup ({item.get('batch')})"
                        if item.get("batch")
                        else "YC startup",
                        "product_company": True,
                    },
                )
            )
        return signals


def default_providers(search: SearchService) -> dict[str, BaseProvider]:
    return {
        "hackernews": HackerNewsProvider(),
        "remotive": RemotiveProvider(),
        "web_search": WebSearchProvider(),
        "yc_oss": YCStartupProvider(),
    }
