# Aliases are literals so check_env_example.py can grep for them.
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = Field(default="dev", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+asyncpg://offerradar:replace-me-for-local-dev@postgres:5432/offerradar",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    host: str = Field(default="0.0.0.0", alias="AGENTS_HOST")  # noqa: S104
    port: int = Field(default=8000, alias="AGENTS_PORT")

    # Rule 3 requires a descriptive User-Agent on every outbound request.
    scout_user_agent: str = Field(
        default="OfferRadar/0.1 (personal co-op job-search tool)",
        alias="SCOUT_USER_AGENT",
    )
    scout_http_timeout_seconds: float = Field(
        default=20.0, gt=0, alias="SCOUT_HTTP_TIMEOUT_SECONDS"
    )
    scout_max_retries: int = Field(default=4, ge=0, alias="SCOUT_MAX_RETRIES")
    scout_retry_base_seconds: float = Field(default=1.0, gt=0, alias="SCOUT_RETRY_BASE_SECONDS")
    scout_retry_max_seconds: float = Field(default=30.0, gt=0, alias="SCOUT_RETRY_MAX_SECONDS")
    scout_rate_limit_rps: float = Field(default=1.0, gt=0, alias="SCOUT_RATE_LIMIT_RPS")
    scout_rate_limit_burst: int = Field(default=5, ge=1, alias="SCOUT_RATE_LIMIT_BURST")
    scout_robots_cache_ttl_seconds: int = Field(
        default=3600, ge=0, alias="SCOUT_ROBOTS_CACHE_TTL_SECONDS"
    )
    # Per source: an adapter must not infer its `source` from a repo owner.
    scout_simplify_repos: str = Field(
        default="SimplifyJobs/Summer2026-Internships,SimplifyJobs/Off-Season-Internships",
        alias="SCOUT_SIMPLIFY_REPOS",
    )
    scout_ouckah_repos: str = Field(
        default="Ouckah/Summer2026-Internships", alias="SCOUT_OUCKAH_REPOS"
    )

    # Unauthenticated GitHub allows 60 requests/hour; a token raises it to 5000.
    github_token: SecretStr | None = Field(default=None, alias="GITHUB_TOKEN")

    @model_validator(mode="after")
    def _retry_window_is_ordered(self) -> Settings:
        if self.scout_retry_max_seconds < self.scout_retry_base_seconds:
            raise ValueError(
                "SCOUT_RETRY_MAX_SECONDS must be >= SCOUT_RETRY_BASE_SECONDS, "
                "otherwise the cap truncates the first delay"
            )
        return self

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def simplify_repos(self) -> tuple[str, ...]:
        return _split_csv(self.scout_simplify_repos)

    @property
    def ouckah_repos(self) -> tuple[str, ...]:
        return _split_csv(self.scout_ouckah_repos)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
