"""Application tracking. UnJob never submits applications or sends outreach; the user does."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.dependencies import SessionDep
from app.api.serializers import application_read, event_read, ts
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationScore,
    Company,
    Job,
    OutreachDraft,
    ResumeVariant,
)
from app.services.applications import STATUSES, WorkflowError, analytics, transition
from app.services.candidates import get_candidate

router = APIRouter(prefix="/applications", tags=["applications"])
StatusLiteral = Literal[
    "DISCOVERED", "ANALYZED", "RECOMMENDED", "RESUME_GENERATED", "USER_REVIEW", "APPROVED",
    "READY_TO_APPLY", "APPLICATION_STARTED", "APPLIED", "OA", "RECRUITER_SCREEN", "TECHNICAL",
    "FINAL", "OFFER", "REJECTED", "WITHDRAWN",
]  # fmt: skip


async def _candidate_id(session: Any) -> uuid.UUID:
    candidate = await get_candidate(session)
    if candidate is None:
        raise HTTPException(status_code=409, detail="Import your master resume first.")
    return candidate.id


async def _application_or_404(session: Any, application_id: uuid.UUID) -> Application:
    candidate_id = await _candidate_id(session)
    application = await session.get(Application, application_id)
    if application is None or application.candidate_id != candidate_id:
        raise HTTPException(status_code=404, detail="Application not found.")
    return application


async def _read(
    session: Any, application: Application, *, with_events: bool = False
) -> dict[str, Any]:
    job = await session.get(Job, application.job_id)
    company = await session.get(Company, application.company_id) if application.company_id else None
    variant = (
        await session.get(ResumeVariant, application.resume_variant_id)
        if application.resume_variant_id
        else None
    )
    score = await session.scalar(
        select(ApplicationScore).where(
            ApplicationScore.candidate_id == application.candidate_id,
            ApplicationScore.job_id == application.job_id,
        )
    )
    data = application_read(application, job, company, variant, score)
    if with_events:
        events = (
            await session.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == application.id)
                .order_by(ApplicationEvent.created_at)
            )
        ).all()
        data["events"] = [event_read(event) for event in events]
        drafts = (
            await session.scalars(
                select(OutreachDraft).where(OutreachDraft.application_id == application.id)
            )
        ).all()
        data["outreach_drafts"] = [_draft_read(draft) for draft in drafts]
    return data


@router.get("")
async def list_applications(
    session: SessionDep,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        return {"items": [], "total": 0, "statuses": list(STATUSES)}
    filters = [Application.candidate_id == candidate.id]
    if status_filter:
        filters.append(
            Application.status.in_([value.strip().upper() for value in status_filter.split(",")])
        )
    applications = (
        await session.scalars(
            select(Application).where(*filters).order_by(Application.updated_at.desc()).limit(limit)
        )
    ).all()
    return {
        "items": [await _read(session, application) for application in applications],
        "total": len(applications),
        "statuses": list(STATUSES),
    }


@router.get("/analytics")
async def application_analytics(session: SessionDep) -> dict[str, Any]:
    return await analytics(session, await _candidate_id(session))


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_application(body: ApplicationCreate, session: SessionDep) -> dict[str, Any]:
    candidate_id = await _candidate_id(session)
    job = await session.get(Job, body.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    existing = await session.scalar(
        select(Application).where(
            Application.candidate_id == candidate_id, Application.job_id == job.id
        )
    )
    if existing is not None:
        return await _read(session, existing, with_events=True)
    application = Application(
        candidate_id=candidate_id,
        job_id=job.id,
        company_id=job.company_id,
        status="DISCOVERED",
        source=job.ats,
    )
    session.add(application)
    await session.flush()
    session.add(
        ApplicationEvent(
            application_id=application.id,
            from_status=None,
            to_status="DISCOVERED",
            note="Tracked by you.",
            actor="user",
        )
    )
    await session.commit()
    return await _read(session, application, with_events=True)


@router.get("/{application_id}")
async def get_application(application_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    return await _read(
        session, await _application_or_404(session, application_id), with_events=True
    )


class ApplicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StatusLiteral | None = None
    note: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=10_000)
    recruiter: str | None = Field(default=None, max_length=200)
    hiring_manager: str | None = Field(default=None, max_length=200)
    next_action: str | None = Field(default=None, max_length=300)
    applied_at: datetime | None = None
    use_master_resume: bool | None = None


@router.patch("/{application_id}")
async def update_application(
    application_id: uuid.UUID, body: ApplicationPatch, session: SessionDep
) -> dict[str, Any]:
    application = await _application_or_404(session, application_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("use_master_resume"):
        application.resume_variant_id = None
    for field in ("notes", "recruiter", "hiring_manager", "next_action", "applied_at"):
        if field in values:
            setattr(application, field, values[field])
    if body.status and body.status != application.status:
        try:
            await transition(session, application, body.status, note=body.note, actor="user")
        except WorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    elif body.note:
        session.add(
            ApplicationEvent(
                application_id=application.id,
                from_status=application.status,
                to_status=application.status,
                note=body.note,
                actor="user",
            )
        )
    await session.commit()
    return await _read(session, application, with_events=True)


def _draft_read(draft: OutreachDraft) -> dict[str, Any]:
    return {
        "id": str(draft.id),
        "application_id": str(draft.application_id) if draft.application_id else None,
        "channel": draft.channel,
        "recipient_name": draft.recipient_name,
        "recipient_role": draft.recipient_role,
        "recipient_url": draft.recipient_url,
        "subject": draft.subject,
        "body": draft.body,
        "status": draft.status,
        "updated_at": ts(draft.updated_at),
        "note": "Drafts are never sent by UnJob. Copy and send it yourself after review.",
    }


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["email", "linkedin", "x"]
    recipient_name: str | None = Field(default=None, max_length=200)
    recipient_role: str | None = Field(default=None, max_length=200)
    recipient_url: str | None = Field(default=None, max_length=500)
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(default="", max_length=10_000)


@router.post("/{application_id}/outreach", status_code=status.HTTP_201_CREATED)
async def create_draft(
    application_id: uuid.UUID, body: DraftCreate, session: SessionDep
) -> dict[str, Any]:
    application = await _application_or_404(session, application_id)
    draft = OutreachDraft(
        application_id=application.id,
        company_id=application.company_id,
        status="DRAFT",
        **body.model_dump(),
    )
    session.add(draft)
    await session.commit()
    return _draft_read(draft)


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=10_000)
    status: Literal["DRAFT", "APPROVED", "SENT_MANUALLY", "DISCARDED"] | None = None


@router.patch("/outreach/{draft_id}")
async def update_draft(
    draft_id: uuid.UUID, body: DraftPatch, session: SessionDep
) -> dict[str, Any]:
    candidate_id = await _candidate_id(session)
    draft = await session.get(OutreachDraft, draft_id)
    application = (
        await session.get(Application, draft.application_id)
        if draft and draft.application_id
        else None
    )
    if draft is None or application is None or application.candidate_id != candidate_id:
        raise HTTPException(status_code=404, detail="Draft not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(draft, field, value)
    await session.commit()
    return _draft_read(draft)
