"""Candidate <-> job matching with an explanation for every score component."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.discovery.names import is_india_location
from app.discovery.roles import TARGET_CATEGORIES, classify_role
from app.intelligence.facts import LEVEL_SCORE, SkillEvidence, aggregate_skills, functional_title
from app.intelligence.jd_analyzer import PEOPLE_MANAGEMENT_LEVELS, SENIORITY_LEVEL
from app.intelligence.taxonomy import (
    FAMILY_TRANSFER,
    TERMS_BY_NAME,
    WEAK_CONCEPTS,
    expand_implied,
    family_peers,
    kind_of,
)

STATUS_LABELS = {
    "STRONG": "Strong",
    "MODERATE": "Moderate",
    "HISTORICAL": "Historical / rusty",
    "PROJECT": "Project experience",
    "LISTED": "Listed only",
    "TRANSFERABLE": "Transferable",
    "SELF_DESCRIBED": "Self-described",
    "GAP": "Gap",
}
LEVEL_TO_STATUS = {
    "STRONG": "STRONG",
    "MODERATE": "MODERATE",
    "RUSTY": "HISTORICAL",
    "PROJECT": "PROJECT",
    "LISTED": "LISTED",
}
DEFAULT_FIT_WEIGHTS = {
    "skills": 0.50,
    "experience": 0.10,
    "role": 0.15,
    "seniority": 0.10,
    "location": 0.10,
    "domain": 0.03,
    "compensation": 0.02,
}
_LEVEL_NAMES = {1: "junior", 2: "mid", 3: "senior", 4: "staff", 5: "principal"}


@dataclass
class ConceptEvidence:
    production: list[str] = field(default_factory=list)
    project: list[str] = field(default_factory=list)
    self_described: list[str] = field(default_factory=list)
    listed: list[str] = field(default_factory=list)


@dataclass
class CandidateModel:
    years: float | None
    level: int | None
    functional_title: str | None
    titles: list[str]
    employers: list[str]
    skills: dict[str, SkillEvidence]
    concepts: dict[str, ConceptEvidence]
    domains: list[str]
    target_categories: set[str]
    preferred_locations: list[str]
    remote_preference: str | None
    open_to_relocation: bool | None
    compensation_min: float | None
    compensation_currency: str | None
    has_degree: bool
    facts: dict[str, Any]

    def strong_skills(self) -> list[str]:
        return [
            name for name, skill in self.skills.items() if skill.level in {"STRONG", "MODERATE"}
        ]


def _level_from(years: float | None, title: str | None) -> int | None:
    title_level = None
    if title:
        if re.search(r"\b(principal|distinguished)\b", title, re.I):
            title_level = 5
        elif re.search(r"\b(staff|lead|architect)\b", title, re.I):
            title_level = 4
        elif re.search(r"\b(senior|sr\.?)\b", title, re.I):
            title_level = 3
        elif re.search(r"\b(junior|jr\.?|intern|trainee)\b", title, re.I):
            title_level = 1
    years_level = None
    if years is not None:
        years_level = (
            1 if years < 2 else 2 if years < 5 else 3 if years < 8 else 4 if years < 12 else 5
        )
    if title_level is None:
        return years_level
    if years_level is None:
        return title_level
    # A title can raise the level by one step over what the years alone suggest, not more.
    return min(title_level, years_level + 1)


def build_candidate_model(
    candidate: Any, facts: Iterable[Any], today: date, rusty_after_years: float = 3.0
) -> CandidateModel:
    active = [fact for fact in facts if getattr(fact, "active", True)]
    evidence_facts = [fact for fact in active if fact.category != "skill"]
    skills = aggregate_skills(evidence_facts, today, rusty_after_years)
    # User overrides recorded on skill facts take precedence over inferred levels.
    for fact in active:
        if (
            fact.category == "skill"
            and fact.skill_name
            and getattr(fact, "level_source", None) == "user"
        ):
            existing = skills.get(fact.skill_name)
            level = fact.skill_level or "LISTED"
            skills[fact.skill_name] = SkillEvidence(
                name=fact.skill_name,
                level=level,
                score=existing.score if existing else 0.0,
                roles=existing.roles if existing else [],
                production_mentions=existing.production_mentions if existing else 0,
                project_mentions=existing.project_mentions if existing else 0,
                listed=True,
                last_used=existing.last_used if existing else None,
                fact_ids=(existing.fact_ids if existing else []) + [str(fact.id)],
                note=f"Level set by you: {level}.",
            )
    concepts: dict[str, ConceptEvidence] = {}
    for fact in evidence_facts:
        names = expand_implied(list(fact.concepts or []) + list(fact.technologies or []))
        for name in names:
            if kind_of(name) != "concept":
                continue
            entry = concepts.setdefault(name, ConceptEvidence())
            if fact.category in {"experience", "tech_stack"}:
                entry.production.append(str(fact.id))
            elif fact.category in {"project", "project_stack", "project_entry"}:
                entry.project.append(str(fact.id))
            elif fact.category == "summary":
                entry.self_described.append(str(fact.id))
            elif fact.category in {"skill_group", "certification"}:
                entry.listed.append(str(fact.id))
    roles = [fact for fact in active if fact.category == "role"]
    roles.sort(key=lambda fact: (fact.end_date != "present", fact.end_date or ""), reverse=False)
    titles = [fact.role_title for fact in roles if fact.role_title]
    current = next(
        (fact for fact in roles if fact.end_date == "present"), roles[0] if roles else None
    )
    years = getattr(candidate, "years_experience", None)
    title = functional_title(current.role_title) if current else None
    target_categories: set[str] = set()
    for role in getattr(candidate, "target_roles", None) or []:
        category = classify_role(role).category
        if category in TARGET_CATEGORIES:
            target_categories.add(category)
    if not target_categories and title:
        category = classify_role(title).category
        if category in TARGET_CATEGORIES:
            target_categories.add(category)
    return CandidateModel(
        years=years,
        level=_level_from(years, title),
        functional_title=title,
        titles=titles,
        employers=list(dict.fromkeys(fact.employer for fact in roles if fact.employer)),
        skills=skills,
        concepts=concepts,
        domains=list(getattr(candidate, "domains", None) or []),
        target_categories=target_categories,
        preferred_locations=list(getattr(candidate, "preferred_locations", None) or []),
        remote_preference=getattr(candidate, "remote_preference", None),
        open_to_relocation=getattr(candidate, "open_to_relocation", None),
        compensation_min=getattr(candidate, "compensation_min", None),
        compensation_currency=getattr(candidate, "compensation_currency", None),
        has_degree=any(fact.category == "education" for fact in active),
        facts={str(fact.id): fact for fact in active},
    )


@dataclass
class TermAssessment:
    name: str
    kind: str
    status: str
    score: float
    note: str
    fact_ids: list[str]
    via: str | None = None


def assess_term(model: CandidateModel, name: str) -> TermAssessment:
    kind = kind_of(name)
    if kind == "concept":
        evidence = model.concepts.get(name)
        if evidence and len(evidence.production) >= 2:
            return TermAssessment(
                name,
                kind,
                "STRONG",
                1.0,
                f"{len(evidence.production)} production facts show this.",
                evidence.production[:6],
            )
        if evidence and evidence.production:
            return TermAssessment(
                name,
                kind,
                "MODERATE",
                0.8,
                "One production fact shows this.",
                evidence.production[:6],
            )
        if evidence and evidence.project:
            return TermAssessment(
                name,
                kind,
                "PROJECT",
                0.5,
                "Shown in projects, not production work.",
                evidence.project[:6],
            )
        if evidence and evidence.listed:
            return TermAssessment(
                name,
                kind,
                "LISTED",
                0.4,
                "Only listed in your skills or certifications.",
                evidence.listed[:6],
            )
        if evidence and evidence.self_described:
            return TermAssessment(
                name,
                kind,
                "SELF_DESCRIBED",
                0.35,
                "Only stated in your summary.",
                evidence.self_described[:6],
            )
        return TermAssessment(name, kind, "GAP", 0.0, "No evidence in your profile.", [])
    skill = model.skills.get(name)
    if skill is not None:
        status = LEVEL_TO_STATUS.get(skill.level, "LISTED")
        return TermAssessment(
            name, kind, status, LEVEL_SCORE.get(skill.level, 0.4), skill.note, skill.fact_ids[:8]
        )
    best: TermAssessment | None = None
    term = TERMS_BY_NAME.get(name)
    transfer = FAMILY_TRANSFER.get(term.family, 0.0) if term and term.family else 0.0
    for peer in family_peers(name):
        peer_skill = model.skills.get(peer)
        if peer_skill is None or peer_skill.level not in {"STRONG", "MODERATE", "RUSTY"}:
            continue
        score = round(transfer * LEVEL_SCORE[peer_skill.level], 2)
        if best is None or score > best.score:
            best = TermAssessment(
                name,
                kind,
                "TRANSFERABLE",
                score,
                f"You have {peer} ({STATUS_LABELS[LEVEL_TO_STATUS[peer_skill.level]].lower()}), "
                f"not {name}; the experience is transferable but not equivalent.",
                peer_skill.fact_ids[:6],
                via=peer,
            )
    if best is not None:
        return best
    return TermAssessment(name, kind, "GAP", 0.0, "No evidence in your profile.", [])


def _requirement_status(score: float) -> str:
    if score >= 0.85:
        return "MET"
    if score >= 0.5:
        return "PARTIAL"
    if score > 0.15:
        return "WEAK"
    return "GAP"


@dataclass
class MatchResult:
    fit_score: float
    breakdown: dict[str, float]
    seniority_fit: str
    requirement_matches: list[dict[str, Any]]
    skill_assessments: list[dict[str, Any]]
    strong_matches: list[str]
    partial_matches: list[str]
    gaps: list[dict[str, Any]]
    explanation: list[str]
    location_fit: dict[str, Any]
    compensation_fit: dict[str, Any]
    component_notes: dict[str, str]


def _assess_requirement(model: CandidateModel, requirement: dict[str, Any]) -> dict[str, Any]:
    names = list(requirement.get("technologies") or []) + list(requirement.get("concepts") or [])
    strong_names = [name for name in names if name not in WEAK_CONCEPTS]
    names = strong_names or names
    result: dict[str, Any] = {
        "id": requirement["id"],
        "kind": requirement["kind"],
        "text": requirement["text"],
        "mode": requirement.get("mode", "all"),
        "terms": [],
    }
    if names:
        assessments = [assess_term(model, name) for name in names]
        result["terms"] = [
            {"name": item.name, "status": item.status, "score": item.score, "via": item.via}
            for item in assessments
        ]
        if requirement.get("mode") == "any":
            score = max(item.score for item in assessments)
        else:
            score = sum(item.score for item in assessments) / len(assessments)
        result["fact_ids"] = list(
            dict.fromkeys(fact_id for item in assessments for fact_id in item.fact_ids)
        )[:10]
    elif requirement.get("years") is not None:
        needed = float(requirement["years"])
        if model.years is None:
            score, note = 0.5, "Your years of experience are unknown."
        elif model.years >= needed:
            score, note = 1.0, f"You have {model.years:g} years; {needed:g}+ required."
        elif model.years >= needed - 1:
            score, note = 0.7, f"You have {model.years:g} years; {needed:g}+ required (close)."
        else:
            score, note = 0.25, f"You have {model.years:g} years; {needed:g}+ required."
        result["note"] = note
        result["fact_ids"] = []
    elif requirement.get("education"):
        score = 1.0 if model.has_degree else 0.3
        result["note"] = (
            "Degree on your profile." if model.has_degree else "No degree on your profile."
        )
        result["fact_ids"] = []
    else:
        return {**result, "status": "UNASSESSED", "score": None}
    result["score"] = round(score, 2)
    result["status"] = _requirement_status(score)
    return result


def _location_fit(
    model: CandidateModel, location: str, requirements: dict[str, Any]
) -> dict[str, Any]:
    prefs = [pref.strip() for pref in model.preferred_locations if pref.strip()]
    # The job's own location string wins; the analysis only adds what the text says.
    places = [part.strip() for part in re.split(r";|\|| / ", location or "") if part.strip()]
    places = places or requirements.get("places") or []
    if not prefs:
        return {
            "status": "UNKNOWN",
            "score": 60.0,
            "note": "Set your preferred locations in Profile to check location fit.",
        }
    if not places or all(place.lower() in {"unknown", ""} for place in places):
        return {"status": "UNKNOWN", "score": 60.0, "note": "The job location is not published."}
    remote_ok = any("remote" in pref.lower() for pref in prefs)
    wants_india = any(is_india_location(pref) or pref.lower() == "india" for pref in prefs)
    cities = [
        pref
        for pref in prefs
        if "remote" not in pref.lower() and pref.lower() not in {"india", "anywhere"}
    ]
    best = {"status": "MISMATCH", "score": 0.0, "note": ""}
    for place in places:
        lower = place.lower()
        india = is_india_location(place) or "india" in lower or re.search(r"\bind\b", lower)
        remote = "remote" in lower
        if any(city.lower() in lower for city in cities):
            candidate = {
                "status": "MATCH",
                "score": 100.0,
                "note": f"{place} matches your preferred locations.",
            }
        elif remote and (india or re.fullmatch(r"\W*remote\W*", lower)) and remote_ok:
            candidate = {
                "status": "MATCH",
                "score": 95.0,
                "note": f"{place}: remote work you accept.",
            }
        elif india and wants_india:
            candidate = {
                "status": "MATCH",
                "score": 85.0,
                "note": f"{place} is in India but not one of your preferred cities.",
            }
        elif remote and remote_ok and not india:
            candidate = {
                "status": "MISMATCH",
                "score": 10.0,
                "note": f"{place}: remote, but restricted to a region outside your preferences.",
            }
        elif model.open_to_relocation:
            candidate = {
                "status": "RELOCATION",
                "score": 40.0,
                "note": f"{place} would require relocation, which you marked as possible.",
            }
        else:
            candidate = {
                "status": "MISMATCH",
                "score": 0.0,
                "note": f"{place} is outside your preferred locations ({', '.join(prefs)}).",
            }
        if candidate["score"] > best["score"] or not best["note"]:
            best = candidate
    return best


def _compensation_fit(model: CandidateModel, compensation: dict[str, Any] | None) -> dict[str, Any]:
    if not compensation:
        return {
            "status": "UNKNOWN",
            "score": 60.0,
            "note": "The job does not publish compensation.",
        }
    if model.compensation_min is None or not model.compensation_currency:
        return {
            "status": "UNKNOWN",
            "score": 60.0,
            "note": f"Published range {compensation.get('text')}; set your target in Profile.",
        }
    if compensation.get("currency") != model.compensation_currency.upper():
        return {
            "status": "UNKNOWN",
            "score": 60.0,
            "note": f"Published in {compensation.get('currency')}, your target is in "
            f"{model.compensation_currency}.",
        }
    top = float(compensation.get("max") or 0)
    if top >= model.compensation_min:
        return {
            "status": "MATCH",
            "score": 100.0,
            "note": f"Range {compensation.get('text')} meets your target.",
        }
    if top >= 0.85 * model.compensation_min:
        return {
            "status": "CLOSE",
            "score": 70.0,
            "note": f"Range {compensation.get('text')} is slightly below target.",
        }
    return {
        "status": "BELOW",
        "score": 35.0,
        "note": f"Range {compensation.get('text')} is below your target.",
    }


def _seniority(model: CandidateModel, analysis: dict[str, Any]) -> tuple[str, float, str]:
    seniority = analysis.get("seniority") or "unknown"
    jd_level = SENIORITY_LEVEL.get(seniority)
    years_min = analysis.get("years_min")
    years_max = analysis.get("years_max")
    verdicts: list[str] = []
    notes: list[str] = []
    if years_min is not None and model.years is not None:
        if model.years < years_min - 1.5:
            verdicts.append("UNDERQUALIFIED")
            notes.append(f"{years_min:g}+ years required; you have {model.years:g}.")
        elif model.years < years_min:
            verdicts.append("STRETCH")
            notes.append(f"{years_min:g}+ years required; you have {model.years:g} (close).")
        elif years_max is not None and model.years > years_max + 3:
            verdicts.append("OVERQUALIFIED")
            notes.append(
                f"The range is {years_min:g}-{years_max:g} years; you have {model.years:g}."
            )
        else:
            verdicts.append("STRONG_MATCH")
    if jd_level is not None and model.level is not None:
        difference = jd_level - model.level
        label = _LEVEL_NAMES.get(model.level, "unknown")
        if difference >= 2:
            verdicts.append("UNDERQUALIFIED")
            notes.append(f"The title is {seniority}-level; your current level reads as {label}.")
        elif difference == 1:
            verdicts.append("STRETCH")
            notes.append(f"The title is {seniority}-level, one step above your {label} level.")
        elif difference <= -2:
            verdicts.append("OVERQUALIFIED")
            notes.append(f"The title is {seniority}-level; your level reads as {label}.")
        else:
            verdicts.append("STRONG_MATCH")
    if seniority in PEOPLE_MANAGEMENT_LEVELS and not any(
        re.search(r"manager|head|director", title, re.I) for title in model.titles
    ):
        verdicts.append("STRETCH")
        notes.append("This is a people-management title; your history is individual-contributor.")
    if not verdicts:
        return "UNKNOWN", 70.0, "Seniority could not be compared."
    for verdict in ("UNDERQUALIFIED", "STRETCH", "OVERQUALIFIED"):
        if verdict in verdicts:
            score = {"UNDERQUALIFIED": 25.0, "STRETCH": 65.0, "OVERQUALIFIED": 60.0}[verdict]
            return verdict, score, " ".join(notes)
    return "STRONG_MATCH", 100.0, "Seniority matches your experience."


def match_job(
    model: CandidateModel,
    analysis: dict[str, Any],
    *,
    title: str,
    location: str,
    role_category: str,
    role_relevance: float,
    weights: dict[str, float] | None = None,
) -> MatchResult:
    weights = weights or DEFAULT_FIT_WEIGHTS
    notes: dict[str, str] = {}
    requirement_results = [
        _assess_requirement(model, requirement) for requirement in analysis.get("requirements", [])
    ]
    weight_by_kind = {"required": 1.0, "responsibility": 0.6, "preferred": 0.35, "context": 0.2}

    def item_weight(item: dict[str, Any]) -> float:
        # Concept-only items ("drive reliability") are fuzzier evidence than named technologies.
        has_tech = any(kind_of(term["name"]) == "tech" for term in item.get("terms", []))
        return weight_by_kind.get(item["kind"], 0.2) * (1.0 if has_tech else 0.6)

    scored = [
        item for item in requirement_results if item.get("score") is not None and item.get("terms")
    ]
    if scored:
        total = sum(item_weight(item) for item in scored)
        skills = 100.0 * sum(item["score"] * item_weight(item) for item in scored) / total
        met = sum(1 for item in scored if item["kind"] == "required" and item["status"] == "MET")
        required = sum(1 for item in scored if item["kind"] == "required")
        notes["skills"] = (
            f"{met}/{required} required items fully met; weighted evidence across "
            f"{len(scored)} JD items."
            if required
            else f"Weighted evidence across {len(scored)} JD items."
        )
    else:
        skills = 50.0
        notes["skills"] = "The JD lists no recognisable skills; neutral score."

    skill_assessments: list[dict[str, Any]] = []
    requirement_by_id = {item["id"]: item for item in requirement_results}
    for importance, entries in (
        ("required", analysis.get("required_skills", [])),
        ("preferred", analysis.get("preferred_skills", [])),
    ):
        for entry in entries:
            if entry["name"] in WEAK_CONCEPTS:
                continue
            assessment = assess_term(model, entry["name"])
            level = importance
            linked = [
                requirement_by_id[rid]
                for rid in entry.get("requirement_ids", [])
                if rid in requirement_by_id
            ]
            # "Databricks, Snowflake or similar": a missing option is not a gap when another
            # accepted option is met.
            if (
                assessment.status == "GAP"
                and linked
                and all(
                    item.get("mode") == "any" and item.get("status") == "MET" for item in linked
                )
            ):
                level = "alternative"
            skill_assessments.append(
                {
                    "name": entry["name"],
                    "kind": assessment.kind,
                    "importance": level,
                    "weight": entry.get("weight", 1.0),
                    "status": assessment.status,
                    "label": STATUS_LABELS[assessment.status],
                    "score": assessment.score,
                    "note": assessment.note,
                    "via": assessment.via,
                    "fact_ids": assessment.fact_ids,
                    "requirement_ids": entry.get("requirement_ids", []),
                }
            )

    years_min = analysis.get("years_min")
    if years_min is None:
        experience, notes["experience"] = 75.0, "The JD does not state years of experience."
    elif model.years is None:
        experience, notes["experience"] = 60.0, "Your years of experience are unknown."
    elif model.years >= years_min:
        experience = 100.0
        notes["experience"] = f"You have {model.years:g} years; {years_min:g}+ required."
    elif model.years >= years_min - 1:
        experience = 75.0
        notes["experience"] = f"You have {model.years:g} years; {years_min:g}+ required."
    elif model.years >= years_min - 2:
        experience = 50.0
        notes["experience"] = f"You have {model.years:g} years; {years_min:g}+ required."
    else:
        experience = 25.0
        notes["experience"] = f"You have {model.years:g} years; {years_min:g}+ required."

    seniority_fit, seniority, notes["seniority"] = _seniority(model, analysis)

    focus = analysis.get("role_focus")
    if role_category in model.target_categories:
        role = 100.0 * min(1.0, max(role_relevance, 0.5))
        notes["role"] = f"{role_category.replace('_', ' ')} is one of your target role types."
    elif role_category in TARGET_CATEGORIES:
        role = 75.0
        notes["role"] = f"{role_category.replace('_', ' ')} is adjacent to your target roles."
    elif role_category == "adjacent_data":
        role = 45.0
        notes["role"] = "Adjacent data role (analytics/BI/science), not data engineering."
    else:
        role = 25.0
        notes["role"] = "Not one of your target role types."
    if focus == "analytics" and role > 55:
        role = 55.0
        notes["role"] += " The responsibilities are analytics-heavy (dashboards, insights)."
    elif role_category == "platform_infrastructure" and focus != "data_engineering" and role > 60:
        # A platform/infrastructure title only counts fully when the work involves data systems.
        role = 70.0 if focus == "platform" else 50.0
        notes["role"] += (
            " The JD is infrastructure/DevOps work rather than data systems."
            if focus == "platform"
            else " The title says platform/infrastructure, but the JD is not data or platform work."
        )
    elif role_category == "data_engineering" and focus == "software" and role > 70:
        role = 70.0
        notes["role"] += " The responsibilities read as general software work."

    jd_domains = analysis.get("domains") or []
    overlap = [domain for domain in jd_domains if domain in model.domains]
    if overlap:
        domain, notes["domain"] = 100.0, f"Domain experience: {', '.join(overlap)}."
    elif jd_domains:
        domain = 55.0
        notes["domain"] = f"No direct experience in {', '.join(jd_domains[:2])} (learnable)."
    else:
        domain, notes["domain"] = 70.0, "No specific business domain in the JD."

    location_fit = _location_fit(model, location, analysis.get("location_requirements") or {})
    notes["location"] = location_fit["note"]
    compensation_fit = _compensation_fit(model, analysis.get("compensation"))
    notes["compensation"] = compensation_fit["note"]

    breakdown = {
        "skills": round(skills, 1),
        "experience": experience,
        "role": round(role, 1),
        "seniority": seniority,
        "location": location_fit["score"],
        "domain": domain,
        "compensation": compensation_fit["score"],
    }
    total_weight = sum(weights.get(name, 0.0) for name in breakdown) or 1.0
    fit = sum(breakdown[name] * weights.get(name, 0.0) for name in breakdown) / total_weight

    strong = [
        f"{item['name']} \u2014 {item['note']}"
        for item in skill_assessments
        if item["status"] in {"STRONG", "MODERATE"}
    ]
    partial = [
        f"{item['name']} \u2014 {item['label'].lower()}: {item['note']}"
        for item in skill_assessments
        if item["status"] in {"TRANSFERABLE", "HISTORICAL", "PROJECT", "LISTED", "SELF_DESCRIBED"}
    ]
    gaps = [
        {"name": item["name"], "importance": item["importance"], "note": item["note"]}
        for item in skill_assessments
        if item["status"] == "GAP"
    ]
    explanation = _explain(
        model,
        analysis,
        skill_assessments,
        requirement_results,
        gaps,
        seniority_fit,
        location_fit,
        role_category,
        fit,
    )
    return MatchResult(
        fit_score=round(fit, 1),
        breakdown=breakdown,
        seniority_fit=seniority_fit,
        requirement_matches=requirement_results,
        skill_assessments=skill_assessments,
        strong_matches=strong[:12],
        partial_matches=partial[:12],
        gaps=gaps[:12],
        explanation=explanation,
        location_fit=location_fit,
        compensation_fit=compensation_fit,
        component_notes=notes,
    )


def _explain(
    model: CandidateModel,
    analysis: dict[str, Any],
    skills: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    seniority_fit: str,
    location_fit: dict[str, Any],
    role_category: str,
    fit: float,
) -> list[str]:
    lines: list[str] = []
    required = [
        item
        for item in requirements
        if item["kind"] == "required" and item.get("score") is not None
    ]
    met = [item for item in required if item["status"] in {"MET", "PARTIAL"}]
    if required:
        lines.append(f"{len(met)} of {len(required)} required JD items are met or partly met.")

    def in_required_item(name: str) -> bool:
        return any(
            str(rid).startswith("R")
            for item in skills
            if item["name"] == name
            for rid in item.get("requirement_ids", [])
        )

    required_gaps = [
        gap["name"]
        for gap in gaps
        if gap["importance"] == "required" and in_required_item(gap["name"])
    ]
    duty_gaps = [
        gap["name"]
        for gap in gaps
        if gap["importance"] == "required" and not in_required_item(gap["name"])
    ]
    preferred_gaps = [gap["name"] for gap in gaps if gap["importance"] == "preferred"]
    alternative_gaps = [gap["name"] for gap in gaps if gap["importance"] == "alternative"]
    if preferred_gaps and not required_gaps and not duty_gaps:
        lines.append(
            "All gaps are preferred rather than required skills: "
            + ", ".join(preferred_gaps[:5])
            + "."
        )
    if required_gaps:
        lines.append("Required skills without evidence: " + ", ".join(required_gaps[:5]) + ".")
    if duty_gaps:
        lines.append(
            "Duties of the role your resume does not show yet: " + ", ".join(duty_gaps[:5]) + "."
        )
    if alternative_gaps:
        lines.append(
            "Not a gap: the JD accepts alternatives you have instead of "
            + ", ".join(alternative_gaps[:4])
            + "."
        )
    if (
        role_category in model.target_categories
        and analysis.get("role_focus") == "data_engineering"
    ):
        lines.append(
            "The role's primary focus is data engineering, which matches your "
            + (
                f"current {model.functional_title} work."
                if model.functional_title
                else "target roles."
            )
        )
    transferable = [item for item in skills if item["status"] == "TRANSFERABLE"]
    for item in transferable[:2]:
        lines.append(f"{item['name']}: {item['note']}")
    project_only = [
        item["name"]
        for item in skills
        if item["status"] == "PROJECT" and item["importance"] == "required"
    ]
    if project_only:
        lines.append(
            "Project-only (not production) experience for: "
            + ", ".join(project_only[:4])
            + ". Present it as project work."
        )
    historical = [item["name"] for item in skills if item["status"] == "HISTORICAL"]
    if historical:
        lines.append(
            "Not recently used: " + ", ".join(historical[:4]) + ". Do not claim current expertise."
        )
    if seniority_fit in {"STRETCH", "UNDERQUALIFIED", "OVERQUALIFIED"}:
        lines.append(f"Seniority: {seniority_fit.replace('_', ' ').lower()}.")
    if location_fit["status"] in {"MISMATCH", "RELOCATION"}:
        lines.append(f"Location: {location_fit['note']}")
    if fit >= 70 and (required_gaps or preferred_gaps):
        lines.append(
            "Why this is still a strong application: the strongest JD requirements are backed by "
            "production evidence; address the gaps honestly rather than claiming them."
        )
    return lines


def freshness_bucket(age_hours: float | None) -> str:
    if age_hours is None:
        return "unknown"
    if age_hours <= 12:
        return "0-12h"
    if age_hours <= 24:
        return "12-24h"
    if age_hours <= 48:
        return "24-48h"
    if age_hours <= 168:
        return "2-7d"
    return "7d+"


def round_down_years(years: float | None) -> int | None:
    if years is None:
        return None
    return int(math.floor(years + 1e-9))
