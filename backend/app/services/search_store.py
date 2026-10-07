"""Database-backed search result persistence, query cache and provider health."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.discovery.search import SearchResult
from app.models import ProviderHealth, SearchResultRecord


class SearchStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID | None = None,
        cache_ttl_seconds: float = 6 * 3600,
    ) -> None:
        self.session_factory = session_factory
        self.run_id = run_id
        self.cache_ttl_seconds = cache_ttl_seconds

    async def cached(self, query: str) -> list[SearchResult] | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.cache_ttl_seconds)
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(SearchResultRecord)
                    .where(
                        SearchResultRecord.query == query[:500],
                        SearchResultRecord.fetched_at >= cutoff,
                    )
                    .order_by(SearchResultRecord.fetched_at.desc(), SearchResultRecord.rank)
                    .limit(50)
                )
            ).all()
        if not rows:
            return None
        latest = rows[0].fetched_at
        return [
            SearchResult(row.title, row.url, row.snippet, row.provider, query, row.rank)
            for row in rows
            if row.fetched_at == latest
        ]

    async def succeeded(
        self, provider: str, query: str, results: list[SearchResult], latency_ms: float
    ) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            for result in results:
                session.add(
                    SearchResultRecord(
                        provider=provider,
                        query=query[:500],
                        url=result.url,
                        title=result.title,
                        snippet=result.snippet,
                        rank=result.rank,
                        run_id=self.run_id,
                        fetched_at=now,
                    )
                )
            health = await self._health(session, provider)
            health.total_requests += 1
            health.consecutive_failures = 0
            health.last_success_at = now
            health.last_latency_ms = round(latency_ms, 1)
            await session.commit()

    async def failed(self, provider: str, query: str, error: str) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            health = await self._health(session, provider)
            health.total_requests += 1
            health.total_failures += 1
            health.consecutive_failures += 1
            health.last_failure_at = now
            health.last_error = error[:500]
            await session.commit()

    @staticmethod
    async def _health(session: AsyncSession, provider: str) -> ProviderHealth:
        health = await session.get(ProviderHealth, provider)
        if health is None:
            health = ProviderHealth(
                provider=provider, total_requests=0, total_failures=0, consecutive_failures=0
            )
            session.add(health)
        return health
