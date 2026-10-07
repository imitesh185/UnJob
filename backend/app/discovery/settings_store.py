"""Runtime-configurable discovery settings persisted in the database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.intelligence.positioning import DEFAULT_ARCHETYPES, DEFAULT_COMPANY_POSITIONING
from app.models import AppSetting

SETTINGS_KEY = "discovery_settings"

COMPANY_SCORE_WEIGHTS = {
    "data_hiring_signal": 0.30,
    "engineering_quality": 0.20,
    "recent_hiring": 0.15,
    "role_relevance": 0.15,
    "india_remote_opportunity": 0.10,
    "growth_potential": 0.10,
}
PRIORITY_WEIGHTS = {
    "de_openings_5plus": 50,
    "de_openings_3plus": 35,
    "de_openings_1plus": 15,
    "hiring_post": 30,
    "hiring_campaign": 20,
    "recent_funding": 10,
    "india_presence": 15,
    "product_company": 10,
    "fresh_signal": 15,
    "seed": 25,
}
DEFAULT_TIERS = ["S", "A", "B", "C", "UNCATEGORIZED"]
SETTINGS_VERSION = 2
# From the user's tailoring brief; editable in settings, never hard-coded elsewhere.
DEFAULT_S_TIER = [
    "Amazon", "Microsoft", "Uber", "Google", "Apple", "Meta", "Netflix", "NVIDIA", "Adobe",
    "Salesforce", "Oracle", "Databricks", "Snowflake", "LinkedIn", "JPMorgan Chase",
    "Morgan Stanley", "Visa", "Mastercard", "Walmart Global Tech", "Atlassian", "Airbnb", "Stripe",
]  # fmt: skip
# Public career portals verified by live scans (October 2026). Each is still checked on use:
# a portal that stops answering is marked BLOCKED/NOT_FOUND rather than trusted.
DEFAULT_COMPANY_REGISTRY: list[dict[str, Any]] = [
    {"name": "Amazon", "aliases": [], "career_urls": ["https://www.amazon.jobs/en"]},
    {
        "name": "NVIDIA",
        "aliases": [],
        "career_urls": ["https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"],
    },
    {
        "name": "Adobe",
        "aliases": [],
        "career_urls": ["https://adobe.wd5.myworkdayjobs.com/external_experienced"],
    },
    {
        "name": "Databricks",
        "aliases": [],
        "career_urls": ["https://boards.greenhouse.io/databricks"],
    },
    {"name": "Snowflake", "aliases": [], "career_urls": ["https://jobs.ashbyhq.com/snowflake"]},
    {
        "name": "JPMorgan Chase",
        "aliases": ["JPMorgan", "JPMC", "J.P. Morgan", "JP Morgan"],
        "career_urls": [
            "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001"
        ],
    },
    {
        "name": "Mastercard",
        "aliases": [],
        "career_urls": ["https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"],
    },
    {
        "name": "Salesforce",
        "aliases": [],
        "career_urls": ["https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"],
    },
    {"name": "Airbnb", "aliases": [], "career_urls": ["https://boards.greenhouse.io/airbnb"]},
    {"name": "Stripe", "aliases": [], "career_urls": ["https://boards.greenhouse.io/stripe"]},
    {
        "name": "Oracle",
        "aliases": [],
        "career_urls": [
            "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch"
        ],
    },
    {"name": "Walmart Global Tech", "aliases": ["Walmart"], "career_urls": []},
    {"name": "Microsoft", "aliases": [], "career_urls": []},
    {"name": "Uber", "aliases": [], "career_urls": []},
    {"name": "Visa", "aliases": [], "career_urls": []},
]
DEFAULT_TAILORING_POLICY: dict[str, dict[str, Any]] = {
    "S": {"mode": "all_relevant"},
    "A": {"mode": "min_fit", "threshold": 70},
    "B": {"mode": "priority", "classes": ["P0", "P1"]},
    "C": {"mode": "min_priority_score", "threshold": 72},
    "UNCATEGORIZED": {"mode": "priority", "classes": ["P0"]},
}
TARGET_SCORE_WEIGHTS = {
    "hiring_activity": 0.20,
    "role_relevance": 0.15,
    "engineering_quality": 0.10,
    "product_quality": 0.10,
    "career_growth": 0.10,
    "compensation_potential": 0.05,
    "india_remote_fit": 0.15,
    "technical_relevance": 0.15,
}
FIT_WEIGHTS = {
    "skills": 0.50,
    "experience": 0.10,
    "role": 0.15,
    "seniority": 0.10,
    "location": 0.10,
    "domain": 0.03,
    "compensation": 0.02,
}
APPLICATION_PRIORITY_WEIGHTS = {
    "fit": 0.45,
    "company_target": 0.25,
    "freshness": 0.15,
    "career_value": 0.15,
}
APPLICATION_PRIORITY_THRESHOLDS = {"P0": 80.0, "P1": 68.0, "P2": 55.0, "P3": 40.0}
TIER_VALUES = {"S": 100.0, "A": 85.0, "B": 70.0, "C": 55.0, "UNCATEGORIZED": 50.0}
DEFAULT_ROLES = [
    "Data Engineer",
    "Senior Data Engineer",
    "Data Platform Engineer",
    "Data Infrastructure Engineer",
    "Big Data Engineer",
    "Distributed Systems Engineer",
    "Software Engineer Data",
]
DEFAULT_LOCATIONS = [
    "India",
    "Bengaluru",
    "Pune",
    "Mumbai",
    "Hyderabad",
    "Gurugram",
    "Remote India",
]
PROVIDERS = [
    "searxng",
    "duckduckgo",
    "brave",
    "tavily",
    "hackernews",
    "remotive",
    "yc_oss",
    "linkedin_via_search",
    "x_via_search",
]


class ExplorationBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_new_companies: int = Field(default=25, ge=0, le=1000)
    max_search_queries: int = Field(default=8, ge=0, le=200)
    max_pages: int = Field(default=150, ge=0, le=5000)
    max_results_per_query: int = Field(default=20, ge=0, le=50)
    max_company_expansion: int = Field(default=10, ge=0, le=200)
    max_depth: int = Field(default=2, ge=0, le=5)


class PartialBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_new_companies: int | None = Field(default=None, ge=0, le=1000)
    max_search_queries: int | None = Field(default=None, ge=0, le=200)
    max_pages: int | None = Field(default=None, ge=0, le=5000)
    max_results_per_query: int | None = Field(default=None, ge=0, le=50)
    max_company_expansion: int | None = Field(default=None, ge=0, le=200)
    max_depth: int | None = Field(default=None, ge=0, le=5)


class DailyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_queries: int = Field(default=200, ge=0, le=5000)
    new_companies: int = Field(default=1000, ge=0, le=20000)
    pages: int = Field(default=10000, ge=0, le=200000)


def _unique_labels(values: list[str], label: str, max_length: int) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        raise ValueError(f"At least one {label} is required.")
    if len({value.lower() for value in cleaned}) != len(cleaned):
        raise ValueError(f"{label.capitalize()}s must be unique.")
    if any(len(value) > max_length for value in cleaned):
        raise ValueError(f"Each {label} must be at most {max_length} characters.")
    return cleaned


def _weights(value: dict[str, float], allowed: dict[str, float], label: str) -> dict[str, float]:
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {label}: {', '.join(sorted(unknown))}.")
    merged = {**allowed, **value}
    if any(weight < 0 for weight in merged.values()):
        raise ValueError(f"{label.capitalize()} must be nonnegative.")
    if sum(merged.values()) <= 0:
        raise ValueError(f"At least one {label[:-1]} must be greater than zero.")
    return {key: float(merged[key]) for key in allowed}


class RegistryEntry(BaseModel):
    """A company name with aliases and verified public career portals (all user-editable)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    career_urls: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("career_urls")
    @classmethod
    def _urls(cls, value: list[str]) -> list[str]:
        cleaned = [url.strip() for url in value if url and url.strip()]
        for url in cleaned:
            if not url.startswith("https://") or len(url) > 500:
                raise ValueError("Career URLs must be https:// URLs of at most 500 characters.")
        return cleaned


class TailoringRule(BaseModel):
    """When resumes are tailored automatically for a tier.

    ``all_relevant``: every relevant job; ``min_fit``: fit >= threshold; ``priority``: priority
    class in ``classes``; ``min_priority_score``: priority score >= threshold; ``off``: never.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["all_relevant", "min_fit", "priority", "min_priority_score", "off"]
    threshold: float | None = Field(default=None, ge=0, le=100)
    classes: list[Literal["P0", "P1", "P2", "P3"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _complete(self) -> TailoringRule:
        if self.mode in {"min_fit", "min_priority_score"} and self.threshold is None:
            raise ValueError(f"Tailoring mode '{self.mode}' needs a threshold.")
        if self.mode == "priority" and not self.classes:
            raise ValueError("Tailoring mode 'priority' needs at least one priority class.")
        return self


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings_version: int = SETTINGS_VERSION
    tiers: list[str] = Field(default_factory=lambda: list(DEFAULT_TIERS), max_length=20)
    default_tier: str = "UNCATEGORIZED"
    freshness_hours: float = Field(default=48, gt=0, le=720)
    secondary_freshness_hours: float = Field(default=168, gt=0, le=2160)
    # Relevant jobs older than the secondary window are still kept (the "7+ days" bucket) up to
    # this age; they rank below fresh ones.
    max_job_age_hours: float = Field(default=720, gt=0, le=4320)
    budgets: ExplorationBudgets = Field(default_factory=ExplorationBudgets)
    daily_limits: DailyLimits = Field(default_factory=DailyLimits)
    company_score_weights: dict[str, float] = Field(
        default_factory=lambda: dict(COMPANY_SCORE_WEIGHTS)
    )
    company_priority_weights: dict[str, float] = Field(
        default_factory=lambda: dict(PRIORITY_WEIGHTS)
    )
    scan_interval_hours: float = Field(default=6, gt=0, le=720)
    exploration_interval_hours: float = Field(default=24, gt=0, le=720)
    reconciliation_interval_hours: float = Field(default=24, gt=0, le=720)
    frontier_interval_hours: float = Field(default=1, gt=0, le=720)
    scheduler_enabled: bool = True
    target_roles: list[str] = Field(default_factory=lambda: list(DEFAULT_ROLES), max_length=30)
    target_locations: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LOCATIONS), max_length=30
    )
    startup_regions: list[str] = Field(default_factory=lambda: ["India", "Remote"], max_length=10)
    providers: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(PROVIDERS, True))
    # Phase 3: targeting, tailoring and prioritisation.
    s_tier_companies: list[str] = Field(
        default_factory=lambda: list(DEFAULT_S_TIER), max_length=200
    )
    company_registry: list[RegistryEntry] = Field(
        default_factory=lambda: [RegistryEntry(**entry) for entry in DEFAULT_COMPANY_REGISTRY],
        max_length=500,
    )
    tailoring_policy: dict[str, TailoringRule] = Field(
        default_factory=lambda: {
            tier: TailoringRule(**rule) for tier, rule in DEFAULT_TAILORING_POLICY.items()
        }
    )
    tailoring_require_location_fit: bool = True
    max_variants_per_run: int = Field(default=30, ge=0, le=500)
    llm_rewrite_enabled: bool = True
    target_score_weights: dict[str, float] = Field(
        default_factory=lambda: dict(TARGET_SCORE_WEIGHTS)
    )
    fit_weights: dict[str, float] = Field(default_factory=lambda: dict(FIT_WEIGHTS))
    application_priority_weights: dict[str, float] = Field(
        default_factory=lambda: dict(APPLICATION_PRIORITY_WEIGHTS)
    )
    application_priority_thresholds: dict[str, float] = Field(
        default_factory=lambda: dict(APPLICATION_PRIORITY_THRESHOLDS)
    )
    tier_values: dict[str, float] = Field(default_factory=lambda: dict(TIER_VALUES))
    rusty_after_years: float = Field(default=3.0, gt=0, le=20)
    positioning_profiles: dict[str, list[str]] = Field(
        default_factory=lambda: {
            name: list(values) for name, values in DEFAULT_COMPANY_POSITIONING.items()
        }
    )
    archetype_signals: dict[str, list[str]] = Field(
        default_factory=lambda: {name: list(values) for name, values in DEFAULT_ARCHETYPES.items()}
    )

    @field_validator("tiers")
    @classmethod
    def _tiers(cls, value: list[str]) -> list[str]:
        return _unique_labels(value, "tier", 40)

    @field_validator("target_roles", "target_locations", "startup_regions")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        return _unique_labels(value, "value", 80)

    @field_validator("s_tier_companies")
    @classmethod
    def _s_tier(cls, value: list[str]) -> list[str]:
        cleaned = [name.strip() for name in value if name and name.strip()]
        if any(len(name) > 200 for name in cleaned):
            raise ValueError("Company names must be at most 200 characters.")
        return list(dict.fromkeys(cleaned))

    @field_validator("company_score_weights")
    @classmethod
    def _score_weights(cls, value: dict[str, float]) -> dict[str, float]:
        return _weights(value, COMPANY_SCORE_WEIGHTS, "score weights")

    @field_validator("company_priority_weights")
    @classmethod
    def _priority_weights(cls, value: dict[str, float]) -> dict[str, float]:
        return _weights(value, PRIORITY_WEIGHTS, "priority weights")

    @field_validator("target_score_weights")
    @classmethod
    def _target_weights(cls, value: dict[str, float]) -> dict[str, float]:
        return _weights(value, TARGET_SCORE_WEIGHTS, "target score weights")

    @field_validator("fit_weights")
    @classmethod
    def _fit_weights(cls, value: dict[str, float]) -> dict[str, float]:
        return _weights(value, FIT_WEIGHTS, "fit weights")

    @field_validator("application_priority_weights")
    @classmethod
    def _application_weights(cls, value: dict[str, float]) -> dict[str, float]:
        return _weights(value, APPLICATION_PRIORITY_WEIGHTS, "application priority weights")

    @field_validator("application_priority_thresholds")
    @classmethod
    def _thresholds(cls, value: dict[str, float]) -> dict[str, float]:
        merged = {**APPLICATION_PRIORITY_THRESHOLDS, **value}
        if set(merged) != set(APPLICATION_PRIORITY_THRESHOLDS):
            raise ValueError("Priority thresholds must be P0, P1, P2 and P3.")
        values = [merged[name] for name in ("P0", "P1", "P2", "P3")]
        if any(not 0 <= item <= 100 for item in values) or values != sorted(values, reverse=True):
            raise ValueError(
                "Priority thresholds must be between 0 and 100 with P0 >= P1 >= P2 >= P3."
            )
        return {name: float(merged[name]) for name in ("P0", "P1", "P2", "P3")}

    @field_validator("tier_values")
    @classmethod
    def _tier_values(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not 0 <= item <= 100 for item in value.values()):
            raise ValueError("Tier values must be between 0 and 100.")
        return {name.strip(): float(item) for name, item in value.items() if name.strip()}

    @field_validator("positioning_profiles", "archetype_signals")
    @classmethod
    def _signals(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        cleaned: dict[str, list[str]] = {}
        for name, signals in value.items():
            items = [signal.strip() for signal in signals if signal and signal.strip()]
            if name.strip() and items:
                cleaned[name.strip()] = items[:20]
        return cleaned

    @field_validator("providers")
    @classmethod
    def _providers(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = set(value) - set(PROVIDERS)
        if unknown:
            raise ValueError(f"Unknown providers: {', '.join(sorted(unknown))}.")
        return {name: bool(value.get(name, True)) for name in PROVIDERS}

    @model_validator(mode="after")
    def _consistency(self) -> RuntimeSettings:
        if self.default_tier not in self.tiers:
            self.default_tier = "UNCATEGORIZED" if "UNCATEGORIZED" in self.tiers else self.tiers[-1]
        if self.secondary_freshness_hours < self.freshness_hours:
            raise ValueError("secondary_freshness_hours must be at least freshness_hours.")
        if self.max_job_age_hours < self.secondary_freshness_hours:
            raise ValueError("max_job_age_hours must be at least secondary_freshness_hours.")
        if self.s_tier_companies and "S" not in self.tiers:
            raise ValueError("Keep tier 'S' while the S-tier company list is not empty.")
        # Policies for tiers that no longer exist are dropped rather than rejected.
        self.tailoring_policy = {
            tier: rule for tier, rule in self.tailoring_policy.items() if tier in self.tiers
        }
        return self


class RuntimeSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tiers: list[str] | None = None
    default_tier: str | None = None
    freshness_hours: float | None = Field(default=None, gt=0, le=720)
    secondary_freshness_hours: float | None = Field(default=None, gt=0, le=2160)
    max_job_age_hours: float | None = Field(default=None, gt=0, le=4320)
    budgets: PartialBudgets | None = None
    daily_limits: dict[str, int] | None = None
    company_score_weights: dict[str, float] | None = None
    company_priority_weights: dict[str, float] | None = None
    scan_interval_hours: float | None = Field(default=None, gt=0, le=720)
    exploration_interval_hours: float | None = Field(default=None, gt=0, le=720)
    reconciliation_interval_hours: float | None = Field(default=None, gt=0, le=720)
    frontier_interval_hours: float | None = Field(default=None, gt=0, le=720)
    scheduler_enabled: bool | None = None
    target_roles: list[str] | None = None
    target_locations: list[str] | None = None
    startup_regions: list[str] | None = None
    providers: dict[str, bool] | None = None
    s_tier_companies: list[str] | None = None
    company_registry: list[RegistryEntry] | None = None
    tailoring_policy: dict[str, TailoringRule] | None = None
    tailoring_require_location_fit: bool | None = None
    max_variants_per_run: int | None = Field(default=None, ge=0, le=500)
    llm_rewrite_enabled: bool | None = None
    target_score_weights: dict[str, float] | None = None
    fit_weights: dict[str, float] | None = None
    application_priority_weights: dict[str, float] | None = None
    application_priority_thresholds: dict[str, float] | None = None
    tier_values: dict[str, float] | None = None
    rusty_after_years: float | None = Field(default=None, gt=0, le=20)
    positioning_profiles: dict[str, list[str]] | None = None
    archetype_signals: dict[str, list[str]] | None = None


# Settings replaced as a whole on update, so entries can be removed.
_REPLACE_KEYS = {
    "positioning_profiles",
    "archetype_signals",
    "tailoring_policy",
    "tier_values",
    "company_registry",
}


def default_runtime(settings: Settings) -> RuntimeSettings:
    return RuntimeSettings(
        freshness_hours=settings.freshness_hours,
        secondary_freshness_hours=max(settings.secondary_freshness_hours, settings.freshness_hours),
        max_job_age_hours=max(
            settings.max_job_age_hours, settings.secondary_freshness_hours, settings.freshness_hours
        ),
        scan_interval_hours=settings.discovery_scan_interval_hours,
        exploration_interval_hours=settings.discovery_exploration_interval_hours,
        reconciliation_interval_hours=settings.discovery_reconciliation_interval_hours,
        frontier_interval_hours=settings.discovery_frontier_interval_hours,
        scheduler_enabled=settings.discovery_scheduler_enabled,
    )


def _merge(base: dict[str, Any], patch: dict[str, Any], *, top: bool = True) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if top and key in _REPLACE_KEYS and value is not None:
            merged[key] = value
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value, top=False)
        elif value is not None:
            merged[key] = value
    return merged


def _upgrade_stored(value: dict[str, Any]) -> dict[str, Any]:
    """Bring settings saved by an earlier version up to date without losing user choices."""
    value = dict(value)
    if int(value.get("settings_version") or 1) < 2:
        tiers = value.get("tiers")
        if isinstance(tiers, list) and "S" not in tiers:
            value["tiers"] = ["S", *tiers]
        value["settings_version"] = SETTINGS_VERSION
    return value


async def load_runtime_settings(session: AsyncSession, settings: Settings) -> RuntimeSettings:
    defaults = default_runtime(settings)
    row = await session.get(AppSetting, SETTINGS_KEY)
    if row is None or not row.value:
        return defaults
    try:
        return RuntimeSettings.model_validate(
            _merge(defaults.model_dump(), _upgrade_stored(row.value))
        )
    except ValidationError:
        return defaults


async def save_runtime_settings(
    session: AsyncSession, settings: Settings, patch: RuntimeSettingsPatch
) -> RuntimeSettings:
    current = await load_runtime_settings(session, settings)
    updated = RuntimeSettings.model_validate(
        _merge(current.model_dump(), patch.model_dump(exclude_none=True))
    )
    row = await session.get(AppSetting, SETTINGS_KEY)
    if row is None:
        session.add(AppSetting(key=SETTINGS_KEY, value=updated.model_dump(mode="json")))
    else:
        row.value = updated.model_dump(mode="json")
    await session.commit()
    return updated


def effective_settings(settings: Settings, runtime: RuntimeSettings) -> Settings:
    return settings.model_copy(
        update={
            "freshness_hours": runtime.freshness_hours,
            "secondary_freshness_hours": runtime.secondary_freshness_hours,
            "max_job_age_hours": runtime.max_job_age_hours,
        }
    )


async def cache_get(session: AsyncSession, key: str, ttl_seconds: float) -> Any | None:
    row = await session.get(AppSetting, f"cache:{key}")
    if row is None or not row.value:
        return None
    try:
        fetched = datetime.fromisoformat(row.value["fetched_at"])
    except (KeyError, ValueError):
        return None
    if datetime.now(UTC) - fetched > timedelta(seconds=ttl_seconds):
        return None
    return row.value.get("data")


async def cache_set(session: AsyncSession, key: str, data: Any) -> None:
    value = {"fetched_at": datetime.now(UTC).isoformat(), "data": data}
    row = await session.get(AppSetting, f"cache:{key}")
    if row is None:
        session.add(AppSetting(key=f"cache:{key}", value=value))
    else:
        row.value = value
    await session.commit()


def provider_statuses(settings: Settings, runtime: RuntimeSettings) -> list[dict[str, Any]]:
    flags = runtime.providers
    web_available = (
        (bool(settings.searxng_url) and flags["searxng"])
        or flags["duckduckgo"]
        or (bool(settings.brave_search_api_key) and flags["brave"])
        or (bool(settings.tavily_api_key) and flags["tavily"])
    )
    return [
        {
            "name": "duckduckgo",
            "enabled": flags["duckduckgo"],
            "requires_key": False,
            "detail": (
                "Primary no-key web search (html.duckduckgo.com; robots.txt permits access). "
                f"Paced at least {settings.discovery_search_min_interval_seconds:g}s per query. "
                "Rate-limit or anomaly pages are recorded as SOURCE_BLOCKED and never bypassed."
            ),
        },
        {
            "name": "searxng",
            "enabled": bool(settings.searxng_url) and flags["searxng"],
            "requires_key": False,
            "detail": (
                "Configured via SEARXNG_URL; preferred over DuckDuckGo when available."
                if settings.searxng_url
                else "Optional free backend. Set SEARXNG_URL to an instance you operate or may "
                "query, with JSON output enabled."
            ),
        },
        {
            "name": "brave",
            "enabled": bool(settings.brave_search_api_key) and flags["brave"],
            "requires_key": True,
            "detail": (
                "Configured via BRAVE_SEARCH_API_KEY."
                if settings.brave_search_api_key
                else "Optional. Not configured; discovery does not require it."
            ),
        },
        {
            "name": "tavily",
            "enabled": bool(settings.tavily_api_key) and flags["tavily"],
            "requires_key": True,
            "detail": (
                "Configured via TAVILY_API_KEY; preferred when available."
                if settings.tavily_api_key
                else "Optional search API (free tier with a key). Not configured; discovery does "
                "not require it."
            ),
        },
        {
            "name": "hackernews",
            "enabled": flags["hackernews"],
            "requires_key": False,
            "detail": (
                "No-key fallback: monthly HN 'Who is hiring?' posts via the public Algolia API."
            ),
        },
        {
            "name": "remotive",
            "enabled": flags["remotive"],
            "requires_key": False,
            "detail": "No-key fallback: public Remotive remote-jobs API, cached for 6 hours.",
        },
        {
            "name": "yc_oss",
            "enabled": flags["yc_oss"],
            "requires_key": False,
            "detail": (
                "No-key startup source: YC companies marked as hiring in the open yc-oss dataset "
                "(cached 24 hours). Candidates still require career-page verification."
            ),
        },
        {
            "name": "linkedin_via_search",
            "enabled": flags["linkedin_via_search"] and web_available,
            "requires_key": False,
            "detail": (
                "Public LinkedIn job pages as indexed by the web search backend. LinkedIn itself "
                "is never fetched, logged into, or scraped."
            ),
        },
        {
            "name": "x_via_search",
            "enabled": flags["x_via_search"] and web_available,
            "requires_key": False,
            "detail": (
                "Public X hiring posts as indexed by the web search backend; X is never fetched "
                "directly. Treated as low-confidence evidence."
            ),
        },
        {
            "name": "bing",
            "enabled": False,
            "requires_key": False,
            "detail": "Not used: bing.com/robots.txt disallows /search for automated agents.",
        },
    ]
