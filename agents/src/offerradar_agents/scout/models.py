from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Posting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    company_name: str = Field(min_length=1)
    normalized_company_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    normalized_title: str = Field(min_length=1)
    location: str | None = None
    remote: bool = False
    term: str | None = None
    canonical_url: str = Field(min_length=1)
    # sha256 hexdigest, lowercase, full width: the value is permanent once rows exist.
    dedup_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    description: str | None = None
    description_hash: str | None = None
    source: str = Field(min_length=1)
    source_url: str = Field(min_length=1)

    @model_validator(mode="after")
    def _description_pairing(self) -> Posting:
        if (self.description is None) != (self.description_hash is None):
            raise ValueError("description and description_hash must be both set or both null")
        return self


@dataclass(frozen=True)
class Poll[T]:
    items: tuple[T, ...] = ()
    etag: str | None = None
    not_modified: bool = False


@dataclass(frozen=True)
class ReadmeSnapshot:
    repo: str
    markdown: str
    etag: str | None = None
    not_modified: bool = False


@dataclass(frozen=True)
class CollectResult:
    postings: tuple[Posting, ...] = ()
    etag: str | None = None
    not_modified: bool = False
    # 🔒 rows, kept apart from `postings`: a locked row is an observation that the
    # posting closed, not an absence. Never upserted as open.
    locked: tuple[Posting, ...] = ()


class SourceAdapter(Protocol):
    source: str

    async def collect(self) -> CollectResult: ...
