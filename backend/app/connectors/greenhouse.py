from __future__ import annotations

from app.config import Settings, get_settings
from app.connectors.base import JobConnector, ScanRequest, ScanState, bounded_id, run_adapter
from app.discovery.http import SafeFetcher
from app.discovery.jsonld import html_to_text, parse_datetime
from app.discovery.names import detect_remote_status
from app.schemas import IngestedJob

API = "https://boards-api.greenhouse.io/v1/boards"


def greenhouse_job(item: dict, company: str) -> IngestedJob:
    location = (item.get("location") or {}).get("name") or "Unknown"
    # first_published is the posting time; updated_at changes on any edit and is kept separately.
    posted_at, precision = parse_datetime(item.get("first_published"))
    updated_at, _ = parse_datetime(item.get("updated_at"))
    return IngestedJob(
        company=company,
        title=item["title"],
        location=location,
        remote_status=detect_remote_status(location),
        application_url=item["absolute_url"],
        source_url=item["absolute_url"],
        source="greenhouse",
        ats="greenhouse",
        external_id=bounded_id(str(item["id"])),
        posted_at=posted_at,
        posted_at_precision=precision,
        source_updated_at=updated_at,
        description=html_to_text(item.get("content")),
        raw_payload={key: value for key, value in item.items() if key != "content"},
    )


class GreenhouseAdapter:
    platform = "greenhouse"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        token = request.identifier["token"]
        board = await fetcher.get_json(f"{API}/{token}")
        state.company_name = (board or {}).get("name") or None
        data = await fetcher.get_json(f"{API}/{token}/jobs", params={"content": "true"})
        company = state.company_name or request.company_name
        for item in data.get("jobs", []):
            state.listings_seen += 1
            state.jobs.append(greenhouse_job(item, item.get("company_name") or company))
        state.complete = True


class GreenhouseConnector(JobConnector):
    """Phase 1 interface retained for direct board imports."""

    def __init__(self, timeout: float, settings: Settings | None = None) -> None:
        self.settings = (settings or get_settings()).model_copy(
            update={"request_timeout_seconds": timeout}
        )

    async def fetch(self, identifier: str) -> list[IngestedJob]:
        request = ScanRequest(
            url=f"https://boards.greenhouse.io/{identifier}",
            platform="greenhouse",
            identifier={"token": identifier},
            company_name=identifier.replace("-", " ").title(),
            relevant_only=False,
        )
        async with SafeFetcher(self.settings) as fetcher:
            result = await run_adapter(GreenhouseAdapter(), fetcher, request)
        if result.status not in {"OK", "PARTIAL"}:
            raise RuntimeError("; ".join(result.errors) or result.status)
        return result.jobs
