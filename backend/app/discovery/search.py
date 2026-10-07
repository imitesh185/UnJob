"""Web search backends. Discovery uses whichever configured backend answers first."""

from __future__ import annotations

import html as html_lib
import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from app.config import Settings
from app.discovery.http import FetchError, SafeFetcher, looks_like_challenge
from app.discovery.jsonld import html_to_text
from app.discovery.settings_store import RuntimeSettings


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str
    query: str
    rank: int


class SearchBackend(Protocol):
    name: str

    def configured(self) -> bool: ...

    async def search(self, fetcher: SafeFetcher, query: str, limit: int) -> list[SearchResult]: ...


def _inline(value: str | None, limit: int) -> str:
    """Search titles and snippets are single-line evidence strings."""
    return " ".join(html_to_text(value, limit).split())


_DDG_ANCHOR = re.compile(r"<a\b([^>]*\bclass=\"[^\"]*\bresult__a\b[^\"]*\"[^>]*)>(.*?)</a>", re.S)
_HREF = re.compile(r"\bhref=\"([^\"]+)\"")
_DDG_SNIPPET = re.compile(
    r"class=\"[^\"]*\bresult__snippet\b[^\"]*\"[^>]*>(.*?)</(?:a|div|td)>", re.S
)


def parse_duckduckgo(page: str, query: str, limit: int) -> list[SearchResult]:
    anchors = list(_DDG_ANCHOR.finditer(page))
    results: list[SearchResult] = []
    for index, match in enumerate(anchors):
        href_match = _HREF.search(match.group(1))
        if not href_match:
            continue
        href = html_lib.unescape(href_match.group(1))
        if href.startswith("//"):
            href = "https:" + href
        target = parse_qs(urlsplit(href).query).get("uddg", [href])[0]
        host = (urlsplit(target).hostname or "").lower()
        if not host or host.endswith("duckduckgo.com") or "/y.js" in href:
            continue
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(page)
        snippet = _DDG_SNIPPET.search(page, match.end(), end)
        results.append(
            SearchResult(
                title=_inline(match.group(2), 300),
                url=target,
                snippet=_inline(snippet.group(1), 600) if snippet else "",
                provider="duckduckgo",
                query=query,
                rank=len(results) + 1,
            )
        )
        if len(results) >= limit:
            break
    return results


class DuckDuckGoBackend:
    name = "duckduckgo"
    host = "html.duckduckgo.com"
    endpoint = "https://html.duckduckgo.com/html/"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def configured(self) -> bool:
        return True

    async def search(self, fetcher: SafeFetcher, query: str, limit: int) -> list[SearchResult]:
        response = await fetcher.get(
            self.endpoint,
            params={"q": query},
            headers={"Accept": "text/html"},
            min_interval=self.settings.discovery_search_min_interval_seconds,
        )
        # DuckDuckGo answers throttled or suspected-automation requests with HTTP 202 and an
        # anomaly page. That is treated as a block, never bypassed.
        if response.status_code == 202 or looks_like_challenge(response.text):
            fetcher.cooldown(fetcher.host_of(response.url))
            raise FetchError(
                "RATE_LIMITED",
                "DuckDuckGo returned a rate-limit/anomaly page; it was not bypassed.",
                response.url,
                response.status_code,
            )
        return parse_duckduckgo(response.text, query, limit)


class SearxngBackend:
    name = "searxng"

    def __init__(self, settings: Settings) -> None:
        self.base = (settings.searxng_url or "").rstrip("/")
        self.host = (urlsplit(self.base).hostname or "") if self.base else ""

    def configured(self) -> bool:
        return bool(self.base)

    async def search(self, fetcher: SafeFetcher, query: str, limit: int) -> list[SearchResult]:
        data = await fetcher.get_json(
            f"{self.base}/search", params={"q": query, "format": "json", "safesearch": 1}
        )
        return [
            SearchResult(
                title=_inline(item.get("title"), 300),
                url=item["url"],
                snippet=_inline(item.get("content"), 600),
                provider=self.name,
                query=query,
                rank=index + 1,
            )
            for index, item in enumerate((data.get("results") or [])[:limit])
            if item.get("url")
        ]


class BraveBackend:
    name = "brave"
    host = "api.search.brave.com"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, settings: Settings) -> None:
        self.key = settings.brave_search_api_key

    def configured(self) -> bool:
        return bool(self.key)

    async def search(self, fetcher: SafeFetcher, query: str, limit: int) -> list[SearchResult]:
        data = await fetcher.get_json(
            self.endpoint,
            params={"q": query, "count": max(1, min(limit, 20))},
            headers={"X-Subscription-Token": self.key or ""},
            check_robots=False,
        )
        items = ((data.get("web") or {}).get("results") or [])[:limit]
        return [
            SearchResult(
                title=_inline(item.get("title"), 300),
                url=item["url"],
                snippet=_inline(item.get("description"), 600),
                provider=self.name,
                query=query,
                rank=index + 1,
            )
            for index, item in enumerate(items)
            if item.get("url")
        ]


class TavilyBackend:
    """Optional Tavily search API (free tier requires a key)."""

    name = "tavily"
    host = "api.tavily.com"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, settings: Settings) -> None:
        self.key = settings.tavily_api_key

    def configured(self) -> bool:
        return bool(self.key)

    async def search(self, fetcher: SafeFetcher, query: str, limit: int) -> list[SearchResult]:
        data = await fetcher.post_json(
            self.endpoint,
            {
                "query": query,
                "max_results": max(1, min(limit, 20)),
                "search_depth": "basic",
                "include_answer": False,
            },
            headers={"Authorization": f"Bearer {self.key or ''}"},
            check_robots=False,
        )
        return [
            SearchResult(
                title=_inline(item.get("title"), 300),
                url=item["url"],
                snippet=_inline(item.get("content"), 600),
                provider=self.name,
                query=query,
                rank=index + 1,
            )
            for index, item in enumerate((data.get("results") or [])[:limit])
            if item.get("url")
        ]


class SearchObserver(Protocol):
    """Persists results and provider health; also serves recent identical queries."""

    async def cached(self, query: str) -> list[SearchResult] | None: ...

    async def succeeded(
        self, provider: str, query: str, results: list[SearchResult], latency_ms: float
    ) -> None: ...

    async def failed(self, provider: str, query: str, error: str) -> None: ...


def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        parts = urlsplit(result.url)
        key = f"{(parts.hostname or '').lower().removeprefix('www.')}{parts.path.rstrip('/')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


class SearchService:
    def __init__(
        self,
        settings: Settings,
        backends: list[SearchBackend] | None = None,
        observer: SearchObserver | None = None,
    ) -> None:
        self.backends = (
            backends
            if backends is not None
            else [
                SearxngBackend(settings),
                TavilyBackend(settings),
                DuckDuckGoBackend(settings),
                BraveBackend(settings),
            ]
        )
        self.observer = observer

    def available(self, runtime: RuntimeSettings) -> list[SearchBackend]:
        return [
            backend
            for backend in self.backends
            if backend.configured() and runtime.providers.get(backend.name, True)
        ]

    @staticmethod
    def _usable(backend: SearchBackend) -> bool:
        host = getattr(backend, "host", None)
        return not host or SafeFetcher.cooldown_remaining(host) <= 0

    def ready(self, _fetcher: SafeFetcher, runtime: RuntimeSettings, errors: list[str]) -> bool:
        """True when some enabled backend is not cooling down after rate limiting/blocking.

        When none is usable, one explanatory error is recorded and callers stop issuing
        queries, so a blocked provider does not consume the run's query budget."""
        enabled = self.available(runtime)
        if any(self._usable(backend) for backend in enabled):
            return True
        if enabled:
            message = (
                "SOURCE_BLOCKED web search: every enabled backend ("
                + ", ".join(backend.name for backend in enabled)
                + ") is cooling down after rate limiting or blocking; remaining web queries "
                "were skipped."
            )
        else:
            message = "No web search backend is enabled; web queries were skipped."
        if message not in errors:
            errors.append(message)
        return False

    async def search(
        self,
        fetcher: SafeFetcher,
        runtime: RuntimeSettings,
        query: str,
        limit: int,
        errors: list[str],
    ) -> list[SearchResult]:
        if self.observer is not None:
            cached = await self.observer.cached(query)
            if cached:
                return cached[:limit]
        for backend in self.available(runtime):
            if not self._usable(backend):
                continue
            started = time.monotonic()
            try:
                results = _dedupe(await backend.search(fetcher, query, limit))
            except FetchError as exc:
                prefix = "SOURCE_BLOCKED" if exc.blocked else "SOURCE_ERROR"
                errors.append(f"{prefix} {backend.name}: {exc}")
                if self.observer is not None:
                    await self.observer.failed(backend.name, query, str(exc))
                continue
            if self.observer is not None:
                await self.observer.succeeded(
                    backend.name, query, results, (time.monotonic() - started) * 1000
                )
            return results
        return []
