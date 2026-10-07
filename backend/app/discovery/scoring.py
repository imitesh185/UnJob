"""Company discovery score, frontier priority, and confidence.

The discovery score is separate from a job's opportunity score. Missing information is
scored as neutral or zero and labelled UNKNOWN; it is never filled in with assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompanyStats:
    open_jobs: int = 0
    data_roles: int = 0
    fresh_data_roles: int = 0
    recent_data_roles: int = 0
    platform_roles: int = 0
    technologies: list[str] = field(default_factory=list)
    job_relevance: list[float] = field(default_factory=list)
    hiring_signals: int = 0
    hiring_sources: int = 0
    hiring_posts: int = 0
    evidence_relevance: list[float] = field(default_factory=list)
    latest_signal_age_hours: float | None = None
    india_presence: bool | None = None
    remote_presence: bool | None = None
    company_stage: str | None = None
    product_company: bool = False
    is_seed: bool = False
    confidences: list[float] = field(default_factory=list)
    verified_hiring: bool = False
    similar_to: list[str] = field(default_factory=list)


def score_company(
    stats: CompanyStats, weights: dict[str, float]
) -> tuple[float, dict[str, float], list[str]]:
    reasons: list[str] = []

    data_hiring = min(100.0, 20.0 * stats.data_roles + 12.0 * min(stats.hiring_signals, 4))
    if stats.data_roles or stats.hiring_signals:
        reasons.append(
            f"Data hiring: {stats.data_roles} open data-engineering role(s) on career sources and "
            f"{stats.hiring_signals} external hiring signal(s)."
        )
    else:
        reasons.append("Data hiring: no data-engineering hiring evidence yet.")

    if stats.open_jobs == 0 and not stats.technologies:
        engineering = 50.0
        reasons.append(
            "Engineering quality: UNKNOWN (neutral) until job descriptions are available."
        )
    else:
        engineering = min(
            100.0, 40.0 + 6.0 * min(len(stats.technologies), 6) + 6.0 * min(stats.platform_roles, 3)
        )
        reasons.append(
            f"Engineering quality: {len(stats.technologies)} technologies and "
            f"{stats.platform_roles} platform/infrastructure role(s) in job descriptions."
        )

    age = stats.latest_signal_age_hours
    if stats.fresh_data_roles:
        recent = 100.0
        reasons.append(
            f"Recency: {stats.fresh_data_roles} data role(s) posted within the freshness window."
        )
    elif stats.recent_data_roles:
        recent = 70.0
        reasons.append(
            f"Recency: {stats.recent_data_roles} data role(s) posted within the secondary window."
        )
    elif age is not None and age <= 48:
        recent = 85.0
        reasons.append("Recency: dated hiring signal within the last 48 hours.")
    elif age is not None and age <= 168:
        recent = 60.0
        reasons.append("Recency: dated hiring signal within the last 7 days.")
    elif age is not None:
        recent = 25.0
        reasons.append("Recency: latest dated hiring signal is older than 7 days.")
    else:
        recent = 0.0
        reasons.append("Recency: no dated hiring evidence.")

    relevances = sorted(stats.job_relevance + stats.evidence_relevance, reverse=True)[:3]
    relevance = round(100.0 * sum(relevances) / len(relevances), 1) if relevances else 0.0
    reasons.append(
        f"Role relevance: {relevance:.0f}/100 from the strongest matching roles."
        if relevances
        else "Role relevance: no target-role evidence yet."
    )

    if stats.india_presence:
        location = 100.0
        reasons.append("India/remote: India presence is evidenced by job locations or source data.")
    elif stats.remote_presence:
        location = 80.0
        reasons.append("India/remote: remote roles are evidenced.")
    else:
        location = 50.0
        reasons.append("India/remote: UNKNOWN (neutral).")

    growth = 50.0
    growth_notes = []
    if stats.data_roles >= 3:
        growth += 20.0
        growth_notes.append(f"{stats.data_roles} concurrent data roles suggest team expansion")
    if stats.company_stage and stats.company_stage.lower() in {
        "growth",
        "early",
        "series a",
        "series b",
        "series c",
    }:
        growth += 10.0
        growth_notes.append(f"stage reported as {stats.company_stage}")
    reasons.append(
        "Growth potential: "
        + ("; ".join(growth_notes) + "." if growth_notes else "UNKNOWN (neutral).")
    )

    breakdown = {
        "data_hiring_signal": round(data_hiring, 1),
        "engineering_quality": round(engineering, 1),
        "recent_hiring": round(recent, 1),
        "role_relevance": round(relevance, 1),
        "india_remote_opportunity": round(location, 1),
        "growth_potential": round(growth, 1),
    }
    total_weight = sum(weights.get(name, 0.0) for name in breakdown) or 1.0
    score = sum(breakdown[name] * weights.get(name, 0.0) for name in breakdown) / total_weight
    return round(score, 1), breakdown, reasons


def company_confidence(stats: CompanyStats) -> float:
    remaining = 1.0
    for value in stats.confidences:
        remaining *= 1.0 - max(0.0, min(value, 0.99))
    confidence = 1.0 - remaining
    if stats.verified_hiring:
        confidence = max(confidence, 0.95)
    return round(min(confidence, 0.99), 2)


def frontier_priority(
    stats: CompanyStats, weights: dict[str, float]
) -> tuple[float, dict[str, float]]:
    applied: dict[str, float] = {}
    openings = max(stats.data_roles, stats.hiring_signals)
    if openings >= 5:
        applied["de_openings_5plus"] = weights["de_openings_5plus"]
    elif openings >= 3:
        applied["de_openings_3plus"] = weights["de_openings_3plus"]
    elif openings >= 1:
        applied["de_openings_1plus"] = weights["de_openings_1plus"]
    if stats.hiring_posts:
        applied["hiring_post"] = weights["hiring_post"]
    if stats.hiring_sources >= 2:
        applied["hiring_campaign"] = weights["hiring_campaign"]
    if stats.india_presence:
        applied["india_presence"] = weights["india_presence"]
    if stats.product_company:
        applied["product_company"] = weights["product_company"]
    if stats.latest_signal_age_hours is not None and stats.latest_signal_age_hours <= 48:
        applied["fresh_signal"] = weights["fresh_signal"]
    if stats.is_seed:
        applied["seed"] = weights["seed"]
    return round(sum(applied.values()), 1), applied


def confidence_band(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"
