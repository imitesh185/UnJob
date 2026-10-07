"""Tailored resume review: content with provenance, diff, quality, edits, approval, download."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.dependencies import SessionDep, SettingsDep
from app.api.serializers import claim_read, variant_summary
from app.discovery.settings_store import load_runtime_settings
from app.intelligence.quality import evaluate_resume
from app.intelligence.render import resume_blocks, to_docx, to_html, to_markdown, to_text
from app.intelligence.tailoring import ClaimDraft, build_resume_base, master_resume
from app.intelligence.verification import verify_claim
from app.models import Application, CandidateFact, Company, Job, Resume, ResumeClaim, ResumeVariant
from app.services.applications import WorkflowError, transition
from app.services.candidates import active_facts, get_candidate
from app.services.intelligence import IntelligenceService, claim_resolver, resolve_claims

router = APIRouter(prefix="/resume-variants", tags=["resumes"])
MEDIA = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


async def _variant_or_404(session: Any, variant_id: uuid.UUID) -> ResumeVariant:
    variant = await session.get(ResumeVariant, variant_id)
    candidate = await get_candidate(session)
    if variant is None or candidate is None or variant.candidate_id != candidate.id:
        raise HTTPException(status_code=404, detail="Resume variant not found.")
    return variant


def _blocks(blocks: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [{"kind": kind, "value": value} for kind, value in blocks]


async def _detail(session: Any, variant: ResumeVariant) -> dict[str, Any]:
    claims = await claim_resolver(session, variant)
    resolve = resolve_claims(claims)
    facts = {
        str(fact.id): fact
        for fact in (
            await session.scalars(
                select(CandidateFact).where(CandidateFact.candidate_id == variant.candidate_id)
            )
        ).all()
    }
    job = await session.get(Job, variant.job_id) if variant.job_id else None
    company = await session.get(Company, variant.company_id) if variant.company_id else None
    candidate = await get_candidate(session)
    master_blocks: list[dict[str, Any]] = []
    if candidate is not None:
        active = await active_facts(session, candidate.id)
        resume = (
            await session.get(Resume, variant.master_resume_id)
            if variant.master_resume_id
            else None
        )
        base = build_resume_base(
            active, (resume.parsed or {}).get("section_order") if resume else None
        )
        master = master_resume(candidate=candidate, base=base)
        master_blocks = _blocks(
            resume_blocks(
                master.content, lambda ref: master.claims[ref] if isinstance(ref, int) else None
            )
        )
    ordered = sorted(
        claims.values(), key=lambda claim: (claim.section, claim.entry_key or "", claim.position)
    )
    return {
        **(variant_summary(variant) or {}),
        "job_title": job.title if job else None,
        "company": company.name if company else (job.company if job else None),
        "blocks": _blocks(resume_blocks(variant.content or {}, resolve)),
        "master_blocks": master_blocks,
        "claims": [claim_read(claim, facts) for claim in ordered],
        "changes": variant.changes or {},
        "quality_checks": variant.quality_checks or [],
        "truth_validation": variant.truth_validation or {},
        "source_fact_count": len(variant.source_fact_ids or []),
        "downloads": sorted(MEDIA),
    }


@router.get("")
async def list_variants(
    session: SessionDep,
    job_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    candidate = await get_candidate(session)
    if candidate is None:
        return {"items": [], "total": 0}
    filters = [ResumeVariant.candidate_id == candidate.id]
    if job_id:
        filters.append(ResumeVariant.job_id == job_id)
    if status_filter:
        filters.append(ResumeVariant.status == status_filter.upper())
    rows = (
        await session.execute(
            select(ResumeVariant, Job, Company)
            .outerjoin(Job, Job.id == ResumeVariant.job_id)
            .outerjoin(Company, Company.id == ResumeVariant.company_id)
            .where(*filters)
            .order_by(ResumeVariant.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                **(variant_summary(variant) or {}),
                "job_title": job.title if job else None,
                "company": company.name if company else (job.company if job else None),
                "tier": company.tier if company else None,
            }
            for variant, job, company in rows
        ],
        "total": len(rows),
    }


@router.get("/{variant_id}")
async def get_variant(variant_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    return await _detail(session, await _variant_or_404(session, variant_id))


@router.get("/{variant_id}/download")
async def download_variant(
    variant_id: uuid.UUID,
    session: SessionDep,
    format: Literal["md", "txt", "html", "docx"] = "docx",  # noqa: A002 - query parameter name
) -> Response:
    variant = await _variant_or_404(session, variant_id)
    claims = await claim_resolver(session, variant)
    resolve = resolve_claims(claims)
    job = await session.get(Job, variant.job_id) if variant.job_id else None
    company = await session.get(Company, variant.company_id) if variant.company_id else None
    company_name = company.name if company else (job.company if job else "Company")
    title = f"{company_name} - {job.title if job else 'Role'}"
    content = variant.content or {}
    body: bytes = {
        "md": lambda: to_markdown(content, resolve).encode(),
        "txt": lambda: to_text(content, resolve).encode(),
        "html": lambda: to_html(content, resolve, title).encode(),
        "docx": lambda: to_docx(content, resolve),
    }[format]()
    safe = "".join(char if char.isalnum() or char in " -_" else "_" for char in title)[:80].strip()
    filename = f"{safe or 'resume'} v{variant.version}.{format}"
    return Response(
        content=body,
        media_type=MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ClaimPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included: bool | None = None
    text: str | None = Field(default=None, min_length=3, max_length=2000)


@router.patch("/{variant_id}/claims/{claim_id}")
async def edit_claim(
    variant_id: uuid.UUID,
    claim_id: uuid.UUID,
    body: ClaimPatch,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    variant = await _variant_or_404(session, variant_id)
    if variant.status in {"SUPERSEDED", "STALE"}:
        raise HTTPException(
            status_code=409, detail="This version is no longer current; edit the latest one."
        )
    claim = await session.get(ResumeClaim, claim_id)
    if claim is None or claim.variant_id != variant.id:
        raise HTTPException(status_code=404, detail="Claim not found.")
    candidate = await get_candidate(session)
    facts = {str(fact.id): fact for fact in await active_facts(session, candidate.id)}
    if body.included is not None:
        claim.included = body.included
    if body.text is not None and body.text.strip() != claim.generated_text:
        sources = [facts[fact_id] for fact_id in claim.source_fact_ids or [] if fact_id in facts]
        job = await session.get(Job, variant.job_id) if variant.job_id else None
        titles = [
            fact.role_title
            for fact in facts.values()
            if fact.category == "role" and fact.role_title
        ]
        result = verify_claim(
            body.text.strip(),
            sources,
            kind="generated",
            candidate_titles=titles,
            jd_text=f"{job.title}\n{job.description}" if job else None,
        )
        claim.generated_text = body.text.strip()
        claim.rewritten_by = "user"
        claim.verified = result.ok
        claim.verification_notes = result.notes or [
            "Edited by you and verified against the cited facts."
        ]
    await session.flush()
    await _rescore(session, settings, variant)
    variant.status = "USER_REVIEW"
    variant.approved_at = None
    await session.commit()
    return await _detail(session, variant)


async def _rescore(session: Any, settings: Any, variant: ResumeVariant) -> None:
    """Recompute quality, truth validation and files after an edit."""
    claims = await claim_resolver(session, variant)
    resolve = resolve_claims(claims)
    job = await session.get(Job, variant.job_id) if variant.job_id else None
    if job is None:
        return
    runtime = await load_runtime_settings(session, settings)
    service = IntelligenceService(settings, None)  # type: ignore[arg-type]
    context = await service.candidate_context(session, runtime)
    if context is None:
        return
    company = await session.get(Company, job.company_id) if job.company_id else None
    analysis_row, _changed = await service.ensure_analysis(
        session, job, company.name if company else job.company
    )
    analysis = service.analysis_dict(analysis_row)
    match = service.compute_match(context, job, analysis, runtime)
    drafts = [
        ClaimDraft(
            claim.section,
            claim.entry_key,
            claim.position,
            claim.generated_text,
            claim.original_text,
            list(claim.source_fact_ids or []),
            list(claim.jd_requirement_ids or []),
            claim.tailoring_reason,
            claim.relevance,
            label=claim.label,
            included=claim.included,
            verified=claim.verified,
            notes=list(claim.verification_notes or []),
        )
        for claim in claims.values()
    ]
    master = master_resume(candidate=context.candidate, base=context.base)
    baseline = to_text(
        master.content, lambda ref: master.claims[ref] if isinstance(ref, int) else None
    )
    quality = evaluate_resume(
        drafts,
        analysis=analysis,
        match=match,
        rendered_text=to_text(variant.content or {}, resolve),
        candidate_titles=context.model.titles,
        years_experience=context.model.years,
        baseline_text=baseline,
    )
    variant.alignment_score = quality["alignment_score"]
    variant.alignment_breakdown = quality["alignment_breakdown"]
    variant.quality_checks = quality["quality_checks"]
    variant.truth_validation = quality["truth_validation"]
    changes = dict(variant.changes or {})
    changes["unsupported_claims"] = len(quality["truth_validation"].get("unsupported", []))
    changes["user_edits"] = sum(1 for claim in claims.values() if claim.rewritten_by == "user")
    variant.changes = changes
    folder = Path(settings.data_dir) / "resumes" / "variants"
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / str(variant.id)
    title = f"{company.name if company else job.company} - {job.title}"
    outputs = {
        "md": to_markdown(variant.content or {}, resolve).encode(),
        "txt": to_text(variant.content or {}, resolve).encode(),
        "html": to_html(variant.content or {}, resolve, title).encode(),
        "docx": to_docx(variant.content or {}, resolve),
    }
    paths = {}
    for extension, data in outputs.items():
        path = stem.with_suffix(f".{extension}")
        path.write_bytes(data)
        paths[extension] = str(path)
    variant.file_paths = paths


@router.post("/{variant_id}/approve")
async def approve_variant(variant_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    variant = await _variant_or_404(session, variant_id)
    if variant.status not in {"USER_REVIEW", "GENERATED", "APPROVED"}:
        raise HTTPException(
            status_code=409, detail=f"Variant is {variant.status}; approve the current version."
        )
    if not (variant.truth_validation or {}).get("passed"):
        raise HTTPException(
            status_code=409,
            detail="This resume has unsupported claims. Exclude or fix them (or add the fact to "
            "your profile) before approving.",
        )
    variant.status = "APPROVED"
    variant.approved_at = datetime.now(UTC)
    application = await session.scalar(
        select(Application).where(
            Application.candidate_id == variant.candidate_id, Application.job_id == variant.job_id
        )
    )
    if application is not None:
        application.resume_variant_id = variant.id
        if application.status in {
            "DISCOVERED",
            "ANALYZED",
            "RECOMMENDED",
            "RESUME_GENERATED",
            "USER_REVIEW",
        }:
            try:
                await transition(
                    session,
                    application,
                    "APPROVED",
                    note=f"Approved tailored resume v{variant.version}.",
                )
            except WorkflowError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return await _detail(session, variant)


@router.post("/{variant_id}/reject")
async def reject_variant(variant_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    variant = await _variant_or_404(session, variant_id)
    variant.status = "REJECTED"
    variant.approved_at = None
    await session.commit()
    return await _detail(session, variant)
