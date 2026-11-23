from __future__ import annotations

from offerradar_agents.config import Settings
from offerradar_agents.scout.github_readme import fetch_readme
from offerradar_agents.scout.http import ScoutHttpClient
from offerradar_agents.scout.models import CollectResult, Posting, ReadmeSnapshot
from offerradar_agents.scout.normalize import (
    canonicalize_url,
    dedup_key,
    normalized_company_name,
    normalized_job_title,
    parse_term,
)
from offerradar_agents.scout.readme_table import ReadmeRow, parse_readme_table


class SimplifyAdapter:
    source = "simplify"

    def __init__(
        self,
        http: ScoutHttpClient,
        repo: str,
        etag: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._http = http
        self._repo = repo
        self._etag = etag
        self._settings = settings

    async def fetch(self) -> ReadmeSnapshot:
        return await fetch_readme(
            self._http, repo=self._repo, etag=self._etag, settings=self._settings
        )

    def normalize(self, raw: ReadmeRow) -> Posting:
        normalized_company = normalized_company_name(raw.company)
        normalized_title = normalized_job_title(raw.role)
        term = parse_term(raw.role)
        return Posting(
            company_name=raw.company,
            normalized_company_name=normalized_company,
            title=raw.role,
            normalized_title=normalized_title,
            location=raw.location,
            remote=raw.location is not None and "remote" in raw.location.casefold(),
            term=term,
            canonical_url=canonicalize_url(raw.application_url),
            dedup_key=dedup_key(normalized_company, normalized_title, term),
            description=None,
            description_hash=None,
            source=self.source,
            source_url=raw.application_url,
        )

    async def collect(self) -> CollectResult:
        snapshot = await self.fetch()
        if snapshot.not_modified:
            return CollectResult(etag=snapshot.etag, not_modified=True)
        rows = parse_readme_table(snapshot.markdown)
        return CollectResult(
            postings=tuple(self.normalize(row) for row in rows if not row.locked),
            locked=tuple(self.normalize(row) for row in rows if row.locked),
            etag=snapshot.etag,
        )
