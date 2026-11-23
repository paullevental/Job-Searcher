from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db import tables
from offerradar_agents.scout.models import Posting

_UNIMPLEMENTED = "Paul writes this: AGENTS.md §11 M2"

# companies.source tracks how the company row was discovered, not which Scout
# adapter found this posting -- ck_companies_source admits a different vocabulary.
_COMPANY_SOURCE_BY_POSTING_SOURCE: dict[str, str] = {
    "greenhouse": "ats_discovery",
    "lever": "ats_discovery",
    "simplify": "github_list",
    "ouckah": "github_list",
}


@dataclass(frozen=True)
class AtsBoardRow:
    id: int
    company_id: int
    provider: str
    board_token: str


async def get_or_create_company(
    session: AsyncSession,
    *,
    name: str,
    normalized_name: str,
    source: str,
) -> int:
    statement = insert(tables.companies).values(
        name=name, normalized_name=normalized_name, source=source
    )
    upsert = statement.on_conflict_do_update(
        index_elements=[tables.companies.c.normalized_name],
        set_={"normalized_name": statement.excluded.normalized_name},
    ).returning(tables.companies.c.id)

    result = await session.execute(upsert)
    return int(result.scalar_one())


async def upsert_posting(session: AsyncSession, posting: Posting) -> int:
    company_id = await get_or_create_company(
        session,
        name=posting.company_name,
        normalized_name=posting.normalized_company_name,
        source=_COMPANY_SOURCE_BY_POSTING_SOURCE.get(posting.source, "manual"),
    )

    post_insert = insert(tables.postings).values(
        company_id=company_id,
        title=posting.title,
        normalized_title=posting.normalized_title,
        location=posting.location,
        remote=posting.remote,
        term=posting.term,
        canonical_url=posting.canonical_url,
        dedup_key=posting.dedup_key,
        description=posting.description,
        description_hash=posting.description_hash,
    )
    post_upsert = post_insert.on_conflict_do_update(
        index_elements=[tables.postings.c.dedup_key],
        set_={
            "title": post_insert.excluded.title,
            "normalized_title": post_insert.excluded.normalized_title,
            "location": post_insert.excluded.location,
            "remote": post_insert.excluded.remote,
            "term": post_insert.excluded.term,
            "canonical_url": post_insert.excluded.canonical_url,
            "description": func.coalesce(
                tables.postings.c.description, post_insert.excluded.description
            ),
            "description_hash": func.coalesce(
                tables.postings.c.description_hash, post_insert.excluded.description_hash
            ),
            "last_seen_at": func.now(),
        },
    ).returning(tables.postings.c.id)
    posting_id = int((await session.execute(post_upsert)).scalar_one())

    sighting_insert = insert(tables.posting_sightings).values(
        posting_id=posting_id, source=posting.source, source_url=posting.source_url
    )
    sighting_upsert = sighting_insert.on_conflict_do_nothing(
        index_elements=[
            tables.posting_sightings.c.posting_id,
            tables.posting_sightings.c.source,
            tables.posting_sightings.c.source_url,
        ],
    )
    await session.execute(sighting_upsert)

    return posting_id


async def source_cursor_etag(
    session: AsyncSession,
    *,
    source: str,
    cursor_key: str,
) -> str | None:
    statement = select(tables.source_cursors.c.etag).where(
        tables.source_cursors.c.source == source,
        tables.source_cursors.c.cursor_key == cursor_key,
    )
    result = await session.execute(statement)
    stored = result.scalar_one_or_none()
    return None if stored is None else str(stored)


async def upsert_source_cursor(
    session: AsyncSession,
    *,
    source: str,
    cursor_key: str,
    etag: str | None,
) -> None:
    statement = insert(tables.source_cursors).values(
        source=source, cursor_key=cursor_key, etag=etag, last_polled_at=func.now()
    )
    upsert = statement.on_conflict_do_update(
        index_elements=[
            tables.source_cursors.c.source,
            tables.source_cursors.c.cursor_key,
        ],
        set_={
            "etag": func.coalesce(statement.excluded.etag, tables.source_cursors.c.etag),
            "last_polled_at": func.now(),
        },
    )
    await session.execute(upsert)


async def ats_board_etag(session: AsyncSession, *, board_id: int) -> str | None:
    statement = select(tables.ats_boards.c.etag).where(tables.ats_boards.c.id == board_id)
    result = await session.execute(statement)
    stored = result.scalar_one_or_none()
    return None if stored is None else str(stored)


async def touch_ats_board(
    session: AsyncSession,
    *,
    board_id: int,
    etag: str | None,
) -> None:
    statement = (
        update(tables.ats_boards)
        .where(tables.ats_boards.c.id == board_id)
        .values(
            etag=func.coalesce(etag, tables.ats_boards.c.etag),
            last_polled_at=func.now(),
        )
    )
    result = cast("CursorResult[Any]", await session.execute(statement))
    if result.rowcount == 0:
        # A stale board_id would report a poll against a cursor that never moved.
        raise LookupError(f"no ats_boards row with id {board_id}")


async def list_ats_boards(
    session: AsyncSession, *, providers: tuple[str, ...]
) -> list[AtsBoardRow]:
    statement = (
        select(
            tables.ats_boards.c.id,
            tables.ats_boards.c.company_id,
            tables.ats_boards.c.provider,
            tables.ats_boards.c.board_token,
        )
        .where(tables.ats_boards.c.provider.in_(providers))
        .order_by(tables.ats_boards.c.id)
    )
    result = await session.execute(statement)
    return [
        AtsBoardRow(
            id=int(row.id),
            company_id=int(row.company_id),
            provider=str(row.provider),
            board_token=str(row.board_token),
        )
        for row in result.all()
    ]
