"""Keyword-scoped adapters for portals that expose public search endpoints.

These portals are searched for target roles only, so their scans are never complete and
cannot close unseen jobs; reconciliation verifies those jobs individually instead.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.connectors.base import ScanRequest, ScanState, bounded_id
from app.discovery.http import FetchError, SafeFetcher
from app.discovery.jsonld import age_bounds_hours, html_to_text, parse_datetime, relative_days_ago
from app.discovery.names import detect_remote_status
from app.discovery.roles import classify_role
from app.schemas import IngestedJob


def _wanted(title: str, request: ScanRequest) -> bool:
    return not request.relevant_only or classify_role(title).is_target


def _too_old(posted_at: datetime | None, precision: str | None, request: ScanRequest) -> bool:
    bounds = age_bounds_hours(posted_at, precision, datetime.now(UTC))
    return bool(bounds and bounds[0] > request.max_age_hours)


def _keywords(request: ScanRequest) -> list[str]:
    cleaned = [re.sub(r"[\"';,]", " ", keyword).strip() for keyword in request.keywords]
    return [keyword for keyword in dict.fromkeys(cleaned) if keyword] or ["data engineer"]


def _search_terms(request: ScanRequest) -> list[str]:
    """Location-qualified queries first ("data engineer India"), then the plain keywords, so
    roles in the candidate's preferred locations are not crowded out by global results."""
    keywords = _keywords(request)
    located = [f"{keyword} {term}" for term in request.location_terms[:2] for keyword in keywords]
    return list(dict.fromkeys(located + keywords))


class WorkdayAdapter:
    platform = "workday"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        host, tenant, site = (request.identifier[key] for key in ("host", "tenant", "site"))
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        seen: set[str] = set()
        details = 0
        today = datetime.now(UTC).date()
        for keyword in _search_terms(request):
            offset, total = 0, None
            for _page in range(request.max_listing_pages):
                data = await fetcher.post_json(
                    f"{base}/jobs",
                    {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": keyword},
                )
                postings = data.get("jobPostings") or []
                # Workday reports the total on the first page only.
                total = data.get("total") or total or 0
                for posting in postings:
                    path = posting.get("externalPath")
                    title = posting.get("title") or ""
                    if not path or path in seen:
                        continue
                    seen.add(path)
                    state.listings_seen += 1
                    listed_day = relative_days_ago(posting.get("postedOn"), today)
                    if "30+" in (posting.get("postedOn") or "") and request.max_age_hours < 720:
                        continue
                    if listed_day and _too_old(
                        datetime(listed_day.year, listed_day.month, listed_day.day, tzinfo=UTC),
                        "date",
                        request,
                    ):
                        continue
                    if not _wanted(title, request) or details >= request.max_detail_pages:
                        continue
                    details += 1
                    try:
                        detail = await fetcher.get_json(f"{base}{path}")
                    except FetchError as exc:
                        if exc.blocked:
                            raise
                        state.errors.append(f"Workday detail {path}: {exc}")
                        continue
                    job = self._job(
                        detail.get("jobPostingInfo") or {}, posting, request, host, site, listed_day
                    )
                    if job:
                        state.jobs.append(job)
                offset += len(postings)
                if not postings or offset >= total:
                    break
        state.complete = False

    @staticmethod
    def _job(info, posting, request, host, site, listed_day) -> IngestedJob | None:
        title = info.get("title") or posting.get("title")
        if not title:
            return None
        posted_at, precision = parse_datetime(info.get("startDate"))
        if posted_at is None and listed_day is not None:
            posted_at, precision = parse_datetime(listed_day)
        location = info.get("location") or posting.get("locationsText") or "Unknown"
        extra = info.get("additionalLocations") or []
        if extra:
            location = "; ".join([location, *extra])
        path = posting.get("externalPath")
        url = info.get("externalUrl") or f"https://{host}/{site}{path}"
        return IngestedJob(
            company=request.company_name,
            title=title,
            location=location[:300],
            remote_status=detect_remote_status(location, info.get("remoteType")),
            employment_type=info.get("timeType"),
            application_url=url,
            source_url=url,
            source="workday",
            ats="workday",
            external_id=bounded_id(f"{host}:{info.get('jobReqId') or path}"),
            posted_at=posted_at,
            posted_at_precision=precision,
            description=html_to_text(info.get("jobDescription")),
            raw_payload={
                "externalPath": path,
                "postedOn": posting.get("postedOn"),
                "startDate": info.get("startDate"),
                "jobReqId": info.get("jobReqId"),
                "timeType": info.get("timeType"),
                "country": (info.get("country") or {}).get("descriptor"),
            },
        )


class OracleRecruitingAdapter:
    platform = "oracle"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        host, site = request.identifier["host"], request.identifier["site"]
        lang = request.identifier.get("lang", "en")
        base = f"https://{host}/hcmRestApi/resources/latest"
        seen: set[str] = set()
        details = 0
        for keyword in _search_terms(request):
            offset = 0
            for _page in range(request.max_listing_pages):
                finder = (
                    f"findReqs;siteNumber={site},limit=25,offset={offset},"
                    f'keyword="{keyword}",sortBy=POSTING_DATES_DESC'
                )
                data = await fetcher.get_json(
                    f"{base}/recruitingCEJobRequisitions",
                    params={
                        "onlyData": "true",
                        "expand": "requisitionList.secondaryLocations",
                        "finder": finder,
                    },
                )
                item = (data.get("items") or [{}])[0]
                requisitions = item.get("requisitionList") or []
                total = item.get("TotalJobsCount") or 0
                older_than_window = False
                for requisition in requisitions:
                    requisition_id = str(requisition.get("Id") or "")
                    if not requisition_id or requisition_id in seen:
                        continue
                    seen.add(requisition_id)
                    state.listings_seen += 1
                    listed, listed_precision = parse_datetime(requisition.get("PostedDate"))
                    if _too_old(listed, listed_precision, request):
                        older_than_window = True
                        break
                    title = requisition.get("Title") or ""
                    if not _wanted(title, request) or details >= request.max_detail_pages:
                        continue
                    details += 1
                    try:
                        detail = await fetcher.get_json(
                            f"{base}/recruitingCEJobRequisitionDetails",
                            params={
                                "expand": "all",
                                "onlyData": "true",
                                "finder": f'ById;Id="{requisition_id}",siteNumber={site}',
                            },
                        )
                    except FetchError as exc:
                        if exc.blocked:
                            raise
                        state.errors.append(f"Oracle requisition {requisition_id}: {exc}")
                        continue
                    info = (detail.get("items") or [{}])[0]
                    posted_at, precision = parse_datetime(info.get("ExternalPostedStartDate"))
                    if posted_at is None:
                        posted_at, precision = listed, listed_precision
                    location = (
                        info.get("PrimaryLocation")
                        or requisition.get("PrimaryLocation")
                        or "Unknown"
                    )
                    url = f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{requisition_id}"
                    description = "\n".join(
                        html_to_text(info.get(key))
                        for key in (
                            "ExternalDescriptionStr",
                            "ExternalResponsibilitiesStr",
                            "ExternalQualificationsStr",
                        )
                        if info.get(key)
                    ) or html_to_text(requisition.get("ShortDescriptionStr"))
                    state.jobs.append(
                        IngestedJob(
                            company=request.company_name,
                            title=info.get("Title") or title,
                            location=location[:300],
                            remote_status=detect_remote_status(
                                location,
                                info.get("WorkplaceType") or requisition.get("WorkplaceType"),
                            ),
                            employment_type=info.get("JobSchedule") or info.get("WorkerType"),
                            application_url=url,
                            source_url=url,
                            source="oracle",
                            ats="oracle",
                            external_id=bounded_id(f"{host}:{requisition_id}"),
                            posted_at=posted_at,
                            posted_at_precision=precision,
                            description=description,
                            raw_payload={
                                "Id": requisition_id,
                                "PostedDate": requisition.get("PostedDate"),
                                "ExternalPostedStartDate": info.get("ExternalPostedStartDate"),
                                "ExternalPostedEndDate": info.get("ExternalPostedEndDate"),
                                "LegalEmployer": info.get("LegalEmployer"),
                            },
                        )
                    )
                offset += len(requisitions)
                if older_than_window or not requisitions or offset >= total:
                    break
        state.complete = False


class AmazonJobsAdapter:
    platform = "amazon_jobs"

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        seen: set[str] = set()
        # Country-filtered searches (the candidate's preferred countries) come first.
        scopes: list[dict[str, str]] = [
            {"normalized_country_code[]": code} for code in request.countries[:3]
        ] + [{}]
        for scope in scopes:
            for keyword in _keywords(request):
                await self._search(fetcher, request, state, seen, keyword, scope)
        state.complete = False

    async def _search(
        self,
        fetcher: SafeFetcher,
        request: ScanRequest,
        state: ScanState,
        seen: set[str],
        keyword: str,
        scope: dict[str, str],
    ) -> None:
        offset = 0
        for _page in range(request.max_listing_pages):
            data = await fetcher.get_json(
                "https://www.amazon.jobs/en/search.json",
                params={
                    "base_query": keyword,
                    "result_limit": 50,
                    "offset": offset,
                    "sort": "recent",
                    **scope,
                },
            )
            jobs = data.get("jobs") or []
            older_than_window = False
            for item in jobs:
                job_id = str(item.get("id_icims") or item.get("id") or "")
                if not job_id or job_id in seen:
                    continue
                seen.add(job_id)
                state.listings_seen += 1
                posted_at, precision = parse_datetime(item.get("posted_date"))
                if _too_old(posted_at, precision, request):
                    older_than_window = True
                    break
                title = item.get("title") or ""
                # Section headings are kept so qualifications can be told apart later.
                description = "\n".join(
                    f"{heading}\n{html_to_text(item.get(key))}"
                    if heading
                    else html_to_text(item.get(key))
                    for key, heading in (
                        ("description", ""),
                        ("basic_qualifications", "Basic qualifications"),
                        ("preferred_qualifications", "Preferred qualifications"),
                    )
                    if item.get(key)
                )
                if (
                    request.relevant_only
                    and not classify_role(title, description, request.company_name).is_target
                ):
                    continue
                location = item.get("normalized_location") or item.get("location") or "Unknown"
                url = f"https://www.amazon.jobs{item.get('job_path') or f'/en/jobs/{job_id}'}"
                state.jobs.append(
                    IngestedJob(
                        company=request.company_name,
                        title=title,
                        location=location[:300],
                        remote_status=detect_remote_status(location),
                        employment_type=item.get("job_schedule_type"),
                        application_url=item.get("url_next_step") or url,
                        source_url=url,
                        source="amazon_jobs",
                        ats="amazon_jobs",
                        external_id=bounded_id(job_id),
                        posted_at=posted_at,
                        posted_at_precision=precision,
                        description=description,
                        raw_payload={
                            "id_icims": job_id,
                            "posted_date": item.get("posted_date"),
                            "updated_time": item.get("updated_time"),
                            "company_name": item.get("company_name"),
                            "country_code": item.get("country_code"),
                        },
                    )
                )
            offset += len(jobs)
            if older_than_window or not jobs or offset >= (data.get("hits") or 0):
                break
