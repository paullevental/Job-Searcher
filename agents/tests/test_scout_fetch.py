from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from offerradar_agents.config import Settings
from offerradar_agents.scout.greenhouse import GreenhouseAdapter
from offerradar_agents.scout.http import ScoutHttpClient, SourceResponseError
from offerradar_agents.scout.lever import LeverAdapter
from offerradar_agents.scout.ouckah import OuckahAdapter
from offerradar_agents.scout.readme_table import parse_readme_table
from offerradar_agents.scout.simplify import SimplifyAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/examplecorp?mode=json"
SIMPLIFY_URL = "https://api.github.com/repos/SimplifyJobs/Summer2026-Internships/readme"
OUCKAH_URL = "https://api.github.com/repos/Ouckah/Summer2026-Internships/readme"

# Checked before every request (rule 3); a 404 is RFC 9309 "no restrictions".
ORIGIN_ROBOTS_URLS = (
    "https://boards-api.greenhouse.io/robots.txt",
    "https://api.lever.co/robots.txt",
    "https://api.github.com/robots.txt",
)


@pytest.fixture(autouse=True)
def _allow_robots(respx_mock: respx.MockRouter) -> None:
    for url in ORIGIN_ROBOTS_URLS:
        respx_mock.get(url).mock(return_value=httpx.Response(404))


def _load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# --- Greenhouse --------------------------------------------------------------


async def test_greenhouse_fetch_validates_fixture_into_raw_jobs(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(GREENHOUSE_URL).mock(
        return_value=httpx.Response(200, json=_load("greenhouse_jobs.json"))
    )
    adapter = GreenhouseAdapter(ScoutHttpClient(), board_token="examplecorp")

    poll = await adapter.fetch()

    assert poll.not_modified is False
    assert [job.id for job in poll.items] == [4002001001, 4002001002, 4002001003]
    assert poll.items[0].title == "Software Engineering Intern (Summer 2026)"
    assert poll.items[1].location.name is None  # awkward row: missing location


async def test_greenhouse_fetch_raises_on_renamed_required_field(
    respx_mock: respx.MockRouter,
) -> None:
    payload = _load("greenhouse_jobs.json")
    payload["jobs"][0]["job_url"] = payload["jobs"][0].pop("absolute_url")
    respx_mock.get(GREENHOUSE_URL).mock(return_value=httpx.Response(200, json=payload))
    adapter = GreenhouseAdapter(ScoutHttpClient(), board_token="examplecorp")

    with pytest.raises(ValidationError):
        await adapter.fetch()


async def test_greenhouse_collect_304_is_not_modified_with_empty_postings(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(GREENHOUSE_URL).mock(return_value=httpx.Response(304))
    adapter = GreenhouseAdapter(ScoutHttpClient(), board_token="examplecorp", etag='"abc"')

    result = await adapter.collect()

    assert result.not_modified is True
    assert result.postings == ()


async def test_greenhouse_collect_empty_200_is_not_modified_false(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(GREENHOUSE_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    adapter = GreenhouseAdapter(ScoutHttpClient(), board_token="examplecorp")

    result = await adapter.collect()

    assert result.not_modified is False
    assert result.postings == ()


# --- Lever ---------------------------------------------------------------


async def test_lever_fetch_validates_fixture_into_raw_postings(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(LEVER_URL).mock(
        return_value=httpx.Response(200, json=_load("lever_postings.json"))
    )
    adapter = LeverAdapter(ScoutHttpClient(), company="examplecorp")

    poll = await adapter.fetch()

    assert poll.not_modified is False
    assert [posting.id for posting in poll.items] == [
        "a1b2c3d4-0001-4000-8000-000000000001",
        "a1b2c3d4-0002-4000-8000-000000000002",
    ]
    assert poll.items[1].categories.location is None  # awkward row: missing location
    assert "Term: Spring 2026" in poll.items[1].text


async def test_lever_fetch_raises_on_renamed_required_field(respx_mock: respx.MockRouter) -> None:
    payload = _load("lever_postings.json")
    payload[0]["hostedURL"] = payload[0].pop("hostedUrl")
    respx_mock.get(LEVER_URL).mock(return_value=httpx.Response(200, json=payload))
    adapter = LeverAdapter(ScoutHttpClient(), company="examplecorp")

    with pytest.raises(ValidationError):
        await adapter.fetch()


async def test_lever_collect_304_is_not_modified_with_empty_postings(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(LEVER_URL).mock(return_value=httpx.Response(304))
    adapter = LeverAdapter(ScoutHttpClient(), company="examplecorp", etag='"abc"')

    result = await adapter.collect()

    assert result.not_modified is True
    assert result.postings == ()


async def test_lever_collect_empty_200_is_not_modified_false(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(LEVER_URL).mock(return_value=httpx.Response(200, json=[]))
    adapter = LeverAdapter(ScoutHttpClient(), company="examplecorp")

    result = await adapter.collect()

    assert result.not_modified is False
    assert result.postings == ()


# --- Simplify --------------------------------------------------------------


async def test_simplify_fetch_validates_fixture_into_readme_snapshot(
    respx_mock: respx.MockRouter,
) -> None:
    markdown = _load("simplify_readme.json")["markdown"]
    respx_mock.get(SIMPLIFY_URL).mock(return_value=httpx.Response(200, text=markdown))
    adapter = SimplifyAdapter(ScoutHttpClient(), repo="SimplifyJobs/Summer2026-Internships")

    snapshot = await adapter.fetch()

    assert snapshot.repo == "SimplifyJobs/Summer2026-Internships"
    assert snapshot.not_modified is False
    assert "🔒" in snapshot.markdown  # awkward row: closed/locked marker


async def test_simplify_fetch_304_is_not_modified_with_empty_markdown(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(SIMPLIFY_URL).mock(return_value=httpx.Response(304))
    adapter = SimplifyAdapter(
        ScoutHttpClient(), repo="SimplifyJobs/Summer2026-Internships", etag='"abc"'
    )

    snapshot = await adapter.fetch()

    assert snapshot.not_modified is True
    assert snapshot.markdown == ""


async def test_simplify_fetch_empty_200_is_not_modified_false(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(SIMPLIFY_URL).mock(return_value=httpx.Response(200, text=""))
    adapter = SimplifyAdapter(ScoutHttpClient(), repo="SimplifyJobs/Summer2026-Internships")

    snapshot = await adapter.fetch()

    assert snapshot.not_modified is False
    assert snapshot.markdown == ""


# --- Ouckah ----------------------------------------------------------------


async def test_ouckah_fetch_validates_fixture_into_readme_snapshot(
    respx_mock: respx.MockRouter,
) -> None:
    markdown = _load("ouckah_readme.json")["markdown"]
    respx_mock.get(OUCKAH_URL).mock(return_value=httpx.Response(200, text=markdown))
    adapter = OuckahAdapter(ScoutHttpClient(), repo="Ouckah/Summer2026-Internships")

    snapshot = await adapter.fetch()

    assert snapshot.repo == "Ouckah/Summer2026-Internships"
    assert snapshot.not_modified is False
    assert "🔒" in snapshot.markdown  # awkward row: closed/locked marker


async def test_ouckah_fetch_304_is_not_modified_with_empty_markdown(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(OUCKAH_URL).mock(return_value=httpx.Response(304))
    adapter = OuckahAdapter(ScoutHttpClient(), repo="Ouckah/Summer2026-Internships", etag='"abc"')

    snapshot = await adapter.fetch()

    assert snapshot.not_modified is True
    assert snapshot.markdown == ""


async def test_ouckah_fetch_empty_200_is_not_modified_false(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(OUCKAH_URL).mock(return_value=httpx.Response(200, text=""))
    adapter = OuckahAdapter(ScoutHttpClient(), repo="Ouckah/Summer2026-Internships")

    snapshot = await adapter.fetch()

    assert snapshot.not_modified is False
    assert snapshot.markdown == ""


# --- error statuses never reach a parser -----------------------------------


async def test_greenhouse_fetch_raises_source_response_error_on_an_html_error_page(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(GREENHOUSE_URL).mock(
        return_value=httpx.Response(500, text="<html>Service Unavailable</html>")
    )
    adapter = GreenhouseAdapter(ScoutHttpClient(), board_token="examplecorp")

    with pytest.raises(SourceResponseError):
        await adapter.fetch()


async def test_lever_fetch_raises_source_response_error_on_a_server_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(LEVER_URL).mock(return_value=httpx.Response(503, text="try later"))
    adapter = LeverAdapter(ScoutHttpClient(), company="examplecorp")

    with pytest.raises(SourceResponseError):
        await adapter.fetch()


@pytest.mark.parametrize(
    ("factory", "url", "repo"),
    [
        (SimplifyAdapter, SIMPLIFY_URL, "SimplifyJobs/Summer2026-Internships"),
        (OuckahAdapter, OUCKAH_URL, "Ouckah/Summer2026-Internships"),
    ],
)
async def test_readme_fetch_rejects_a_rate_limit_body_instead_of_parsing_it(
    respx_mock: respx.MockRouter,
    factory: type[SimplifyAdapter] | type[OuckahAdapter],
    url: str,
    repo: str,
) -> None:
    respx_mock.get(url).mock(
        return_value=httpx.Response(403, json={"message": "API rate limit exceeded"})
    )
    adapter = factory(ScoutHttpClient(), repo=repo)

    with pytest.raises(SourceResponseError):
        await adapter.fetch()


async def test_readme_fetch_sends_the_github_token_when_one_is_configured(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(SIMPLIFY_URL).mock(return_value=httpx.Response(200, text="# empty"))
    settings = Settings(GITHUB_TOKEN=SecretStr("ghp-not-a-real-token"))  # type: ignore[call-arg]
    adapter = SimplifyAdapter(
        ScoutHttpClient(),
        repo="SimplifyJobs/Summer2026-Internships",
        settings=settings,
    )

    await adapter.fetch()

    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer ghp-not-a-real-token"
    assert sent.headers["accept"] == "application/vnd.github.raw"


def test_simplify_normalizes_a_parsed_row_into_a_posting() -> None:
    markdown = _load("simplify_readme.json")["markdown"]
    row = parse_readme_table(markdown)[0]

    posting = SimplifyAdapter(http=None, repo="example/repo").normalize(row)  # type: ignore[arg-type]

    assert posting.normalized_title == "software engineer intern"
    assert posting.canonical_url == "https://job-boards.greenhouse.io/examplecorp/jobs/4002001001"
    assert posting.remote is True
