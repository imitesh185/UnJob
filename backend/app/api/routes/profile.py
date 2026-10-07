"""Candidate profile: master-resume import, preferences and fact review."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.dependencies import SessionDep, SettingsDep
from app.api.serializers import candidate_read, fact_read, resume_read
from app.discovery.settings_store import load_runtime_settings
from app.intelligence.render import to_markdown
from app.intelligence.resume_parser import ResumeFormatError
from app.intelligence.tailoring import build_resume_base, master_resume
from app.models import CandidateFact, Resume
from app.services.candidates import (
    active_facts,
    add_user_fact,
    get_candidate,
    import_resume,
    update_fact,
    update_preferences,
)
from app.services.queue import enqueue_run

router = APIRouter(prefix="/profile", tags=["profile"])
EXPERIENCE_TYPES = Literal[
    "PRODUCTION", "PROJECT", "ACADEMIC", "CERTIFICATION", "LISTED", "SELF_DESCRIBED"
]


async def _queue_analysis(session: Any, reason: str) -> str:
    run, _created = await enqueue_run(
        session,
        "candidate_analysis",
        params={},
        trigger=reason,
        dedupe_key="candidate_analysis:full",
    )
    return str(run.id)


async def _profile(session: Any) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        return {"candidate": None, "resume": None, "facts": [], "skills": [], "stats": {}}
    resume = (
        await session.get(Resume, candidate.active_resume_id)
        if candidate.active_resume_id
        else None
    )
    facts = await active_facts(session, candidate.id)
    skills = sorted(
        (fact for fact in facts if fact.category == "skill"),
        key=lambda fact: (
            ["STRONG", "MODERATE", "RUSTY", "PROJECT", "LISTED"].index(fact.skill_level)
            if fact.skill_level in {"STRONG", "MODERATE", "RUSTY", "PROJECT", "LISTED"}
            else 9,
            fact.skill_name or "",
        ),
    )
    return {
        "candidate": candidate_read(candidate),
        "resume": resume_read(resume),
        "facts": [fact_read(fact) for fact in facts if fact.category != "skill"],
        "skills": [
            {
                "fact_id": str(fact.id),
                "name": fact.skill_name,
                "level": fact.skill_level,
                "level_source": fact.level_source,
                "note": (fact.source_ref or {}).get("note"),
                "roles": (fact.source_ref or {}).get("roles", []),
                "last_used": (fact.source_ref or {}).get("last_used"),
                "evidence_fact_ids": (fact.source_ref or {}).get("evidence_fact_ids", []),
            }
            for fact in skills
        ],
        "stats": {
            "facts": len([fact for fact in facts if fact.category != "skill"]),
            "verified": len([fact for fact in facts if fact.verified and fact.category != "skill"]),
            "skills": len(skills),
            "user_added": len([fact for fact in facts if fact.source == "user"]),
        },
    }


@router.get("")
async def get_profile(session: SessionDep) -> dict[str, Any]:
    return await _profile(session)


async def _import(
    session: Any, settings: Any, data: bytes, filename: str, content_type: str | None
) -> dict[str, Any]:
    runtime = await load_runtime_settings(session, settings)
    try:
        report = await import_resume(
            session,
            settings,
            data=data,
            filename=filename,
            content_type=content_type,
            rusty_after_years=runtime.rusty_after_years,
            default_target_roles=runtime.target_roles,
        )
    except (ResumeFormatError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run_id = None if report.unchanged else await _queue_analysis(session, "profile_import")
    profile = await _profile(session)
    return {
        **profile,
        "import": {
            "unchanged": report.unchanged,
            "facts_created": report.facts_created,
            "warnings": report.warnings,
            "analysis_run_id": run_id,
        },
    }


@router.post("/resume", status_code=status.HTTP_201_CREATED)
async def upload_resume(
    session: SessionDep, settings: SettingsDep, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    data = await file.read(5_000_001)
    return await _import(session, settings, data, file.filename or "resume", file.content_type)


class ResumeText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=200, max_length=100_000)
    filename: str = Field(default="pasted-resume.txt", max_length=200)


@router.post("/resume/text", status_code=status.HTTP_201_CREATED)
async def paste_resume(
    body: ResumeText, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    name = (
        body.filename if body.filename.lower().endswith((".txt", ".md")) else f"{body.filename}.txt"
    )
    return await _import(session, settings, body.text.encode(), name, "text/plain")


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_roles: list[str] | None = Field(default=None, max_length=30)
    preferred_locations: list[str] | None = Field(default=None, max_length=30)
    remote_preference: Literal["remote_only", "remote_ok", "onsite_ok", "hybrid_ok"] | None = None
    open_to_relocation: bool | None = None
    compensation_target: str | None = Field(default=None, max_length=100)
    compensation_min: float | None = Field(default=None, gt=0)
    compensation_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    notice_period: str | None = Field(default=None, max_length=100)
    years_experience: float | None = Field(default=None, ge=0, le=60)
    headline: str | None = Field(default=None, max_length=300)

    @field_validator("target_roles", "preferred_locations")
    @classmethod
    def _labels(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item and item.strip()]
        if any(len(item) > 100 for item in cleaned):
            raise ValueError("Each value must be at most 100 characters.")
        return list(dict.fromkeys(cleaned))


@router.patch("")
async def patch_profile(body: ProfilePatch, session: SessionDep) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Import your master resume first.")
    values = body.model_dump(exclude_unset=True)
    if "compensation_currency" in values and values["compensation_currency"]:
        values["compensation_currency"] = values["compensation_currency"].upper()
    await update_preferences(session, candidate, values)
    run_id = await _queue_analysis(session, "profile_update")
    return {**(await _profile(session)), "analysis_run_id": run_id}


class FactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "experience", "project", "achievement", "certification", "education", "skill_group"
    ]
    statement: str = Field(min_length=3, max_length=1000)
    experience_type: EXPERIENCE_TYPES
    employer: str | None = Field(default=None, max_length=200)
    role_title: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=200)
    end_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}|present)$")


@router.post("/facts", status_code=status.HTTP_201_CREATED)
async def create_fact(
    body: FactCreate, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Import your master resume first.")
    runtime = await load_runtime_settings(session, settings)
    fact = await add_user_fact(
        session,
        candidate,
        category=body.category,
        statement=body.statement,
        experience_type=body.experience_type,
        employer=body.employer,
        role_title=body.role_title,
        label=body.label,
        end_date=body.end_date,
        rusty_after_years=runtime.rusty_after_years,
    )
    run_id = await _queue_analysis(session, "fact_added")
    return {"fact": fact_read(fact), "analysis_run_id": run_id}


class FactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str | None = Field(default=None, min_length=3, max_length=1000)
    experience_type: EXPERIENCE_TYPES | None = None
    active: bool | None = None
    verified: bool | None = None
    skill_level: Literal["STRONG", "MODERATE", "RUSTY", "PROJECT", "LISTED"] | None = None


@router.patch("/facts/{fact_id}")
async def patch_fact(
    fact_id: uuid.UUID, body: FactPatch, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    candidate = await get_candidate(session)
    fact = await session.get(CandidateFact, fact_id)
    if candidate is None or fact is None or fact.candidate_id != candidate.id:
        raise HTTPException(status_code=404, detail="Fact not found.")
    if body.skill_level and fact.category != "skill":
        raise HTTPException(status_code=422, detail="skill_level applies only to skill facts.")
    runtime = await load_runtime_settings(session, settings)
    fact = await update_fact(
        session,
        candidate,
        fact,
        body.model_dump(exclude_unset=True),
        rusty_after_years=runtime.rusty_after_years,
    )
    run_id = await _queue_analysis(session, "fact_updated")
    return {"fact": fact_read(fact), "analysis_run_id": run_id}


@router.get("/master-resume")
async def get_master_resume(session: SessionDep) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Import your master resume first.")
    facts = await active_facts(session, candidate.id)
    resume = await session.scalar(select(Resume).where(Resume.id == candidate.active_resume_id))
    base = build_resume_base(facts, (resume.parsed or {}).get("section_order") if resume else None)
    master = master_resume(candidate=candidate, base=base)
    markdown = to_markdown(
        master.content, lambda ref: master.claims[ref] if isinstance(ref, int) else None
    )
    return {"markdown": markdown, "resume": resume_read(resume)}
