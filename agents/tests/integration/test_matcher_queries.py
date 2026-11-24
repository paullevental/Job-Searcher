from __future__ import annotations

import math
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from offerradar_agents.config import Settings
from offerradar_agents.db.session import build_engine
from offerradar_agents.db.tables import EMBEDDING_DIM, companies, matches, postings, resume_chunks
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.matcher.queries import chunk_similarities, nearest_chunks, upsert_matches
from offerradar_agents.matcher.rank import rank_postings, retrieve_resume_context

pytestmark = pytest.mark.integration

MODEL = "nomic-embed-text"
VERSION = "v1"
TABLES = "matches, resume_chunks, postings, companies"


def vec(*head: float) -> list[float]:
    return [*head, *([0.0] * (EMBEDDING_DIM - len(head)))]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.hypot(*a) * math.hypot(*b))


@pytest.fixture
async def vector_engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    created = build_engine(Settings(database_url=url))
    try:
        async with created.begin() as conn:
            await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def session(vector_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(vector_engine, expire_on_commit=False)() as opened:
        yield opened


async def add_company(session: AsyncSession) -> int:
    result = await session.execute(
        insert(companies).values(name="Acme", normalized_name="acme", source="manual")
    )
    return int(result.inserted_primary_key[0])


async def add_posting(
    session: AsyncSession,
    company_id: int,
    slug: str,
    embedding: list[float] | None,
    *,
    model: str | None = MODEL,
    status: str = "open",
    merged_into_id: int | None = None,
) -> int:
    values: dict[str, Any] = {
        "company_id": company_id,
        "title": slug,
        "normalized_title": slug,
        "canonical_url": f"https://acme.example/{slug}",
        "dedup_key": slug,
        "embedding": embedding,
        "embedding_model": model if embedding is not None else None,
        "status": status,
        "merged_into_id": merged_into_id,
    }
    result = await session.execute(insert(postings).values(**values))
    return int(result.inserted_primary_key[0])


async def add_chunk(
    session: AsyncSession,
    ordinal: int,
    embedding: list[float] | None,
    *,
    version: str = VERSION,
    model: str | None = MODEL,
    kind: str = "bullet",
) -> None:
    await session.execute(
        insert(resume_chunks).values(
            version=version,
            kind=kind,
            ordinal=ordinal,
            text=f"chunk {ordinal}",
            embedding=embedding,
            embedding_model=model if embedding is not None else None,
        )
    )


async def stored_matches(session: AsyncSession) -> dict[int, Any]:
    rows = await session.execute(select(matches))
    return {row.posting_id: row for row in rows}


async def test_chunk_similarities_match_hand_computed_cosine(session: AsyncSession) -> None:
    company = await add_company(session)
    chunk_a, chunk_b = vec(1, 0), vec(0, 1)
    posting_vec = vec(3, 4)
    pid = await add_posting(session, company, "p1", posting_vec)
    await add_chunk(session, 0, chunk_a)
    await add_chunk(session, 1, chunk_b)
    await session.commit()

    result = await chunk_similarities(session, resume_version=VERSION, model=MODEL)

    assert list(result) == [pid]
    assert result[pid] == pytest.approx(
        [cosine(posting_vec, chunk_a), cosine(posting_vec, chunk_b)], abs=1e-5
    )
    assert result[pid] == pytest.approx([0.6, 0.8], abs=1e-5)


async def test_chunk_similarities_covers_every_open_embedded_posting(
    session: AsyncSession,
) -> None:
    company = await add_company(session)
    first = await add_posting(session, company, "p1", vec(1, 0))
    second = await add_posting(session, company, "p2", vec(0, 1))
    await add_chunk(session, 0, vec(1, 1))
    await session.commit()

    result = await chunk_similarities(session, resume_version=VERSION, model=MODEL)

    assert set(result) == {first, second}
    assert all(len(sims) == 1 for sims in result.values())


async def test_chunk_similarities_excludes_closed_merged_stale_unembedded_and_other_model(
    session: AsyncSession,
) -> None:
    company = await add_company(session)
    kept = await add_posting(session, company, "open", vec(1, 0))
    await add_posting(session, company, "closed", vec(1, 0), status="closed")
    await add_posting(session, company, "stale", vec(1, 0), status="stale")
    await add_posting(session, company, "merged", vec(1, 0), status="merged", merged_into_id=kept)
    await add_posting(session, company, "bare", None)
    await add_posting(session, company, "other-model", vec(1, 0), model="bge-small")
    await add_chunk(session, 0, vec(1, 0))
    await session.commit()

    result = await chunk_similarities(session, resume_version=VERSION, model=MODEL)

    assert list(result) == [kept]


async def test_chunk_similarities_scopes_chunks_to_version_and_model(
    session: AsyncSession,
) -> None:
    company = await add_company(session)
    pid = await add_posting(session, company, "p1", vec(1, 0))
    await add_chunk(session, 0, vec(1, 0))
    await add_chunk(session, 1, vec(0, 1), version="v0")
    await add_chunk(session, 2, vec(0, 1), model="bge-small")
    await add_chunk(session, 3, None)
    await session.commit()

    result = await chunk_similarities(session, resume_version=VERSION, model=MODEL)

    assert result[pid] == pytest.approx([1.0], abs=1e-5)


async def test_chunk_similarities_is_empty_without_chunks(session: AsyncSession) -> None:
    company = await add_company(session)
    await add_posting(session, company, "p1", vec(1, 0))
    await session.commit()

    assert await chunk_similarities(session, resume_version=VERSION, model=MODEL) == {}


async def test_chunk_similarities_stay_inside_the_matches_range(session: AsyncSession) -> None:
    company = await add_company(session)
    direction = vec(0.1, 0.7, 0.3, 0.9)
    pid = await add_posting(session, company, "same", direction)
    await add_chunk(session, 0, direction)
    await add_chunk(session, 1, [-x for x in direction])
    await session.commit()

    sims = (await chunk_similarities(session, resume_version=VERSION, model=MODEL))[pid]

    assert all(-1.0 <= s <= 1.0 for s in sims)
    assert sims == pytest.approx([1.0, -1.0], abs=1e-5)


async def test_nearest_chunks_are_ordered_most_similar_first(session: AsyncSession) -> None:
    await add_chunk(session, 0, vec(0, 1))
    await add_chunk(session, 1, vec(1, 0.2))
    await add_chunk(session, 2, vec(1, 1))
    await add_chunk(session, 3, vec(1, 0), version="v0")
    await session.commit()
    query = vec(1, 0)

    found = await nearest_chunks(session, query, resume_version=VERSION, model=MODEL, k=3)

    assert [c.ordinal for c in found] == [1, 2, 0]
    assert [c.similarity for c in found] == sorted((c.similarity for c in found), reverse=True)
    assert found[0].similarity == pytest.approx(cosine(query, vec(1, 0.2)), abs=1e-5)
    assert found[0].text == "chunk 1"
    assert found[0].kind == "bullet"


async def test_nearest_chunks_honours_k_and_model_filter(session: AsyncSession) -> None:
    await add_chunk(session, 0, vec(1, 0))
    await add_chunk(session, 1, vec(1, 0.1))
    await add_chunk(session, 2, vec(1, 0.2), model="bge-small")
    await add_chunk(session, 3, None)
    await session.commit()

    one = await nearest_chunks(session, vec(1, 0), resume_version=VERSION, model=MODEL, k=1)
    many = await nearest_chunks(session, vec(1, 0), resume_version=VERSION, model=MODEL, k=10)

    assert [c.ordinal for c in one] == [0]
    assert [c.ordinal for c in many] == [0, 1]


async def test_upsert_matches_inserts_then_updates_in_place(session: AsyncSession) -> None:
    company = await add_company(session)
    first = await add_posting(session, company, "p1", vec(1, 0))
    second = await add_posting(session, company, "p2", vec(0, 1))

    written = await upsert_matches(
        session, {first: 0.25, second: -0.5}, resume_version="v1", model=MODEL
    )
    await session.commit()
    before = await stored_matches(session)
    rewritten = await upsert_matches(session, {first: 0.75}, resume_version="v2", model="bge-small")
    await session.commit()
    after = await stored_matches(session)

    assert (written, rewritten) == (2, 1)
    assert before[first].similarity == pytest.approx(0.25)
    assert before[second].similarity == pytest.approx(-0.5)
    assert len(after) == 2
    assert after[first].id == before[first].id
    assert after[first].similarity == pytest.approx(0.75)
    assert after[first].resume_version == "v2"
    assert after[first].embedding_model == "bge-small"
    assert after[second].similarity == pytest.approx(-0.5)


async def test_upsert_matches_is_idempotent(session: AsyncSession) -> None:
    company = await add_company(session)
    pid = await add_posting(session, company, "p1", vec(1, 0))

    for _ in range(2):
        await upsert_matches(session, {pid: 0.4}, resume_version=VERSION, model=MODEL)
        await session.commit()

    rows = await stored_matches(session)
    assert len(rows) == 1
    assert rows[pid].similarity == pytest.approx(0.4)


async def test_upsert_matches_preserves_selected_for_analysis(session: AsyncSession) -> None:
    company = await add_company(session)
    pid = await add_posting(session, company, "p1", vec(1, 0))
    await upsert_matches(session, {pid: 0.1}, resume_version=VERSION, model=MODEL)
    await session.execute(update(matches).values(selected_for_analysis=True))
    await session.commit()

    await upsert_matches(session, {pid: 0.9}, resume_version=VERSION, model=MODEL)
    await session.commit()

    row = (await stored_matches(session))[pid]
    assert row.selected_for_analysis is True
    assert row.similarity == pytest.approx(0.9)


async def test_upsert_matches_defaults_to_not_selected(session: AsyncSession) -> None:
    company = await add_company(session)
    pid = await add_posting(session, company, "p1", vec(1, 0))

    await upsert_matches(session, {pid: 0.1}, resume_version=VERSION, model=MODEL)
    await session.commit()

    assert (await stored_matches(session))[pid].selected_for_analysis is False


@pytest.mark.parametrize("bad", [1.01, -1.5, math.nan, math.inf])
async def test_upsert_matches_rejects_out_of_range_before_writing(
    session: AsyncSession, bad: float
) -> None:
    company = await add_company(session)
    pid = await add_posting(session, company, "p1", vec(1, 0))
    other = await add_posting(session, company, "p2", vec(0, 1))

    with pytest.raises(ValueError, match="outside"):
        await upsert_matches(session, {pid: 0.5, other: bad}, resume_version=VERSION, model=MODEL)

    assert await stored_matches(session) == {}


async def test_upsert_matches_accepts_the_bounds_and_empty_input(session: AsyncSession) -> None:
    company = await add_company(session)
    high = await add_posting(session, company, "p1", vec(1, 0))
    low = await add_posting(session, company, "p2", vec(0, 1))

    assert await upsert_matches(session, {}, resume_version=VERSION, model=MODEL) == 0
    assert (
        await upsert_matches(session, {high: 1.0, low: -1.0}, resume_version=VERSION, model=MODEL)
        == 2
    )


@pytest.mark.xfail(raises=NotImplementedError, strict=True)
async def test_rank_postings_writes_a_match_for_every_open_embedded_posting(
    session: AsyncSession,
) -> None:
    company = await add_company(session)
    open_ids = [await add_posting(session, company, f"p{i}", vec(1, i / 10)) for i in range(5)]
    await add_posting(session, company, "closed", vec(1, 0), status="closed")
    await add_posting(session, company, "bare", None)
    await add_chunk(session, 0, vec(1, 0))
    await add_chunk(session, 1, vec(0, 1))
    await session.commit()

    top = await rank_postings(session, resume_version=VERSION, model=MODEL, k=3)
    await session.commit()

    assert set(await stored_matches(session)) == set(open_ids)
    assert len(top) == 3
    assert [r.similarity for r in top] == sorted((r.similarity for r in top), reverse=True)
    assert {r.posting_id for r in top} <= set(open_ids)
    stored = await stored_matches(session)
    assert all(-1.0 <= row.similarity <= 1.0 for row in stored.values())
    assert all(
        row.resume_version == VERSION and row.embedding_model == MODEL for row in stored.values()
    )


@pytest.mark.xfail(raises=NotImplementedError, strict=True)
async def test_rank_postings_keeps_gate_one_selection_and_is_rerunnable(
    session: AsyncSession,
) -> None:
    company = await add_company(session)
    pid = await add_posting(session, company, "p1", vec(1, 0))
    await add_chunk(session, 0, vec(1, 0))
    await session.commit()
    await rank_postings(session, resume_version=VERSION, model=MODEL, k=1)
    await session.execute(update(matches).values(selected_for_analysis=True))
    await session.commit()

    await rank_postings(session, resume_version=VERSION, model=MODEL, k=1)
    await session.commit()

    rows = await stored_matches(session)
    assert len(rows) == 1
    assert rows[pid].selected_for_analysis is True


@pytest.mark.xfail(raises=NotImplementedError, strict=True)
async def test_rank_postings_returns_at_most_the_open_count(session: AsyncSession) -> None:
    company = await add_company(session)
    await add_posting(session, company, "p1", vec(1, 0))
    await add_chunk(session, 0, vec(1, 0))
    await session.commit()

    top = await rank_postings(session, resume_version=VERSION, model=MODEL, k=20)

    assert len(top) == 1


@pytest.mark.xfail(raises=NotImplementedError, strict=True)
async def test_retrieve_resume_context_returns_at_most_k_sorted_descending(
    session: AsyncSession,
) -> None:
    provider = FakeEmbeddingProvider(dimension=EMBEDDING_DIM)
    for ordinal in range(6):
        await add_chunk(session, ordinal, vec(1, ordinal / 5))
    await session.commit()

    found = await retrieve_resume_context(
        session, provider, "python backend", resume_version=VERSION, model=MODEL, k=4
    )

    assert 0 < len(found) <= 4
    assert [c.similarity for c in found] == sorted((c.similarity for c in found), reverse=True)
    assert all(-1.0 <= c.similarity <= 1.0 for c in found)


@pytest.mark.xfail(raises=NotImplementedError, strict=True)
async def test_retrieve_resume_context_with_no_chunks_is_empty(session: AsyncSession) -> None:
    found = await retrieve_resume_context(
        session,
        FakeEmbeddingProvider(dimension=EMBEDDING_DIM),
        "anything",
        resume_version=VERSION,
        model=MODEL,
        k=5,
    )

    assert found == []
