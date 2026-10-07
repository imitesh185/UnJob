"""Candidate profile persistence: master-resume import, facts, skill levels and preferences."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.intelligence.facts import (
    LISTED,
    PROJECT,
    aggregate_skills,
    build_facts,
    candidate_domains,
    functional_title,
)
from app.intelligence.resume_parser import (
    PARSER_VERSION,
    extract_text,
    parse_resume,
    years_of_experience,
)
from app.intelligence.taxonomy import find_metrics, find_terms
from app.models import Candidate, CandidateFact, Resume

SKILL_LEVEL_TYPES = {
    "STRONG": "PRODUCTION",
    "MODERATE": "PRODUCTION",
    "RUSTY": "PRODUCTION",
    "PROJECT": PROJECT,
    "LISTED": LISTED,
}
MAX_RESUME_BYTES = 5_000_000


@dataclass
class ImportReport:
    candidate: Candidate
    resume: Resume
    facts_created: int
    facts_preserved: int
    warnings: list[str]
    unchanged: bool


async def get_candidate(session: AsyncSession) -> Candidate | None:
    """UnJob is single-user: the profile is the earliest candidate record."""
    return await session.scalar(select(Candidate).order_by(Candidate.created_at).limit(1))


async def active_facts(session: AsyncSession, candidate_id: uuid.UUID) -> list[CandidateFact]:
    return list(
        (
            await session.scalars(
                select(CandidateFact)
                .where(CandidateFact.candidate_id == candidate_id, CandidateFact.active.is_(True))
                .order_by(CandidateFact.position)
            )
        ).all()
    )


def _store_file(settings: Settings, digest: str, filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()[:10] or ".bin"
    folder = Path(settings.data_dir) / "resumes" / "master"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest[:24]}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


async def refresh_skill_facts(
    session: AsyncSession, candidate: Candidate, rusty_after_years: float, today: date | None = None
) -> int:
    """Recompute inferred skill facts; skills whose level the user set are left alone."""
    today = today or datetime.now(UTC).date()
    facts = await active_facts(session, candidate.id)
    evidence_facts = [fact for fact in facts if fact.category != "skill"]
    user_levels = {
        fact.skill_name: fact
        for fact in facts
        if fact.category == "skill" and fact.level_source == "user"
    }
    await session.execute(
        update(CandidateFact)
        .where(
            CandidateFact.candidate_id == candidate.id,
            CandidateFact.category == "skill",
            CandidateFact.level_source != "user",
        )
        .values(active=False)
    )
    skills = aggregate_skills(evidence_facts, today, rusty_after_years)
    created = 0
    for position, (name, evidence) in enumerate(
        sorted(skills.items(), key=lambda item: -item[1].score)
    ):
        if name in user_levels:
            continue
        session.add(
            CandidateFact(
                candidate_id=candidate.id,
                resume_id=None,
                category="skill",
                statement=f"{name}: {evidence.level.lower()} \u2014 {evidence.note}",
                source="derived",
                source_ref={
                    "evidence_fact_ids": evidence.fact_ids,
                    "roles": evidence.roles,
                    "last_used": evidence.last_used,
                    "production_mentions": evidence.production_mentions,
                    "project_mentions": evidence.project_mentions,
                    "listed": evidence.listed,
                    "score": evidence.score,
                    "note": evidence.note,
                },
                confidence=0.8,
                experience_type=SKILL_LEVEL_TYPES.get(evidence.level, "UNKNOWN"),
                verified=False,
                technologies=[name],
                concepts=[],
                metrics=[],
                skill_name=name,
                skill_level=evidence.level,
                level_source="inferred",
                position=10_000 + position,
                active=True,
            )
        )
        created += 1
    candidate.domains = candidate_domains(evidence_facts)
    await session.flush()
    return created


async def import_resume(
    session: AsyncSession,
    settings: Settings,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    rusty_after_years: float = 3.0,
    default_target_roles: list[str] | None = None,
) -> ImportReport:
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_RESUME_BYTES:
        raise ValueError("Resume files must be at most 5 MB.")
    text, parser = extract_text(data, filename, content_type)
    parsed = parse_resume(text)
    digest = hashlib.sha256(data).hexdigest()
    candidate = await get_candidate(session)
    created_candidate = candidate is None
    if candidate is None:
        candidate = Candidate(
            links=[],
            target_roles=[],
            preferred_locations=[],
            domains=[],
            preference_sources={},
            profile_version=1,
        )
        session.add(candidate)
        await session.flush()
    previous = await session.scalar(
        select(Resume).where(Resume.candidate_id == candidate.id, Resume.is_active.is_(True))
    )
    if previous is not None and previous.file_hash == digest:
        return ImportReport(candidate, previous, 0, 0, list(previous.warnings or []), True)
    version = (
        await session.scalar(
            select(Resume.version)
            .where(Resume.candidate_id == candidate.id)
            .order_by(Resume.version.desc())
            .limit(1)
        )
        or 0
    ) + 1
    await session.execute(
        update(Resume).where(Resume.candidate_id == candidate.id).values(is_active=False)
    )
    resume = Resume(
        candidate_id=candidate.id,
        version=version,
        filename=filename[:255],
        content_type=(content_type or "application/octet-stream")[:100],
        file_hash=digest,
        file_path=_store_file(settings, digest, filename, data),
        raw_text=text,
        parsed=parsed.to_dict(),
        parser=f"{PARSER_VERSION}/{parser}",
        warnings=parsed.warnings,
        is_active=True,
    )
    session.add(resume)
    await session.flush()
    # Facts from the previous master resume are retired; facts the user added are kept.
    await session.execute(
        update(CandidateFact)
        .where(
            CandidateFact.candidate_id == candidate.id,
            CandidateFact.source.in_(("resume", "derived")),
            CandidateFact.level_source.is_(None) | (CandidateFact.level_source != "user"),
        )
        .values(active=False)
    )
    drafts = build_facts(parsed)
    for draft in drafts:
        session.add(
            CandidateFact(
                candidate_id=candidate.id,
                resume_id=resume.id,
                category=draft.category,
                statement=draft.statement,
                source="resume",
                source_ref={**draft.source_ref, "resume_version": version},
                confidence=draft.confidence,
                experience_type=draft.experience_type,
                verified=True,
                technologies=draft.technologies,
                concepts=draft.concepts,
                metrics=draft.metrics,
                employer=draft.employer,
                role_title=draft.role_title,
                label=draft.label,
                start_date=draft.start_date,
                end_date=draft.end_date,
                position=draft.position,
                active=True,
            )
        )
    await session.flush()
    preserved = await session.scalar(
        select(CandidateFact.id)
        .where(
            CandidateFact.candidate_id == candidate.id,
            CandidateFact.source == "user",
            CandidateFact.active.is_(True),
        )
        .limit(1)
    )
    today = datetime.now(UTC).date()
    candidate.full_name = parsed.name or candidate.full_name
    candidate.email = parsed.email or candidate.email
    candidate.phone = parsed.phone or candidate.phone
    candidate.links = parsed.links or candidate.links or []
    current = next(
        (item for item in parsed.experiences if item.end == "present"),
        parsed.experiences[0] if parsed.experiences else None,
    )
    if current is not None:
        candidate.current_title = current.title
        candidate.current_company = current.employer
        candidate.location = current.location or candidate.location
        candidate.headline = candidate.headline or functional_title(current.title)
    sources = dict(candidate.preference_sources or {})
    if sources.get("years_experience") != "user":
        years = years_of_experience(parsed.experiences, today)
        candidate.years_experience = years
        candidate.years_experience_source = (
            "Computed from the employment dates in your resume." if years is not None else None
        )
    if not candidate.target_roles and default_target_roles:
        candidate.target_roles = list(default_target_roles)
        sources["target_roles"] = "Discovery settings target roles (edit in Profile)."
    candidate.preference_sources = sources
    candidate.active_resume_id = resume.id
    candidate.profile_version = (candidate.profile_version or 0) + (0 if created_candidate else 1)
    await refresh_skill_facts(session, candidate, rusty_after_years, today)
    await session.commit()
    return ImportReport(
        candidate, resume, len(drafts), 1 if preserved else 0, parsed.warnings, False
    )


PREFERENCE_FIELDS = (
    "target_roles",
    "preferred_locations",
    "remote_preference",
    "open_to_relocation",
    "compensation_target",
    "compensation_min",
    "compensation_currency",
    "notice_period",
    "years_experience",
    "headline",
)


async def update_preferences(
    session: AsyncSession, candidate: Candidate, values: dict[str, Any], source: str = "user"
) -> Candidate:
    sources = dict(candidate.preference_sources or {})
    for field in PREFERENCE_FIELDS:
        if field in values:
            setattr(candidate, field, values[field])
            sources[field] = source
            if field == "years_experience":
                candidate.years_experience_source = (
                    "Entered by you." if source == "user" else f"Set from {source}."
                )
    candidate.preference_sources = sources
    candidate.profile_version = (candidate.profile_version or 0) + 1
    await session.commit()
    return candidate


def _fact_terms(statement: str) -> tuple[list[str], list[str]]:
    hits = find_terms(statement)
    return (
        [hit.name for hit in hits if hit.kind == "tech"],
        [hit.name for hit in hits if hit.kind == "concept"],
    )


async def add_user_fact(
    session: AsyncSession,
    candidate: Candidate,
    *,
    category: str,
    statement: str,
    experience_type: str,
    employer: str | None = None,
    role_title: str | None = None,
    label: str | None = None,
    end_date: str | None = None,
    rusty_after_years: float = 3.0,
) -> CandidateFact:
    techs, concepts = _fact_terms(f"{label}: {statement}" if label else statement)
    position = (
        await session.scalar(
            select(CandidateFact.position)
            .where(CandidateFact.candidate_id == candidate.id)
            .order_by(CandidateFact.position.desc())
            .limit(1)
        )
        or 0
    ) + 1
    fact = CandidateFact(
        candidate_id=candidate.id,
        category=category,
        statement=statement.strip(),
        source="user",
        source_ref={"added_at": datetime.now(UTC).isoformat()},
        confidence=1.0,
        experience_type=experience_type,
        verified=True,
        technologies=techs,
        concepts=concepts,
        metrics=find_metrics(statement),
        employer=employer,
        role_title=role_title,
        label=label,
        end_date=end_date,
        position=position,
        active=True,
    )
    session.add(fact)
    await session.flush()
    candidate.profile_version = (candidate.profile_version or 0) + 1
    await refresh_skill_facts(session, candidate, rusty_after_years)
    await session.commit()
    return fact


async def update_fact(
    session: AsyncSession,
    candidate: Candidate,
    fact: CandidateFact,
    values: dict[str, Any],
    rusty_after_years: float = 3.0,
) -> CandidateFact:
    if "statement" in values and values["statement"] is not None:
        statement = values["statement"].strip()
        if fact.source == "resume" and statement != fact.statement:
            # Editing a resume fact makes it the user's own statement.
            fact.source = "user"
        fact.statement = statement
        fact.technologies, fact.concepts = _fact_terms(
            f"{fact.label}: {statement}" if fact.label else statement
        )
        fact.metrics = find_metrics(statement)
    for field in ("experience_type", "active", "verified"):
        if field in values and values[field] is not None:
            setattr(fact, field, values[field])
    if values.get("skill_level"):
        fact.skill_level = values["skill_level"]
        fact.level_source = "user"
        fact.verified = True
        fact.statement = re.sub(
            r"^([^:]+):\s*\w+", rf"\1: {fact.skill_level.lower()}", fact.statement
        )
    candidate.profile_version = (candidate.profile_version or 0) + 1
    if fact.category != "skill":
        await refresh_skill_facts(session, candidate, rusty_after_years)
    await session.commit()
    return fact
