from __future__ import annotations

import respx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from offerradar_agents.config import Settings
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.scout import repository
from offerradar_agents.scout.assemble import (
    build_http_client,
    build_retry_policy,
    build_targets,
    collect_all,
)
from offerradar_agents.scout.http import ScoutHttpClient
from offerradar_agents.scout.runner import run_scout

SIMPLIFY_REPO = "SimplifyJobs/Summer2026-Internships"
OUCKAH_REPO = "Ouckah/Summer2026-Internships"
SIMPLIFY_URL = f"https://api.github.com/repos/{SIMPLIFY_REPO}/readme"
OUCKAH_URL = f"https://api.github.com/repos/{OUCKAH_REPO}/readme"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "SCOUT_SIMPLIFY_REPOS": SIMPLIFY_REPO,
        "SCOUT_OUCKAH_REPOS": OUCKAH_REPO,
        "SCOUT_RATE_LIMIT_RPS": 1000.0,
        "SCOUT_RATE_LIMIT_BURST": 100,
        "GITHUB_TOKEN": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def seed_board(session: AsyncSession, *, company: str, provider: str, token: str) -> int:
    company_id = await repository.get_or_create_company(
        session, name=company, normalized_name=company.lower(), source="github_list"
    )
    from offerradar_agents.db import tables

    result = await session.execute(
        tables.ats_boards.insert()
        .values(company_id=company_id, provider=provider, board_token=token)
        .returning(tables.ats_boards.c.id)
    )
    return int(result.scalar_one())


def allow_robots(router: respx.MockRouter) -> None:
    router.get("https://api.github.com/robots.txt").respond(404)


async def test_build_targets_covers_both_lists_and_only_supported_boards(
    db: AsyncSession,
) -> None:
    await seed_board(db, company="Acme", provider="greenhouse", token="acme")
    await seed_board(db, company="Globex", provider="lever", token="globex")
    # No adapter exists for ashby in M2, so it must not become a target.
    await seed_board(db, company="Initech", provider="ashby", token="initech")

    async with ScoutHttpClient(settings=make_settings()) as http:
        targets = await build_targets(db, http=http, settings=make_settings())

    assert [(target.adapter.source, target.cursor.label) for target in targets] == [
        ("simplify", SIMPLIFY_REPO),
        ("ouckah", OUCKAH_REPO),
        ("greenhouse", "acme"),
        ("lever", "globex"),
    ]


async def test_a_stored_etag_reaches_the_request_as_if_none_match(
    engine: AsyncEngine, db: AsyncSession
) -> None:
    await repository.upsert_source_cursor(
        db, source="simplify", cursor_key=SIMPLIFY_REPO, etag='W/"stored"'
    )
    await db.commit()
    settings = make_settings(SCOUT_OUCKAH_REPOS="")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    with respx.mock(assert_all_called=False) as router:
        allow_robots(router)
        route = router.get(SIMPLIFY_URL).respond(304)
        # policy=None is one attempt: ExponentialBackoff's body is Paul's.
        async with ScoutHttpClient(settings=settings) as http:
            targets = await build_targets(db, http=http, settings=settings)
            outcomes = await run_scout(targets, factory, provider=FakeEmbeddingProvider())

    assert route.calls.last.request.headers["if-none-match"] == 'W/"stored"'
    assert [outcome.not_modified for outcome in outcomes] == [True]


def test_build_retry_policy_carries_the_configured_window() -> None:
    settings = make_settings(
        SCOUT_MAX_RETRIES=7, SCOUT_RETRY_BASE_SECONDS=0.5, SCOUT_RETRY_MAX_SECONDS=12.0
    )

    policy = build_retry_policy(settings)

    assert (policy.max_retries, policy.base_seconds, policy.max_seconds) == (7, 0.5, 12.0)


def test_build_http_client_wires_the_policy_in() -> None:
    client = build_http_client(settings=make_settings())

    assert client._policy is not None


async def test_collect_all_polls_every_configured_list(
    engine: AsyncEngine, db: AsyncSession
) -> None:
    settings = make_settings()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # A 304 without a stored ETag is an unsolicited response (test_scout_http.py's
    # test_unsolicited_304_is_a_source_error_not_unchanged), so seed one per source.
    await repository.upsert_source_cursor(
        db, source="simplify", cursor_key=SIMPLIFY_REPO, etag='W/"s"'
    )
    await repository.upsert_source_cursor(db, source="ouckah", cursor_key=OUCKAH_REPO, etag='W/"o"')
    await db.commit()

    with respx.mock(assert_all_called=False) as router:
        allow_robots(router)
        router.get(SIMPLIFY_URL).respond(304, headers={"ETag": 'W/"s"'})
        router.get(OUCKAH_URL).respond(304, headers={"ETag": 'W/"o"'})
        outcomes = await collect_all(factory, settings=settings, provider=FakeEmbeddingProvider())

    assert [outcome.source for outcome in outcomes] == ["simplify", "ouckah"]
    assert all(outcome.not_modified for outcome in outcomes)
