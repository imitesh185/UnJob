"""Company-specific positioning: which *genuine* parts of the candidate's experience to emphasize.

Positioning signals describe what a company values; they never add experience. Each signal maps
to taxonomy terms, and the tailoring engine only boosts candidate facts that already contain
those terms.
"""

from __future__ import annotations

import re
from typing import Any

from app.discovery.names import company_key

# From the user's tailoring brief; editable in settings (``positioning_profiles``).
DEFAULT_COMPANY_POSITIONING: dict[str, list[str]] = {
    "Amazon": [
        "scale",
        "ownership",
        "operational excellence",
        "distributed systems",
        "customer impact",
    ],
    "Microsoft": ["Azure", "cloud infrastructure", "engineering systems", "platform thinking"],
    "Uber": [
        "distributed systems",
        "real-time data",
        "marketplace infrastructure",
        "large-scale engineering",
    ],
    "Databricks": ["data infrastructure", "Spark", "lakehouse", "distributed systems"],
}

DEFAULT_ARCHETYPES: dict[str, list[str]] = {
    "data_platform": [
        "data platform",
        "reliability",
        "storage",
        "concurrency",
        "distributed processing",
        "cloud infrastructure",
    ],
    "fintech": [
        "production data pipelines",
        "scale",
        "data quality",
        "reliability",
        "business-critical systems",
    ],
    "ai": [
        "data infrastructure",
        "large-scale pipelines",
        "distributed processing",
        "data availability",
        "platform engineering",
    ],
    "startup": [
        "ownership",
        "breadth",
        "building systems",
        "debugging",
        "speed",
        "end-to-end execution",
    ],
}

# Signal phrase -> taxonomy terms it emphasizes.
SIGNAL_TERMS: dict[str, list[str]] = {
    "scale": ["Scalability / high throughput"],
    "large-scale engineering": ["Scalability / high throughput", "Distributed systems"],
    "large-scale pipelines": ["Scalability / high throughput", "Data pipelines / ETL"],
    "ownership": ["Ownership & end-to-end delivery", "Data migration"],
    "end-to-end execution": ["Ownership & end-to-end delivery"],
    "operational excellence": ["Reliability / fault tolerance", "Observability & monitoring"],
    "distributed systems": ["Distributed systems", "Concurrency"],
    "distributed processing": ["Distributed systems", "Spark"],
    "customer impact": ["Low-latency serving", "API development"],
    "azure": ["Azure", "Azure Data Factory", "Databricks"],
    "cloud infrastructure": ["Cloud data platforms", "Azure"],
    "engineering systems": ["CI/CD & DevOps", "Observability & monitoring"],
    "platform thinking": ["Data platform engineering", "Metadata-driven frameworks"],
    "real-time data": ["Streaming / real-time processing", "Kafka", "Change data capture (CDC)"],
    "marketplace infrastructure": ["Low-latency serving", "API development", "Caching"],
    "data infrastructure": ["Data platform engineering", "Data pipelines / ETL"],
    "spark": ["Spark", "Performance tuning"],
    "lakehouse": ["Data lake / lakehouse", "Delta Lake"],
    "data platform": ["Data platform engineering"],
    "reliability": ["Reliability / fault tolerance"],
    "storage": ["Partitioning & storage layout", "Data lake / lakehouse"],
    "concurrency": ["Concurrency", "Rate limiting & backpressure"],
    "production data pipelines": ["Data pipelines / ETL", "Data ingestion"],
    "data quality": ["Data quality"],
    "business-critical systems": ["Reliability / fault tolerance", "Low-latency serving"],
    "data availability": ["Reliability / fault tolerance", "Change data capture (CDC)"],
    "platform engineering": ["Data platform engineering"],
    "building systems": ["System design & architecture"],
    "debugging": ["Performance tuning"],
    "breadth": [],
    "speed": [],
}

_ARCHETYPE_HINTS = (
    (
        "data_platform",
        re.compile(
            r"\b(data cloud|lakehouse|data platform company|data warehouse "
            r"company|data infrastructure company|databricks|snowflake|"
            r"confluent|clickhouse|starburst|fivetran|dbt labs)\b",
            re.I,
        ),
    ),
    (
        "ai",
        re.compile(
            r"\b(ai company|artificial intelligence|machine learning platform|llms?|"
            r"gpus?|nvidia|openai|anthropic|accelerated computing)\b",
            re.I,
        ),
    ),
    (
        "fintech",
        re.compile(
            r"\b(payments?|fintech|bank(?:ing)?|card network|financial services|"
            r"lending|mastercard|visa|jpmorgan|morgan stanley|stripe|razorpay|"
            r"phonepe|paytm|perfios)\b",
            re.I,
        ),
    ),
    (
        "startup",
        re.compile(r"\b(startup|seed|series [abc]\b|y combinator|yc\b|early[- ]stage)\b", re.I),
    ),
)


def infer_archetype(
    company_name: str,
    *,
    industry: str | None = None,
    company_type: str | None = None,
    stage: str | None = None,
    jd_context: str | None = None,
    jd_domains: list[str] | None = None,
) -> tuple[str | None, str]:
    """Return ``(archetype, reason)``; ``None`` when nothing indicates one."""
    sources = {
        "company name": company_name or "",
        "industry": industry or "",
        "company type": company_type or "",
        "stage": stage or "",
        "job description": jd_context or "",
    }
    for archetype, pattern in _ARCHETYPE_HINTS:
        for label, text in sources.items():
            match = pattern.search(text)
            if match:
                return archetype, f"{label} mentions '{match.group(0)}'"
    if jd_domains and "Payments & financial services" in jd_domains:
        return "fintech", "the job description is in payments/financial services"
    if jd_domains and "AI & machine learning" in jd_domains:
        return "ai", "the job description is AI/ML-related"
    return None, "no archetype signal"


def positioning_for(
    company_name: str,
    *,
    profiles: dict[str, list[str]] | None = None,
    archetypes: dict[str, list[str]] | None = None,
    industry: str | None = None,
    company_type: str | None = None,
    stage: str | None = None,
    jd_context: str | None = None,
    jd_domains: list[str] | None = None,
) -> dict[str, Any]:
    profiles = profiles if profiles is not None else DEFAULT_COMPANY_POSITIONING
    archetypes = archetypes if archetypes is not None else DEFAULT_ARCHETYPES
    key = company_key(company_name or "")
    signals: list[str] = []
    sources: list[str] = []
    for name, values in profiles.items():
        if company_key(name) == key and key:
            signals.extend(values)
            sources.append(f"company profile for {name} (settings)")
            break
    archetype, reason = infer_archetype(
        company_name,
        industry=industry,
        company_type=company_type,
        stage=stage,
        jd_context=jd_context,
        jd_domains=jd_domains,
    )
    if archetype and archetype in archetypes:
        signals.extend(value for value in archetypes[archetype] if value not in signals)
        sources.append(f"{archetype.replace('_', ' ')} archetype ({reason})")
    terms: dict[str, float] = {}
    for signal in signals:
        for term in SIGNAL_TERMS.get(signal.lower(), []):
            terms[term] = terms.get(term, 0.0) + 0.3
    return {
        "archetype": archetype,
        "signals": signals,
        "sources": sources,
        "term_boosts": {term: round(min(weight, 0.6), 2) for term, weight in terms.items()},
    }
