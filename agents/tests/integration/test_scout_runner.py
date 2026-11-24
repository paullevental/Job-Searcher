from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

import httpx
import pytest
import redis.exceptions
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from offerradar_agents.db import tables
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.embeddings.provider import Vector
from offerradar_agents.scout import repository
from offerradar_agents.scout.alerts import AlertKind
from offerradar_agents.scout.blocklist import BannedDomainError
from offerradar_agents.scout.http import (
    RedirectLimitError,
    RobotsDisallowedError,
    SourceResponseError,
)
from offerradar_agents.scout.models import CollectResult, Posting
from offerradar_agents.scout.runner import (
    AtsBoardCursor,
    ListCursor,
    ScoutTarget,
    run_scout,
    run_target,
)

REPO = "SimplifyJobs/Summer2026-Internships"
PROVIDER = FakeEmbeddingProvider()


class StoppedOllama:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.calls += 1
        raise httpx.ConnectError("connection refused")


class FakeAdapter:
    def __init__(self, source: str, result: CollectResult | Exception) -> None:
        self.source = source
        self._result = result

    async def collect(self) -> CollectResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def list_target(result: CollectResult | Exception) -> ScoutTarget:
    return ScoutTarget(
        adapter=FakeAdapter("simplify", result),
        cursor=ListCursor(source="simplify", repo=REPO),
    )


async def seed_board(session: AsyncSession, token: str = "acme") -> int:
    company_id = await repository.get_or_create_company(
        session, name="Acme Labs", normalized_name="acme labs", source="github_list"
    )
    result = await session.execute(
        tables.ats_boards.insert()
        .values(company_id=company_id, provider="greenhouse", board_token=token)
        .returning(tables.ats_boards.c.id)
    )
    return int(result.scalar_one())


async def test_not_modified_raises_no_alert_and_advances_the_list_cursor(
    db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        outcome = await run_target(
            list_target(CollectResult(not_modified=True, etag='W/"abc"')), db, provider=PROVIDER
        )

    assert outcome.not_modified is True
    assert outcome.alert is None
    assert caplog.records == []
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) == 'W/"abc"'


async def test_an_ats_target_advances_ats_boards_not_source_cursors(db: AsyncSession) -> None:
    # source_cursors' CHECK admits README sources only, hence a store per target.
    board_id = await seed_board(db)
    target = ScoutTarget(
        adapter=FakeAdapter("greenhouse", CollectResult(not_modified=True, etag='W/"gh"')),
        cursor=AtsBoardCursor(board_id=board_id, board_token="acme"),
    )

    await run_target(target, db, provider=PROVIDER)

    assert await repository.ats_board_etag(db, board_id=board_id) == 'W/"gh"'
    stored = await db.execute(select(tables.source_cursors))
    assert stored.all() == []


async def test_empty_200_raises_an_alert_and_still_advances_the_cursor(
    db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        outcome = await run_target(list_target(CollectResult(etag='W/"z"')), db, provider=PROVIDER)

    assert outcome.alert is AlertKind.EMPTY
    assert [record.alert_kind for record in caplog.records] == ["empty"]
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) == 'W/"z"'


async def test_transport_failure_is_an_alert_not_a_crash(
    db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        outcome = await run_target(list_target(httpx.ConnectTimeout("boom")), db, provider=PROVIDER)

    assert outcome.alert is AlertKind.FAILED
    assert [record.alert_kind for record in caplog.records] == ["failed"]
    # A failed poll must not advance the cursor, or the next run skips content.
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) is None


@pytest.mark.parametrize("error", [BannedDomainError("rule 2"), RobotsDisallowedError("rule 3")])
async def test_rule_refusals_are_reported_as_refused(
    db: AsyncSession, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    with caplog.at_level(logging.WARNING):
        outcome = await run_target(list_target(error), db, provider=PROVIDER)

    assert outcome.alert is AlertKind.REFUSED
    assert [record.alert_kind for record in caplog.records] == ["refused"]
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) is None


async def test_a_programming_error_is_not_swallowed_as_an_alert(db: AsyncSession) -> None:
    # An unwritten normalize() must surface, not hide behind "the source was empty".
    with pytest.raises(NotImplementedError):
        await run_target(
            list_target(NotImplementedError("Paul writes this")), db, provider=PROVIDER
        )


def make_posting(title: str = "Software Engineer Intern") -> Posting:
    key = hashlib.sha256(f"acme labs|{title.lower()}|Summer 2026".encode()).hexdigest()
    return Posting(
        company_name="Acme Labs",
        normalized_company_name="acme labs",
        title=title,
        normalized_title=title.lower(),
        term="Summer 2026",
        canonical_url=f"https://boards.greenhouse.io/acme/jobs/{key[:8]}",
        dedup_key=key,
        source="simplify",
        source_url="https://github.com/SimplifyJobs/Summer2026-Internships",
    )


@pytest.mark.parametrize(
    "error",
    [SourceResponseError("500"), RedirectLimitError("loop"), redis.exceptions.ConnectionError()],
)
async def test_every_error_the_fetch_path_can_raise_is_an_alert(
    db: AsyncSession, error: Exception
) -> None:
    outcome = await run_target(list_target(error), db, provider=PROVIDER)

    assert outcome.alert is AlertKind.FAILED


async def test_run_target_writes_every_posting_it_collected(db: AsyncSession) -> None:
    outcome = await run_target(
        list_target(CollectResult(postings=(make_posting(),))), db, provider=PROVIDER
    )

    assert outcome.postings_seen == 1
    assert outcome.alert is None
    count = await db.execute(select(func.count()).select_from(tables.postings))
    assert count.scalar_one() == 1


async def test_running_a_target_twice_on_the_same_payload_adds_no_rows(
    db: AsyncSession,
) -> None:
    collected = CollectResult(postings=(make_posting(),), etag='W/"a"')
    await run_target(list_target(collected), db, provider=PROVIDER)
    await run_target(list_target(collected), db, provider=PROVIDER)

    count = await db.execute(select(func.count()).select_from(tables.postings))
    assert count.scalar_one() == 1


async def test_run_scout_keeps_going_after_one_source_fails_and_commits_the_rest(
    engine: AsyncEngine, db: AsyncSession
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    failing = ScoutTarget(
        adapter=FakeAdapter("simplify", httpx.ConnectTimeout("boom")),
        cursor=ListCursor(source="simplify", repo=REPO),
    )
    healthy = ScoutTarget(
        adapter=FakeAdapter("ouckah", CollectResult(not_modified=True, etag='W/"ok"')),
        cursor=ListCursor(source="ouckah", repo="Ouckah/Summer2026-Internships"),
    )

    outcomes = await run_scout([failing, healthy], factory, provider=PROVIDER)

    assert [outcome.alert for outcome in outcomes] == [AlertKind.FAILED, None]
    # Read on a different session: this is the commit, not the in-flight state.
    assert (
        await repository.source_cursor_etag(
            db, source="ouckah", cursor_key="Ouckah/Summer2026-Internships"
        )
        == 'W/"ok"'
    )
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) is None


async def test_run_scout_reports_a_failed_write_as_an_alert_and_continues(
    engine: AsyncEngine, db: AsyncSession
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # No such board: touch_ats_board updates zero rows and raises.
    doomed = ScoutTarget(
        adapter=FakeAdapter("greenhouse", CollectResult(not_modified=True, etag='W/"gh"')),
        cursor=AtsBoardCursor(board_id=987_654, board_token="ghost"),
    )
    healthy = ScoutTarget(
        adapter=FakeAdapter("simplify", CollectResult(not_modified=True, etag='W/"s"')),
        cursor=ListCursor(source="simplify", repo=REPO),
    )

    outcomes = await run_scout([doomed, healthy], factory, provider=PROVIDER)

    assert [outcome.alert for outcome in outcomes] == [AlertKind.FAILED, None]
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) == 'W/"s"'


async def test_a_stopped_ollama_skips_dedup_but_commits_ingestion(
    engine: AsyncEngine, db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    collected = CollectResult(
        postings=(make_posting(), make_posting("Data Engineer Intern")), etag='W/"new"'
    )
    ollama = StoppedOllama()

    with caplog.at_level(logging.WARNING):
        outcomes = await run_scout([list_target(collected)], factory, provider=ollama)

    assert [(o.postings_seen, o.alert) for o in outcomes] == [(2, None)]
    # Once per target, not once per posting.
    assert ollama.calls == 1
    assert [(r.source, r.alert_kind) for r in caplog.records] == [("dedup", "failed")]
    count = await db.execute(select(func.count()).select_from(tables.postings))
    assert count.scalar_one() == 2
    # Held back so the next poll is a 200 and dedup reruns over every seen id.
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key=REPO) is None
