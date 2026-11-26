# Aliases are literals so check_env_example.py can grep for them.
from __future__ import annotations

from decimal import Decimal
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
        default="SimplifyJobs/Summer2027-Internships,SimplifyJobs/New-Grad-Positions",
        alias="SCOUT_SIMPLIFY_REPOS",
    )
    # Ouckah deleted both internship repos; empty means the source is skipped.
    scout_ouckah_repos: str = Field(default="", alias="SCOUT_OUCKAH_REPOS")

    # Unauthenticated GitHub allows 60 requests/hour; a token raises it to 5000.
    github_token: SecretStr | None = Field(default=None, alias="GITHUB_TOKEN")

    dedup_merge_threshold: float = Field(default=0.92, ge=0, le=1, alias="DEDUP_MERGE_THRESHOLD")
    dedup_review_threshold: float = Field(default=0.84, ge=0, le=1, alias="DEDUP_REVIEW_THRESHOLD")
    # §8: an ATS posting missing for 2 consecutive polls closes; one miss must not.
    dedup_missed_poll_limit: int = Field(default=2, ge=2, alias="DEDUP_MISSED_POLL_LIMIT")
    dedup_stale_after_days: int = Field(default=21, ge=1, alias="DEDUP_STALE_AFTER_DAYS")

    # Retrieval (M4). EmbeddingProvider selection; see AGENTS.md §5/§8.
    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    ollama_url: str = Field(default="http://host.docker.internal:11434", alias="OLLAMA_URL")
    embedding_model: str = Field(default="nomic-embed-text", alias="EMBEDDING_MODEL")
    embedding_batch_size: int = Field(default=32, ge=1, alias="EMBEDDING_BATCH_SIZE")
    embedding_timeout_seconds: float = Field(default=60.0, gt=0, alias="EMBEDDING_TIMEOUT_SECONDS")

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_provider: str = Field(default="anthropic", alias="LLM_PROVIDER")
    llm_score_model: str = Field(default="claude-haiku-5-5", alias="LLM_SCORE_MODEL")
    ollama_llm_model: str = Field(default="", alias="OLLAMA_LLM_MODEL")
    score_prompt_version: str = Field(default="score_v1", alias="SCORE_PROMPT_VERSION")
    run_budget_usd: Decimal = Field(default=Decimal("0.50"), ge=0, alias="RUN_BUDGET_USD")
    max_llm_calls_per_run: int = Field(default=100, ge=1, alias="MAX_LLM_CALLS_PER_RUN")
    llm_max_tokens_score: int = Field(default=400, ge=1, alias="LLM_MAX_TOKENS_SCORE")
    llm_timeout_seconds: float = Field(default=60.0, gt=0, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=3, ge=0, alias="LLM_MAX_RETRIES")
    llm_retry_base_seconds: float = Field(default=1.0, gt=0, alias="LLM_RETRY_BASE_SECONDS")
    llm_retry_max_seconds: float = Field(default=30.0, gt=0, alias="LLM_RETRY_MAX_SECONDS")
    # USD per million tokens for LLM_SCORE_MODEL; Ollama is always free.
    llm_price_input_per_mtok: Decimal = Field(
        default=Decimal("0.10"), ge=0, alias="LLM_PRICE_INPUT_PER_MTOK"
    )
    llm_price_output_per_mtok: Decimal = Field(
        default=Decimal("0.50"), ge=0, alias="LLM_PRICE_OUTPUT_PER_MTOK"
    )
    llm_price_cache_read_per_mtok: Decimal = Field(
        default=Decimal("0.01"), ge=0, alias="LLM_PRICE_CACHE_READ_PER_MTOK"
    )
    llm_price_cache_write_per_mtok: Decimal = Field(
        default=Decimal("0.125"), ge=0, alias="LLM_PRICE_CACHE_WRITE_PER_MTOK"
    )

    gate1_shortlist_size: int = Field(default=20, ge=1, alias="GATE1_SHORTLIST_SIZE")
    auto_forward_top_n: int = Field(default=0, ge=0, alias="AUTO_FORWARD_TOP_N")
    # Comma-separated; empty admits every value.
    prefilter_terms: str = Field(default="", alias="PREFILTER_TERMS")
    prefilter_locations: str = Field(default="", alias="PREFILTER_LOCATIONS")

    @model_validator(mode="after")
    def _llm_provider_is_known(self) -> Settings:
        if self.llm_provider not in ("anthropic", "ollama"):
            raise ValueError("LLM_PROVIDER must be 'anthropic' or 'ollama'")
        if self.llm_provider == "ollama" and not self.ollama_llm_model:
            raise ValueError("LLM_PROVIDER=ollama needs OLLAMA_LLM_MODEL")
        return self

    @model_validator(mode="after")
    def _llm_retry_window_is_ordered(self) -> Settings:
        if self.llm_retry_max_seconds < self.llm_retry_base_seconds:
            raise ValueError("LLM_RETRY_MAX_SECONDS must be >= LLM_RETRY_BASE_SECONDS")
        return self

    @model_validator(mode="after")
    def _retry_window_is_ordered(self) -> Settings:
        if self.scout_retry_max_seconds < self.scout_retry_base_seconds:
            raise ValueError(
                "SCOUT_RETRY_MAX_SECONDS must be >= SCOUT_RETRY_BASE_SECONDS, "
                "otherwise the cap truncates the first delay"
            )
        return self

    @model_validator(mode="after")
    def _dedup_thresholds_are_ordered(self) -> Settings:
        if self.dedup_merge_threshold < self.dedup_review_threshold:
            raise ValueError(
                "DEDUP_MERGE_THRESHOLD must be >= DEDUP_REVIEW_THRESHOLD, "
                "otherwise 'review' never fires below the merge cutoff"
            )
        return self

    @model_validator(mode="after")
    def _embedding_provider_is_known(self) -> Settings:
        if self.embedding_provider != "ollama":
            raise ValueError(
                "EMBEDDING_PROVIDER must be 'ollama'; EmbeddingProvider has no "
                "other implementation, so the agents service would fail to "
                "start rather than fall back to a default"
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

    @property
    def prefilter_term_list(self) -> tuple[str, ...]:
        return _split_csv(self.prefilter_terms)

    @property
    def prefilter_location_list(self) -> tuple[str, ...]:
        return _split_csv(self.prefilter_locations)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
