from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Empty variables (for example ``LLM_PROVIDER=`` from docker-compose) mean "not set".
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_name: str = "JobOS API"
    database_url: str = "sqlite+aiosqlite:///./jobos.db"
    api_cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    freshness_hours: int = Field(default=48, ge=1, le=720)
    secondary_freshness_hours: int = Field(default=168, ge=1, le=2160)
    # Relevant jobs up to this age are ingested (the "7+ days" bucket); older ones are skipped.
    max_job_age_hours: float = Field(default=720, ge=1, le=4320)
    high_fit_threshold: float = Field(default=75, ge=0, le=100)
    request_timeout_seconds: float = Field(default=20, gt=0, le=120)
    scoring_weights: dict[str, float] = {
        "jd_fit": 0.30,
        "technical_value": 0.15,
        "freshness": 0.15,
        "company": 0.10,
        "compensation": 0.10,
        "role_alignment": 0.10,
        "application_friction": 0.10,
    }

    # Optional search backends. Discovery remains functional when these are absent.
    searxng_url: str | None = None
    brave_search_api_key: str | None = None
    tavily_api_key: str | None = None

    # Optional AI rewording of resume bullets. Every rewrite is verified against the
    # candidate's facts and discarded if it adds anything unsupported. Without a key the
    # tailoring engine is fully deterministic.
    llm_provider: str | None = Field(default=None, pattern=r"^(openai|azure)$")
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_version: str = "2024-10-21"
    llm_timeout_seconds: float = Field(default=60, gt=0, le=300)

    # Local storage for uploaded master resumes and generated resume files (personal data).
    data_dir: str = "./data"

    discovery_user_agent: str = (
        "Mozilla/5.0 (compatible; UnJobDiscovery/0.2; local personal job-market research)"
    )
    discovery_robots_token: str = "UnJobDiscovery"
    discovery_max_response_bytes: int = Field(default=10_000_000, ge=100_000, le=50_000_000)
    discovery_host_min_interval_seconds: float = Field(default=1.0, ge=0, le=60)
    discovery_search_min_interval_seconds: float = Field(default=10.0, ge=0, le=300)
    discovery_max_retries: int = Field(default=2, ge=0, le=5)
    discovery_rate_limit_cooldown_seconds: float = Field(default=900, ge=0, le=86_400)

    discovery_scheduler_enabled: bool = True
    discovery_scan_interval_hours: float = Field(default=6, gt=0, le=720)
    discovery_exploration_interval_hours: float = Field(default=24, gt=0, le=720)
    discovery_reconciliation_interval_hours: float = Field(default=24, gt=0, le=720)
    discovery_frontier_interval_hours: float = Field(default=1, gt=0, le=720)

    worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=300, ge=30, le=7200)


@lru_cache
def get_settings() -> Settings:
    return Settings()
