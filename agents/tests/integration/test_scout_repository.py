from __future__ import annotations

import hashlib

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db import tables
from offerradar_agents.scout import repository
from offerradar_agents.scout.models import Posting

DESCRIPTION = "Build the ingestion pipeline."


def make_posting(**overrides: object) -> Posting:
    fields: dict[str, object] = {
        "company_name": "Acme Labs",
        "normalized_company_name": "acme labs",
        "title": "Software Engineer Intern",
        "normalized_title": "software engineer intern",
        "location": "Toronto, ON",
        "remote": False,
        "term": "Summer 2026",
        "canonical_url": "https://boards.greenhouse.io/acme/jobs/4012",
        "dedup_key": hashlib.sha256(b"acme labs|software engineer intern|Summer 2026").hexdigest(),
        "description": DESCRIPTION,
        "description_hash": hashlib.sha256(DESCRIPTION.encode()).hexdigest(),
        "source": "greenhouse",
        "source_url": "https://boards.greenhouse.io/acme/jobs/4012",
    }
    fields.update(overrides)
    return Posting(**fields)  # type: ignore[arg-type]


async def row_count(session: AsyncSession, table: object) -> int:
    result = await session.execute(select(func.count()).select_from(table))  # type: ignore[arg-type]
    return int(result.scalar_one())


async def test_get_or_create_company_returns_the_same_id_twice(db: AsyncSession) -> None:
    first = await repository.get_or_create_company(
        db, name="Acme Labs", normalized_name="acme labs", source="github_list"
    )
    second = await repository.get_or_create_company(
        db, name="Acme Labs, Inc.", normalized_name="acme labs", source="manual"
    )

    assert first == second
    assert await row_count(db, tables.companies) == 1


async def test_source_cursor_round_trips(db: AsyncSession) -> None:
    assert await repository.source_cursor_etag(db, source="simplify", cursor_key="o/r") is None

    await repository.upsert_source_cursor(db, source="simplify", cursor_key="o/r", etag='W/"abc"')

    assert await repository.source_cursor_etag(db, source="simplify", cursor_key="o/r") == 'W/"abc"'


async def test_upsert_source_cursor_keeps_a_stored_etag_when_a_304_omits_it(
    db: AsyncSession,
) -> None:
    await repository.upsert_source_cursor(db, source="ouckah", cursor_key="o/r", etag='W/"abc"')
    await repository.upsert_source_cursor(db, source="ouckah", cursor_key="o/r", etag=None)

    assert await repository.source_cursor_etag(db, source="ouckah", cursor_key="o/r") == 'W/"abc"'


async def test_upsert_posting_inserts_company_posting_and_sighting(db: AsyncSession) -> None:
    await repository.upsert_posting(db, make_posting())

    assert await row_count(db, tables.companies) == 1
    assert await row_count(db, tables.postings) == 1
    assert await row_count(db, tables.posting_sightings) == 1


async def test_upsert_posting_twice_adds_no_rows(db: AsyncSession) -> None:
    posting = make_posting()
    await repository.upsert_posting(db, posting)
    await repository.upsert_posting(db, posting)

    assert await row_count(db, tables.postings) == 1
    assert await row_count(db, tables.posting_sightings) == 1
    assert await row_count(db, tables.companies) == 1


async def test_upsert_posting_preserves_first_seen_at_and_moves_last_seen_at(
    db: AsyncSession,
) -> None:
    posting = make_posting()
    await repository.upsert_posting(db, posting)
    before = (
        await db.execute(select(tables.postings.c.first_seen_at, tables.postings.c.last_seen_at))
    ).one()

    # Postgres now() is transaction_timestamp(): without a commit it never moves.
    await db.commit()
    await db.execute(text("SELECT pg_sleep(0.01)"))
    await repository.upsert_posting(db, posting)
    after = (
        await db.execute(select(tables.postings.c.first_seen_at, tables.postings.c.last_seen_at))
    ).one()

    assert after.first_seen_at == before.first_seen_at
    assert after.last_seen_at > before.last_seen_at


async def test_upsert_posting_does_not_blank_a_description_another_source_stored(
    db: AsyncSession,
) -> None:
    await repository.upsert_posting(db, make_posting())
    # A README row carries no description; the Greenhouse one already did.
    await repository.upsert_posting(
        db, make_posting(source="simplify", description=None, description_hash=None)
    )

    stored = (
        await db.execute(select(tables.postings.c.description, tables.postings.c.description_hash))
    ).one()
    assert stored.description == DESCRIPTION
    assert stored.description_hash is not None


async def test_upsert_posting_does_not_resurrect_a_closed_posting(db: AsyncSession) -> None:
    posting = make_posting()
    await repository.upsert_posting(db, posting)
    await db.execute(text("UPDATE postings SET status = 'closed'"))

    await repository.upsert_posting(db, posting)

    status = (await db.execute(select(tables.postings.c.status))).scalar_one()
    assert status == "closed"


async def test_two_sources_for_one_posting_make_one_row_and_two_sightings(
    db: AsyncSession,
) -> None:
    await repository.upsert_posting(db, make_posting())
    await repository.upsert_posting(
        db,
        make_posting(
            source="simplify",
            source_url="https://github.com/SimplifyJobs/Summer2026-Internships#acme",
            description=None,
            description_hash=None,
        ),
    )

    assert await row_count(db, tables.postings) == 1
    assert await row_count(db, tables.posting_sightings) == 2
