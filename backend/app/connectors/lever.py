from __future__ import annotations

from app.config import Settings, get_settings
from app.connectors.base import JobConnector, ScanRequest, ScanState, bounded_id, run_adapter
from app.discovery.http import SafeFetcher
from app.discovery.jsonld import html_to_text, parse_datetime
from app.discovery.names import detect_remote_status
from app.schemas import IngestedJob


def lever_job(item: dict, company: str) -> IngestedJob:
    categories = item.get("categories") or {}
    location = (
        categories.get("location") or ", ".join(categories.get("allLocations") or []) or "Unknown"
    )
    lists = " ".join(
        f"{entry.get('text', '')}\n{html_to_text(entry.get('content'))}"
        for entry in item.get("lists", [])
    )
    description = "\n".join(
        part
        for part in (
            item.get("descriptionPlain") or html_to_text(item.get("description")),
            lists,
            item.get("additionalPlain") or html_to_text(item.get("additional")),
        )
        if part
    )
    # Lever exposes the posting creation time (milliseconds since epoch).
    posted_at, precision = parse_datetime(item.get("createdAt"))
    salary = item.get("salaryRange") or {}
    salary_text = (
        f"{salary.get('currency', '')} {salary.get('min')}-{salary.get('max')} "
        f"{salary.get('interval', '')}".strip()
        if salary.get("min") and salary.get("max")
        else None
    )
    return IngestedJob(
        company=company,
        title=item["text"],
        location=location,
        remote_status=detect_remote_status(location, item.get("workplaceType")),
        employment_type=categories.get("commitment"),
        salary=salary_text,
        application_url=item.get("applyUrl") or item["hostedUrl"],
        source_url=item["hostedUrl"],
        source="lever",
        ats="lever",
        external_id=bounded_id(str(item["id"])),
        posted_at=posted_at,
        posted_at_precision=precision,
        description=description,
        raw_payload={
            key: value
            for key, value in item.items()
            if key
            not in {"description", "descriptionPlain", "lists", "additional", "additionalPlain"}
        },
    )


class LeverAdapter:
    platform = "lever"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        slug = request.identifier["slug"]
        host = "api.eu.lever.co" if request.identifier.get("region") == "eu" else "api.lever.co"
        items = await fetcher.get_json(
            f"https://{host}/v0/postings/{slug}", params={"mode": "json"}
        )
        if not isinstance(items, list):
            state.status = "ERROR"
            state.errors.append("Lever returned an unexpected payload.")
            return
        for item in items:
            state.listings_seen += 1
            state.jobs.append(lever_job(item, request.company_name))
        state.complete = True


class LeverConnector(JobConnector):
    """Phase 1 interface retained for direct board imports."""

    def __init__(self, timeout: float, settings: Settings | None = None) -> None:
        self.settings = (settings or get_settings()).model_copy(
            update={"request_timeout_seconds": timeout}
        )

    async def fetch(self, identifier: str) -> list[IngestedJob]:
        request = ScanRequest(
            url=f"https://jobs.lever.co/{identifier}",
            platform="lever",
            identifier={"slug": identifier, "region": "global"},
            company_name=identifier.replace("-", " ").title(),
            relevant_only=False,
        )
        async with SafeFetcher(self.settings) as fetcher:
            result = await run_adapter(LeverAdapter(), fetcher, request)
        if result.status not in {"OK", "PARTIAL"}:
            raise RuntimeError("; ".join(result.errors) or result.status)
        return result.jobs
