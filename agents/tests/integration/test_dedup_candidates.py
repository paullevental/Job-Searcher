from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.config import get_settings
from offerradar_agents.db import tables
from offerradar_agents.dedup.candidates import open_siblings, resolve_duplicates
from offerradar_agents.scout import repository
from offerradar_agents.scout.models import Posting

SOURCE = "greenhouse"
# One board lists each requisition once, so open_siblings refuses two rows that
# share an ATS board. A pair that can actually be a duplicate is cross-source.
LIST_SOURCE = "simplify"


def make_posting(title: str, source: str = SOURCE, **overrides: object) -> Posting:
    slug = hashlib.sha256(f"{title}{source}".encode()).hexdigest()[:8]
    host = "boards.greenhouse.io" if source == SOURCE else "github.com"
    fields: dict[str, object] = {
        "company_name": "Acme Labs",
        "normalized_company_name": "acme labs",
        "title": title.title(),
        "normalized_title": title,
        "canonical_url": f"https://{host}/acme/jobs/{slug}",
        "dedup_key": hashlib.sha256(f"{title}{source}".encode()).hexdigest(),
        "source": source,
        "source_url": f"https://{host}/acme/jobs/{slug}",
    }
    fields.update(overrides)
    return Posting(**fields)  # type: ignore[arg-type]


def listed(title: str, **overrides: object) -> Posting:
    return make_posting(title, LIST_SOURCE, **overrides)


async def age(session: AsyncSession, posting_id: int, days: int) -> None:
    await session.execute(
        update(tables.postings)
        .where(tables.postings.c.id == posting_id)
        .values(first_seen_at=datetime.now(UTC) - timedelta(days=days))
    )


async def read(session: AsyncSession, posting_id: int) -> tuple[str, int | None, int | None]:
    result = await session.execute(
        select(
            tables.postings.c.status,
            tables.postings.c.merged_into_id,
            tables.postings.c.possible_duplicate_of,
        ).where(tables.postings.c.id == posting_id)
    )
    status, merged_into, flagged = result.one()
    return str(status), merged_into, flagged


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


async def test_no_siblings_means_no_decision(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(db, make_posting("software engineer intern"))

    assert await resolve_duplicates(db, posting_id=posting_id) is None


async def test_siblings_are_scoped_to_the_same_company(db: AsyncSession) -> None:
    mine = await repository.upsert_posting(db, make_posting("software engineer intern"))
    await repository.upsert_posting(
        db,
        make_posting(
            "software engineering intern",
            company_name="Globex",
            normalized_company_name="globex",
        ),
    )

    assert await resolve_duplicates(db, posting_id=mine) is None


async def test_the_older_posting_is_canonical(db: AsyncSession) -> None:
    older = await repository.upsert_posting(db, make_posting("backend engineer intern"))
    await age(db, older, days=20)
    newer = await repository.upsert_posting(db, listed("backend engineering intern"))

    decision = await resolve_duplicates(db, posting_id=newer)

    assert decision is not None
    assert decision.verdict == "merge"
    assert decision.canonical_id == older
    assert decision.duplicate_id == newer
    assert await read(db, newer) == ("merged", older, None)
    assert await read(db, older) == ("open", None, None)


async def test_merge_direction_is_the_same_whichever_row_is_resolved(db: AsyncSession) -> None:
    older = await repository.upsert_posting(db, make_posting("backend engineer intern"))
    await age(db, older, days=20)
    newer = await repository.upsert_posting(db, listed("backend engineering intern"))

    decision = await resolve_duplicates(db, posting_id=older)

    assert decision is not None
    assert decision.canonical_id == older
    assert decision.duplicate_id == newer


async def test_identical_first_seen_at_breaks_the_tie_on_id(db: AsyncSession) -> None:
    # now() is transaction_timestamp(): both rows share first_seen_at exactly,
    # so without the id tie-break the direction would be arbitrary.
    first = await repository.upsert_posting(db, make_posting("backend engineer intern"))
    second = await repository.upsert_posting(db, listed("backend engineering intern"))

    decision = await resolve_duplicates(db, posting_id=second)

    assert decision is not None
    assert decision.canonical_id == first
    assert decision.duplicate_id == second


async def test_the_review_band_flags_without_merging(db: AsyncSession) -> None:
    older = await repository.upsert_posting(db, make_posting("product manager intern"))
    await age(db, older, days=20)
    newer = await repository.upsert_posting(db, listed("product management intern"))

    decision = await resolve_duplicates(db, posting_id=newer)

    assert decision is not None
    assert decision.verdict == "review"
    assert await read(db, newer) == ("open", None, older)
    assert await read(db, older) == ("open", None, None)


async def test_a_distinct_sibling_is_left_alone(db: AsyncSession) -> None:
    await repository.upsert_posting(db, make_posting("machine learning intern"))
    other = await repository.upsert_posting(db, make_posting("frontend developer intern"))

    assert await resolve_duplicates(db, posting_id=other) is None
    assert await read(db, other) == ("open", None, None)


async def test_an_already_flagged_sibling_is_not_reconsidered(db: AsyncSession) -> None:
    older = await repository.upsert_posting(db, make_posting("product manager intern"))
    await age(db, older, days=20)
    newer = await repository.upsert_posting(db, listed("product management intern"))
    await resolve_duplicates(db, posting_id=newer)

    # The flagged row is excluded from the sibling set, so a second pass is a no-op
    # rather than overwriting a pairing Paul has not seen.
    assert await resolve_duplicates(db, posting_id=newer) is None
    assert await read(db, newer) == ("open", None, older)


async def test_a_merged_posting_is_not_resolved_again(db: AsyncSession) -> None:
    older = await repository.upsert_posting(db, make_posting("backend engineer intern"))
    await age(db, older, days=20)
    newer = await repository.upsert_posting(db, listed("backend engineering intern"))
    await resolve_duplicates(db, posting_id=newer)

    assert await resolve_duplicates(db, posting_id=newer) is None


async def test_a_merged_row_cannot_also_carry_a_duplicate_flag(db: AsyncSession) -> None:
    # ck_postings_possible_duplicate_not_merged: two answers to one question.
    older = await repository.upsert_posting(db, make_posting("backend engineer intern"))
    await age(db, older, days=20)
    newer = await repository.upsert_posting(db, listed("backend engineering intern"))
    await resolve_duplicates(db, posting_id=newer)

    status, merged_into, flagged = await read(db, newer)
    assert status == "merged"
    assert merged_into == older
    assert flagged is None


async def test_open_siblings_excludes_the_subject(db: AsyncSession) -> None:
    mine = await repository.upsert_posting(db, make_posting("software engineer intern"))
    other = await repository.upsert_posting(db, listed("data engineer intern"))

    siblings = await open_siblings(db, company_id=1, exclude_id=mine)

    assert [sibling.id for sibling in siblings] == [other]


async def test_two_rows_on_one_board_are_never_candidates(db: AsyncSession) -> None:
    # Measured on a 4210-posting harvest: of the merge-band pairs sharing a board,
    # 11 of 11 were seniority or variant reqs, not duplicates. See ADR-004.
    mine = await repository.upsert_posting(db, make_posting("systems engineer"))
    await repository.upsert_posting(db, make_posting("system engineer"))

    assert await open_siblings(db, company_id=1, exclude_id=mine) == ()
    assert await resolve_duplicates(db, posting_id=mine) is None


async def test_a_cross_source_sighting_still_pairs(db: AsyncSession) -> None:
    mine = await repository.upsert_posting(db, make_posting("systems engineer"))
    listed_row = await repository.upsert_posting(db, listed("system engineer"))

    siblings = await open_siblings(db, company_id=1, exclude_id=mine)

    assert [sibling.id for sibling in siblings] == [listed_row]


async def test_a_board_sighting_added_later_withdraws_the_candidate(db: AsyncSession) -> None:
    # The same job can be seen on a list first and the board second; the second
    # sighting is what reveals the two rows are separate requisitions.
    mine = await repository.upsert_posting(db, make_posting("systems engineer"))
    other = await repository.upsert_posting(db, listed("system engineer"))
    assert len(await open_siblings(db, company_id=1, exclude_id=mine)) == 1

    await repository.upsert_posting(db, make_posting("system engineer", source=SOURCE))
    await db.execute(
        tables.posting_sightings.insert().values(
            posting_id=other, source=SOURCE, source_url="https://boards.greenhouse.io/acme/late"
        )
    )

    assert await open_siblings(db, company_id=1, exclude_id=mine) == ()
