"""Career-portal platform detection from URLs and page markup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

API_PLATFORMS = {"greenhouse", "lever", "ashby", "workday", "oracle", "recruitee", "amazon_jobs"}
LISTING_PLATFORMS = {
    "smartrecruiters",
    "icims",
    "jobvite",
    "teamtailor",
    "schema_org",
    "generic_html",
}
SUPPORTED_PLATFORMS = API_PLATFORMS | LISTING_PLATFORMS
ALL_PLATFORMS = sorted(SUPPORTED_PLATFORMS | {"oracle_taleo", "unknown"})


@dataclass(frozen=True)
class PlatformMatch:
    platform: str
    confidence: float
    canonical_url: str
    identifier: dict[str, str] = field(default_factory=dict)

    @property
    def scan_supported(self) -> bool:
        return self.platform in SUPPORTED_PLATFORMS


_LOCALE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.I)
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.%-]{0,99}$")
_WORKDAY_HOST = re.compile(
    r"^(?P<tenant>[a-z0-9-]+)\.(?P<dc>wd\d+)\.myworkday(?P<kind>jobs|site)\.com$", re.I
)
_ORACLE_PATH = re.compile(
    r"/hcmUI/CandidateExperience/(?P<lang>[a-z]{2}(?:-[A-Z]{2})?)/sites/(?P<site>[A-Za-z0-9_-]+)",
    re.I,
)


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def detect_from_url(url: str) -> PlatformMatch | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    segments = _segments(parts.path)
    query = parse_qs(parts.query)

    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    }:
        token = (
            query.get("for", [None])[0]
            if segments[:1] == ["embed"]
            else (segments[0] if segments else None)
        )
        if token and _SLUG.match(token):
            return PlatformMatch(
                "greenhouse", 0.98, f"https://boards.greenhouse.io/{token}", {"token": token}
            )
    if (
        host == "boards-api.greenhouse.io"
        and len(segments) >= 3
        and segments[:2] == ["v1", "boards"]
    ):
        token = segments[2]
        return PlatformMatch(
            "greenhouse", 0.98, f"https://boards.greenhouse.io/{token}", {"token": token}
        )
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and segments and _SLUG.match(segments[0]):
        region = "eu" if ".eu." in host else "global"
        return PlatformMatch(
            "lever", 0.98, f"https://{host}/{segments[0]}", {"slug": segments[0], "region": region}
        )
    if (
        host in {"api.lever.co", "api.eu.lever.co"}
        and len(segments) >= 3
        and segments[:2] == ["v0", "postings"]
    ):
        region = "eu" if ".eu." in host else "global"
        board_host = "jobs.eu.lever.co" if region == "eu" else "jobs.lever.co"
        return PlatformMatch(
            "lever",
            0.98,
            f"https://{board_host}/{segments[2]}",
            {"slug": segments[2], "region": region},
        )
    if host == "jobs.ashbyhq.com" and segments and _SLUG.match(segments[0]):
        return PlatformMatch(
            "ashby", 0.98, f"https://jobs.ashbyhq.com/{segments[0]}", {"slug": segments[0]}
        )
    if (
        host == "api.ashbyhq.com"
        and len(segments) >= 3
        and segments[:2] == ["posting-api", "job-board"]
    ):
        return PlatformMatch(
            "ashby", 0.98, f"https://jobs.ashbyhq.com/{segments[2]}", {"slug": segments[2]}
        )
    workday = _WORKDAY_HOST.match(host)
    if workday:
        tenant = workday.group("tenant")
        remaining = [segment for segment in segments if not _LOCALE.match(segment)]
        if remaining[:2] == ["wday", "cxs"] and len(remaining) >= 4:
            site = remaining[3]
        elif remaining[:1] == ["recruiting"] and len(remaining) >= 3:
            site = remaining[2]
        else:
            site = remaining[0] if remaining else ""
        if site and site not in {"job", "details", "login"}:
            return PlatformMatch(
                "workday",
                0.97,
                f"https://{host}/{site}",
                {"host": host, "tenant": tenant, "site": site},
            )
    oracle = _ORACLE_PATH.search(parts.path)
    if host.endswith(".oraclecloud.com") and oracle:
        lang, site = oracle.group("lang"), oracle.group("site")
        return PlatformMatch(
            "oracle",
            0.97,
            f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}",
            {"host": host, "site": site, "lang": lang},
        )
    if host.endswith(".taleo.net"):
        return PlatformMatch("oracle_taleo", 0.9, f"https://{host}/", {"host": host})
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and segments:
        company = segments[0]
        if _SLUG.match(company):
            return PlatformMatch(
                "smartrecruiters",
                0.95,
                f"https://jobs.smartrecruiters.com/{company}",
                {"company": company},
            )
    if host.endswith(".icims.com"):
        return PlatformMatch(
            "icims", 0.9, f"https://{host}/jobs/search?ss=1&in_iframe=1", {"host": host}
        )
    if host == "jobs.jobvite.com" and segments and _SLUG.match(segments[0]):
        return PlatformMatch(
            "jobvite",
            0.9,
            f"https://jobs.jobvite.com/{segments[0]}/jobs",
            {"company": segments[0]},
        )
    if host.endswith(".teamtailor.com"):
        return PlatformMatch("teamtailor", 0.9, f"https://{host}/jobs", {"host": host})
    if host.endswith(".recruitee.com") and host.count(".") == 2:
        slug = host.split(".")[0]
        return PlatformMatch("recruitee", 0.95, f"https://{slug}.recruitee.com", {"slug": slug})
    if host in {"www.amazon.jobs", "amazon.jobs"}:
        return PlatformMatch("amazon_jobs", 0.95, "https://www.amazon.jobs/en", {})
    return None


_HTML_MARKERS: list[tuple[str, re.Pattern[str], float]] = [
    (
        "greenhouse",
        re.compile(
            r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?"
            r"(?P<id>[A-Za-z0-9_-]+)",
            re.I,
        ),
        0.85,
    ),
    (
        "greenhouse",
        re.compile(r"boards-api\.greenhouse\.io/v1/boards/(?P<id>[A-Za-z0-9_-]+)", re.I),
        0.85,
    ),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/(?P<id>[A-Za-z0-9_.-]+)", re.I), 0.85),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/(?P<id>[A-Za-z0-9_.%-]+)", re.I), 0.85),
    (
        "workday",
        re.compile(
            r"https?://(?P<id>[a-z0-9-]+\.wd\d+\.myworkday(?:jobs|site)\.com/"
            r"(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)",
            re.I,
        ),
        0.85,
    ),
    (
        "oracle",
        re.compile(
            r"https?://(?P<id>[a-z0-9.-]+\.oraclecloud\.com/hcmUI/CandidateExperience/"
            r"[a-z]{2}(?:-[A-Z]{2})?/sites/[A-Za-z0-9_-]+)",
            re.I,
        ),
        0.85,
    ),
    (
        "smartrecruiters",
        re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/(?P<id>[A-Za-z0-9_-]+)", re.I),
        0.8,
    ),
    ("recruitee", re.compile(r"https?://(?P<id>[a-z0-9-]+)\.recruitee\.com", re.I), 0.8),
    ("icims", re.compile(r"https?://(?P<id>[a-z0-9-]+\.icims\.com)", re.I), 0.75),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/(?P<id>[A-Za-z0-9_-]+)", re.I), 0.75),
    ("teamtailor", re.compile(r"https?://(?P<id>[a-z0-9-]+\.teamtailor\.com)", re.I), 0.75),
]
_IGNORED_SLUGS = {"embed", "js", "v1", "api", "jobs", "job", "search", "static", "assets"}


def detect_from_html(page_url: str, html: str) -> PlatformMatch | None:
    for platform, pattern, confidence in _HTML_MARKERS:
        for match in pattern.finditer(html):
            value = match.group("id").rstrip("/.")
            if value.lower() in _IGNORED_SLUGS:
                continue
            candidate_url = {
                "greenhouse": f"https://boards.greenhouse.io/{value}",
                "lever": f"https://jobs.lever.co/{value}",
                "ashby": f"https://jobs.ashbyhq.com/{value}",
                "workday": f"https://{value}",
                "oracle": f"https://{value}",
                "smartrecruiters": f"https://jobs.smartrecruiters.com/{value}",
                "recruitee": f"https://{value}.recruitee.com",
                "icims": f"https://{value}/jobs",
                "jobvite": f"https://jobs.jobvite.com/{value}",
                "teamtailor": f"https://{value}/jobs",
            }[platform]
            detected = detect_from_url(candidate_url)
            if detected and detected.platform == platform:
                return PlatformMatch(
                    detected.platform, confidence, detected.canonical_url, detected.identifier
                )
    if re.search(r'"@type"\s*:\s*"JobPosting"', html):
        return PlatformMatch("schema_org", 0.7, page_url, {})
    return None


def detect_platform(url: str, html: str | None = None) -> PlatformMatch:
    match = detect_from_url(url)
    if match:
        return match
    if html:
        embedded = detect_from_html(url, html)
        if embedded:
            return embedded
        return PlatformMatch("generic_html", 0.4, url, {})
    return PlatformMatch("unknown", 0.0, url, {})
