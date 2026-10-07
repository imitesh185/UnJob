import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)

from app.discovery.settings_store import PartialBudgets


class UTCResponse(BaseModel):
    """Stored timestamps are UTC, but SQLite returns them without a zone; mark them as UTC so
    clients do not read them as local time. ``posted_at`` keeps the source's precision."""

    @field_validator("*", mode="after")
    @classmethod
    def _mark_utc(cls, value: Any, info: ValidationInfo) -> Any:
        if info.field_name != "posted_at" and isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class IngestedJob(BaseModel):
    company: str
    title: str
    location: str = "Unknown"
    remote_status: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    salary: str | None = None
    employment_type: str | None = None
    application_url: str
    source_url: str
    source: str
    ats: str
    external_id: str
    posted_at: datetime | None = None
    # "datetime" (zone-aware), "local_datetime" (no zone) or "date" (calendar date only).
    posted_at_precision: Literal["datetime", "local_datetime", "date"] | None = None
    source_updated_at: datetime | None = None
    description: str
    raw_payload: dict[str, Any]

    @model_validator(mode="after")
    def _default_precision(self) -> "IngestedJob":
        if self.posted_at is None:
            self.posted_at_precision = None
        elif self.posted_at_precision is None:
            self.posted_at_precision = "datetime" if self.posted_at.tzinfo else "local_datetime"
        return self


class ScoreResult(BaseModel):
    overall: float
    breakdown: dict[str, float]
    reasons: list[str]


class JobSourceRead(UTCResponse):
    model_config = ConfigDict(from_attributes=True)
    source: str
    external_id: str
    source_url: str
    career_source_id: uuid.UUID | None = None
    last_seen_at: datetime | None = None
    listing_status: str = "OPEN"


class JobRead(UTCResponse):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company: str
    title: str
    location: str
    remote_status: str
    salary: str | None
    employment_type: str | None
    ats: str
    posted_at: datetime | None
    posted_at_precision: str | None = None
    source_updated_at: datetime | None = None
    discovered_at: datetime
    last_seen_at: datetime | None = None
    listing_status: str = "OPEN"
    closed_at: datetime | None = None
    description: str
    requirements: list[str]
    preferred_requirements: list[str]
    technology_stack: list[str]
    experience_requirement: str | None
    application_url: str
    status: str
    overall_score: float
    score_breakdown: dict[str, float]
    score_reasons: list[str]
    company_id: uuid.UUID | None = None
    role_category: str = "unclassified"
    role_relevance: float = 0.0
    # Set by the API from the runtime freshness window; "unknown" when no posting date exists.
    freshness_status: str = "unknown"
    sources: list[JobSourceRead]

    @model_validator(mode="after")
    def _posted_at_zone(self) -> "JobRead":
        # A "local_datetime" posting time has no known zone; every other precision is UTC.
        if (
            self.posted_at is not None
            and self.posted_at.tzinfo is None
            and self.posted_at_precision != "local_datetime"
        ):
            self.posted_at = self.posted_at.replace(tzinfo=UTC)
        return self

    @computed_field
    @property
    def source(self) -> str | None:
        return self.sources[0].source if self.sources else None

    @computed_field
    @property
    def age_hours(self) -> float | None:
        if not self.posted_at:
            return None
        posted = self.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=UTC)
        return round(max(0, (datetime.now(UTC) - posted).total_seconds() / 3600), 1)


class JobPage(BaseModel):
    items: list[JobRead]
    total: int


class GreenhouseRequest(BaseModel):
    board_token: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=100)


class LeverRequest(BaseModel):
    company_slug: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=100)


class IngestionResult(BaseModel):
    discovered: int
    created: int
    deduplicated: int
    stale_skipped: int
    updated: int = 0
    irrelevant_skipped: int = 0
    closed: int = 0


class DashboardSummary(BaseModel):
    jobs_discovered_today: int
    high_fit_jobs: int
    applications_prepared: int
    applications_submitted: int
    blocked_applications: int
    human_reviews: int
    outreach_opportunities: int
    interviews: int


class CompanyRead(UTCResponse):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    aliases: list[str] = []
    legal_name: str | None = None
    domain: str | None
    careers_url: str | None
    linkedin_url: str | None = None
    x_url: str | None = None
    tier: str
    tier_source: str = "default"
    archetype: str | None = None
    status: str
    review_status: str
    is_seed: bool
    discovery_score: float
    confidence: float
    discovery_reason: str
    discovery_source: str
    industry: str | None
    company_type: str | None = None
    company_stage: str | None
    headquarters: str | None
    india_presence: bool | None
    remote_presence: bool | None
    employee_range: str | None = None
    funding_stage: str | None = None
    funding: str | None = None
    known_technologies: list[str]
    data_infrastructure_signals: list[str] = []
    engineering_signals: list[str] = []
    score_breakdown: dict[str, float]
    score_reasons: list[str]
    jobs_count: int = 0
    data_roles_count: int = 0
    fresh_data_roles_count: int = 0
    platform_roles_count: int = 0
    # Candidate-aware company target score (None until your profile has been analysed).
    target_score: float | None = None
    target_breakdown: dict[str, float] = {}
    target_reasons: list[str] = []
    last_scanned_at: datetime | None
    last_explored_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CompanyPage(BaseModel):
    items: list[CompanyRead]
    total: int


class CareerSourceRead(UTCResponse):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    url: str
    platform: str
    platform_confidence: float
    platform_identifier: dict[str, str] = {}
    active: bool
    discovered_via: str
    evidence: str | None
    scan_status: str
    error: str | None
    jobs_found: int
    relevant_jobs_found: int
    last_scanned_at: datetime | None
    last_success_at: datetime | None
    last_complete_scan_at: datetime | None
    created_at: datetime


class CareerSourceCreated(CareerSourceRead):
    # The company scan queued for the new source; ``None`` when no scan was queued.
    scan_run_id: uuid.UUID | None = None


class EventRead(UTCResponse):
    id: uuid.UUID
    company_id: uuid.UUID | None
    company_name: str
    source: str
    source_url: str | None
    reason: str
    evidence: str
    evidence_data: dict[str, Any] = {}
    confidence: float
    discovery_score: float
    status: Literal["PENDING", "ACCEPTED", "IGNORED"]
    observation_count: int
    discovered_at: datetime
    last_seen_at: datetime


class EventPage(BaseModel):
    items: list[EventRead]
    total: int


class RelationshipRead(BaseModel):
    id: uuid.UUID
    source_company_id: uuid.UUID
    source_company_name: str
    target_company_id: uuid.UUID
    target_company_name: str
    relationship_type: str
    confidence: float
    evidence: str
    evidence_url: str | None


class CompanyDetail(CompanyRead):
    sources: list[CareerSourceRead]
    events: list[EventRead]
    relationships: list[RelationshipRead]


def _clean_names(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        raise ValueError("Provide at least one non-blank company name.")
    if any(len(value) > 200 for value in cleaned):
        raise ValueError("Company names must be at most 200 characters.")
    return cleaned


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(min_length=1, max_length=1000)
    tier: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _validate(self) -> "SeedRequest":
        self.names = _clean_names(self.names)
        self.tier = self.tier.strip() if self.tier and self.tier.strip() else None
        return self


class SeedResponse(BaseModel):
    companies: list[CompanyRead]
    run_id: uuid.UUID | None


class CompanyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    tier: str | None = Field(default=None, max_length=40)
    status: Literal["DISCOVERED", "UNVERIFIED", "VERIFIED", "ACTIVE", "INACTIVE"] | None = None
    domain: str | None = Field(default=None, max_length=253)
    careers_url: str | None = Field(default=None, max_length=2000)


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2000)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["exploration", "scan", "reconciliation", "frontier", "candidate_analysis"]
    budgets: PartialBudgets | None = None


class RunRead(UTCResponse):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    trigger: str
    params: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    companies_discovered: int
    jobs_inspected: int
    jobs_created: int
    attempts: int
    error: str | None
    errors: list[str]
    budgets: dict[str, Any]
    progress: dict[str, Any]


class RunPage(BaseModel):
    items: list[RunRead]
    total: int


class FrontierRead(UTCResponse):
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    reason: str
    priority: float
    priority_breakdown: dict[str, float]
    status: str
    depth: int
    error: str | None
    created_at: datetime


class FrontierPage(BaseModel):
    items: list[FrontierRead]
    total: int


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "ignore"]


class DiscoverySummaryRead(BaseModel):
    companies_total: int
    companies_discovered_today: int
    high_confidence: int
    medium_confidence: int
    low_confidence: int
    frontier_pending: int
    active_runs: int
    fresh_data_jobs: int
    pending_reviews: int
