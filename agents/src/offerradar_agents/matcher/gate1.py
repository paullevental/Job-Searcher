from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db.tables import companies, matches, postings
from offerradar_agents.matcher.prefilter import PostingFacts, PrefilterRules, passes


@dataclass(frozen=True)
class ShortlistItem:
    posting_id: int
    similarity: float
    title: str
    company: str


async def shortlist(session: AsyncSession, *, n: int, rules: PrefilterRules) -> list[ShortlistItem]:
    statement = (
        select(
            postings.c.id,
            matches.c.similarity,
            postings.c.title,
            companies.c.name,
            postings.c.location,
            postings.c.remote,
            postings.c.term,
            postings.c.description,
        )
        .select_from(
            postings.join(matches, matches.c.posting_id == postings.c.id).join(
                companies, companies.c.id == postings.c.company_id
            )
        )
        .where(postings.c.status == "open")
        .order_by(matches.c.similarity.desc(), postings.c.id)
    )
    items: list[ShortlistItem] = []
    if n <= 0:
        return items
    result = await session.stream(statement)
    async for row in result:
        facts = PostingFacts(row.title, row.location, row.remote, row.term, row.description)
        if passes(facts, rules):
            items.append(ShortlistItem(row.id, float(row.similarity), row.title, row.name))
            if len(items) == n:
                break
    await result.close()
    return items


async def select_for_analysis(session: AsyncSession, posting_ids: list[int]) -> int:
    if not posting_ids:
        return 0
    result = await session.execute(
        update(matches)
        .where(matches.c.posting_id.in_(posting_ids))
        .values(selected_for_analysis=True)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def auto_forward(session: AsyncSession, *, n: int, rules: PrefilterRules) -> list[int]:
    if n == 0:
        return []
    ids = [item.posting_id for item in await shortlist(session, n=n, rules=rules)]
    await select_for_analysis(session, ids)
    return ids
