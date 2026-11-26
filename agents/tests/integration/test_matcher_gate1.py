from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from offerradar_agents.config import Settings
from offerradar_agents.db.session import build_engine
from offerradar_agents.db.tables import companies, matches, postings
from offerradar_agents.matcher.gate1 import auto_forward, select_for_analysis, shortlist
from offerradar_agents.matcher.prefilter import PrefilterRules

pytestmark = pytest.mark.integration

RULES = PrefilterRules(terms=(), locations=())


@pytest.fixture
async def session(postgres_container: Any) -> AsyncIterator[AsyncSession]:
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    engine: AsyncEngine = build_engine(Settings(database_url=url))
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE matches, postings, companies RESTART IDENTITY CASCADE")
            )
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


async def add_company(session: AsyncSession) -> int:
    result = await session.execute(
        insert(companies).values(name="Acme", normalized_name="acme", source="manual")
    )
    return int(result.inserted_primary_key[0])


async def add_ranked(
    session: AsyncSession,
    company_id: int,
    slug: str,
    similarity: float,
    *,
    title: str | None = None,
    status: str = "open",
) -> int:
    result = await session.execute(
        insert(postings).values(
            company_id=company_id,
            title=title or slug,
            normalized_title=slug,
            canonical_url=f"https://acme.example/{slug}",
            dedup_key=slug,
            status=status,
        )
    )
    posting_id = int(result.inserted_primary_key[0])
    await session.execute(
        insert(matches).values(
            posting_id=posting_id,
            similarity=similarity,
            embedding_model="nomic-embed-text",
            resume_version="v1",
        )
    )
    return posting_id


async def selected_ids(session: AsyncSession) -> set[int]:
    rows = await session.execute(
        select(matches.c.posting_id).where(matches.c.selected_for_analysis.is_(True))
    )
    return {row.posting_id for row in rows}


async def test_shortlist_orders_filters_and_skips_non_open(session: AsyncSession) -> None:
    company = await add_company(session)
    low = await add_ranked(session, company, "low", 0.2)
    top = await add_ranked(session, company, "top", 0.9)
    await add_ranked(session, company, "senior", 0.95, title="Senior Engineer")
    await add_ranked(session, company, "closed", 0.99, status="closed")
    mid = await add_ranked(session, company, "mid", 0.5)
    await session.commit()

    items = await shortlist(session, n=10, rules=RULES)

    assert [item.posting_id for item in items] == [top, mid, low]
    assert items[0].company == "Acme"
    assert items[0].similarity == pytest.approx(0.9)
    assert [item.posting_id for item in await shortlist(session, n=2, rules=RULES)] == [top, mid]


async def test_auto_forward_zero_selects_nothing(session: AsyncSession) -> None:
    company = await add_company(session)
    await add_ranked(session, company, "a", 0.9)
    await session.commit()

    assert await auto_forward(session, n=0, rules=RULES) == []
    await session.commit()
    assert await selected_ids(session) == set()


async def test_auto_forward_selects_top_passing(session: AsyncSession) -> None:
    company = await add_company(session)
    ids = [await add_ranked(session, company, f"p{i}", 0.9 - i / 10) for i in range(5)]
    await add_ranked(session, company, "senior", 0.99, title="Staff Engineer")
    await session.commit()

    forwarded = await auto_forward(session, n=3, rules=RULES)
    await session.commit()

    assert forwarded == ids[:3]
    assert await selected_ids(session) == set(ids[:3])


async def test_select_for_analysis_returns_count(session: AsyncSession) -> None:
    company = await add_company(session)
    first = await add_ranked(session, company, "a", 0.9)
    second = await add_ranked(session, company, "b", 0.8)
    await session.commit()

    assert await select_for_analysis(session, []) == 0
    assert await select_for_analysis(session, [first, second, 999]) == 2
    await session.commit()
    assert await selected_ids(session) == {first, second}
