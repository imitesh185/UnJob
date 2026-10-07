"""Company target score, application priority and application strategy.

The company target score is candidate-aware and separate from both the discovery score and the
job fit score. Tiers are user metadata: they contribute a configurable career-value input but
never filter anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.intelligence.matching import CandidateModel, MatchResult, freshness_bucket

DEFAULT_TARGET_WEIGHTS = {
    "hiring_activity": 0.20,
    "role_relevance": 0.15,
    "engineering_quality": 0.10,
    "product_quality": 0.10,
    "career_growth": 0.10,
    "compensation_potential": 0.05,
    "india_remote_fit": 0.15,
    "technical_relevance": 0.15,
}
DEFAULT_PRIORITY_WEIGHTS = {
    "fit": 0.45,
    "company_target": 0.25,
    "freshness": 0.15,
    "career_value": 0.15,
}
DEFAULT_PRIORITY_THRESHOLDS = {"P0": 80.0, "P1": 68.0, "P2": 55.0, "P3": 40.0}
DEFAULT_TIER_VALUES = {"S": 100.0, "A": 85.0, "B": 70.0, "C": 55.0, "UNCATEGORIZED": 50.0}
ACCOUNT_REQUIRED_ATS = {
    "workday",
    "oracle",
    "oracle_taleo",
    "icims",
    "amazon_jobs",
    "smartrecruiters",
}
RECOMMENDATIONS = {
    "P0": "Apply today",
    "P1": "Apply this week",
    "P2": "Worth applying if time allows",
    "P3": "Low priority: apply only after higher priorities",
}


@dataclass
class CompanyContext:
    name: str
    tier: str
    discovery_breakdown: dict[str, float]
    relevant_open_jobs: int
    fresh_relevant_jobs: int
    india_presence: bool | None
    remote_presence: bool | None
    company_type: str | None
    industry: str | None
    job_fits: list[float] = field(default_factory=list)
    job_locations_ok: list[bool] = field(default_factory=list)
    tech_terms: list[str] = field(default_factory=list)
    compensation_matches: list[bool] = field(default_factory=list)
    current_employer: bool = False


# An external application to the company you already work for is rarely the right move.
CURRENT_EMPLOYER_TARGET_CAP = 35.0


def company_target_score(
    company: CompanyContext,
    model: CandidateModel,
    *,
    weights: dict[str, float] | None = None,
    tier_values: dict[str, float] | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    weights = weights or DEFAULT_TARGET_WEIGHTS
    tier_values = tier_values or DEFAULT_TIER_VALUES
    reasons: list[str] = []
    discovery = company.discovery_breakdown or {}

    hiring = min(100.0, 20.0 * company.relevant_open_jobs + 15.0 * company.fresh_relevant_jobs)
    if company.relevant_open_jobs:
        reasons.append(
            f"Hiring activity: {company.relevant_open_jobs} relevant open role(s), "
            f"{company.fresh_relevant_jobs} fresh."
        )
    else:
        hiring = min(hiring, float(discovery.get("data_hiring_signal", 0.0)))
        reasons.append("Hiring activity: no relevant open roles found yet.")

    fits = sorted(company.job_fits, reverse=True)[:3]
    if fits:
        role = sum(fits) / len(fits)
        reasons.append(f"Role relevance: your best fits here average {role:.0f}/100.")
    else:
        role = float(discovery.get("role_relevance", 50.0))
        reasons.append("Role relevance: from discovery evidence (no analysed roles yet).")

    engineering = float(discovery.get("engineering_quality", 50.0))
    reasons.append(f"Engineering quality: {engineering:.0f}/100 from job-description evidence.")

    text = f"{company.company_type or ''} {company.industry or ''}".lower()
    if any(word in text for word in ("product", "startup", "saas", "software")):
        product = 80.0
        reasons.append("Product quality: product/software company per company data.")
    else:
        product = 50.0
        reasons.append("Product quality: UNKNOWN (neutral).")

    tier_value = float(tier_values.get(company.tier, tier_values.get("UNCATEGORIZED", 50.0)))
    growth_signal = float(discovery.get("growth_potential", 50.0))
    career = 0.5 * tier_value + 0.5 * growth_signal
    reasons.append(
        f"Career growth: tier {company.tier} (your configured value {tier_value:.0f}) and growth "
        f"signals {growth_signal:.0f}."
    )

    if company.compensation_matches:
        compensation = 85.0 if any(company.compensation_matches) else 40.0
        reasons.append("Compensation: published ranges compared with your target.")
    else:
        compensation = 50.0
        reasons.append("Compensation: UNKNOWN (no published ranges).")

    if company.job_locations_ok:
        location = 100.0 * sum(company.job_locations_ok) / len(company.job_locations_ok)
        reasons.append(
            f"India/remote fit: {sum(company.job_locations_ok)} of {len(company.job_locations_ok)} "
            "relevant role(s) are in your preferred locations."
        )
    elif company.india_presence:
        location = 85.0
        reasons.append("India/remote fit: India presence evidenced.")
    elif company.remote_presence:
        location = 75.0
        reasons.append("India/remote fit: remote roles evidenced.")
    else:
        location = 50.0
        reasons.append("India/remote fit: UNKNOWN (neutral).")

    strong = set(model.strong_skills()) | {
        name for name, evidence in model.concepts.items() if len(evidence.production) >= 1
    }
    terms = list(dict.fromkeys(company.tech_terms))[:15]
    if terms:
        matched = [term for term in terms if term in strong]
        technical = 100.0 * len(matched) / len(terms)
        reasons.append(
            f"Technical relevance: you have production evidence for {len(matched)} of {len(terms)} "
            "technologies/skills in their relevant roles."
        )
    else:
        technical = 50.0
        reasons.append("Technical relevance: UNKNOWN until relevant roles are analysed.")

    breakdown = {
        "hiring_activity": round(hiring, 1),
        "role_relevance": round(role, 1),
        "engineering_quality": round(engineering, 1),
        "product_quality": round(product, 1),
        "career_growth": round(career, 1),
        "compensation_potential": round(compensation, 1),
        "india_remote_fit": round(location, 1),
        "technical_relevance": round(technical, 1),
    }
    total = sum(weights.get(name, 0.0) for name in breakdown) or 1.0
    score = sum(breakdown[name] * weights.get(name, 0.0) for name in breakdown) / total
    if company.current_employer:
        score = min(score, CURRENT_EMPLOYER_TARGET_CAP)
        reasons.insert(
            0,
            f"Your current employer: capped at {CURRENT_EMPLOYER_TARGET_CAP:.0f}; explore an "
            "internal move rather than an external application.",
        )
    return round(score, 1), breakdown, reasons


def freshness_score(age_hours: float | None) -> tuple[float, str]:
    """Score from the oldest possible posting age; unknown dates are never treated as fresh."""
    bucket = freshness_bucket(age_hours)
    if age_hours is None:
        return 30.0, "Posting date UNKNOWN."
    if age_hours <= 12:
        score = 100.0
    elif age_hours <= 24:
        score = 95.0
    elif age_hours <= 48:
        score = 88.0
    elif age_hours <= 72:
        score = 75.0
    elif age_hours <= 168:
        score = 60.0
    elif age_hours <= 336:
        score = 40.0
    elif age_hours <= 720:
        score = 25.0
    else:
        score = 10.0
    return (
        score,
        f"Posted within {bucket}."
        if bucket != "7d+"
        else f"Posted {age_hours / 24:.0f}+ days ago.",
    )


@dataclass
class PriorityResult:
    priority_score: float
    priority_class: str
    gates: list[str]
    reasons: list[str]
    recommendation: str
    career_value: float
    effort_minutes: int


def application_priority(
    *,
    match: MatchResult,
    company_target: float,
    company_career_growth: float,
    freshness: float,
    freshness_note: str,
    age_hours: float | None,
    role_is_target: bool,
    listing_open: bool,
    ats: str,
    tailored_resume_ready: bool,
    current_employer: bool = False,
    weights: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
) -> PriorityResult:
    weights = weights or DEFAULT_PRIORITY_WEIGHTS
    thresholds = thresholds or DEFAULT_PRIORITY_THRESHOLDS
    career = company_career_growth
    career_notes: list[str] = []
    if match.seniority_fit == "STRETCH":
        career += 10.0
        career_notes.append("a step up in level")
    elif match.seniority_fit == "OVERQUALIFIED":
        career -= 15.0
        career_notes.append("below your current level")
    if match.compensation_fit.get("status") == "MATCH":
        career += 10.0
        career_notes.append("meets your compensation target")
    career = max(0.0, min(100.0, career))
    components = {
        "fit": match.fit_score,
        "company_target": company_target,
        "freshness": freshness,
        "career_value": career,
    }
    total = sum(weights.get(name, 0.0) for name in components) or 1.0
    score = sum(components[name] * weights.get(name, 0.0) for name in components) / total

    gates: list[str] = []
    order = ["P0", "P1", "P2", "P3", "REJECT"]
    caps: list[tuple[str, str]] = []
    if not listing_open:
        gates.append("The listing is closed.")
    if not role_is_target:
        gates.append("Not one of your target role types.")
    if match.location_fit.get("status") == "MISMATCH":
        gates.append(f"Location: {match.location_fit.get('note')}")
    if match.fit_score < 45:
        gates.append(f"Fit {match.fit_score:.0f}/100 is below the minimum of 45.")
    if match.seniority_fit == "UNDERQUALIFIED":
        caps.append(("P3", "Capped at P3: underqualified for the stated requirements."))
    if match.seniority_fit == "OVERQUALIFIED":
        caps.append(
            (
                "P2",
                "Capped at P2: the role is below your experience level, so pay and growth are "
                "likely lower.",
            )
        )
    if age_hours is None or age_hours > 720:
        caps.append(("P2", "Capped at P2: the posting date is unknown or older than 30 days."))
    if match.breakdown.get("role", 100) <= 55:
        caps.append(("P2", "Capped at P2: the role is only adjacent to your target roles."))
    if "people-management" in match.component_notes.get("seniority", ""):
        caps.append(
            (
                "P2",
                "Capped at P2: a people-management title while your history is "
                "individual-contributor.",
            )
        )
    if current_employer:
        caps.append(
            (
                "P3",
                "Capped at P3: this is your current employer; an internal move is usually a "
                "better route than an external application.",
            )
        )
    cap = max((name for name, _ in caps), key=order.index, default=None)
    cap_reasons = [reason for name, reason in caps if name == cap]

    # Interview probability depends on fit first: each class also needs a minimum fit.
    fit_floors = {"P0": 75.0, "P1": 65.0, "P2": 55.0, "P3": 0.0}
    if gates:
        priority_class = "REJECT"
    else:
        priority_class = "REJECT"
        for name in ("P0", "P1", "P2", "P3"):
            if score >= thresholds[name] and match.fit_score >= fit_floors[name]:
                priority_class = name
                break
        if cap and order.index(priority_class) < order.index(cap):
            priority_class = cap

    reasons = [
        f"Fit {match.fit_score:.0f}, company target {company_target:.0f}, freshness "
        f"{freshness:.0f} ({freshness_note}), career value {career:.0f}"
        + (f" ({'; '.join(career_notes)})" if career_notes else "")
        + f" -> priority score {score:.0f}.",
    ]
    if cap and priority_class == cap:
        reasons.extend(cap_reasons)
    elif priority_class != "REJECT" and score >= thresholds.get("P0", 80) and match.fit_score < 75:
        reasons.append("Not P0: fit is below 75, so interview probability is not yet top-tier.")
    effort = 45
    if tailored_resume_ready:
        effort -= 10
    if ats in ACCOUNT_REQUIRED_ATS:
        effort += 10
    recommendation = (
        f"Skip: {gates[0]}"
        if priority_class == "REJECT" and gates
        else "Skip: priority score below the P3 threshold."
        if priority_class == "REJECT"
        else "Your current employer: explore an internal transfer instead of applying externally."
        if current_employer
        else RECOMMENDATIONS[priority_class]
    )
    return PriorityResult(
        priority_score=round(score, 1),
        priority_class=priority_class,
        gates=gates,
        reasons=[reason for reason in reasons if reason],
        recommendation=recommendation,
        career_value=round(career, 1),
        effort_minutes=effort,
    )


def is_current_employer(current_company: str | None, company_name: str | None) -> bool:
    """``Accenture Technology Services`` (your employer) matches the company ``Accenture``.

    Names match when their normalised keys are equal or one name's words are a leading
    prefix of the other's, so ``Meta`` does not match ``Metabase``.
    """
    from app.discovery.names import company_key, normalize_company_name

    mine, theirs = company_key(current_company or ""), company_key(company_name or "")
    if len(mine) < 3 or len(theirs) < 3:
        return False
    if mine == theirs:
        return True
    mine_words = normalize_company_name(current_company or "").split()
    their_words = normalize_company_name(company_name or "").split()
    shorter, longer = sorted((mine_words, their_words), key=len)
    return bool(shorter) and longer[: len(shorter)] == shorter


def application_strategy(
    *,
    model: CandidateModel,
    match: MatchResult,
    analysis: dict[str, Any],
    positioning: dict[str, Any],
    company_name: str,
) -> dict[str, Any]:
    """What to emphasize, what to play down, and how to handle concerns honestly."""
    required = [item for item in match.skill_assessments if item["importance"] == "required"]
    strong = [item for item in required if item["status"] in {"STRONG", "MODERATE"}]
    strong.sort(key=lambda item: -item.get("weight", 1.0))
    boosts = positioning.get("term_boosts") or {}
    emphasize: list[dict[str, Any]] = []
    for item in strong[:6]:
        emphasize.append({"term": item["name"], "why": "Required by the JD; " + item["note"]})
    for term in sorted(boosts, key=lambda name: -boosts[name]):
        if len(emphasize) >= 8:
            break
        assessment_has = any(entry["term"] == term for entry in emphasize)
        evidence = model.skills.get(term)
        concept = model.concepts.get(term)
        genuine = (evidence and evidence.level in {"STRONG", "MODERATE"}) or (
            concept and concept.production
        )
        if genuine and not assessment_has:
            signals = ", ".join(positioning.get("signals", [])[:3])
            emphasize.append(
                {
                    "term": term,
                    "why": f"{company_name} values this ({signals}); you have production evidence.",
                }
            )
    jd_terms = {item["name"] for item in match.skill_assessments}
    de_emphasize = [
        name
        for name, evidence in model.skills.items()
        if name not in jd_terms and evidence.level in {"LISTED", "PROJECT"}
    ][:6]
    concerns: list[dict[str, str]] = []

    def concern(text: str, handle: str) -> None:
        concerns.append({"concern": text, "handle": handle})

    for item in match.skill_assessments:
        name, status, importance = item["name"], item["status"], item["importance"]
        if status == "GAP" and importance == "required":
            concern(
                f"{name} is required and not in your profile.",
                f"Do not claim {name}. If you have genuinely used it, add it to your profile "
                "first; otherwise be ready to discuss how you would ramp up.",
            )
        elif status == "TRANSFERABLE":
            concern(
                f"{name}: {item['note']}",
                f"Lead with your {item.get('via')} work and name it accurately; do not "
                f"present it as {name} experience.",
            )
        elif status == "HISTORICAL":
            concern(
                f"{name} experience is not recent.",
                f"Mention {name} as earlier experience with its dates; do not claim current "
                "production expertise.",
            )
        elif status == "PROJECT" and importance == "required":
            concern(
                f"{name} appears only in your projects.",
                f"Keep {name} under Projects; do not convert it into production experience.",
            )
        elif status == "LISTED" and importance == "required":
            concern(
                f"{name} is listed in your skills without a supporting experience bullet.",
                f"Keep {name} in Skills; prepare a concrete example before the interview.",
            )
    if match.seniority_fit in {"STRETCH", "UNDERQUALIFIED"}:
        concern(
            f"Seniority: {match.component_notes.get('seniority', '')}",
            "Lead with ownership and end-to-end examples that are already on your resume; "
            "never inflate titles or years.",
        )
    if match.location_fit.get("status") in {"RELOCATION", "UNKNOWN"}:
        concerns.append(
            {
                "concern": f"Location: {match.location_fit.get('note')}",
                "handle": "Confirm location and relocation expectations before applying.",
            }
        )
    why = [line for line in match.strong_matches[:3]]
    return {
        "why_this_role": why,
        "emphasize": emphasize,
        "de_emphasize": de_emphasize,
        "concerns": concerns[:8],
        "positioning": {
            "archetype": positioning.get("archetype"),
            "signals": positioning.get("signals", []),
            "sources": positioning.get("sources", []),
        },
        "role_focus": analysis.get("role_focus"),
    }
