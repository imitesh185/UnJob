"""Structured-data, HTML, and date helpers shared by connectors and providers."""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

# A date-only or zone-less timestamp may be up to 14 hours earlier than its UTC reading.
TIMEZONE_SLACK_HOURS = 14.0
_JSON_LD = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S
)
_TAG = re.compile(r"<[^>]+>")
_BLOCK_TAGS = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|section|article)[^>]*>", re.I)
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|svg|template)[^>]*>.*?</\1>", re.I | re.S)
_ANCHOR = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a>", re.I | re.S)


def html_to_text(value: str | None, limit: int = 20_000) -> str:
    if not value:
        return ""
    text = html_lib.unescape(value)
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _BLOCK_TAGS.sub("\n", text)
    text = html_lib.unescape(_TAG.sub(" ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text[:limit]


def parse_datetime(value: Any) -> tuple[datetime | None, str | None]:
    """Parse a source timestamp without inventing precision.

    Returns ``(value, precision)`` where precision is ``datetime`` (zone-aware),
    ``local_datetime`` (no zone), or ``date`` (calendar date only).
    """
    if value is None or value == "":
        return None, None
    if isinstance(value, datetime):
        return (
            (value, "datetime") if value.tzinfo else (value.replace(tzinfo=UTC), "local_datetime")
        )
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC), "date"
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC), "datetime"
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            return None, None
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC), "date"
    normalized = text.replace("Z", "+00:00").replace(" UTC", "+00:00")
    normalized = re.sub(r"^(\d{4}-\d{2}-\d{2}) (\d)", r"\1T\2", normalized)
    try:
        parsed_dt = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
            try:
                parsed_dt = datetime.strptime(re.sub(r"\s+", " ", text), pattern)
            except ValueError:
                continue
            return parsed_dt.replace(tzinfo=UTC), "date"
        return None, None
    if parsed_dt.tzinfo is None:
        return parsed_dt.replace(tzinfo=UTC), "local_datetime"
    return parsed_dt.astimezone(UTC), "datetime"


def age_bounds_hours(
    posted_at: datetime | None, precision: str | None, now: datetime
) -> tuple[float, float] | None:
    """Minimum and maximum possible age of a posting, honouring source precision."""
    if posted_at is None:
        return None
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    age = (now - posted_at).total_seconds() / 3600
    if precision == "date":
        return max(0.0, age - 36.0), max(0.0, age + TIMEZONE_SLACK_HOURS)
    if precision == "local_datetime":
        return max(0.0, age - 12.0), max(0.0, age + TIMEZONE_SLACK_HOURS)
    return max(0.0, age), max(0.0, age)


def freshness_status(
    posted_at: datetime | None,
    precision: str | None,
    now: datetime,
    fresh_hours: float,
    secondary_hours: float,
) -> str:
    bounds = age_bounds_hours(posted_at, precision, now)
    if bounds is None:
        return "unknown"
    _minimum, maximum = bounds
    if maximum <= fresh_hours:
        return "fresh"
    if maximum <= secondary_hours:
        return "recent"
    return "stale"


def _iter_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in value:
                yield from _iter_objects(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _iter_objects(item)


def extract_json_ld(page_html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for block in _JSON_LD.findall(page_html):
        raw = html_lib.unescape(block.strip()) if "&quot;" in block else block.strip()
        try:
            data = json.loads(raw)
        except ValueError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw), strict=False)
            except ValueError:
                continue
        objects.extend(_iter_objects(data))
    return objects


def find_job_postings(page_html: str) -> list[dict[str, Any]]:
    postings = []
    for item in extract_json_ld(page_html):
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            postings.append(item)
    return postings


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("value") or value.get("@value"))
    if isinstance(value, list) and value:
        return _text(value[0])
    return None


def _location(posting: dict[str, Any]) -> str | None:
    places = posting.get("jobLocation")
    places = places if isinstance(places, list) else [places] if places else []
    labels = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address")
        if isinstance(address, dict):
            country = _text(address.get("addressCountry"))
            parts = [
                _text(address.get("addressLocality")),
                _text(address.get("addressRegion")),
                country,
            ]
            label = ", ".join(dict.fromkeys(part for part in parts if part))
        else:
            label = _text(address) or _text(place.get("name"))
        if label:
            labels.append(label)
    if not labels:
        requirement = posting.get("applicantLocationRequirements")
        requirement_label = _text(requirement)
        if requirement_label:
            labels.append(requirement_label)
    return "; ".join(dict.fromkeys(labels))[:300] or None


def _salary(posting: dict[str, Any]) -> str | None:
    salary = posting.get("baseSalary")
    if not isinstance(salary, dict):
        return _text(salary)
    currency = salary.get("currency") or ""
    value = salary.get("value")
    if isinstance(value, dict):
        low, high = value.get("minValue"), value.get("maxValue")
        unit = value.get("unitText") or ""
        amount = f"{low}-{high}" if low and high else str(value.get("value") or low or high or "")
        return " ".join(part for part in (currency, amount, unit) if part).strip() or None
    return " ".join(part for part in (currency, str(value or "")) if part).strip() or None


def posting_fields(posting: dict[str, Any], page_url: str) -> dict[str, Any]:
    posted_at, precision = parse_datetime(posting.get("datePosted"))
    valid_through, _ = parse_datetime(posting.get("validThrough"))
    location = _location(posting)
    location_type = str(posting.get("jobLocationType") or "").upper()
    employment = posting.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(item) for item in employment)
    identifier = posting.get("identifier")
    identifier_value = (
        _text(identifier) if not isinstance(identifier, (str, int)) else str(identifier)
    )
    return {
        "title": html_lib.unescape(_text(posting.get("title")) or "").strip(),
        "description": html_to_text(posting.get("description")),
        "posted_at": posted_at,
        "precision": precision,
        "valid_through": valid_through,
        "company_name": _text(posting.get("hiringOrganization")),
        "location": location,
        "remote": "TELECOMMUTE" in location_type,
        "employment_type": str(employment) if employment else None,
        "salary": _salary(posting),
        "identifier": identifier_value,
        "url": urljoin(page_url, _text(posting.get("url")) or page_url),
    }


def page_title(page_html: str) -> str | None:
    match = re.search(
        r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)", page_html, re.I
    ) or re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
    return html_lib.unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else None


def first_heading(page_html: str) -> str | None:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.I | re.S)
    return html_to_text(match.group(1), 300) or None if match else None


def extract_links(page_html: str, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, label in _ANCHOR.findall(page_html):
        href = html_lib.unescape(href.strip())
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = canonical_url(urljoin(base_url, href))
        if absolute in seen or not absolute.startswith(("http://", "https://")):
            continue
        seen.add(absolute)
        links.append((absolute, html_to_text(label, 300)))
    return links


_TRACKING_PARAMS = re.compile(r"^(utm_|gclid|fbclid|ref|source|src)", re.I)


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = "&".join(
        piece for piece in parts.query.split("&") if piece and not _TRACKING_PARAMS.match(piece)
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def visible_text_length(page_html: str) -> int:
    return len(html_to_text(page_html, 200_000))


def looks_javascript_rendered(page_html: str) -> bool:
    markers = (
        'id="root"',
        "id='root'",
        'id="app"',
        "__NEXT_DATA__",
        "ng-app",
        "data-reactroot",
        "window.__INITIAL_STATE__",
        'id="__nuxt"',
    )
    return visible_text_length(page_html) < 1500 and (
        any(marker in page_html for marker in markers) or page_html.lower().count("<script") >= 5
    )


def relative_days_ago(text: str | None, today: date) -> date | None:
    """Interpret Workday-style "Posted N Days Ago" labels; returns None when imprecise."""
    if not text:
        return None
    lowered = text.lower()
    if "30+" in lowered:
        return None
    if "today" in lowered:
        return today
    if "yesterday" in lowered:
        return today - timedelta(days=1)
    match = re.search(r"(\d+)\s+days?\s+ago", lowered)
    return today - timedelta(days=int(match.group(1))) if match else None
