from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.config import get_settings
from offerradar_agents.db import tables
from offerradar_agents.dedup.staleness import (
    apply_poll_result,
    mark_locked_stale,
    terminal_status_for,
)
from offerradar_agents.scout import repository
from offerradar_agents.scout.models import Posting


def make_posting(title: str, source: str, **overrides: object) -> Posting:
    slug = hashlib.sha256(f"{title}{source}".encode()).hexdigest()[:8]
    host = "boards.greenhouse.io" if source == "greenhouse" else "github.com"
    fields: dict[str, object] = {
        "company_name": "Acme Labs",
        "normalized_company_name": "acme labs",
        "title": title.title(),
        "normalized_title": title,
        "canonical_url": f"https://{host}/acme/{slug}",
        # Shared across sources on purpose: the same job seen in two places.
        "dedup_key": hashlib.sha256(title.encode()).hexdigest(),
        "source": source,
        "source_url": f"https://{host}/acme/{slug}",
    }
    fields.update(overrides)
    return Posting(**fields)  # type: ignore[arg-type]


async def status_of(session: AsyncSession, posting_id: int) -> str:
    result = await session.execute(
        select(tables.postings.c.status).where(tables.postings.c.id == posting_id)
    )
    return str(result.scalar_one())


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_a_github_list_retires_to_stale_and_an_ats_board_to_closed() -> None:
    assert terminal_status_for("simplify") == "stale"
    assert terminal_status_for("ouckah") == "stale"
    assert terminal_status_for("greenhouse") == "closed"
    assert terminal_status_for("lever") == "closed"


async def test_a_vanished_github_row_goes_stale_not_closed(db: AsyncSession) -> None:
    posting_id = await repository.upsert_posting(
        db, make_posting("software engineer intern", "simplify")
    )

    await apply_poll_result(db, source="simplify", seen_posting_ids=[], allow_empty=True)
    sweep = await apply_poll_result(db, source="simplify", seen_posting_ids=[], allow_empty=True)

    assert sweep.terminal_status == "stale"
    assert sweep.closed == 1
    assert await status_of(db, posting_id) == "stale"


async def test_an_ats_sighting_outranks_a_github_absence(db: AsyncSession) -> None:
    # "confirmed against ATS": the company's own board owns the posting's status.
    posting_id = await repository.upsert_posting(
        db, make_posting("software engineer intern", "simplify")
    )
    await repository.upsert_posting(db, make_posting("software engineer intern", "greenhouse"))

    await apply_poll_result(db, source="simplify", seen_posting_ids=[], allow_empty=True)
    sweep = await apply_poll_result(db, source="simplify", seen_posting_ids=[], allow_empty=True)

    assert sweep.closed == 0
    assert await status_of(db, posting_id) == "open"


async def test_a_locked_row_goes_stale_on_one_sighting(db: AsyncSession) -> None:
    # A 🔒 is an observation, not an absence: no DEDUP_MISSED_POLL_LIMIT wait.
    posting = make_posting("software engineer intern", "simplify")
    posting_id = await repository.upsert_posting(db, posting)

    assert await mark_locked_stale(db, dedup_keys=[posting.dedup_key]) == 1
    assert await status_of(db, posting_id) == "stale"


async def test_a_locked_row_does_not_outrank_an_ats_listing(db: AsyncSession) -> None:
    posting = make_posting("software engineer intern", "simplify")
    posting_id = await repository.upsert_posting(db, posting)
    await repository.upsert_posting(db, make_posting("software engineer intern", "greenhouse"))

    assert await mark_locked_stale(db, dedup_keys=[posting.dedup_key]) == 0
    assert await status_of(db, posting_id) == "open"


async def test_marking_locked_is_idempotent(db: AsyncSession) -> None:
    posting = make_posting("software engineer intern", "simplify")
    await repository.upsert_posting(db, posting)

    assert await mark_locked_stale(db, dedup_keys=[posting.dedup_key]) == 1
    assert await mark_locked_stale(db, dedup_keys=[posting.dedup_key]) == 0


async def test_no_locked_keys_is_a_no_op(db: AsyncSession) -> None:
    assert await mark_locked_stale(db, dedup_keys=[]) == 0


async def test_an_unknown_locked_key_marks_nothing(db: AsyncSession) -> None:
    assert await mark_locked_stale(db, dedup_keys=["0" * 64]) == 0


async def test_one_boards_poll_does_not_retire_another_boards_postings(db: AsyncSession) -> None:
    # Found on the first live run: five Greenhouse boards polled in sequence
    # closed 1815 live postings, because the sweep was scoped to the source
    # ('greenhouse') and not to the board's company.
    acme = await repository.upsert_posting(
        db, make_posting("software engineer intern", "greenhouse")
    )
    other = await repository.upsert_posting(
        db,
        make_posting(
            "data engineer intern",
            "greenhouse",
            company_name="Globex",
            normalized_company_name="globex",
        ),
    )
    acme_company = (
        await db.execute(select(tables.postings.c.company_id).where(tables.postings.c.id == acme))
    ).scalar_one()

    for _ in range(3):
        await apply_poll_result(
            db, source="greenhouse", seen_posting_ids=[acme], company_id=int(acme_company)
        )

    assert await status_of(db, acme) == "open"
    assert await status_of(db, other) == "open"
