from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.config import get_settings
from offerradar_agents.db import tables
from offerradar_agents.dedup.fuzzy import merge_postings
from offerradar_agents.dedup.staleness import apply_poll_result, sweep_stale_by_age
from offerradar_agents.scout import repository
from offerradar_agents.scout.models import Posting

SOURCE = "greenhouse"


def make_posting(title: str, **overrides: object) -> Posting:
    slug = hashlib.sha256(title.encode()).hexdigest()[:8]
    fields: dict[str, object] = {
        "company_name": "Acme Labs",
        "normalized_company_name": "acme labs",
        "title": title.title(),
        "normalized_title": title,
        "canonical_url": f"https://boards.greenhouse.io/acme/jobs/{slug}",
        "dedup_key": hashlib.sha256(title.encode()).hexdigest(),
        "source": SOURCE,
        "source_url": f"https://boards.greenhouse.io/acme/jobs/{slug}",
    }
    fields.update(overrides)
    return Posting(**fields)  # type: ignore[arg-type]


async def status_of(session: AsyncSession, posting_id: int) -> str:
    result = await session.execute(
        select(tables.postings.c.status).where(tables.postings.c.id == posting_id)
    )
    return str(result.scalar_one())


async def missed_polls_of(session: AsyncSession, posting_id: int) -> int:
    result = await session.execute(
        select(tables.postings.c.missed_polls).where(tables.postings.c.id == posting_id)
    )
    return int(result.scalar_one())


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


async def test_an_empty_seen_set_is_refused_by_default(db: AsyncSession) -> None:
    # A 304 or an empty 200 reaching the sweep would close the whole source,
    # because `notin_([])` is SQL-true.
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    with pytest.raises(ValueError, match="empty seen-set"):
        await apply_poll_result(db, source=SOURCE, seen_posting_ids=[])

    assert await missed_polls_of(db, posting_id) == 0
    assert await status_of(db, posting_id) == "open"


async def test_one_missed_poll_does_not_close_a_posting(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    sweep = await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)

    assert sweep.missed == 1
    assert sweep.closed == 0
    assert await missed_polls_of(db, posting_id) == 1
    assert await status_of(db, posting_id) == "open"


async def test_two_consecutive_missed_polls_close_a_posting(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)
    sweep = await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)

    assert sweep.closed == 1
    assert await status_of(db, posting_id) == "closed"


async def test_a_sighting_resets_the_miss_counter(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)
    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[posting_id])
    sweep = await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)

    assert sweep.closed == 0
    assert await missed_polls_of(db, posting_id) == 1
    assert await status_of(db, posting_id) == "open"


async def test_a_poll_only_touches_its_own_source(db: AsyncSession) -> None:
    other = await repository.upsert_posting(
        db,
        make_posting(
            "data engineer intern",
            source="lever",
            source_url="https://jobs.lever.co/acme/abc-123",
            canonical_url="https://jobs.lever.co/acme/abc-123",
        ),
    )

    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)

    assert await missed_polls_of(db, other) == 0


async def test_a_closed_posting_is_not_swept_again(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))
    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)
    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)

    sweep = await apply_poll_result(db, source=SOURCE, seen_posting_ids=[], allow_empty=True)

    assert sweep.missed == 0
    assert sweep.closed == 0
    assert await status_of(db, posting_id) == "closed"


async def test_age_sweep_marks_a_quiet_posting_stale(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))
    aged = datetime.now(UTC) - timedelta(days=get_settings().dedup_stale_after_days + 1)
    await db.execute(
        update(tables.postings).where(tables.postings.c.id == posting_id).values(last_seen_at=aged)
    )

    assert await sweep_stale_by_age(db) == 1
    assert await status_of(db, posting_id) == "stale"


async def test_age_sweep_leaves_a_recent_posting_open(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    assert await sweep_stale_by_age(db) == 0
    assert await status_of(db, posting_id) == "open"


async def test_merge_sets_status_and_parent(db: AsyncSession) -> None:
    canonical = await repository.upsert_posting(db, make_posting("software engineer intern"))
    duplicate = await repository.upsert_posting(db, make_posting("software engineering intern"))

    await merge_postings(db, duplicate_id=duplicate, canonical_id=canonical)

    result = await db.execute(
        select(tables.postings.c.status, tables.postings.c.merged_into_id).where(
            tables.postings.c.id == duplicate
        )
    )
    status, merged_into = result.one()
    assert status == "merged"
    assert merged_into == canonical
    assert await status_of(db, canonical) == "open"


async def test_merging_an_already_merged_posting_raises(db: AsyncSession) -> None:
    canonical = await repository.upsert_posting(db, make_posting("software engineer intern"))
    duplicate = await repository.upsert_posting(db, make_posting("software engineering intern"))
    await merge_postings(db, duplicate_id=duplicate, canonical_id=canonical)

    with pytest.raises(LookupError):
        await merge_postings(db, duplicate_id=duplicate, canonical_id=canonical)


async def test_merging_a_posting_into_itself_raises(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    with pytest.raises(ValueError, match="itself"):
        await merge_postings(db, duplicate_id=posting_id, canonical_id=posting_id)


async def test_a_merged_posting_is_not_swept(db: AsyncSession) -> None:
    canonical = await repository.upsert_posting(db, make_posting("software engineer intern"))
    duplicate = await repository.upsert_posting(db, make_posting("software engineering intern"))
    await merge_postings(db, duplicate_id=duplicate, canonical_id=canonical)

    await apply_poll_result(db, source=SOURCE, seen_posting_ids=[canonical])

    assert await status_of(db, duplicate) == "merged"
    assert await missed_polls_of(db, duplicate) == 0
