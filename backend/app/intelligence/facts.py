"""Candidate facts from a parsed master resume, and evidence-based skill levels.

Statements are copied verbatim from the resume. Inferred values (skill levels, experience
type) are labelled as inferred so the user can confirm or correct them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from app.intelligence.resume_parser import ParsedResume, years_of_experience
from app.intelligence.taxonomy import (
    TERMS_BY_NAME,
    expand_implied,
    find_domains,
    find_metrics,
    find_terms,
    kind_of,
)

# Experience types, strongest first.
PRODUCTION, PROJECT, ACADEMIC, CERTIFICATION, LISTED, SELF_DESCRIBED = (
    "PRODUCTION",
    "PROJECT",
    "ACADEMIC",
    "CERTIFICATION",
    "LISTED",
    "SELF_DESCRIBED",
)
SKILL_LEVELS = ("STRONG", "MODERATE", "RUSTY", "PROJECT", "LISTED")
LEVEL_SCORE = {"STRONG": 1.0, "MODERATE": 0.8, "RUSTY": 0.55, "PROJECT": 0.5, "LISTED": 0.4}
EVIDENCE_CATEGORIES = {"experience", "tech_stack", "project", "project_stack", "project_entry"}


@dataclass
class FactDraft:
    category: str
    statement: str
    experience_type: str
    source_ref: dict[str, Any]
    technologies: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    employer: str | None = None
    role_title: str | None = None
    label: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    skill_name: str | None = None
    skill_level: str | None = None
    level_source: str | None = None
    position: int = 0
    verified: bool = True
    confidence: float = 1.0


def canonical_technologies(items: Iterable[str]) -> list[str]:
    """Map listed tool names onto taxonomy names; unknown tools keep their own name."""
    names: list[str] = []
    for item in items:
        hits = [hit.name for hit in find_terms(item, kinds=("tech",))]
        if hits:
            names.extend(hits)
        elif item.strip():
            names.append(item.strip())
    return list(dict.fromkeys(names))


def _terms(text: str) -> tuple[list[str], list[str]]:
    hits = find_terms(text)
    return (
        [hit.name for hit in hits if hit.kind == "tech"],
        [hit.name for hit in hits if hit.kind == "concept"],
    )


def functional_title(title: str | None) -> str | None:
    """``Senior Analyst (Senior Data Engineer)`` -> ``Senior Data Engineer`` when the
    parenthetical is a role; otherwise the title itself."""
    if not title:
        return None
    inner = re.search(r"\(([^)]+)\)", title)
    if inner and re.search(r"engineer|developer|architect|scientist|analyst", inner.group(1), re.I):
        return inner.group(1).strip()
    return title.strip()


def build_facts(parsed: ParsedResume) -> list[FactDraft]:
    facts: list[FactDraft] = []
    position = 0

    def add(draft: FactDraft) -> None:
        nonlocal position
        draft.position = position
        position += 1
        facts.append(draft)

    for index, sentence in enumerate(parsed.summary):
        techs, concepts = _terms(sentence)
        add(
            FactDraft(
                "summary",
                sentence,
                SELF_DESCRIBED,
                {"section": "summary", "sentence": index},
                techs,
                concepts,
                find_metrics(sentence),
            )
        )
    for entry_index, experience in enumerate(parsed.experiences):
        base = {
            "employer": experience.employer,
            "role_title": experience.title,
            "start_date": experience.start,
            "end_date": experience.end,
        }
        role_text = " at ".join(part for part in (experience.title, experience.employer) if part)
        details = ", ".join(part for part in (experience.location, experience.date_text) if part)
        stack_techs = canonical_technologies(experience.stack)
        add(
            FactDraft(
                "role",
                f"{role_text} ({details})" if details else role_text,
                PRODUCTION,
                {
                    "section": "experience",
                    "entry": entry_index,
                    "location": experience.location,
                    "date_text": experience.date_text,
                },
                stack_techs,
                [],
                [],
                **base,
            )
        )
        for bullet_index, bullet in enumerate(experience.bullets):
            ref = {"section": "experience", "entry": entry_index, "bullet": bullet_index}
            if bullet.kind == "stack":
                add(
                    FactDraft(
                        "tech_stack",
                        bullet.text,
                        PRODUCTION,
                        {**ref, "items": experience.stack},
                        stack_techs,
                        [],
                        [],
                        label=bullet.label,
                        **base,
                    )
                )
                continue
            text = f"{bullet.label}: {bullet.text}" if bullet.label else bullet.text
            techs, concepts = _terms(text)
            add(
                FactDraft(
                    "experience",
                    bullet.text,
                    PRODUCTION,
                    ref,
                    techs,
                    concepts,
                    find_metrics(bullet.text),
                    label=bullet.label,
                    **base,
                )
            )
    for entry_index, project in enumerate(parsed.projects):
        stack_techs = canonical_technologies(project.stack)
        heading = f"{project.name} \u2014 {project.subtitle}" if project.subtitle else project.name
        techs, concepts = _terms(heading)
        add(
            FactDraft(
                "project_entry",
                heading,
                PROJECT,
                {
                    "section": "projects",
                    "entry": entry_index,
                    "links": project.links,
                    "date_text": project.date_text,
                },
                list(dict.fromkeys(techs + stack_techs)),
                concepts,
                [],
                label=project.name,
            )
        )
        for bullet_index, bullet in enumerate(project.bullets):
            ref = {
                "section": "projects",
                "entry": entry_index,
                "bullet": bullet_index,
                "project": project.name,
            }
            if bullet.kind == "stack":
                add(
                    FactDraft(
                        "project_stack",
                        bullet.text,
                        PROJECT,
                        {**ref, "items": project.stack},
                        stack_techs,
                        [],
                        [],
                        label=bullet.label,
                    )
                )
                continue
            techs, concepts = _terms(bullet.text)
            add(
                FactDraft(
                    "project",
                    bullet.text,
                    PROJECT,
                    ref,
                    techs,
                    concepts,
                    find_metrics(bullet.text),
                    label=bullet.label,
                )
            )
    for group_index, group in enumerate(parsed.skills):
        add(
            FactDraft(
                "skill_group",
                group.raw,
                LISTED,
                {
                    "section": "skills",
                    "group": group_index,
                    "category": group.category,
                    "items": group.items,
                },
                canonical_technologies(group.items),
                [],
                [],
                label=group.category,
            )
        )
    for index, education in enumerate(parsed.education):
        statement = ", ".join(
            part
            for part in (
                education.degree,
                education.institution,
                education.location,
                education.year,
            )
            if part
        )
        add(
            FactDraft(
                "education",
                statement,
                ACADEMIC,
                {
                    "section": "education",
                    "entry": index,
                    "institution": education.institution,
                    "degree": education.degree,
                    "location": education.location,
                    "year": education.year,
                },
            )
        )
    for index, certification in enumerate(parsed.certifications):
        techs, concepts = _terms(certification)
        add(
            FactDraft(
                "certification",
                certification,
                CERTIFICATION,
                {"section": "certifications", "entry": index},
                techs,
                concepts,
            )
        )
    return facts


class FactLike(Protocol):
    id: Any
    category: str
    technologies: list[str]
    experience_type: str
    employer: str | None
    end_date: str | None
    active: bool


@dataclass
class SkillEvidence:
    name: str
    level: str
    score: float
    roles: list[str]
    production_mentions: int
    project_mentions: int
    listed: bool
    last_used: str | None
    fact_ids: list[str]
    note: str


def _years_since(end: str | None, today: date) -> float | None:
    if not end:
        return None
    if end == "present":
        return 0.0
    year, month = (int(part) for part in end.split("-"))
    return max(0.0, ((today.year - year) * 12 + (today.month - month)) / 12)


def aggregate_skills(
    facts: Iterable[FactLike], today: date, rusty_after_years: float = 3.0
) -> dict[str, SkillEvidence]:
    """Evidence-based level for every technology the candidate mentions.

    Production use (experience bullets or a role's tech stack) outranks projects, which
    outrank skills that are only listed. A skill used in production but not within
    ``rusty_after_years`` is RUSTY rather than current.
    """
    roles: dict[str, set[str]] = defaultdict(set)
    production: dict[str, int] = defaultdict(int)
    projects: dict[str, int] = defaultdict(int)
    listed: dict[str, bool] = defaultdict(bool)
    last_end: dict[str, str] = {}
    fact_ids: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        if not getattr(fact, "active", True):
            continue
        names = [
            name for name in expand_implied(fact.technologies or []) if kind_of(name) != "concept"
        ]
        for name in names:
            fact_ids[name].append(str(fact.id))
            if fact.category in {"experience", "tech_stack", "role"}:
                roles[name].add(fact.employer or "unknown employer")
                if fact.category == "experience":
                    production[name] += 1
                end = fact.end_date
                if end and (
                    name not in last_end
                    or end == "present"
                    or (last_end[name] != "present" and end > last_end[name])
                ):
                    last_end[name] = end
            elif fact.category in {"project", "project_stack", "project_entry"}:
                projects[name] += 1
            elif fact.category in {"skill_group", "certification"}:
                listed[name] = True
    evidence: dict[str, SkillEvidence] = {}
    for name in fact_ids:
        role_count = len(roles[name])
        score = (
            2.0 * role_count
            + 1.5 * production[name]
            + (0.5 if listed[name] else 0.0)
            + min(1.0, 0.5 * projects[name])
        )
        since = _years_since(last_end.get(name), today)
        if role_count:
            if since is not None and since > rusty_after_years:
                level = "RUSTY"
                note = f"Production use until {last_end[name]} ({since:.1f} years ago)."
            elif score >= 3.5:
                level = "STRONG"
                note = (
                    f"Production use at {', '.join(sorted(roles[name]))}"
                    + (f"; {production[name]} experience bullet(s)" if production[name] else "")
                    + "."
                )
            else:
                level = "MODERATE"
                note = f"Used in production at {', '.join(sorted(roles[name]))} (limited detail)."
        elif projects[name]:
            level = "PROJECT"
            note = "Project experience only (not production)."
        else:
            level = "LISTED"
            note = "Listed in skills or certifications; no experience bullet demonstrates it."
        evidence[name] = SkillEvidence(
            name=name,
            level=level,
            score=round(score, 2),
            roles=sorted(roles[name]),
            production_mentions=production[name],
            project_mentions=projects[name],
            listed=listed[name],
            last_used=last_end.get(name),
            fact_ids=list(dict.fromkeys(fact_ids[name])),
            note=note,
        )
    return evidence


def candidate_domains(facts: Iterable[FactLike]) -> list[str]:
    found: list[str] = []
    for fact in facts:
        if fact.category == "experience" and getattr(fact, "active", True):
            for domain in find_domains(getattr(fact, "statement", "")):
                if domain not in found:
                    found.append(domain)
    return found


def parsed_years(parsed: ParsedResume, today: date) -> float | None:
    return years_of_experience(parsed.experiences, today)


def is_known_term(name: str) -> bool:
    return name in TERMS_BY_NAME
