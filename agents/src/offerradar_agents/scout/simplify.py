from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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


class SimplifyReadmeRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str = Field(min_length=1)
    role: str = Field(min_length=1)
    location: str | None = None
    application_url: str = Field(min_length=1)
    date_posted: str | None = None
    locked: bool = False


def parse_readme_table(markdown: str) -> list[SimplifyReadmeRow]:
    rows: list[SimplifyReadmeRow] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 5 or cells[0] in ("Company", "") or set(cells[0]) <= {"-", ":"}:
            continue
        company, role_raw, location, application, date = cells
        locked = "🔒" in role_raw
        start, end = application.find("("), application.find(")")
        url = application[start + 1 : end] if 0 <= start < end else application
        rows.append(
            SimplifyReadmeRow(
                company=company,
                role=role_raw.replace("🔒", "").strip(),
                location=location or None,
                application_url=url,
                date_posted=date or None,
                locked=locked,
            )
        )
    return rows


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

    def normalize(self, raw: SimplifyReadmeRow) -> Posting:
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
        # A locked row is closed; Scout has no status field to mark it, so it is
        # never submitted as an open posting.
        return CollectResult(
            postings=tuple(self.normalize(row) for row in rows if not row.locked),
            etag=snapshot.etag,
        )
