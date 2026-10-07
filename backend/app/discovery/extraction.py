"""Convert observed search results into attributable company candidates.

A candidate is produced only when the result itself names an employer together with a
target role (or, for similar-company discovery, an explicit competitor list). Every
candidate keeps the exact result title/snippet and URL as evidence.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from app.discovery.ats import detect_from_url
from app.discovery.names import (
    clean_display_name,
    company_key,
    hostname,
    is_aggregator,
    is_location_text,
    is_platform_host,
    is_plausible_company_name,
    name_matches_domain,
    normalize_company_name,
    registrable_domain,
)
from app.discovery.roles import TARGET_CATEGORIES, classify_role, find_role_phrase
from app.discovery.search import SearchResult

HIRING_REASONS = {
    "JOB_SEARCH",
    "LINKEDIN_HIRING_SIGNAL",
    "X_HIRING_SIGNAL",
    "SEARCH_ENGINE",
    "CAREER_PAGE",
}
DISCOVERY_REASONS = HIRING_REASONS | {"SIMILAR_COMPANY", "STARTUP_DISCOVERY", "MANUAL_SEED"}


@dataclass
class CandidateSignal:
    company_name: str
    reason: str
    source: str
    source_url: str | None
    evidence: str
    confidence: float
    role_title: str | None = None
    role_relevance: float = 0.0
    location: str | None = None
    signal_date: datetime | None = None
    signal_date_precision: str | None = None
    domain: str | None = None
    career_url: str | None = None
    job_url: str | None = None
    query: str | None = None
    related_company: str | None = None
    relationship_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_hiring_evidence(self) -> bool:
        return self.reason in HIRING_REASONS and self.role_relevance >= 0.5


_SPLIT = re.compile(r"\s+[|\-\u2013\u2014\u00b7\u2022:]\s+|\s*\|\s*")
_PREFIX = re.compile(
    r"^(?:careers?\s+at|jobs?\s+at|working\s+at|work\s+at|join|life\s+at|"
    r"job\s+openings?\s+at|open\s+positions?\s+at)\s+",
    re.I,
)
_SUFFIX = re.compile(
    r"(?:\s+|^)(?:careers?(?:\s+site|\s+page)?|jobs?(?:\s+board)?|hiring|recruitment|talent)$",
    re.I,
)
_ROLE_VOCABULARY = {
    "data",
    "platform",
    "infrastructure",
    "distributed",
    "systems",
    "system",
    "engineering",
    "engineer",
    "software",
    "backend",
    "senior",
    "staff",
    "lead",
    "principal",
    "analytics",
    "cloud",
    "devops",
    "big",
    "team",
    "remote",
    "hybrid",
    "onsite",
    "full",
    "time",
    "ii",
    "iii",
    "sr",
    "jr",
    "junior",
    "associate",
    "manager",
    "science",
    "scientist",
    "etl",
}
# Sentence fragments ("What does a ...", "... jobs for the") are never company names.
_LEADING_STOPWORDS = {
    "what", "how", "why", "who", "when", "where", "which", "does", "do", "is", "are", "top",
    "best", "latest", "find", "search", "apply", "get", "hire", "become", "your", "my", "our",
    "all", "browse", "view", "explore",
}  # fmt: skip
_TRAILING_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "at", "in", "and", "or", "with", "does", "do", "is",
    "are", "as", "by", "on",
}  # fmt: skip
# Anonymous descriptors ("Early-stage Startup", "Our Client") are not employer names.
_DESCRIPTOR_TAILS = {
    "startup", "startups", "companies", "firm", "client", "organization", "organisation",
    "agency", "employer", "unicorn", "mnc",
}  # fmt: skip
_HIRING_URL = re.compile(
    r"/(?:jobs?|careers?|positions?|openings?|vacanc|requisition|opportunit|o/|apply|"
    r"job-details|jobdetails|hiring)|gh_jid=|jobid=|job_id=",
    re.I,
)
_HIRING_TEXT = re.compile(
    r"\b(?:hiring|jobs?|careers?|apply|openings?|vacanc\w*|positions?|join our|"
    r"we're looking|we are looking|recruit\w*)\b",
    re.I,
)
_JOB_PAGE_URL = re.compile(
    r"/(?:job|jobs|o|positions?|requisitions?|job-details|jobdetails)/[^/?#]+|gh_jid=|jobid=|job_id="
    r"|/job-?details\b",
    re.I,
)
_EXPLICIT_TITLES = [
    re.compile(r"^job application for (?P<role>.+?) at (?P<company>.+?)$", re.I),
    re.compile(
        r"^(?P<company>.+?) hiring (?P<role>.+?) in (?P<location>.+?)"
        r"(?:\s*[|\-\u2013\u2014]\s*linkedin.*)?$",
        re.I,
    ),
    re.compile(
        r"^(?P<company>.+?) is hiring (?:an? )?(?P<role>.+?)(?:\s*[|\-\u2013\u2014].*)?$", re.I
    ),
    re.compile(
        r"^(?P<role>[^|\-\u2013\u2014@]+?) (?:job )?(?:at|@) (?P<company>[^|\-\u2013\u2014,]+?)"
        r"(?:,? in [^|]+)?(?:\s*[|\-\u2013\u2014].*)?$",
        re.I,
    ),
]
_LINKEDIN_LISTING = re.compile(
    r"^\s*[\d,]+\+?\s+(?P<rest>.+?)\s+jobs?\s+in\s+(?P<location>.+?)"
    r"\s*(?:[-|\u2013\u2014]\s*linkedin.*)?$",
    re.I,
)
_X_COMPANY = [
    re.compile(
        r"\b(?i:we(?:'re| are)|is)\s+(?i:hiring)\b[^.!?]{0,80}?\b(?i:at|@)\s+@?"
        r"(?P<company>[A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
    ),
    re.compile(r"\b(?P<company>[A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+is\s+(?i:hiring)\b"),
    re.compile(
        r"\b(?i:join\s+(?:us|our\s+\w+(?:\s+\w+)?\s+team)\s+at)\s+@?"
        r"(?P<company>[A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
    ),
]
_SIMILAR_PATTERNS = [
    (
        re.compile(
            r"(?:top\s+)?(?:competitors|alternatives|rivals|similar\s+companies|peers)\s+"
            r"(?:of|to|for)\s+[^:.]{0,60}?\s*(?:include|includes|are|:)\s+(?P<list>[^.]{5,300})",
            re.I,
        ),
        "competes_with",
    ),
    (
        re.compile(
            r"(?:competitors|alternatives|rivals|similar\s+companies|peers)\s+"
            r"(?:include|includes|are|:)\s+(?P<list>[^.]{5,300})",
            re.I,
        ),
        "competes_with",
    ),
    (re.compile(r"\bcompet(?:es|ing)\s+with\s+(?P<list>[^.]{5,300})", re.I), "competes_with"),
    (
        re.compile(
            r"\bcompanies\s+(?:similar\s+to|like)\s+[^,.]{2,60}?\s+(?:include|are|such as)\s+"
            r"(?P<list>[^.]{5,300})",
            re.I,
        ),
        "similar_to",
    ),
]


_HOST_LIKE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+){2,}$", re.I)


def clean_company_candidate(text: str | None) -> str | None:
    value = html_lib.unescape(text or "").strip(" \"'\u201c\u201d\u2018\u2019")
    value = re.sub(r"https?://\S+|\bwww\.\S+", " ", value).strip()
    # Result titles sometimes show a hostname ("acme.wd1.myworkdayjobs.com") instead of a name.
    if _HOST_LIKE.match(value) or (
        "." in value and " " not in value and (is_platform_host(value) or is_aggregator(value))
    ):
        return None
    value = _PREFIX.sub("", value)
    for _ in range(2):
        value = _SUFFIX.sub("", value).strip(" ,.-|:")
    value = re.sub(r"\s*\((?![^)]*\bYC\b)[^)]*\)\s*$", "", value).strip()
    if not value or not is_plausible_company_name(value) or is_location_text(value):
        return None
    words = set(re.sub(r"[^a-z0-9 ]+", " ", value.lower()).split())
    if words and words <= _ROLE_VOCABULARY:
        return None
    ordered = normalize_company_name(value).split()
    if ordered and (
        ordered[0] in _LEADING_STOPWORDS
        or ordered[-1] in _TRAILING_STOPWORDS
        or ordered[-1] in _DESCRIPTOR_TAILS
    ):
        return None
    if find_role_phrase(value) or classify_role(value).category in TARGET_CATEGORIES:
        return None
    return clean_display_name(value)


def parse_title(title: str) -> tuple[str | None, str | None, str | None]:
    """Return (company, role, location) observed in a result title."""
    text = re.sub(r"\s+", " ", html_lib.unescape(title or "")).strip()
    for pattern in _EXPLICIT_TITLES:
        match = pattern.match(text)
        if not match:
            continue
        role = match.group("role").strip()
        company = clean_company_candidate(match.group("company"))
        if company and (find_role_phrase(role) or classify_role(role).is_target):
            return company, role, match.groupdict().get("location")
    segments = [segment.strip() for segment in _SPLIT.split(text) if segment and segment.strip()]
    if len(segments) == 1 and "," in text:
        segments = [segment.strip() for segment in text.split(",") if segment.strip()]
    company = role = None
    for segment in segments:
        phrase = find_role_phrase(segment)
        if phrase and role is None:
            role = phrase.group(0)
            prefix = segment[: phrase.start()].strip()
            if prefix and company is None:
                company = clean_company_candidate(prefix)
            continue
        if role is None and classify_role(segment).is_target:
            role = segment
            continue
        if company is None:
            company = clean_company_candidate(segment)
    if role is None and classify_role(text).is_target:
        role = text
    return company, role, None


_SITE_WORDS = re.compile(
    r"^(?:careers?|jobs?|job details|apply|apply now|openings?|open positions|home|join us|"
    r"we'?re hiring|hiring|full[- ]time|part[- ]time|contract|internship)$",
    re.I,
)


def _names_another_party(title: str) -> bool:
    """True when a title segment names an organisation that could not be accepted."""
    for segment in _SPLIT.split(re.sub(r"\s+", " ", html_lib.unescape(title or "")).strip()):
        text = segment.strip(" ,.")
        if not text or _SITE_WORDS.match(text) or is_location_text(text):
            continue
        if find_role_phrase(text) or classify_role(text).category in TARGET_CATEGORIES:
            continue
        return True
    return False


def career_root(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    for index, segment in enumerate(segments):
        if re.search(r"career|jobs?$", segment, re.I):
            return f"{parts.scheme}://{host}/" + "/".join(segments[: index + 1])
    return f"{parts.scheme}://{host}/"


_WORKDAY_SITE_NOISE = {
    "external", "ext", "careers", "career", "site", "sites", "jobs", "job", "professional",
    "corporate", "global", "experienced", "hiring", "opportunities", "search", "portal", "en",
    "us", "all", "public", "page", "board", "openings", "candidate", "home",
}  # fmt: skip


def workday_company_name(tenant: str, site: str | None = None) -> str | None:
    """``External_Rockwell_Automation`` on tenant ``rockwellautomation`` -> Rockwell Automation.

    The site name is used only when its non-generic words spell the tenant exactly;
    otherwise the tenant itself is the name (``NXP`` for short tenants).
    """
    tenant_key = re.sub(r"[^a-z0-9]", "", (tenant or "").lower())
    if not tenant_key:
        return None
    words: list[str] = []
    for token in re.split(r"[_\-\s]+", site or ""):
        words.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", token))
    kept = [word for word in words if word.lower() not in _WORKDAY_SITE_NOISE]
    if kept and "".join(kept).lower() == tenant_key:
        return clean_display_name(" ".join(kept))
    readable = re.sub(r"[-_]+", " ", tenant).strip()
    return readable.upper() if len(tenant_key) <= 3 else readable.title()


def _name_from_platform(identifier: dict[str, str]) -> str | None:
    if identifier.get("tenant"):
        name = workday_company_name(identifier["tenant"], identifier.get("site"))
        return clean_company_candidate(name) if name else None
    raw = (
        identifier.get("token")
        or identifier.get("slug")
        or identifier.get("company")
        or (identifier.get("tenant"))
    )
    if not raw:
        return None
    candidate = re.sub(r"[-_]+", " ", raw).strip()
    candidate = candidate.title() if candidate.islower() else candidate
    return clean_company_candidate(candidate)


def _evidence(result: SearchResult, note: str) -> str:
    snippet = f' Snippet: "{result.snippet[:220]}"' if result.snippet else ""
    return f'{result.provider} result for query "{result.query}": "{result.title}". {note}{snippet}'


def extract_signal(result: SearchResult, intent: str = "job") -> CandidateSignal | None:
    url = result.url
    host = hostname(url)
    domain = registrable_domain(host) if host else None
    if not host or not domain:
        return None
    if intent == "x" or domain in {"x.com", "twitter.com"}:
        return _from_x(result) if domain in {"x.com", "twitter.com"} else None
    if domain == "linkedin.com":
        return _from_linkedin(result)

    company, role, location = parse_title(result.title)
    snippet_only = False
    role_class = classify_role(role) if role else None
    if role_class is None or not role_class.is_target:
        phrase = find_role_phrase(result.snippet)
        if not phrase:
            return None
        role, role_class, snippet_only = phrase.group(0), classify_role(phrase.group(0)), True
        if not role_class.is_target:
            return None
    job_page = bool(_JOB_PAGE_URL.search(url))

    platform = detect_from_url(url)
    if platform and platform.platform not in {"generic_html", "schema_org", "unknown"}:
        name = company or _name_from_platform(platform.identifier)
        if not name:
            return None
        derived = company is None
        confidence = 0.5 if derived else 0.6 if snippet_only or not job_page else 0.8
        note = f"Hosted on {platform.platform}"
        if derived:
            note += "; company name taken from the ATS board identifier"
        return CandidateSignal(
            company_name=name,
            reason="JOB_SEARCH",
            source=result.provider,
            source_url=url,
            evidence=_evidence(result, note + "."),
            confidence=confidence,
            role_title=role,
            role_relevance=role_class.relevance,
            location=location,
            career_url=platform.canonical_url,
            job_url=url if job_page else None,
            query=result.query,
            metadata={
                "platform": platform.platform,
                "name_source": "ats_identifier" if derived else "result_title",
            },
        )

    if is_aggregator(host):
        if not company or not (_HIRING_URL.search(url) or _HIRING_TEXT.search(result.title)):
            return None
        return CandidateSignal(
            company_name=company,
            reason="JOB_SEARCH",
            source=result.provider,
            source_url=url,
            evidence=_evidence(result, f"Job board {domain} lists the employer in the title."),
            confidence=0.5,
            role_title=role,
            role_relevance=role_class.relevance,
            location=location,
            query=result.query,
            metadata={"job_board": domain},
        )

    if not (_HIRING_URL.search(url) or _HIRING_TEXT.search(result.title)):
        return None
    reason = "JOB_SEARCH" if job_page else "SEARCH_ENGINE"
    if company and name_matches_domain(company, domain) >= 0.7:
        return CandidateSignal(
            company_name=company,
            reason=reason,
            source=result.provider,
            source_url=url,
            evidence=_evidence(result, f"Page is on {domain}, which matches the employer name."),
            confidence=0.8 if job_page and not snippet_only else 0.65,
            role_title=role,
            role_relevance=role_class.relevance,
            location=location,
            domain=domain,
            career_url=career_root(url),
            job_url=url if job_page else None,
            query=result.query,
            metadata={"name_source": "result_title"},
        )
    if not company:
        careers_page = (
            job_page
            or host.startswith(("careers.", "jobs.", "career."))
            or bool(re.search(r"/careers?(?:/|$)", urlsplit(url).path, re.I))
        )
        # Only a careers page whose title names nobody else is attributed to the site owner.
        if not careers_page or _names_another_party(result.title):
            return None
        label = domain.split(".")[0]
        name = clean_company_candidate(label.replace("-", " ").title())
        if not name:
            return None
        return CandidateSignal(
            company_name=name,
            reason=reason,
            source=result.provider,
            source_url=url,
            evidence=_evidence(
                result, f"Hiring page on {domain}; company name inferred from the domain."
            ),
            confidence=0.45,
            role_title=role,
            role_relevance=role_class.relevance,
            location=location,
            domain=domain,
            career_url=career_root(url),
            job_url=url if job_page else None,
            query=result.query,
            metadata={"name_source": "domain"},
        )
    return CandidateSignal(
        company_name=company,
        reason=reason,
        source=result.provider,
        source_url=url,
        evidence=_evidence(
            result, f"Employer named in the title; page domain {domain} is not verified as theirs."
        ),
        confidence=0.4,
        role_title=role,
        role_relevance=role_class.relevance,
        location=location,
        job_url=url if job_page else None,
        query=result.query,
        metadata={"name_source": "result_title", "unverified_domain": domain},
    )


def _from_linkedin(result: SearchResult) -> CandidateSignal | None:
    title = re.sub(r"\s+", " ", result.title).strip()
    match = _EXPLICIT_TITLES[1].match(title)
    confidence, note = 0.65, "Search-indexed LinkedIn job page; LinkedIn was not accessed."
    company = role = location = None
    if match:
        company, role, location = (
            clean_company_candidate(match.group("company")),
            match.group("role"),
            match.group("location"),
        )
    else:
        listing = _LINKEDIN_LISTING.match(title)
        if listing:
            phrase = find_role_phrase(listing.group("rest"))
            if phrase:
                company = clean_company_candidate(listing.group("rest")[: phrase.start()])
                role, location = phrase.group(0), listing.group("location")
                confidence, note = (
                    0.45,
                    "Search-indexed LinkedIn listing page; LinkedIn was not accessed.",
                )
    if not company or not role:
        return None
    role_class = classify_role(role)
    if not role_class.is_target:
        return None
    return CandidateSignal(
        company_name=company,
        reason="LINKEDIN_HIRING_SIGNAL",
        source=result.provider,
        source_url=result.url,
        evidence=_evidence(result, note),
        confidence=confidence,
        role_title=role,
        role_relevance=role_class.relevance,
        location=location,
        query=result.query,
        metadata={"via": "search_index"},
    )


def _from_x(result: SearchResult) -> CandidateSignal | None:
    text = f"{result.title}. {result.snippet}"
    phrase = find_role_phrase(text)
    if not phrase or not classify_role(phrase.group(0)).is_target:
        return None
    for pattern in _X_COMPANY:
        match = pattern.search(text)
        company = clean_company_candidate(match.group("company")) if match else None
        if company:
            return CandidateSignal(
                company_name=company,
                reason="X_HIRING_SIGNAL",
                source=result.provider,
                source_url=result.url,
                evidence=_evidence(result, "Search-indexed public X post; X was not accessed."),
                confidence=0.35,
                role_title=phrase.group(0),
                role_relevance=classify_role(phrase.group(0)).relevance,
                query=result.query,
                metadata={"via": "search_index"},
            )
    return None


def extract_similar(result: SearchResult, company_name: str) -> list[CandidateSignal]:
    text = f"{result.title}. {result.snippet}"
    own_key = company_key(company_name)
    signals: list[CandidateSignal] = []
    seen: set[str] = set()
    for pattern, relationship in _SIMILAR_PATTERNS:
        for match in pattern.finditer(text):
            for raw in re.split(r",\s*|\s+and\s+|;\s*|\s+&\s+", match.group("list")):
                raw = re.sub(
                    r"\b(?:etc|and more|among others|others|more)\b\.?", "", raw, flags=re.I
                )
                name = clean_company_candidate(raw)
                key = company_key(name) if name else ""
                if not name or key == own_key or key in seen:
                    continue
                seen.add(key)
                label = "a competitor" if relationship == "competes_with" else "similar"
                signals.append(
                    CandidateSignal(
                        company_name=name,
                        reason="SIMILAR_COMPANY",
                        source=result.provider,
                        source_url=result.url,
                        evidence=_evidence(
                            result, f"The snippet lists {name} as {label} of {company_name}."
                        ),
                        confidence=0.3,
                        query=result.query,
                        related_company=company_name,
                        relationship_type=relationship,
                    )
                )
                if len(signals) >= 6:
                    return signals
    return signals
