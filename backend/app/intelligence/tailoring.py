"""JD- and company-specific resume tailoring with per-claim provenance.

The engine only *selects, orders and emphasizes* candidate facts, and composes the headline and
one summary sentence from facts whose evidence it cites. Bullets keep the master wording unless
an optional rewrite passes verification. Nothing is added that the facts do not support.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.intelligence.matching import CandidateModel, MatchResult, round_down_years
from app.intelligence.taxonomy import (
    TERMS_BY_NAME,
    WEAK_CONCEPTS,
    expand_implied,
    find_terms,
    kind_of,
)
from app.intelligence.verification import verify_claim

GENERATOR = "unjob-tailor-2"
SECTION_TITLES = {
    "summary": "Professional Summary",
    "experience": "Professional Experience",
    "skills": "Technical Skills",
    "projects": "Projects",
    "education": "Education & Certifications",
}
CONCEPT_PHRASES = {
    "Data pipelines / ETL": "data pipelines",
    "Data ingestion": "data ingestion platforms",
    "Streaming / real-time processing": "streaming and event-driven systems",
    "Change data capture (CDC)": "CDC-based ingestion",
    "Distributed systems": "distributed data systems",
    "Performance tuning": "performance tuning",
    "Reliability / fault tolerance": "reliability engineering",
    "Low-latency serving": "low-latency data serving",
    "Caching": "caching for data APIs",
    "Data migration": "production migrations",
    "Partitioning & storage layout": "storage layout optimization",
    "Data quality": "data quality and validation",
    "Scalability / high throughput": "high-throughput systems",
    "Data platform engineering": "data platform engineering",
    "Batch processing": "batch processing",
    "Workflow orchestration": "workflow orchestration",
    "Rate limiting & backpressure": "backpressure and rate limiting",
    "Data modeling": "data modeling",
    "Data warehousing": "data warehousing",
    "Data lake / lakehouse": "lakehouse pipelines",
    "Observability & monitoring": "observability",
    "System design & architecture": "system design",
    "Cloud data platforms": "cloud data platforms",
}
_TYPO_SPACING = re.compile(r"\)(?=[A-Za-z])")


@dataclass
class ClaimDraft:
    section: str
    entry_key: str | None
    position: int
    text: str
    original_text: str | None
    source_fact_ids: list[str]
    jd_requirement_ids: list[str]
    reason: str
    relevance: float
    kind: str = "verbatim"
    label: str | None = None
    included: bool = True
    rewritten_by: str | None = None
    verified: bool = False
    notes: list[str] = field(default_factory=list)
    allowed_numbers: list[str] = field(default_factory=list)


@dataclass
class EntryBase:
    key: str
    kind: str
    head: Any
    bullets: list[Any] = field(default_factory=list)
    stack: Any | None = None


@dataclass
class ResumeBase:
    summary: list[Any]
    experiences: list[EntryBase]
    projects: list[EntryBase]
    skill_groups: list[Any]
    education: list[Any]
    certifications: list[Any]
    section_order: list[str]


def _ref(fact: Any, key: str, default: Any = None) -> Any:
    return (getattr(fact, "source_ref", None) or {}).get(key, default)


def build_resume_base(facts: list[Any], section_order: list[str] | None = None) -> ResumeBase:
    active = [fact for fact in facts if getattr(fact, "active", True)]
    ordered = sorted(active, key=lambda fact: getattr(fact, "position", 0))
    experiences: dict[int, EntryBase] = {}
    projects: dict[int, EntryBase] = {}
    user_bullets: list[Any] = []
    summary, skill_groups, education, certifications = [], [], [], []
    for fact in ordered:
        category = fact.category
        if category == "summary":
            summary.append(fact)
        elif category == "role":
            index = _ref(fact, "entry", len(experiences))
            experiences.setdefault(index, EntryBase(f"exp-{index}", "experience", fact)).head = fact
        elif category in {"experience", "tech_stack"}:
            index = _ref(fact, "entry")
            if index is None:
                user_bullets.append(fact)
                continue
            entry = experiences.setdefault(index, EntryBase(f"exp-{index}", "experience", None))
            if category == "tech_stack":
                entry.stack = fact
            else:
                entry.bullets.append(fact)
        elif category == "project_entry":
            index = _ref(fact, "entry", len(projects))
            projects.setdefault(index, EntryBase(f"proj-{index}", "project", fact)).head = fact
        elif category in {"project", "project_stack"}:
            index = _ref(fact, "entry")
            if index is None:
                continue
            entry = projects.setdefault(index, EntryBase(f"proj-{index}", "project", None))
            if category == "project_stack":
                entry.stack = fact
            else:
                entry.bullets.append(fact)
        elif category == "skill_group":
            skill_groups.append(fact)
        elif category == "education":
            education.append(fact)
        elif category == "certification":
            certifications.append(fact)
    # Facts the user added later attach to the role with the same employer.
    for fact in user_bullets:
        for entry in experiences.values():
            head = entry.head
            if (
                head is not None
                and fact.employer
                and head.employer
                and (fact.employer.lower() == head.employer.lower())
            ):
                entry.bullets.append(fact)
                break
    order = [name for name in (section_order or []) if name in SECTION_TITLES]
    for name in ("summary", "experience", "skills", "projects", "education"):
        if name not in order:
            order.append(name)
    return ResumeBase(
        summary=summary,
        experiences=[experiences[key] for key in sorted(experiences) if experiences[key].head],
        projects=[projects[key] for key in sorted(projects) if projects[key].head],
        skill_groups=skill_groups,
        education=education,
        certifications=certifications,
        section_order=order,
    )


def term_weights(
    analysis: dict[str, Any], positioning: dict[str, Any] | None = None
) -> dict[str, float]:
    weights: dict[str, float] = {}
    for requirement in analysis.get("requirements", []):
        importance = float(requirement.get("importance", 0.5))
        for name in list(requirement.get("technologies") or []) + list(
            requirement.get("concepts") or []
        ):
            factor = 0.4 if name in WEAK_CONCEPTS else 1.0
            weights[name] = weights.get(name, 0.0) + importance * factor
    for name, boost in ((positioning or {}).get("term_boosts") or {}).items():
        weights[name] = weights.get(name, 0.0) + float(boost)
    # Diminishing returns so one term repeated ten times does not dominate.
    return {name: round(math.log1p(value) * 1.5, 3) for name, value in weights.items()}


def fact_terms(fact: Any) -> set[str]:
    names = set(getattr(fact, "technologies", None) or []) | set(
        getattr(fact, "concepts", None) or []
    )
    label = getattr(fact, "label", None)
    if label:
        names |= {hit.name for hit in find_terms(label)}
    return expand_implied(names)


def _requirement_ids(terms: set[str], analysis: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for requirement in analysis.get("requirements", []):
        names = set(requirement.get("technologies") or []) | set(requirement.get("concepts") or [])
        names -= WEAK_CONCEPTS
        if names & terms:
            ids.append(requirement["id"])
    return ids


def _relevance(fact: Any, weights: dict[str, float]) -> tuple[float, list[str]]:
    terms = fact_terms(fact)
    matched = sorted((name for name in terms if name in weights), key=lambda name: -weights[name])
    score = sum(weights[name] for name in matched)
    if getattr(fact, "metrics", None):
        score += 0.25
    return round(score, 3), matched


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _split_top_level(raw: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in raw:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _canonical_display(item: str) -> str:
    """Fix the casing of a known tool name ("Azure Data factory" -> "Azure Data Factory")."""
    stripped = item.strip()
    for hit in find_terms(stripped, kinds=("tech",)):
        if hit.text.lower() == stripped.lower() and hit.name.lower() == stripped.lower():
            return hit.name
    return item


@dataclass
class TailoredResume:
    claims: list[ClaimDraft]
    content: dict[str, Any]
    changes: dict[str, Any]
    weights: dict[str, float]
    positioning: dict[str, Any]


def tailor_resume(
    *,
    candidate: Any,
    model: CandidateModel,
    base: ResumeBase,
    facts_by_id: dict[str, Any],
    analysis: dict[str, Any],
    match: MatchResult,
    positioning: dict[str, Any],
    jd_text: str,
    max_bullets_current: int = 5,
    max_bullets_other: int = 4,
) -> TailoredResume:
    weights = term_weights(analysis, positioning)
    claims: list[ClaimDraft] = []
    changes: dict[str, Any] = {
        "added_emphasis": [],
        "reordered": [],
        "de_emphasized": [],
        "skills_reordered": [],
        "terminology": [],
        "rewrites": [],
    }
    content: dict[str, Any] = {
        "header": {
            "name": getattr(candidate, "full_name", None),
            "contact": [
                value
                for value in (
                    getattr(candidate, "email", None),
                    getattr(candidate, "phone", None),
                    *(getattr(candidate, "links", None) or []),
                )
                if value
            ],
        },
        "sections": [],
    }

    def add(claim: ClaimDraft) -> int:
        claims.append(claim)
        return len(claims) - 1

    role_facts = [entry.head for entry in base.experiences if entry.head is not None]
    role_ids = [str(fact.id) for fact in role_facts]
    years = round_down_years(model.years)
    title = model.functional_title

    # Technologies and concepts worth surfacing: JD-relevant and genuinely evidenced.
    evidenced_techs = [
        name
        for name, skill in model.skills.items()
        if skill.level in {"STRONG", "MODERATE"} and TERMS_BY_NAME.get(name)
    ]
    jd_techs = sorted(
        (name for name in evidenced_techs if weights.get(name)),
        key=lambda name: (-weights[name], -model.skills[name].score),
    )
    # Generic parents ("Spark" over "PySpark") read better when both are evidenced.
    surfaced: list[str] = []
    for name in jd_techs:
        if any(name in expand_implied([other]) and name != other for other in surfaced):
            continue
        surfaced = [
            other for other in surfaced if other not in expand_implied([name]) or other == name
        ]
        surfaced.append(name)
    if len(surfaced) < 3:
        for name in sorted(evidenced_techs, key=lambda name: -model.skills[name].score):
            if name not in surfaced and model.skills[name].level == "STRONG":
                surfaced.append(name)
            if len(surfaced) >= 4:
                break
    surfaced = surfaced[:4]
    concept_candidates = [
        name
        for name, evidence in model.concepts.items()
        if evidence.production and name in CONCEPT_PHRASES and weights.get(name)
    ]
    concept_candidates.sort(key=lambda name: -weights[name])
    phrases = concept_candidates[:3]

    # Headline
    if title and surfaced:
        headline = f"{title} | " + " \u00b7 ".join(surfaced)
        sources = role_ids + [
            fact_id for name in surfaced for fact_id in model.skills[name].fact_ids[:3]
        ]
        content["headline"] = add(
            ClaimDraft(
                "headline",
                None,
                0,
                headline,
                None,
                list(dict.fromkeys(sources)),
                _requirement_ids(set(surfaced), analysis),
                "Current functional title with the JD's most important technologies you have "
                "production evidence for.",
                1.0,
                kind="generated",
            )
        )
        changes["headline_added"] = headline
        for name in surfaced:
            changes["added_emphasis"].append({"term": name, "where": "headline"})

    # Summary
    summary_claims: list[int] = []
    if title and years:
        sources = list(role_ids)
        for name in phrases:
            sources.extend(model.concepts[name].production[:3])
        for name in surfaced:
            sources.extend(model.skills[name].fact_ids[:3])
        # Total tenure and the areas of work stay separate: "5+ years of experience in X"
        # would claim five years of X specifically.
        sentence = f"{title} with {years}+ years of experience"
        if phrases:
            sentence += ", including production work in " + _join(
                [CONCEPT_PHRASES[name] for name in phrases]
            )
            if surfaced:
                sentence += " using " + _join(surfaced)
        elif surfaced:
            sentence += ", working with " + _join(surfaced)
        sentence += "."
        summary_claims.append(
            add(
                ClaimDraft(
                    "summary",
                    None,
                    0,
                    sentence,
                    None,
                    list(dict.fromkeys(sources)),
                    _requirement_ids(set(phrases) | set(surfaced), analysis),
                    f"Composed from your role history ({years}+ years from employment dates) and "
                    "the JD-relevant skills with production evidence.",
                    1.0,
                    kind="generated",
                    allowed_numbers=[f"{years}+", str(years)],
                )
            )
        )
        for name in phrases:
            changes["added_emphasis"].append({"term": name, "where": "summary"})
    ranked_summary = sorted(
        ((fact, *_relevance(fact, weights)) for fact in base.summary),
        key=lambda item: -item[1],
    )
    keep = []
    if ranked_summary:
        top = ranked_summary[0][1]
        keep = [item for item in ranked_summary if item[1] >= 0.5 * top and item[1] > 0][:2] or [
            ranked_summary[0]
        ]
        if summary_claims:
            keep = keep[:1]
    for position, (fact, score, matched) in enumerate(
        sorted(keep, key=lambda item: base.summary.index(item[0])), start=len(summary_claims)
    ):
        summary_claims.append(
            add(
                ClaimDraft(
                    "summary",
                    None,
                    position,
                    fact.statement,
                    fact.statement,
                    [str(fact.id)],
                    _requirement_ids(fact_terms(fact), analysis),
                    "Your own summary sentence most relevant to this JD"
                    + (f" ({', '.join(matched[:3])})." if matched else "."),
                    score,
                )
            )
        )
    for fact in base.summary:
        if fact not in [item[0] for item in keep]:
            changes["de_emphasized"].append(
                {
                    "section": "summary",
                    "text": fact.statement[:120],
                    "reason": "Less relevant to this JD; omitted.",
                }
            )
    master_summary = [fact.statement for fact in base.summary]
    tailored_summary = [claims[index].text for index in summary_claims]
    changes["summary_changed"] = master_summary != tailored_summary
    changes["summary_before"] = master_summary
    changes["summary_after"] = tailored_summary
    content["summary"] = summary_claims

    # Experience
    experience_entries: list[dict[str, Any]] = []
    best_experience = 0.0
    for entry_index, entry in enumerate(base.experiences):
        head = entry.head
        head_claim = add(
            ClaimDraft(
                "role",
                entry.key,
                0,
                head.statement,
                head.statement,
                [str(head.id)],
                [],
                "Employment history is kept as written.",
                0.0,
            )
        )
        scored = [(fact, *_relevance(fact, weights)) for fact in entry.bullets]
        ordered = sorted(enumerate(scored), key=lambda item: (-item[1][1], item[0]))
        limit = max_bullets_current if entry_index == 0 else max_bullets_other
        bullet_claims: list[int] = []
        for new_position, (old_position, (fact, score, matched)) in enumerate(ordered):
            best_experience = max(best_experience, score)
            include = new_position < limit or score > 0 and new_position < limit + 1
            text = _TYPO_SPACING.sub(") ", fact.statement)
            reason = (
                f"Matches JD terms: {', '.join(matched[:4])}."
                if matched
                else "No JD-specific terms; kept for completeness."
                if include
                else "Low relevance to this JD."
            )
            bullet_claims.append(
                add(
                    ClaimDraft(
                        "experience",
                        entry.key,
                        new_position,
                        text,
                        fact.statement,
                        [str(fact.id)],
                        _requirement_ids(fact_terms(fact), analysis),
                        reason,
                        score,
                        label=getattr(fact, "label", None),
                        included=include,
                    )
                )
            )
            if new_position != old_position:
                changes["reordered"].append(
                    {
                        "section": "experience",
                        "entry": head.employer or head.statement[:60],
                        "label": getattr(fact, "label", None) or fact.statement[:60],
                        "from": old_position + 1,
                        "to": new_position + 1,
                        "direction": "up" if new_position < old_position else "down",
                    }
                )
            if not include:
                changes["de_emphasized"].append(
                    {
                        "section": "experience",
                        "text": getattr(fact, "label", None) or fact.statement[:80],
                        "reason": "Omitted: lowest relevance to this JD (you can re-include it).",
                    }
                )
        stack_claim = None
        if entry.stack is not None:
            stack_claim = add(_stack_claim(entry.stack, entry.key, weights, analysis, changes))
        experience_entries.append(
            {
                "key": entry.key,
                "head": head_claim,
                "bullets": bullet_claims,
                "stack": stack_claim,
                "employer": head.employer,
                "title": head.role_title,
                "location": _ref(head, "location"),
                "dates": _ref(head, "date_text"),
            }
        )

    # Projects
    project_entries: list[dict[str, Any]] = []
    project_scores = []
    for entry in base.projects:
        scores = [
            _relevance(fact, weights)[0]
            for fact in entry.bullets + ([entry.stack] if entry.stack else [])
        ]
        project_scores.append((entry, max(scores) if scores else 0.0))
    ordered_projects = sorted(enumerate(project_scores), key=lambda item: (-item[1][1], item[0]))
    best_project = max((score for _entry, score in project_scores), default=0.0)
    for new_position, (old_position, (entry, score)) in enumerate(ordered_projects):
        head = entry.head
        include = new_position < 3
        head_claim = add(
            ClaimDraft(
                "project_entry",
                entry.key,
                new_position,
                head.statement,
                head.statement,
                [str(head.id)],
                _requirement_ids(fact_terms(head), analysis),
                "Project kept as written.",
                score,
                included=include,
            )
        )
        bullet_claims = []
        for position, fact in enumerate(entry.bullets):
            bullet_score, matched = _relevance(fact, weights)
            bullet_claims.append(
                add(
                    ClaimDraft(
                        "project",
                        entry.key,
                        position,
                        _TYPO_SPACING.sub(") ", fact.statement),
                        fact.statement,
                        [str(fact.id)],
                        _requirement_ids(fact_terms(fact), analysis),
                        (
                            f"Project evidence for: {', '.join(matched[:4])}."
                            if matched
                            else "Project kept as written."
                        ),
                        bullet_score,
                        label=getattr(fact, "label", None),
                        included=include,
                    )
                )
            )
        stack_claim = None
        if entry.stack is not None:
            stack_claim = add(
                _stack_claim(
                    entry.stack,
                    entry.key,
                    weights,
                    analysis,
                    changes,
                    section="project_stack",
                    included=include,
                )
            )
        if new_position != old_position:
            changes["reordered"].append(
                {
                    "section": "projects",
                    "entry": getattr(head, "label", None) or head.statement[:60],
                    "label": getattr(head, "label", None) or head.statement[:60],
                    "from": old_position + 1,
                    "to": new_position + 1,
                    "direction": "up" if new_position < old_position else "down",
                }
            )
        project_entries.append(
            {
                "key": entry.key,
                "head": head_claim,
                "bullets": bullet_claims,
                "stack": stack_claim,
                "links": _ref(head, "links"),
            }
        )

    # Skills
    group_entries: list[tuple[float, int, int]] = []
    for original_index, fact in enumerate(base.skill_groups):
        category = getattr(fact, "label", None) or _ref(fact, "category", "Skills")
        raw_items = fact.statement.split(":", 1)[1] if ":" in fact.statement else fact.statement
        items = _split_top_level(raw_items)
        scored_items = []
        for position, item in enumerate(items):
            names = expand_implied(hit.name for hit in find_terms(item))
            item_weight = max((weights.get(name, 0.0) for name in names), default=0.0)
            scored_items.append((item_weight, position, _canonical_display(item)))
            if _canonical_display(item) != item:
                changes["terminology"].append({"before": item, "after": _canonical_display(item)})
        ordered_items = [
            item for _w, _p, item in sorted(scored_items, key=lambda value: (-value[0], value[1]))
        ]
        if ordered_items != [_canonical_display(item) for item in items]:
            changes["skills_reordered"].append(
                {"group": category, "before": items, "after": ordered_items}
            )
        group_score = sum(weight for weight, _p, _i in scored_items)
        claim_index = add(
            ClaimDraft(
                "skills",
                category,
                original_index,
                f"{category}: " + ", ".join(ordered_items),
                fact.statement,
                [str(fact.id)],
                _requirement_ids(fact_terms(fact), analysis),
                "Same skills, ordered by this JD's priorities."
                if group_score
                else "Same skills; none are specific to this JD.",
                group_score,
            )
        )
        group_entries.append((group_score, original_index, claim_index))
    ordered_groups = sorted(group_entries, key=lambda value: (-value[0], value[1]))
    for new_position, (_score, _original_index, claim_index) in enumerate(ordered_groups):
        claims[claim_index].position = new_position

    # Education and certifications
    education_claims = [
        add(
            ClaimDraft(
                "education",
                None,
                position,
                fact.statement,
                fact.statement,
                [str(fact.id)],
                [],
                "Kept as written.",
                0.0,
            )
        )
        for position, fact in enumerate(base.education)
    ]
    ranked_certs = sorted(
        enumerate(base.certifications),
        key=lambda item: (-_relevance(item[1], weights)[0], item[0]),
    )
    certification_claims = [
        add(
            ClaimDraft(
                "certification",
                None,
                position,
                fact.statement,
                fact.statement,
                [str(fact.id)],
                _requirement_ids(fact_terms(fact), analysis),
                "Ordered by relevance to this JD.",
                _relevance(fact, weights)[0],
            )
        )
        for position, (_old, fact) in enumerate(ranked_certs)
    ]

    order = list(base.section_order)
    experience_terms = {
        name
        for entry in base.experiences
        for fact in entry.bullets + ([entry.stack] if entry.stack else [])
        for name in fact_terms(fact)
    }
    required_terms = {
        item["name"] for item in match.skill_assessments if item["importance"] == "required"
    }
    project_only = sorted(
        {
            name
            for entry in base.projects
            for fact in entry.bullets + ([entry.stack] if entry.stack else [])
            for name in fact_terms(fact)
            if name in required_terms and name not in experience_terms and name not in WEAK_CONCEPTS
        }
    )
    if (
        "projects" in order
        and "skills" in order
        and len(project_only) >= 2
        and best_project > 0
        and order.index("projects") > order.index("skills")
    ):
        order.remove("projects")
        order.insert(order.index("skills"), "projects")
        changes["section_order"] = {
            "before": base.section_order,
            "after": order,
            "reason": "These JD requirements are demonstrated only in your projects: "
            + ", ".join(project_only[:5])
            + ".",
        }
    content["section_order"] = order
    content["experience"] = experience_entries
    content["projects"] = project_entries
    content["skills"] = [claim_index for _score, _original, claim_index in ordered_groups]
    content["education"] = education_claims
    content["certifications"] = certification_claims
    if ordered_groups:
        first = claims[ordered_groups[0][2]].text.split(":", 1)[-1]
        for item in [value.strip() for value in first.split(",")[:2]]:
            changes["added_emphasis"].append({"term": item, "where": "skills (first group)"})

    # Every claim is verified against the facts it cites.
    titles = model.titles
    for claim in claims:
        sources = [
            facts_by_id[fact_id] for fact_id in claim.source_fact_ids if fact_id in facts_by_id
        ]
        result = verify_claim(
            claim.text,
            sources,
            kind=claim.kind,
            allowed_numbers=claim.allowed_numbers,
            candidate_titles=titles,
            jd_text=jd_text if claim.kind == "generated" else None,
        )
        claim.verified = result.ok
        claim.notes = result.notes
    changes["unsupported_claims"] = sum(
        1 for claim in claims if claim.included and not claim.verified
    )
    return TailoredResume(claims, content, changes, weights, positioning)


def _stack_claim(
    fact: Any,
    entry_key: str,
    weights: dict[str, float],
    analysis: dict[str, Any],
    changes: dict[str, Any],
    *,
    section: str = "stack",
    included: bool = True,
) -> ClaimDraft:
    items = _ref(fact, "items") or _split_top_level(fact.statement)
    scored = []
    for position, item in enumerate(items):
        names = expand_implied(hit.name for hit in find_terms(item))
        weight = max(
            (weights.get(name, 0.0) for name in names if kind_of(name) == "tech"), default=0.0
        )
        display = _canonical_display(item)
        if display != item:
            changes["terminology"].append({"before": item, "after": display})
        scored.append((weight, position, display))
    ordered = [item for _w, _p, item in sorted(scored, key=lambda value: (-value[0], value[1]))]
    label = getattr(fact, "label", None) or "Tech Stack"
    return ClaimDraft(
        section,
        entry_key,
        99,
        f"{label}: " + ", ".join(ordered),
        fact.statement,
        [str(fact.id)],
        _requirement_ids(fact_terms(fact), analysis),
        "Same tools, JD-relevant ones first."
        if ordered != [display for _w, _p, display in scored]
        else "Tools as listed.",
        max((weight for weight, _p, _i in scored), default=0.0),
        label=label,
        included=included,
    )


def master_resume(*, candidate: Any, base: ResumeBase) -> TailoredResume:
    """The master resume in its original order, for comparison with tailored variants."""
    claims: list[ClaimDraft] = []

    def add(claim: ClaimDraft) -> int:
        claims.append(claim)
        return len(claims) - 1

    content: dict[str, Any] = {
        "header": {
            "name": getattr(candidate, "full_name", None),
            "contact": [
                value
                for value in (
                    getattr(candidate, "email", None),
                    getattr(candidate, "phone", None),
                    *(getattr(candidate, "links", None) or []),
                )
                if value
            ],
        },
        "section_order": list(base.section_order),
        "summary": [
            add(ClaimDraft("summary", None, i, f.statement, f.statement, [str(f.id)], [], "", 0.0))
            for i, f in enumerate(base.summary)
        ],
        "experience": [],
        "projects": [],
    }
    for entry in base.experiences:
        head = entry.head
        content["experience"].append(
            {
                "key": entry.key,
                "head": add(
                    ClaimDraft(
                        "role",
                        entry.key,
                        0,
                        head.statement,
                        head.statement,
                        [str(head.id)],
                        [],
                        "",
                        0.0,
                    )
                ),
                "bullets": [
                    add(
                        ClaimDraft(
                            "experience",
                            entry.key,
                            i,
                            f.statement,
                            f.statement,
                            [str(f.id)],
                            [],
                            "",
                            0.0,
                            label=getattr(f, "label", None),
                        )
                    )
                    for i, f in enumerate(entry.bullets)
                ],
                "stack": add(
                    ClaimDraft(
                        "stack",
                        entry.key,
                        99,
                        f"{entry.stack.label or 'Tech Stack'}: {entry.stack.statement}",
                        entry.stack.statement,
                        [str(entry.stack.id)],
                        [],
                        "",
                        0.0,
                    )
                )
                if entry.stack
                else None,
                "employer": head.employer,
                "title": head.role_title,
                "location": _ref(head, "location"),
                "dates": _ref(head, "date_text"),
            }
        )
    for entry in base.projects:
        head = entry.head
        content["projects"].append(
            {
                "key": entry.key,
                "head": add(
                    ClaimDraft(
                        "project_entry",
                        entry.key,
                        0,
                        head.statement,
                        head.statement,
                        [str(head.id)],
                        [],
                        "",
                        0.0,
                    )
                ),
                "bullets": [
                    add(
                        ClaimDraft(
                            "project",
                            entry.key,
                            i,
                            f.statement,
                            f.statement,
                            [str(f.id)],
                            [],
                            "",
                            0.0,
                            label=getattr(f, "label", None),
                        )
                    )
                    for i, f in enumerate(entry.bullets)
                ],
                "stack": add(
                    ClaimDraft(
                        "project_stack",
                        entry.key,
                        99,
                        f"{entry.stack.label or 'Stack'}: {entry.stack.statement}",
                        entry.stack.statement,
                        [str(entry.stack.id)],
                        [],
                        "",
                        0.0,
                    )
                )
                if entry.stack
                else None,
                "links": _ref(head, "links"),
            }
        )
    content["skills"] = [
        add(ClaimDraft("skills", None, i, f.statement, f.statement, [str(f.id)], [], "", 0.0))
        for i, f in enumerate(base.skill_groups)
    ]
    content["education"] = [
        add(ClaimDraft("education", None, i, f.statement, f.statement, [str(f.id)], [], "", 0.0))
        for i, f in enumerate(base.education)
    ]
    content["certifications"] = [
        add(
            ClaimDraft("certification", None, i, f.statement, f.statement, [str(f.id)], [], "", 0.0)
        )
        for i, f in enumerate(base.certifications)
    ]
    for claim in claims:
        claim.verified = True
    return TailoredResume(claims, content, {}, {}, {})
