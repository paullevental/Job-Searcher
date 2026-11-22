from __future__ import annotations

from typing import Final

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.scout.http import ScoutHttpClient
from offerradar_agents.scout.models import ReadmeSnapshot

# Returns plain markdown as the body, not the base64-in-JSON envelope.
ACCEPT_RAW: Final = "application/vnd.github.raw"


async def fetch_readme(
    http: ScoutHttpClient,
    *,
    repo: str,
    etag: str | None = None,
    settings: Settings | None = None,
) -> ReadmeSnapshot:
    resolved = settings if settings is not None else get_settings()
    headers = {"Accept": ACCEPT_RAW}
    token = resolved.github_token
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"

    result = await http.get(
        f"https://api.github.com/repos/{repo}/readme", etag=etag, headers=headers
    )
    # Without this, a 403's rate-limit JSON is parsed as markdown.
    result.raise_for_status()
    if result.not_modified:
        return ReadmeSnapshot(repo=repo, markdown="", etag=result.etag, not_modified=True)
    return ReadmeSnapshot(repo=repo, markdown=result.text, etag=result.etag)
