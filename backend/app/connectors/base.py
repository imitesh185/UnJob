"""Connector contracts.

``JobConnector`` is the original Phase 1 interface (fetch everything for an identifier).
``SourceAdapter`` scans one career source under a page budget and reports whether the
enumeration was complete; only complete scans may be used to close unseen jobs.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol

from app.discovery.ats import PlatformMatch
from app.discovery.http import BudgetExhausted, FetchError, SafeFetcher
from app.schemas import IngestedJob


class JobConnector(ABC):
    @abstractmethod
    async def fetch(self, identifier: str) -> list[IngestedJob]:
        raise NotImplementedError


@dataclass
class ScanRequest:
    url: str
    platform: str
    identifier: dict[str, str]
    company_name: str
    keywords: list[str] = field(default_factory=lambda: ["data engineer"])
    max_listing_pages: int = 5
    max_detail_pages: int = 25
    max_age_hours: float = 168.0
    relevant_only: bool = True
    # From the candidate's location preferences: ISO-3166 alpha-3 codes and search terms.
    countries: list[str] = field(default_factory=list)
    location_terms: list[str] = field(default_factory=list)


@dataclass
class ScanState:
    jobs: list[IngestedJob] = field(default_factory=list)
    complete: bool = False
    listings_seen: int = 0
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)
    company_name: str | None = None
    status: str | None = None
    detail: str | None = None
    redirect: PlatformMatch | None = None


@dataclass
class SourceScanResult:
    jobs: list[IngestedJob]
    complete: bool
    status: str
    listings_seen: int
    errors: list[str]
    company_name: str | None = None
    detail: str | None = None
    redirect: PlatformMatch | None = None
    budget_exhausted: str | None = None


class SourceAdapter(Protocol):
    platform: str

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None: ...


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def bounded_id(value: str) -> str:
    return value if len(value) <= 200 else stable_id(value)


def status_for_error(error: FetchError) -> str:
    if error.blocked:
        return "BLOCKED"
    if error.code == "NOT_FOUND":
        return "NOT_FOUND"
    return "ERROR"


async def run_adapter(
    adapter: SourceAdapter, fetcher: SafeFetcher, request: ScanRequest
) -> SourceScanResult:
    state = ScanState()
    budget: str | None = None
    try:
        await adapter.scan(fetcher, request, state)
        status = state.status or "OK"
    except BudgetExhausted as exc:
        state.complete = False
        budget = exc.budget
        status = "PARTIAL"
    except FetchError as exc:
        state.complete = False
        state.errors.append(str(exc))
        status = status_for_error(exc)
        if state.jobs and status == "ERROR":
            status = "PARTIAL"
    if status == "OK" and state.errors:
        status = "PARTIAL"
        state.complete = False
    return SourceScanResult(
        jobs=state.jobs,
        complete=state.complete and status == "OK",
        status=status,
        listings_seen=state.listings_seen,
        errors=state.errors[:20],
        company_name=state.company_name,
        detail=state.detail,
        redirect=state.redirect,
        budget_exhausted=budget,
    )
