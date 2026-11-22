from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from offerradar_agents.scout.http import ScoutHttpClient
from offerradar_agents.scout.models import CollectResult, Poll, Posting
from offerradar_agents.scout.normalize import (
    canonicalize_url,
    dedup_key,
    normalized_company_name,
    normalized_job_title,
    parse_term,
)


class GreenhouseLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None


class GreenhouseJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: str = Field(min_length=1)
    absolute_url: str = Field(min_length=1)
    location: GreenhouseLocation
    content: str
    updated_at: str


class GreenhouseJobsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jobs: list[GreenhouseJob]


class GreenhouseAdapter:
    source = "greenhouse"

    def __init__(self, http: ScoutHttpClient, board_token: str, etag: str | None = None) -> None:
        self._http = http
        self._board_token = board_token
        self._etag = etag

    async def fetch(self) -> Poll[GreenhouseJob]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self._board_token}/jobs?content=true"
        result = await self._http.get(url, etag=self._etag)
        # An error page is not JSON, and JSONDecodeError is unclassified.
        result.raise_for_status()
        if result.not_modified:
            return Poll(etag=result.etag, not_modified=True)
        response = GreenhouseJobsResponse.model_validate(result.json())
        return Poll(items=tuple(response.jobs), etag=result.etag)

    def normalize(self, raw: GreenhouseJob) -> Posting:
        company_name = self._board_token.replace("-", " ").replace("_", " ").title()
        normalized_company = normalized_company_name(company_name)
        normalized_title = normalized_job_title(raw.title)
        term = parse_term(raw.title)
        location = raw.location.name
        return Posting(
            company_name=company_name,
            normalized_company_name=normalized_company,
            title=raw.title,
            normalized_title=normalized_title,
            location=location,
            remote=location is not None and "remote" in location.casefold(),
            term=term,
            canonical_url=canonicalize_url(raw.absolute_url),
            dedup_key=dedup_key(normalized_company, normalized_title, term),
            description=raw.content,
            description_hash=hashlib.sha256(raw.content.encode()).hexdigest(),
            source=self.source,
            source_url=raw.absolute_url,
        )

    async def collect(self) -> CollectResult:
        poll = await self.fetch()
        if poll.not_modified:
            return CollectResult(etag=poll.etag, not_modified=True)
        return CollectResult(
            postings=tuple(self.normalize(item) for item in poll.items), etag=poll.etag
        )
