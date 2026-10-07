"""Generic career-page scanning: embedded ATS detection, schema.org JobPosting data, and
server-rendered job links. JavaScript-only pages are reported as unsupported."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.connectors.ashby import AshbyAdapter, RecruiteeAdapter
from app.connectors.base import ScanRequest, ScanState, SourceAdapter, bounded_id, stable_id
from app.connectors.greenhouse import GreenhouseAdapter
from app.connectors.lever import LeverAdapter
from app.connectors.search_portals import AmazonJobsAdapter, OracleRecruitingAdapter, WorkdayAdapter
from app.discovery.ats import API_PLATFORMS, detect_platform
from app.discovery.http import FetchError, SafeFetcher
from app.discovery.jsonld import (
    canonical_url,
    extract_links,
    find_job_postings,
    first_heading,
    html_to_text,
    looks_javascript_rendered,
    page_title,
    posting_fields,
)
from app.discovery.names import company_key, detect_remote_status, registrable_domain
from app.discovery.roles import classify_role
from app.schemas import IngestedJob

_JOB_PATH = re.compile(
    r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|requisitions?|"
    r"opportunit(?:y|ies)|o|roles?|postings?|job-details|jobdetails)(?:/|$|\?)",
    re.I,
)
_ROLE_SLUG = re.compile(
    r"data[-_ ]?(?:engineer|platform|infra)|big[-_ ]?data|distributed[-_ ]?systems|"
    r"platform[-_ ]?engineer|infrastructure[-_ ]?engineer|\betl\b",
    re.I,
)
_NOT_JOB = re.compile(
    r"/(?:login|signin|sign-in|privacy|terms|cookie|blog|news|press|about|contact|faq|"
    r"benefits|culture|events|students|alerts|talent-community)(?:/|$)",
    re.I,
)
_NEXT_LABEL = re.compile(r"^\s*(?:next|next page|more jobs|load more|›|»)\s*$", re.I)
HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"}


class GenericCareerAdapter:
    def __init__(self, platform: str = "generic_html") -> None:
        self.platform = platform

    async def scan(self, fetcher: SafeFetcher, request: ScanRequest, state: ScanState) -> None:
        if self.platform == "oracle_taleo":
            state.status = "UNSUPPORTED"
            state.detail = (
                "Oracle Taleo career sections are JavaScript applications without a supported "
                "public listing API."
            )
            return
        page = await fetcher.get(request.url, headers=HTML_ACCEPT)
        html = page.text
        detected = detect_platform(page.url, html)
        if detected.platform in API_PLATFORMS and detected.identifier:
            state.redirect = detected
            state.detail = f"Career page is served by {detected.platform}: {detected.canonical_url}"
            return

        candidates: dict[str, int] = {}
        listing_pages = 1
        current_url, current_html = page.url, html
        while True:
            self._collect_postings(current_html, current_url, request, state)
            next_url = None
            for url, label in extract_links(current_html, current_url):
                if _NEXT_LABEL.match(label or ""):
                    next_url = next_url or url
                    continue
                priority = self._link_priority(url, label, request.url)
                if priority is None:
                    continue
                state.listings_seen += 1
                if priority > 0:
                    candidates[url] = max(priority, candidates.get(url, 0))
            if not next_url or listing_pages >= request.max_listing_pages:
                break
            listing_pages += 1
            next_page = await fetcher.get(next_url, headers=HTML_ACCEPT)
            current_url, current_html = next_page.url, next_page.text

        ordered = sorted(candidates, key=lambda url: -candidates[url])
        for url in ordered[: request.max_detail_pages]:
            try:
                detail = await fetcher.get(url, headers=HTML_ACCEPT)
            except FetchError as exc:
                if exc.blocked:
                    raise
                state.errors.append(f"Job page {url}: {exc}")
                continue
            if not self._collect_postings(detail.text, detail.url, request, state):
                self._fallback_job(detail.text, detail.url, request, state)

        if not state.jobs and not state.listings_seen:
            state.status = "UNSUPPORTED"
            state.detail = (
                "The page appears to be rendered by JavaScript; no server-rendered job listings "
                "or structured job data were found."
                if looks_javascript_rendered(html)
                else "No job listings or schema.org JobPosting data were found on this page."
            )
        state.complete = False

    @staticmethod
    def _link_priority(url: str, label: str, listing_url: str) -> int | None:
        if canonical_url(url) == canonical_url(listing_url):
            return None
        listing_domain = registrable_domain(listing_url)
        domain = registrable_domain(url)
        if domain != listing_domain and detect_platform(url).platform in {
            "unknown",
            "generic_html",
        }:
            return None
        path = urlsplit(url).path + "?" + urlsplit(url).query
        if _NOT_JOB.search(path):
            return None
        job_like = bool(_JOB_PATH.search(path)) or bool(_ROLE_SLUG.search(path))
        if not job_like:
            return None
        if label and classify_role(label).is_target:
            return 2
        if _ROLE_SLUG.search(path):
            return 1
        return 0

    def _collect_postings(
        self, page_html: str, page_url: str, request: ScanRequest, state: ScanState
    ) -> bool:
        postings = find_job_postings(page_html)
        for posting in postings:
            fields = posting_fields(posting, page_url)
            if not fields["title"]:
                continue
            state.listings_seen += 1
            organization = fields["company_name"]
            if organization and company_key(organization) == company_key(request.company_name):
                state.company_name = organization
            if (
                request.relevant_only
                and not classify_role(
                    fields["title"], fields["description"], request.company_name
                ).is_target
            ):
                continue
            host = urlsplit(fields["url"]).hostname or ""
            external = (
                f"{host}:{fields['identifier']}"
                if fields["identifier"]
                else stable_id(fields["url"])
            )
            state.jobs.append(
                IngestedJob(
                    company=request.company_name,
                    title=fields["title"][:300],
                    location=(fields["location"] or "Unknown")[:300],
                    remote_status="remote"
                    if fields["remote"]
                    else detect_remote_status(fields["location"]),
                    salary=fields["salary"],
                    employment_type=fields["employment_type"],
                    application_url=fields["url"],
                    source_url=page_url,
                    source=self.platform,
                    ats=self.platform,
                    external_id=bounded_id(external),
                    posted_at=fields["posted_at"],
                    posted_at_precision=fields["precision"],
                    description=fields["description"],
                    raw_payload={
                        "datePosted": posting.get("datePosted"),
                        "validThrough": posting.get("validThrough"),
                        "hiringOrganization": organization,
                        "structured_data": True,
                    },
                )
            )
        return bool(postings)

    def _fallback_job(
        self, page_html: str, page_url: str, request: ScanRequest, state: ScanState
    ) -> None:
        title = first_heading(page_html) or page_title(page_html)
        description = html_to_text(page_html, 8000)
        if not title or not classify_role(title, description, request.company_name).is_target:
            return
        state.jobs.append(
            IngestedJob(
                company=request.company_name,
                title=title[:300],
                remote_status=detect_remote_status(title),
                application_url=page_url,
                source_url=page_url,
                source=self.platform,
                ats=self.platform,
                external_id=stable_id(canonical_url(page_url)),
                description=description,
                raw_payload={"structured_data": False, "note": "Posting date not published."},
            )
        )


ADAPTERS: dict[str, SourceAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
    "recruitee": RecruiteeAdapter(),
    "workday": WorkdayAdapter(),
    "oracle": OracleRecruitingAdapter(),
    "amazon_jobs": AmazonJobsAdapter(),
    "oracle_taleo": GenericCareerAdapter("oracle_taleo"),
    **{
        name: GenericCareerAdapter(name)
        for name in (
            "smartrecruiters",
            "icims",
            "jobvite",
            "teamtailor",
            "schema_org",
            "generic_html",
        )
    },
}


def adapter_for(platform: str) -> SourceAdapter:
    return ADAPTERS.get(platform) or ADAPTERS["generic_html"]
