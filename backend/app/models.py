import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    legal_name: Mapped[str | None] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    careers_url: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    x_url: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(String(200))
    company_type: Mapped[str | None] = mapped_column(String(100))
    company_stage: Mapped[str | None] = mapped_column(String(100))
    headquarters: Mapped[str | None] = mapped_column(String(200))
    india_presence: Mapped[bool | None] = mapped_column(Boolean)
    remote_presence: Mapped[bool | None] = mapped_column(Boolean)
    employee_range: Mapped[str | None] = mapped_column(String(50))
    funding_stage: Mapped[str | None] = mapped_column(String(100))
    funding: Mapped[str | None] = mapped_column(String(200))
    known_technologies: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_infrastructure_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    engineering_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    tier: Mapped[str] = mapped_column(String(40), default="UNCATEGORIZED")
    # "default", "user" (set explicitly) or "s_tier_list" (from the configurable S-tier list).
    tier_source: Mapped[str] = mapped_column(String(20), default="default")
    # Positioning archetype (e.g. data_platform, fintech) and the signals behind it.
    archetype: Mapped[str | None] = mapped_column(String(40))
    positioning: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="DISCOVERED", index=True)
    review_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    discovery_source: Mapped[str] = mapped_column(String(60), default="UNKNOWN")
    discovery_reason: Mapped[str] = mapped_column(Text, default="")
    discovery_score: Mapped[float] = mapped_column(Float, default=0)
    score_breakdown: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    score_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_explored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DiscoveryRun(Base):
    """A durable unit of background work. The table is also the worker queue."""

    __tablename__ = "discovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    budgets: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)
    companies_discovered: Mapped[int] = mapped_column(Integer, default=0)
    jobs_inspected: Mapped[int] = mapped_column(Integer, default=0)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CareerSource(Base):
    __tablename__ = "career_sources"
    __table_args__ = (UniqueConstraint("company_id", "url", name="uq_career_source_company_url"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(40), default="unknown")
    platform_confidence: Mapped[float] = mapped_column(Float, default=0)
    platform_identifier: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    discovered_via: Mapped[str] = mapped_column(String(60), default="UNKNOWN")
    evidence: Mapped[str | None] = mapped_column(Text)
    scan_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    error: Mapped[str | None] = mapped_column(Text)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_complete_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    relevant_jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CompanyDiscoveryEvent(Base):
    __tablename__ = "company_discovery_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(60))
    source_url: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(40), index=True)
    evidence: Mapped[str] = mapped_column(Text)
    evidence_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscoveryCandidate(Base):
    """A frontier entry: a company awaiting deeper exploration."""

    __tablename__ = "discovery_candidates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[float] = mapped_column(Float, default=0)
    priority_breakdown: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    explored_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CompanyRelationship(Base):
    __tablename__ = "company_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_company_id",
            "target_company_id",
            "relationship_type",
            name="uq_company_relationship",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    target_company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    evidence: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    normalized_title: Mapped[str] = mapped_column(String(300), index=True)
    location: Mapped[str] = mapped_column(String(300), default="Unknown")
    remote_status: Mapped[str] = mapped_column(String(30), default="unknown")
    salary: Mapped[str | None] = mapped_column(String(300))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    ats: Mapped[str] = mapped_column(String(50))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    description: Mapped[str] = mapped_column(Text)
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    technology_stack: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_requirement: Mapped[str | None] = mapped_column(String(200))
    application_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="Discovered")
    overall_score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[dict[str, float]] = mapped_column(JSON)
    score_reasons: Mapped[list[str]] = mapped_column(JSON)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    role_category: Mapped[str] = mapped_column(String(40), default="unclassified", index=True)
    role_relevance: Mapped[float] = mapped_column(Float, default=0)
    # "datetime" or "date"; date-only sources are never given an invented time of day.
    posted_at_precision: Mapped[str | None] = mapped_column(String(20))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listing_status: Mapped[str] = mapped_column(String(20), default="OPEN")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    sources: Mapped[list["JobSource"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    career_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("career_sources.id", ondelete="SET NULL"), index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listing_status: Mapped[str] = mapped_column(String(20), default="OPEN")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[Job] = relationship(back_populates="sources")


# --------------------------------------------------------------------------------------
# Phase 3: candidate intelligence, matching, tailoring and application tracking
# --------------------------------------------------------------------------------------


class Candidate(Base):
    """The job seeker. Every field is either parsed from their own resume, entered by them,
    or computed from those sources; ``preference_sources`` records where preferences came
    from. Unknown values stay ``None``."""

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(60))
    links: Mapped[list[str]] = mapped_column(JSON, default=list)
    headline: Mapped[str | None] = mapped_column(String(300))
    current_title: Mapped[str | None] = mapped_column(String(200))
    current_company: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    years_experience: Mapped[float | None] = mapped_column(Float)
    years_experience_source: Mapped[str | None] = mapped_column(String(200))
    target_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    remote_preference: Mapped[str | None] = mapped_column(String(30))
    open_to_relocation: Mapped[bool | None] = mapped_column(Boolean)
    compensation_target: Mapped[str | None] = mapped_column(String(100))
    compensation_min: Mapped[float | None] = mapped_column(Float)
    compensation_currency: Mapped[str | None] = mapped_column(String(10))
    notice_period: Mapped[str | None] = mapped_column(String(100))
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    preference_sources: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    active_resume_id: Mapped[uuid.UUID | None] = mapped_column()
    # Incremented whenever facts change; matches and resume variants record the version used.
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Resume(Base):
    """An uploaded master resume document and its parsed structure."""

    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parser: Mapped[str] = mapped_column(String(60))
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CandidateFact(Base):
    """One verifiable statement about the candidate. Tailored resumes may only say what
    these facts support; each fact records its source and experience type."""

    __tablename__ = "candidate_facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String(30), index=True)
    statement: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(60))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    # PRODUCTION, PROJECT, ACADEMIC, CERTIFICATION, LISTED (skills list only), SELF_DESCRIBED.
    experience_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    technologies: Mapped[list[str]] = mapped_column(JSON, default=list)
    concepts: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[list[str]] = mapped_column(JSON, default=list)
    employer: Mapped[str | None] = mapped_column(String(200))
    role_title: Mapped[str | None] = mapped_column(String(200))
    label: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    skill_name: Mapped[str | None] = mapped_column(String(100), index=True)
    # STRONG, MODERATE, RUSTY, PROJECT, LISTED; ``level_source`` is "inferred" or "user".
    skill_level: Mapped[str | None] = mapped_column(String(20))
    level_source: Mapped[str | None] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobAnalysis(Base):
    """Structured reading of one job description, recomputed when the text changes."""

    __tablename__ = "job_analyses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    description_hash: Mapped[str] = mapped_column(String(64))
    analyzer_version: Mapped[str] = mapped_column(String(20))
    seniority: Mapped[str] = mapped_column(String(20), default="unknown")
    years_min: Mapped[float | None] = mapped_column(Float)
    years_max: Mapped[float | None] = mapped_column(Float)
    requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    required_skills: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    responsibilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    leadership_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    business_context: Mapped[str | None] = mapped_column(Text)
    location_requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compensation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    education: Mapped[list[str]] = mapped_column(JSON, default=list)
    role_focus: Mapped[str] = mapped_column(String(30), default="unknown")
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CandidateJobMatch(Base):
    __tablename__ = "candidate_job_matches"
    __table_args__ = (UniqueConstraint("candidate_id", "job_id", name="uq_candidate_job_match"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_analyses.id", ondelete="SET NULL")
    )
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    description_hash: Mapped[str] = mapped_column(String(64))
    fit_score: Mapped[float] = mapped_column(Float, default=0)
    breakdown: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    seniority_fit: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    requirement_matches: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # One entry per JD skill: candidate evidence level (STRONG ... GAP) and source facts.
    skill_assessments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    strong_matches: Mapped[list[str]] = mapped_column(JSON, default=list)
    partial_matches: Mapped[list[str]] = mapped_column(JSON, default=list)
    gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    explanation: Mapped[list[str]] = mapped_column(JSON, default=list)
    location_fit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compensation_fit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetScore(Base):
    """Candidate-aware company target score, separate from discovery and job fit."""

    __tablename__ = "target_scores"
    __table_args__ = (UniqueConstraint("candidate_id", "company_id", name="uq_target_score"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[float] = mapped_column(Float, default=0)
    breakdown: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApplicationScore(Base):
    """Application priority for one candidate/job pair, with every input recorded."""

    __tablename__ = "application_scores"
    __table_args__ = (UniqueConstraint("candidate_id", "job_id", name="uq_application_score"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    fit_score: Mapped[float] = mapped_column(Float, default=0)
    company_target_score: Mapped[float] = mapped_column(Float, default=0)
    freshness_score: Mapped[float] = mapped_column(Float, default=0)
    career_value_score: Mapped[float] = mapped_column(Float, default=0)
    priority_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    priority_class: Mapped[str] = mapped_column(String(10), default="REJECT", index=True)
    gates: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    strategy: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResumeVariant(Base):
    """A job-specific resume generated from the master resume and candidate facts."""

    __tablename__ = "resume_variants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    master_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    master_version: Mapped[int] = mapped_column(Integer, default=1)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    jd_hash: Mapped[str] = mapped_column(String(64))
    jd_snapshot: Mapped[str] = mapped_column(Text)
    generator: Mapped[str] = mapped_column(String(80))
    trigger: Mapped[str] = mapped_column(String(60), default="manual")
    # GENERATED -> USER_REVIEW -> APPROVED; SUPERSEDED, STALE or REJECTED otherwise.
    status: Mapped[str] = mapped_column(String(20), default="USER_REVIEW", index=True)
    positioning: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_fact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    alignment_score: Mapped[float] = mapped_column(Float, default=0)
    alignment_breakdown: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    truth_validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    file_paths: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ResumeClaim(Base):
    """One generated resume statement with the facts and JD requirements behind it."""

    __tablename__ = "resume_claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resume_variants.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[str] = mapped_column(String(30))
    entry_key: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str | None] = mapped_column(String(200))
    generated_text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)
    source_fact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    jd_requirement_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    tailoring_reason: Mapped[str] = mapped_column(Text, default="")
    relevance: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    rewritten_by: Mapped[str | None] = mapped_column(String(80))


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("candidate_id", "job_id", name="uq_application"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    resume_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resume_variants.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="DISCOVERED", index=True)
    source: Mapped[str | None] = mapped_column(String(60))
    priority_class: Mapped[str | None] = mapped_column(String(10))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recruiter: Mapped[str | None] = mapped_column(String(200))
    hiring_manager: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(20), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutreachDraft(Base):
    """Outreach is drafted for review only; UnJob never sends it."""

    __tablename__ = "outreach_drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(20))
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_role: Mapped[str | None] = mapped_column(String(200))
    recipient_url: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SearchResultRecord(Base):
    """Persisted web-search results: evidence for discovery and a short-lived query cache."""

    __tablename__ = "search_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40))
    query: Mapped[str] = mapped_column(String(500), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="SET NULL")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ProviderHealth(Base):
    __tablename__ = "provider_health"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_latency_ms: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
