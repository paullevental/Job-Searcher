from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from offerradar_agents.config import Settings
from offerradar_agents.db.session import build_engine
from offerradar_agents.db.tables import EMBEDDING_DIM, resume_chunks
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.matcher import store
from offerradar_agents.matcher.resume import ResumeChunk
from offerradar_agents.matcher.store import store_resume

pytestmark = pytest.mark.integration

MARKDOWN = "- built a thing\n- shipped another"


def _fake_chunker(markdown: str) -> list[ResumeChunk]:
    return [
        ResumeChunk(kind="bullet", ordinal=i, text=line)
        for i, line in enumerate(markdown.splitlines())
    ]


@pytest.fixture
async def resume_engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    engine = build_engine(Settings(database_url=url))
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE resume_chunks RESTART IDENTITY CASCADE"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def factory(resume_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(resume_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def fake_chunker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "chunk_resume", _fake_chunker)


async def _run(
    factory: async_sessionmaker[AsyncSession], markdown: str, model: str
) -> store.StoreResult:
    async with factory() as session, session.begin():
        return await store_resume(
            session,
            FakeEmbeddingProvider(dimension=EMBEDDING_DIM),
            markdown=markdown,
            model=model,
        )


async def _rows(factory: async_sessionmaker[AsyncSession]) -> list[tuple[int, str | None]]:
    async with factory() as session:
        result = await session.execute(
            select(resume_chunks.c.ordinal, resume_chunks.c.embedding_model).order_by(
                resume_chunks.c.ordinal
            )
        )
        return [(r.ordinal, r.embedding_model) for r in result]


async def test_stores_one_row_per_chunk(factory: async_sessionmaker[AsyncSession]) -> None:
    result = await _run(factory, MARKDOWN, "m1")

    assert (result.chunk_count, result.skipped) == (2, False)
    assert await _rows(factory) == [(0, "m1"), (1, "m1")]


async def test_second_run_is_a_no_op(factory: async_sessionmaker[AsyncSession]) -> None:
    await _run(factory, MARKDOWN, "m1")
    result = await _run(factory, MARKDOWN, "m1")

    assert (result.chunk_count, result.skipped) == (2, True)
    assert len(await _rows(factory)) == 2


async def test_model_change_re_embeds(factory: async_sessionmaker[AsyncSession]) -> None:
    await _run(factory, MARKDOWN, "m1")
    result = await _run(factory, MARKDOWN, "m2")

    assert result.skipped is False
    assert await _rows(factory) == [(0, "m2"), (1, "m2")]


async def test_edit_keeps_old_version_and_adds_new(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await _run(factory, MARKDOWN, "m1")
    second = await _run(factory, MARKDOWN + "\n- third", "m1")

    assert first.version != second.version
    assert second.chunk_count == 3
    assert len(await _rows(factory)) == 5


async def test_empty_chunks_rejected_and_nothing_written(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "chunk_resume", lambda markdown: [])

    with pytest.raises(ValueError, match="no chunks"):
        await _run(factory, MARKDOWN, "m1")
    assert await _rows(factory) == []


async def test_duplicate_ordinals_rejected(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        store,
        "chunk_resume",
        lambda markdown: [ResumeChunk("bullet", 0, "a"), ResumeChunk("bullet", 0, "b")],
    )

    with pytest.raises(ValueError, match="duplicate"):
        await _run(factory, MARKDOWN, "m1")
