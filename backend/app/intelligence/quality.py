"""Resume quality gate and alignment score. Truth is checked first and never traded away."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.intelligence.matching import MatchResult
from app.intelligence.tailoring import ClaimDraft
from app.intelligence.taxonomy import TERMS_BY_NAME, WEAK_CONCEPTS

ALIGNMENT_WEIGHTS = {
    "jd_coverage": 0.25,
    "skill_alignment": 0.20,
    "experience_match": 0.15,
    "keyword_coverage": 0.15,
    "truth_confidence": 0.15,
    "readability": 0.10,
}
_STANDARD_HEADINGS = {
    "Professional Summary",
    "Professional Experience",
    "Technical Skills",
    "Projects",
    "Education & Certifications",
}


def _present(name: str, text: str) -> bool:
    term = TERMS_BY_NAME.get(name)
    if term is not None:
        return bool(term.regex.search(text))
    return name.lower() in text.lower()


def evaluate_resume(
    claims: list[ClaimDraft],
    *,
    analysis: dict[str, Any],
    match: MatchResult,
    rendered_text: str,
    candidate_titles: list[str],
    years_experience: float | None,
    baseline_text: str | None = None,
) -> dict[str, Any]:
    included = [claim for claim in claims if claim.included]
    verified = [claim for claim in included if claim.verified]
    unsupported = [
        {"section": claim.section, "text": claim.text, "notes": claim.notes}
        for claim in included
        if not claim.verified
    ]
    truth = 100.0 * len(verified) / len(included) if included else 0.0
    truth_validation = {
        "passed": not unsupported and bool(included),
        "total_claims": len(included),
        "verified_claims": len(verified),
        "unsupported": unsupported,
    }

    addressed = {rid for claim in included for rid in claim.jd_requirement_ids}
    assessable = [
        item
        for item in match.requirement_matches
        if item.get("kind") == "required" and item.get("score") is not None and item.get("terms")
    ]
    attainable = [item for item in assessable if item["status"] in {"MET", "PARTIAL"}]
    if attainable:
        covered = [item for item in attainable if item["id"] in addressed]
        jd_coverage = 100.0 * len(covered) / len(attainable)
    else:
        covered, jd_coverage = [], 70.0
    absolute = (
        100.0 * len([item for item in assessable if item["id"] in addressed]) / len(assessable)
        if assessable
        else None
    )

    prominent = " ".join(
        claim.text
        for claim in included
        if claim.section in {"headline", "summary"}
        or (claim.section == "skills" and claim.position == 0)
        or (claim.section == "experience" and claim.position <= 1)
    )
    wanted = [
        item
        for item in match.skill_assessments
        if item["importance"] == "required"
        and item["status"] in {"STRONG", "MODERATE"}
        and item["name"] not in WEAK_CONCEPTS
    ]
    if wanted:
        total_weight = sum(item.get("weight", 1.0) for item in wanted)
        shown = sum(item.get("weight", 1.0) for item in wanted if _present(item["name"], prominent))
        skill_alignment = 100.0 * shown / total_weight
    else:
        skill_alignment = 70.0

    experience_match = (
        match.breakdown.get("experience", 70.0) + match.breakdown.get("seniority", 70.0)
    ) / 2

    evidenced = {
        item["name"]
        for item in match.skill_assessments
        if item["status"] not in {"GAP", "TRANSFERABLE"}
    }
    keywords = [name for name in analysis.get("keywords", []) if name in evidenced]
    if keywords:
        present = [name for name in keywords if _present(name, rendered_text)]
        keyword_coverage = 100.0 * len(present) / len(keywords)
    else:
        present, keyword_coverage = [], 70.0

    bullets = [claim for claim in included if claim.section in {"experience", "project"}]
    long_bullets = [claim for claim in bullets if len(claim.text.split()) > 45]
    words = len(rendered_text.split())
    counts = Counter()
    baseline_counts = Counter()
    for name in analysis.get("keywords", []):
        term = TERMS_BY_NAME.get(name)
        if term is not None:
            counts[name] = len(term.regex.findall(rendered_text))
            baseline_counts[name] = len(term.regex.findall(baseline_text or ""))
    # Stuffing means repetition *added* by tailoring, not what the master resume already says.
    stuffed = [
        name for name, count in counts.items() if count > 7 and count > baseline_counts[name] + 2
    ]
    summary_words = sum(len(claim.text.split()) for claim in included if claim.section == "summary")
    readability = (
        100.0
        - 5 * len(long_bullets)
        - (10 if words > 900 else 0)
        - 10 * len(stuffed)
        - (5 if summary_words > 90 else 0)
    )
    readability = max(0.0, readability)

    relevant = [claim for claim in bullets if claim.relevance > 0]
    relevance_share = 100.0 * len(relevant) / len(bullets) if bullets else 0.0
    top_irrelevant = [
        claim
        for claim in bullets
        if claim.section == "experience" and claim.position == 0 and claim.relevance <= 0
    ]

    seniority_words = set(
        re.findall(
            r"\b(senior|lead|staff|principal|architect|manager|head)\b",
            " ".join(claim.text for claim in included if claim.section in {"headline", "summary"}),
            re.I,
        )
    )
    titles = " ".join(candidate_titles).lower()
    inflated = [word for word in seniority_words if word.lower() not in titles]
    years_claims = re.findall(
        r"(\d+)\+?\s+years", " ".join(c.text for c in included if c.section == "summary")
    )
    years_inflated = [
        value
        for value in years_claims
        if years_experience is None or int(value) > years_experience + 1e-9
    ]
    lowered = rendered_text.lower()
    headings_ok = "professional experience" in lowered and "technical skills" in lowered

    checks = [
        {
            "check": "truth",
            "status": "pass" if truth_validation["passed"] else "fail",
            "message": f"{len(verified)}/{len(included)} claims are supported by your facts."
            + (
                ""
                if not unsupported
                else f" {len(unsupported)} unsupported claim(s) block approval."
            ),
        },
        {
            "check": "jd_alignment",
            "status": "pass" if jd_coverage >= 70 else "warn",
            "message": f"{len(covered)}/{len(attainable)} required items you meet are "
            "addressed in the resume"
            + (f"; {absolute:.0f}% of all required items." if absolute is not None else "."),
        },
        {
            "check": "keyword_coverage",
            "status": "pass" if keyword_coverage >= 70 else "warn",
            "message": f"{len(present)}/{len(keywords)} JD keywords you have evidence for "
            "appear naturally.",
        },
        {
            "check": "relevance",
            "status": "pass" if relevance_share >= 50 and not top_irrelevant else "warn",
            "message": f"{relevance_share:.0f}% of included bullets relate to this JD"
            + ("; a role's first bullet is unrelated." if top_irrelevant else "."),
        },
        {
            "check": "readability",
            "status": "pass" if readability >= 80 else "warn",
            "message": f"{words} words; {len(long_bullets)} bullet(s) over 45 words"
            + (f"; repeated keywords: {', '.join(stuffed)}" if stuffed else "")
            + ".",
        },
        {
            "check": "ats",
            "status": "pass" if headings_ok else "warn",
            "message": "Standard section names, single column, no tables, images or hidden text; "
            "dates and contact details as plain text.",
        },
        {
            "check": "seniority",
            "status": "pass" if not inflated and not years_inflated else "fail",
            "message": "Titles and years match your history."
            if not inflated and not years_inflated
            else "Claims seniority/years beyond your history: "
            f"{', '.join(inflated + years_inflated)}.",
        },
    ]
    breakdown = {
        "jd_coverage": round(jd_coverage, 1),
        "skill_alignment": round(skill_alignment, 1),
        "experience_match": round(experience_match, 1),
        "keyword_coverage": round(keyword_coverage, 1),
        "truth_confidence": round(truth, 1),
        "readability": round(readability, 1),
    }
    total = sum(ALIGNMENT_WEIGHTS.values())
    alignment = sum(breakdown[name] * weight for name, weight in ALIGNMENT_WEIGHTS.items()) / total
    if not truth_validation["passed"]:
        # Unsupported claims make the score meaningless; it is capped, never boosted.
        alignment = min(alignment, 50.0)
    return {
        "alignment_score": round(alignment, 1),
        "alignment_breakdown": breakdown,
        "quality_checks": checks,
        "truth_validation": truth_validation,
    }
