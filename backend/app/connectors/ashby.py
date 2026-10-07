from __future__ import annotations

from app.connectors.base import ScanRequest, ScanState, bounded_id
from app.discovery.http import SafeFetcher
from app.discovery.jsonld import html_to_text, parse_datetime
from app.discovery.names import detect_remote_status
from app.schemas import IngestedJob


class AshbyAdapter:
    platform = "ashby"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        slug = request.identifier["slug"]
        data = await fetcher.get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            params={"includeCompensation": "true"},
        )
        for item in data.get("jobs", []):
            if item.get("isListed") is False:
                continue
            state.listings_seen += 1
            locations = [item.get("location") or ""] + [
                entry.get("location", "") for entry in item.get("secondaryLocations") or []
            ]
            location = "; ".join(dict.fromkeys(part for part in locations if part)) or "Unknown"
            posted_at, precision = parse_datetime(item.get("publishedAt"))
            compensation = item.get("compensation") or {}
            remote = (
                "remote"
                if item.get("isRemote")
                else detect_remote_status(location, item.get("workplaceType"))
            )
            state.jobs.append(
                IngestedJob(
                    company=request.company_name,
                    title=item["title"],
                    location=location[:300],
                    remote_status=remote,
                    employment_type=item.get("employmentType"),
                    salary=compensation.get("compensationTierSummary"),
                    application_url=item.get("applyUrl") or item["jobUrl"],
                    source_url=item["jobUrl"],
                    source="ashby",
                    ats="ashby",
                    external_id=bounded_id(str(item["id"])),
                    posted_at=posted_at,
                    posted_at_precision=precision,
                    description=item.get("descriptionPlain")
                    or html_to_text(item.get("descriptionHtml")),
                    raw_payload={
                        key: value
                        for key, value in item.items()
                        if key not in {"descriptionHtml", "descriptionPlain"}
                    },
                )
            )
        state.complete = True


class RecruiteeAdapter:
    platform = "recruitee"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        slug = request.identifier["slug"]
        data = await fetcher.get_json(f"https://{slug}.recruitee.com/api/offers/")
        for item in data.get("offers", []):
            if item.get("status") not in (None, "published"):
                continue
            state.listings_seen += 1
            location = (
                item.get("location")
                or ", ".join(part for part in (item.get("city"), item.get("country")) if part)
                or "Unknown"
            )
            posted_at, precision = parse_datetime(
                item.get("published_at") or item.get("created_at")
            )
            url = item.get("careers_url") or f"https://{slug}.recruitee.com/o/{item.get('slug')}"
            description = "\n".join(
                html_to_text(item.get(key))
                for key in ("description", "requirements")
                if item.get(key)
            )
            state.jobs.append(
                IngestedJob(
                    company=request.company_name,
                    title=item["title"],
                    location=location[:300],
                    remote_status="remote"
                    if item.get("remote")
                    else detect_remote_status(location),
                    employment_type=item.get("employment_type_code"),
                    application_url=item.get("careers_apply_url") or url,
                    source_url=url,
                    source="recruitee",
                    ats="recruitee",
                    external_id=bounded_id(f"{slug}:{item['id']}"),
                    posted_at=posted_at,
                    posted_at_precision=precision,
                    description=description,
                    raw_payload={
                        key: value
                        for key, value in item.items()
                        if key not in {"description", "requirements"}
                    },
                )
            )
        state.complete = True
