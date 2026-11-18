"""Runtime configuration.

Every field declares an explicit uppercase alias so the variable name is a
string literal in the source. scripts/check_env_example.py greps for those
literals to prove .env.example documents everything the code reads.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
