"""Opportunities: candidate-aware job ranking, today's plan, and per-job analysis detail."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.api.dependencies import SessionDep, SettingsDep
from app.api.serializers import (
    analysis_read,
    application_read,
    event_read,
    job_freshness,
    match_read,
    posted,
    priority_read,
    target_read,
    ts,
    variant_summary,
)
from app.discovery.settings_store import load_runtime_settings
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationScore,
    CandidateJobMatch,
    Company,
    Job,
    JobAnalysis,
    ResumeVariant,
    TargetScore,
)
from app.schemas import RunRead
from app.services.applications import INTERVIEW_STAGES, OUTCOMES
from app.services.candidates import get_candidate
from app.services.queue import enqueue_run

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
CLASS_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "REJECT": 4}
ACTIVE_VARIANT = ("USER_REVIEW", "APPROVED")
DONE_STATUSES = {*OUTCOMES, "REJECTED", "WITHDRAWN"}


def _posting_key(job: Job) -> tuple[str, str]:
    """Re-posts of one role ("Senior Data Engineer-1") or the same title in another city."""
    title = re.sub(r"-\d+$", "", (job.title or "").strip().lower())
    company = str(job.company_id) if job.company_id else (job.company or "").lower()
    return company, re.sub(r"[^a-z0-9]+", " ", title).strip()


def _plan_rows(rows: list[Any], size: int = 6) -> list[Any]:
    """Best rows for today's plan: highest class first, then companies not yet in the plan.

    Duplicate postings of the same role are skipped so the plan never spends two slots on
    one application.
    """
    remaining = list(rows)
    chosen: list[Any] = []
    per_company: Counter[str] = Counter()
    postings: set[tuple[str, str]] = set()
    while remaining and len(chosen) < size:
        best = min(
            remaining,
            key=lambda row: (
                CLASS_ORDER.get(row[0].priority_class, 9),
                per_company[_posting_key(row[1])[0]],
                -row[0].priority_score,
            ),
        )
        remaining.remove(best)
        key = _posting_key(best[1])
        if key in postings:
            continue
        postings.add(key)
        per_company[key[0]] += 1
        chosen.append(best)
    return chosen


async def _latest_variants(
    session: Any, candidate_id: uuid.UUID, job_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ResumeVariant]:
    if not job_ids:
        return {}
    rows = (
        await session.scalars(
            select(ResumeVariant)
            .where(
                ResumeVariant.candidate_id == candidate_id,
                ResumeVariant.job_id.in_(job_ids),
                ResumeVariant.status.in_(ACTIVE_VARIANT),
            )
            .order_by(ResumeVariant.version)
        )
    ).all()
    return {row.job_id: row for row in rows}


def _opportunity(
    job: Job,
    score: ApplicationScore,
    company: Company | None,
    match: CandidateJobMatch | None,
    variant: ResumeVariant | None,
    application: Application | None,
    runtime: Any,
    now: datetime,
) -> dict[str, Any]:
    freshness, age = job_freshness(job, runtime, now)
    why = list((match.explanation if match else [])[:3])
    strategy = score.strategy or {}
    return {
        "job_id": str(job.id),
        "title": job.title,
        "company": company.name if company else job.company,
        "company_id": str(company.id) if company else None,
        "tier": company.tier if company else None,
        "location": job.location,
        "remote_status": job.remote_status,
        "ats": job.ats,
        "application_url": job.application_url,
        "posted_at": posted(job),
        "posted_at_precision": job.posted_at_precision,
        "freshness_status": freshness,
        "age_hours": age,
        "listing_status": job.listing_status,
        "fit_score": score.fit_score,
        "company_target_score": score.company_target_score,
        "priority_score": score.priority_score,
        "priority_class": score.priority_class,
        "recommendation": score.recommendation,
        "gates": score.gates or [],
        "why": why,
        "strong_matches": [
            line.split(" \u2014 ")[0] for line in (match.strong_matches if match else [])[:6]
        ],
        "gaps": [
            gap["name"]
            for gap in (match.gaps if match else [])
            if gap.get("importance") != "alternative"
        ][:6],
        "seniority_fit": match.seniority_fit if match else None,
        "location_fit": (match.location_fit or {}) if match else {},
        "effort_minutes": strategy.get("effort_minutes"),
        "resume": variant_summary(variant),
        "application": {"id": str(application.id), "status": application.status}
        if application
        else None,
    }


@router.get("")
async def list_opportunities(
    session: SessionDep,
    settings: SettingsDep,
    priority: str | None = Query(default=None, description="Comma-separated classes, e.g. P0,P1"),
    min_fit: float | None = Query(default=None, ge=0, le=100),
    tier: str | None = None,
    company_id: uuid.UUID | None = None,
    include_rejected: bool = False,
    tailored: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        return {"items": [], "total": 0, "profile_ready": False}
    runtime = await load_runtime_settings(session, settings)
    filters = [ApplicationScore.candidate_id == candidate.id, Job.listing_status == "OPEN"]
    if priority:
        classes = [value.strip().upper() for value in priority.split(",") if value.strip()]
        filters.append(ApplicationScore.priority_class.in_(classes))
    elif not include_rejected:
        filters.append(ApplicationScore.priority_class != "REJECT")
    if min_fit is not None:
        filters.append(ApplicationScore.fit_score >= min_fit)
    if tier:
        filters.append(Company.tier == tier)
    if company_id:
        filters.append(Job.company_id == company_id)
    base = (
        select(ApplicationScore, Job, Company)
        .join(Job, Job.id == ApplicationScore.job_id)
        .outerjoin(Company, Company.id == Job.company_id)
        .where(*filters)
    )
    rows = (await session.execute(base)).all()
    now = datetime.now(UTC)
    job_ids = [job.id for _score, job, _company in rows]
    variants = await _latest_variants(session, candidate.id, job_ids)
    if tailored is not None:
        rows = [row for row in rows if (row[1].id in variants) == tailored]
    rows.sort(key=lambda row: (CLASS_ORDER.get(row[0].priority_class, 9), -row[0].priority_score))
    total = len(rows)
    page = rows[offset : offset + limit]
    ids = [job.id for _score, job, _company in page]
    matches = (
        {
            row.job_id: row
            for row in (
                await session.scalars(
                    select(CandidateJobMatch).where(
                        CandidateJobMatch.candidate_id == candidate.id,
                        CandidateJobMatch.job_id.in_(ids),
                    )
                )
            ).all()
        }
        if ids
        else {}
    )
    applications = (
        {
            row.job_id: row
            for row in (
                await session.scalars(
                    select(Application).where(
                        Application.candidate_id == candidate.id, Application.job_id.in_(ids)
                    )
                )
            ).all()
        }
        if ids
        else {}
    )
    return {
        "items": [
            _opportunity(
                job,
                score,
                company,
                matches.get(job.id),
                variants.get(job.id),
                applications.get(job.id),
                runtime,
                now,
            )
            for score, job, company in page
        ],
        "total": total,
        "profile_ready": True,
    }


@router.get("/today")
async def todays_targets(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Answers: "If I have only 2 hours today, which 3 applications should I work on?" """
    candidate = await get_candidate(session)
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    new_companies = (
        await session.scalar(
            select(func.count(Company.id)).where(
                Company.created_at >= start, Company.is_seed.is_(False)
            )
        )
        or 0
    )
    if candidate is None:
        return {"profile_ready": False, "plan": [], "new_companies_today": new_companies}
    runtime = await load_runtime_settings(session, settings)
    rows = (
        await session.execute(
            select(ApplicationScore, Job, Company)
            .join(Job, Job.id == ApplicationScore.job_id)
            .outerjoin(Company, Company.id == Job.company_id)
            .where(
                ApplicationScore.candidate_id == candidate.id,
                Job.listing_status == "OPEN",
                ApplicationScore.priority_class.in_(("P0", "P1", "P2")),
            )
        )
    ).all()
    job_ids = [job.id for _score, job, _company in rows]
    applications = (
        {
            row.job_id: row
            for row in (
                await session.scalars(
                    select(Application).where(
                        Application.candidate_id == candidate.id, Application.job_id.in_(job_ids)
                    )
                )
            ).all()
        }
        if job_ids
        else {}
    )
    variants = await _latest_variants(session, candidate.id, job_ids)
    matches = (
        {
            row.job_id: row
            for row in (
                await session.scalars(
                    select(CandidateJobMatch).where(
                        CandidateJobMatch.candidate_id == candidate.id,
                        CandidateJobMatch.job_id.in_(job_ids),
                    )
                )
            ).all()
        }
        if job_ids
        else {}
    )
    open_rows = [
        row
        for row in rows
        if not (applications.get(row[1].id) and applications[row[1].id].status in DONE_STATUSES)
    ]
    open_rows.sort(key=lambda row: (CLASS_ORDER[row[0].priority_class], -row[0].priority_score))
    plan: list[dict[str, Any]] = []
    budget = 120
    for score, job, company in _plan_rows(open_rows):
        if len(plan) >= 3:
            break
        item = _opportunity(
            job,
            score,
            company,
            matches.get(job.id),
            variants.get(job.id),
            applications.get(job.id),
            runtime,
            now,
        )
        minutes = item["effort_minutes"] or 45
        used = sum(entry["effort_minutes"] or 45 for entry in plan)
        if plan and used + minutes > budget + 15:
            continue
        plan.append(item)
    counts = Counter(score.priority_class for score, _job, _company in open_rows)
    fresh_cut = now - timedelta(hours=runtime.freshness_hours)
    fresh_companies = (
        await session.execute(
            select(Company.id, Company.name, Company.tier, func.count(Job.id))
            .join(Job, Job.company_id == Company.id)
            .where(
                Job.listing_status == "OPEN", Job.role_relevance >= 0.5, Job.posted_at >= fresh_cut
            )
            .group_by(Company.id, Company.name, Company.tier)
            .order_by(func.count(Job.id).desc())
            .limit(8)
        )
    ).all()
    status_counts = dict(
        (
            await session.execute(
                select(Application.status, func.count(Application.id))
                .where(Application.candidate_id == candidate.id)
                .group_by(Application.status)
            )
        ).all()
    )
    s_tier_rows = [row for row in open_rows if row[2] is not None and row[2].tier == "S"]
    return {
        "profile_ready": True,
        "plan": plan,
        "plan_minutes": sum(item["effort_minutes"] or 45 for item in plan),
        "counts": {"P0": counts.get("P0", 0), "P1": counts.get("P1", 0), "P2": counts.get("P2", 0)},
        "new_companies_today": new_companies,
        "fresh_hiring_companies": [
            {"id": str(row[0]), "name": row[1], "tier": row[2], "fresh_roles": row[3]}
            for row in fresh_companies
        ],
        "awaiting_review": status_counts.get("USER_REVIEW", 0),
        "approved": status_counts.get("APPROVED", 0) + status_counts.get("READY_TO_APPLY", 0),
        "interview_pipeline": {
            stage: status_counts.get(stage, 0)
            for stage in ("APPLIED", "OA", "RECRUITER_SCREEN", "TECHNICAL", "FINAL", "OFFER")
        },
        "interviews_active": sum(status_counts.get(stage, 0) for stage in INTERVIEW_STAGES),
        "s_tier": {
            "opportunities": len(s_tier_rows),
            "tailored": sum(1 for row in s_tier_rows if row[1].id in variants),
        },
        "generated_at": ts(now),
    }


@router.get("/{job_id}")
async def opportunity_detail(
    job_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    runtime = await load_runtime_settings(session, settings)
    candidate = await get_candidate(session)
    company = await session.get(Company, job.company_id) if job.company_id else None
    analysis = await session.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    match = score = target = application = None
    variants: list[ResumeVariant] = []
    events: list[ApplicationEvent] = []
    if candidate is not None:
        match = await session.scalar(
            select(CandidateJobMatch).where(
                CandidateJobMatch.candidate_id == candidate.id, CandidateJobMatch.job_id == job.id
            )
        )
        score = await session.scalar(
            select(ApplicationScore).where(
                ApplicationScore.candidate_id == candidate.id, ApplicationScore.job_id == job.id
            )
        )
        if company is not None:
            target = await session.scalar(
                select(TargetScore).where(
                    TargetScore.candidate_id == candidate.id, TargetScore.company_id == company.id
                )
            )
        variants = list(
            (
                await session.scalars(
                    select(ResumeVariant)
                    .where(
                        ResumeVariant.candidate_id == candidate.id, ResumeVariant.job_id == job.id
                    )
                    .order_by(ResumeVariant.version.desc())
                )
            ).all()
        )
        application = await session.scalar(
            select(Application).where(
                Application.candidate_id == candidate.id, Application.job_id == job.id
            )
        )
        if application is not None:
            events = list(
                (
                    await session.scalars(
                        select(ApplicationEvent)
                        .where(ApplicationEvent.application_id == application.id)
                        .order_by(ApplicationEvent.created_at)
                    )
                ).all()
            )
    freshness, age = job_freshness(job, runtime, datetime.now(UTC))
    current = next((variant for variant in variants if variant.status in ACTIVE_VARIANT), None)
    return {
        "job": {
            "id": str(job.id),
            "title": job.title,
            "company": company.name if company else job.company,
            "company_id": str(company.id) if company else None,
            "tier": company.tier if company else None,
            "location": job.location,
            "remote_status": job.remote_status,
            "employment_type": job.employment_type,
            "salary": job.salary,
            "ats": job.ats,
            "application_url": job.application_url,
            "posted_at": posted(job),
            "posted_at_precision": job.posted_at_precision,
            "freshness_status": freshness,
            "age_hours": age,
            "listing_status": job.listing_status,
            "description": job.description,
            "role_category": job.role_category,
            "sources": [
                {
                    "source": source.source,
                    "source_url": source.source_url,
                    "last_seen_at": ts(source.last_seen_at),
                }
                for source in job.sources
            ],
        },
        "profile_ready": candidate is not None,
        "analysis": analysis_read(analysis),
        "match": match_read(match),
        "company_target": target_read(target),
        "priority": priority_read(score),
        "resume": variant_summary(current),
        "variants": [variant_summary(variant) for variant in variants],
        "application": (
            {
                **application_read(application, job, company, current, score),
                "events": [event_read(event) for event in events],
            }
            if application is not None
            else None
        ),
    }


class TailorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


@router.post("/{job_id}/tailor", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def request_tailoring(
    job_id: uuid.UUID, session: SessionDep, body: TailorRequest | None = None
) -> RunRead:
    if await session.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if await get_candidate(session) is None:
        raise HTTPException(status_code=409, detail="Import your master resume before tailoring.")
    run, _created = await enqueue_run(
        session,
        "resume_tailoring",
        params={"job_id": str(job_id), "force": bool(body and body.force)},
        dedupe_key=f"resume_tailoring:{job_id}",
    )
    return RunRead.model_validate(run)


@router.post("/analyze", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def request_analysis(session: SessionDep) -> RunRead:
    if await get_candidate(session) is None:
        raise HTTPException(status_code=409, detail="Import your master resume first.")
    run, _created = await enqueue_run(
        session, "candidate_analysis", params={}, dedupe_key="candidate_analysis:full"
    )
    return RunRead.model_validate(run)
