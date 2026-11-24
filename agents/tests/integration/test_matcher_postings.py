from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from offerradar_agents.config import Settings
from offerradar_agents.db.session import build_engine
from offerradar_agents.db.tables import EMBEDDING_DIM, companies, postings
from offerradar_agents.embeddings.errors import EmbeddingResponseError
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.embeddings.provider import Vector
from offerradar_agents.matcher.postings import embed_pending_postings
from offerradar_agents.scout.alerts import EVENT

pytestmark = pytest.mark.integration


class _FailingProvider:
    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        raise EmbeddingResponseError("ollama down")


class _FailsAfterFirstBatch:
    def __init__(self) -> None:
        self._inner = FakeEmbeddingProvider(dimension=EMBEDDING_DIM)
        self._calls = 0

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        self._calls += 1
        if self._calls > 1:
            raise EmbeddingResponseError("ollama down")
        return await self._inner.embed(texts)


@pytest.fixture
async def posting_engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    engine = build_engine(Settings(database_url=url))
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE companies, postings RESTART IDENTITY CASCADE"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def factory(posting_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(posting_engine, expire_on_commit=False)


async def _seed(factory: async_sessionmaker[AsyncSession], statuses: Sequence[str]) -> list[int]:
    async with factory() as session, session.begin():
        company_id = (
            await session.execute(
                insert(companies).values(name="Acme", normalized_name="acme", source="manual")
            )
        ).inserted_primary_key[0]
        ids: list[int] = []
        for i, status in enumerate(statuses):
            ids.append(
                (
                    await session.execute(
                        insert(postings).values(
                            company_id=company_id,
                            title=f"Intern {i}",
                            normalized_title=f"intern {i}",
                            canonical_url=f"https://acme.example/{i}",
                            dedup_key=f"acme|{i}",
                            status="open" if status == "merged" else status,
                        )
                    )
                ).inserted_primary_key[0]
            )
        if "merged" in statuses:
            winner = ids[statuses.index("open")]
            loser = ids[statuses.index("merged")]
            await session.execute(
                text("UPDATE postings SET status = 'merged', merged_into_id = :w WHERE id = :l"),
                {"w": winner, "l": loser},
            )
    return ids


async def _models(factory: async_sessionmaker[AsyncSession]) -> dict[int, str | None]:
    async with factory() as session:
        rows = await session.execute(select(postings.c.id, postings.c.embedding_model))
        return {r.id: r.embedding_model for r in rows}


def _provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dimension=EMBEDDING_DIM)


async def test_embeds_open_postings_once(factory: async_sessionmaker[AsyncSession]) -> None:
    ids = await _seed(factory, ["open", "open", "open"])

    first = await embed_pending_postings(factory, _provider(), model="m1", batch_size=2)
    second = await embed_pending_postings(factory, _provider(), model="m1", batch_size=2)

    assert (first, second) == (3, 0)
    assert await _models(factory) == {i: "m1" for i in ids}


async def test_model_change_re_embeds(factory: async_sessionmaker[AsyncSession]) -> None:
    ids = await _seed(factory, ["open", "open"])
    await embed_pending_postings(factory, _provider(), model="m1")

    count = await embed_pending_postings(factory, _provider(), model="m2")

    assert count == 2
    assert await _models(factory) == {i: "m2" for i in ids}


async def test_skips_merged_and_closed(factory: async_sessionmaker[AsyncSession]) -> None:
    ids = await _seed(factory, ["open", "closed", "merged"])

    count = await embed_pending_postings(factory, _provider(), model="m1")

    assert count == 1
    assert await _models(factory) == {ids[0]: "m1", ids[1]: None, ids[2]: None}


async def test_failure_alerts_and_does_not_raise(
    factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    ids = await _seed(factory, ["open", "open"])

    with caplog.at_level(logging.WARNING):
        count = await embed_pending_postings(factory, _FailingProvider(), model="m1", run_id="r1")

    assert count == 0
    assert await _models(factory) == {i: None for i in ids}
    alerts = [r for r in caplog.records if getattr(r, "event", None) == EVENT]
    assert len(alerts) == 1
    assert alerts[0].source == "embeddings"  # type: ignore[attr-defined]
    assert alerts[0].run_id == "r1"  # type: ignore[attr-defined]


async def test_partial_progress_survives_failure(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed(factory, ["open", "open", "open"])

    count = await embed_pending_postings(factory, _FailsAfterFirstBatch(), model="m1", batch_size=2)

    assert count == 2
    assert await _models(factory) == {ids[0]: "m1", ids[1]: "m1", ids[2]: None}
