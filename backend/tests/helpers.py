"""Offline test doubles: a routed mock network, a fake search backend, and helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.discovery.http import SafeFetcher
from app.discovery.providers import default_providers
from app.discovery.search import SearchResult, SearchService
from app.services.exploration import Explorer, RunContext

PUBLIC_IP = "93.184.216.34"


def fast_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "discovery_host_min_interval_seconds": 0,
        "discovery_search_min_interval_seconds": 0,
        "discovery_max_retries": 1,
        "discovery_rate_limit_cooldown_seconds": 0,
        "request_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


async def public_resolver(_host: str, _port: int) -> list[str]:
    return [PUBLIC_IP]


async def no_sleep(_seconds: float) -> None:
    return None


Handler = Callable[[httpx.Request], httpx.Response | None]


class MockNetwork:
    """Routes requests by host and path prefix; records every request."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Handler]] = []
        self.requests: list[httpx.Request] = []

    def add(self, host: str, path_prefix: str, handler: Handler | dict | list | str | int) -> None:
        if not callable(handler):
            payload = handler
            handler = lambda _request, payload=payload: respond(payload)  # noqa: E731
        self.routes.append((host, path_prefix, handler))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            for host, prefix, handler in self.routes:
                if host == request.url.host and prefix == "/robots.txt":
                    return handler(request) or httpx.Response(404)
            return httpx.Response(404)
        for host, prefix, handler in self.routes:
            if host == request.url.host and request.url.path.startswith(prefix):
                response = handler(request)
                if response is not None:
                    return response
        return httpx.Response(404, text="not found")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def hits(self, host: str, path_prefix: str = "") -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if request.url.host == host
            and request.url.path.startswith(path_prefix)
            and request.url.path != "/robots.txt"
        ]


def respond(
    payload: Any, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    if isinstance(payload, int):
        return httpx.Response(payload)
    if isinstance(payload, str):
        return httpx.Response(
            status, text=payload, headers=headers or {"content-type": "text/html"}
        )
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers=headers or {"content-type": "application/json"},
    )


class FakeSearch:
    """A search backend answering from a query-substring table."""

    name = "fakesearch"

    def __init__(self, table: dict[str, list[tuple[str, str, str]]] | None = None) -> None:
        self.table = table or {}
        self.queries: list[str] = []

    def configured(self) -> bool:
        return True

    async def search(self, fetcher: SafeFetcher, query: str, limit: int) -> list[SearchResult]:
        self.queries.append(query)
        for needle, rows in self.table.items():
            if needle.lower() in query.lower():
                return [
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        provider=self.name,
                        query=query,
                        rank=index + 1,
                    )
                    for index, (title, url, snippet) in enumerate(rows[:limit])
                ]
        return []


def make_explorer(
    settings: Settings, network: MockNetwork, search: FakeSearch, session_factory
) -> Explorer:
    service = SearchService(settings, backends=[search])

    def fetcher_factory(ctx: RunContext) -> SafeFetcher:
        return SafeFetcher(
            settings,
            transport=network.transport(),
            resolver=public_resolver,
            on_request=ctx.page_hook,
            sleep=no_sleep,
        )

    return Explorer(
        settings,
        session_factory,
        fetcher_factory=fetcher_factory,
        search=service,
        providers=default_providers(service),
    )


def iso_hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def empty_structured_sources(network: MockNetwork) -> None:
    network.add("hn.algolia.com", "/api/v1/search_by_date", {"hits": []})
    network.add("remotive.com", "/api/remote-jobs", {"jobs": []})
    network.add("yc-oss.github.io", "/api/companies/hiring.json", [])
