"""JSON serializers shared by the Phase 3 API routes. Timestamps are always UTC ISO-8601."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.discovery.jsonld import age_bounds_hours, freshness_status
from app.discovery.settings_store import RuntimeSettings
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationScore,
    Candidate,
    CandidateFact,
    CandidateJobMatch,
    Company,
    Job,
    JobAnalysis,
    Resume,
    ResumeClaim,
    ResumeVariant,
    TargetScore,
)


def ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


def posted(job: Job) -> str | None:
    if job.posted_at is None:
        return None
    if job.posted_at_precision == "local_datetime":
        return job.posted_at.replace(tzinfo=None).isoformat()
    return ts(job.posted_at)


def job_freshness(job: Job, runtime: RuntimeSettings, now: datetime) -> tuple[str, float | None]:
    posted_at = job.posted_at
    if posted_at is not None and posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    bounds = age_bounds_hours(posted_at, job.posted_at_precision, now)
    status = freshness_status(
        posted_at,
        job.posted_at_precision,
        now,
        runtime.freshness_hours,
        runtime.secondary_freshness_hours,
    )
    return status, (round(bounds[1], 1) if bounds else None)


def candidate_read(candidate: Candidate | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "id": str(candidate.id),
        "full_name": candidate.full_name,
        "email": candidate.email,
        "phone": candidate.phone,
        "links": candidate.links or [],
        "headline": candidate.headline,
        "current_title": candidate.current_title,
        "current_company": candidate.current_company,
        "location": candidate.location,
        "years_experience": candidate.years_experience,
        "years_experience_source": candidate.years_experience_source,
        "target_roles": candidate.target_roles or [],
        "preferred_locations": candidate.preferred_locations or [],
        "remote_preference": candidate.remote_preference,
        "open_to_relocation": candidate.open_to_relocation,
        "compensation_target": candidate.compensation_target,
        "compensation_min": candidate.compensation_min,
        "compensation_currency": candidate.compensation_currency,
        "notice_period": candidate.notice_period,
        "domains": candidate.domains or [],
        "preference_sources": candidate.preference_sources or {},
        "profile_version": candidate.profile_version,
        "updated_at": ts(candidate.updated_at),
    }


def resume_read(resume: Resume | None) -> dict[str, Any] | None:
    if resume is None:
        return None
    return {
        "id": str(resume.id),
        "version": resume.version,
        "filename": resume.filename,
        "content_type": resume.content_type,
        "parser": resume.parser,
        "warnings": resume.warnings or [],
        "section_order": (resume.parsed or {}).get("section_order", []),
        "uploaded_at": ts(resume.created_at),
    }


def fact_read(fact: CandidateFact) -> dict[str, Any]:
    return {
        "id": str(fact.id),
        "category": fact.category,
        "statement": fact.statement,
        "label": fact.label,
        "source": fact.source,
        "source_ref": fact.source_ref or {},
        "confidence": fact.confidence,
        "experience_type": fact.experience_type,
        "verified": fact.verified,
        "technologies": fact.technologies or [],
        "concepts": fact.concepts or [],
        "metrics": fact.metrics or [],
        "employer": fact.employer,
        "role_title": fact.role_title,
        "start_date": fact.start_date,
        "end_date": fact.end_date,
        "skill_name": fact.skill_name,
        "skill_level": fact.skill_level,
        "level_source": fact.level_source,
        "position": fact.position,
        "active": fact.active,
    }


def analysis_read(analysis: JobAnalysis | None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    return {
        "id": str(analysis.id),
        "analyzer_version": analysis.analyzer_version,
        "seniority": analysis.seniority,
        "years_min": analysis.years_min,
        "years_max": analysis.years_max,
        "requirements": analysis.requirements or [],
        "required_skills": analysis.required_skills or [],
        "preferred_skills": analysis.preferred_skills or [],
        "responsibilities": analysis.responsibilities or [],
        "categories": analysis.categories or {},
        "domains": analysis.domains or [],
        "leadership_signals": analysis.leadership_signals or [],
        "keywords": analysis.keywords or [],
        "business_context": analysis.business_context,
        "location_requirements": analysis.location_requirements or {},
        "compensation": analysis.compensation,
        "education": analysis.education or [],
        "role_focus": analysis.role_focus,
        "warnings": analysis.warnings or [],
        "analyzed_at": ts(analysis.updated_at),
    }


def match_read(match: CandidateJobMatch | None) -> dict[str, Any] | None:
    if match is None:
        return None
    return {
        "fit_score": match.fit_score,
        "breakdown": match.breakdown or {},
        "seniority_fit": match.seniority_fit,
        "requirement_matches": match.requirement_matches or [],
        "skill_assessments": match.skill_assessments or [],
        "strong_matches": match.strong_matches or [],
        "partial_matches": match.partial_matches or [],
        "gaps": match.gaps or [],
        "explanation": match.explanation or [],
        "location_fit": match.location_fit or {},
        "compensation_fit": match.compensation_fit or {},
        "profile_version": match.profile_version,
        "computed_at": ts(match.computed_at),
    }


def target_read(target: TargetScore | None) -> dict[str, Any] | None:
    if target is None:
        return None
    return {
        "score": target.score,
        "breakdown": target.breakdown or {},
        "reasons": target.reasons or [],
        "computed_at": ts(target.computed_at),
    }


def priority_read(score: ApplicationScore | None) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "priority_class": score.priority_class,
        "priority_score": score.priority_score,
        "fit_score": score.fit_score,
        "company_target_score": score.company_target_score,
        "freshness_score": score.freshness_score,
        "career_value_score": score.career_value_score,
        "gates": score.gates or [],
        "reasons": score.reasons or [],
        "recommendation": score.recommendation,
        "strategy": score.strategy,
        "computed_at": ts(score.computed_at),
    }


def check_status(variant: ResumeVariant, name: str) -> str | None:
    for check in variant.quality_checks or []:
        if check.get("check") == name:
            return check.get("status")
    return None


def variant_summary(variant: ResumeVariant | None) -> dict[str, Any] | None:
    if variant is None:
        return None
    return {
        "id": str(variant.id),
        "job_id": str(variant.job_id) if variant.job_id else None,
        "company_id": str(variant.company_id) if variant.company_id else None,
        "version": variant.version,
        "status": variant.status,
        "generator": variant.generator,
        "trigger": variant.trigger,
        "alignment_score": variant.alignment_score,
        "alignment_breakdown": variant.alignment_breakdown or {},
        "truth_passed": bool((variant.truth_validation or {}).get("passed")),
        "unsupported_claims": len((variant.truth_validation or {}).get("unsupported", [])),
        "ats_status": check_status(variant, "ats"),
        "jd_analyzed": True,
        "positioning": variant.positioning or {},
        "profile_version": variant.profile_version,
        "master_version": variant.master_version,
        "jd_hash": variant.jd_hash,
        "generated_at": ts(variant.created_at),
        "approved_at": ts(variant.approved_at),
    }


def claim_read(claim: ResumeClaim, facts: dict[str, CandidateFact]) -> dict[str, Any]:
    return {
        "id": str(claim.id),
        "section": claim.section,
        "entry_key": claim.entry_key,
        "position": claim.position,
        "label": claim.label,
        "text": claim.generated_text,
        "original_text": claim.original_text,
        "changed": claim.original_text is not None and claim.generated_text != claim.original_text,
        "source_facts": [
            {
                "id": fact_id,
                "statement": facts[fact_id].statement if fact_id in facts else None,
                "category": facts[fact_id].category if fact_id in facts else None,
                "experience_type": facts[fact_id].experience_type if fact_id in facts else None,
            }
            for fact_id in claim.source_fact_ids or []
        ],
        "jd_requirement_ids": claim.jd_requirement_ids or [],
        "tailoring_reason": claim.tailoring_reason,
        "relevance": claim.relevance,
        "verified": claim.verified,
        "verification_notes": claim.verification_notes or [],
        "included": claim.included,
        "rewritten_by": claim.rewritten_by,
    }


def event_read(event: ApplicationEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "from_status": event.from_status,
        "to_status": event.to_status,
        "note": event.note,
        "actor": event.actor,
        "created_at": ts(event.created_at),
    }


def application_read(
    application: Application,
    job: Job | None = None,
    company: Company | None = None,
    variant: ResumeVariant | None = None,
    score: ApplicationScore | None = None,
) -> dict[str, Any]:
    return {
        "id": str(application.id),
        "job_id": str(application.job_id),
        "company_id": str(application.company_id) if application.company_id else None,
        "status": application.status,
        "source": application.source,
        "priority_class": score.priority_class if score else application.priority_class,
        "priority_score": score.priority_score if score else None,
        "fit_score": score.fit_score if score else None,
        "applied_at": ts(application.applied_at),
        "recruiter": application.recruiter,
        "hiring_manager": application.hiring_manager,
        "notes": application.notes,
        "next_action": application.next_action,
        "job_title": job.title if job else None,
        "company": company.name if company else (job.company if job else None),
        "tier": company.tier if company else None,
        "application_url": job.application_url if job else None,
        "location": job.location if job else None,
        "resume": variant_summary(variant),
        "created_at": ts(application.created_at),
        "updated_at": ts(application.updated_at),
    }
