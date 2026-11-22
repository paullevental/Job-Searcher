from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from offerradar_agents.scout.http import ScoutHttpClient
from offerradar_agents.scout.models import CollectResult, Poll, Posting
from offerradar_agents.scout.normalize import (
    canonicalize_url,
    dedup_key,
    normalized_company_name,
    normalized_job_title,
    parse_term,
)


class LeverCategories(BaseModel):
    model_config = ConfigDict(extra="ignore")

    team: str | None = None
    location: str | None = None
    commitment: str | None = None


class LeverPosting(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    hostedUrl: str = Field(min_length=1)
    applyUrl: str | None = None
    categories: LeverCategories
    descriptionPlain: str | None = None
    createdAt: int  # epoch milliseconds


_POSTINGS_ADAPTER = TypeAdapter(list[LeverPosting])


class LeverAdapter:
    source = "lever"

    def __init__(self, http: ScoutHttpClient, company: str, etag: str | None = None) -> None:
        self._http = http
        self._company = company
        self._etag = etag

    async def fetch(self) -> Poll[LeverPosting]:
        url = f"https://api.lever.co/v0/postings/{self._company}?mode=json"
        result = await self._http.get(url, etag=self._etag)
        # An error page is not JSON, and JSONDecodeError is unclassified.
        result.raise_for_status()
        if result.not_modified:
            return Poll(etag=result.etag, not_modified=True)
        items = _POSTINGS_ADAPTER.validate_python(result.json())
        return Poll(items=tuple(items), etag=result.etag)

    def normalize(self, raw: LeverPosting) -> Posting:
        company_name = self._company.replace("-", " ").replace("_", " ").title()
        normalized_company = normalized_company_name(company_name)
        normalized_title = normalized_job_title(raw.text)
        term = parse_term(raw.text)
        location = raw.categories.location
        description = raw.descriptionPlain
        description_hash = hashlib.sha256(description.encode()).hexdigest() if description else None
        return Posting(
            company_name=company_name,
            normalized_company_name=normalized_company,
            title=raw.text,
            normalized_title=normalized_title,
            location=location,
            remote=location is not None and "remote" in location.casefold(),
            term=term,
            canonical_url=canonicalize_url(raw.hostedUrl),
            dedup_key=dedup_key(normalized_company, normalized_title, term),
            description=description,
            description_hash=description_hash,
            source=self.source,
            source_url=raw.hostedUrl,
        )

    async def collect(self) -> CollectResult:
        poll = await self.fetch()
        if poll.not_modified:
            return CollectResult(etag=poll.etag, not_modified=True)
        return CollectResult(
            postings=tuple(self.normalize(item) for item in poll.items), etag=poll.etag
        )
