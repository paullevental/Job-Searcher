from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.config import get_settings
from offerradar_agents.db import tables

logger = logging.getLogger(__name__)

EVENT = "dedup.staleness"


ATS_SOURCES = frozenset({"greenhouse", "lever", "ashby", "workable"})
GITHUB_LIST_SOURCES = frozenset({"simplify", "ouckah"})


@dataclass(frozen=True)
class PollSweep:
    source: str
    seen: int = 0
    missed: int = 0
    closed: int = 0
    terminal_status: str = "closed"


# §8.4. An ATS board is the company's own publication, so an absent posting is
# evidence the job ended. A GitHub list is volunteer-maintained: an absent row
# means the maintainer stopped tracking it, which is not the same claim.
def terminal_status_for(source: str) -> str:
    return "stale" if source in GITHUB_LIST_SOURCES else "closed"


# "confirmed against ATS" (§8.4): the board that publishes the job owns its
# status, so a GitHub absence must not retire a posting the ATS still lists.
def _has_ats_sighting() -> ColumnElement[bool]:
    return tables.postings.c.id.in_(
        select(tables.posting_sightings.c.posting_id).where(
            tables.posting_sightings.c.source.in_(sorted(ATS_SOURCES))
        )
    )


async def _rowcount(session: AsyncSession, statement: Any) -> int:
    result = cast("CursorResult[Any]", await session.execute(statement))
    return int(result.rowcount)


def _polled_by(source: str, company_id: int | None) -> ColumnElement[bool]:
    sighted = select(tables.posting_sightings.c.posting_id).where(
        tables.posting_sightings.c.source == source
    )
    scoped = tables.postings.c.id.in_(sighted) & (tables.postings.c.status == "open")
    if company_id is None:
        return scoped
    return scoped & (tables.postings.c.company_id == company_id)


# Only a poll that returned a fresh list is an observation. A 304 or an empty 200
# is not, and an empty seen-set would increment every posting on the source, so it
# is rejected rather than documented against: `notin_([])` is SQL-true.
async def apply_poll_result(
    session: AsyncSession,
    *,
    source: str,
    seen_posting_ids: Collection[int],
    company_id: int | None = None,
    allow_empty: bool = False,
) -> PollSweep:
    settings = get_settings()
    candidates = _polled_by(source, company_id)
    seen_ids = set(seen_posting_ids)
    if not seen_ids and not allow_empty:
        raise ValueError(
            f"{source}: refusing to sweep on an empty seen-set; a 304 or empty 200 is "
            "not an observation. Pass allow_empty=True only for a poll that really "
            "returned zero postings."
        )

    seen = await _rowcount(
        session,
        update(tables.postings)
        .where(candidates, tables.postings.c.id.in_(seen_ids))
        .values(missed_polls=0),
    )
    missed = await _rowcount(
        session,
        update(tables.postings)
        .where(candidates, tables.postings.c.id.notin_(seen_ids))
        .values(missed_polls=tables.postings.c.missed_polls + 1),
    )
    terminal = terminal_status_for(source)
    retire = [candidates, tables.postings.c.missed_polls >= settings.dedup_missed_poll_limit]
    if terminal == "stale":
        retire.append(~_has_ats_sighting())
    closed = await _rowcount(
        session,
        update(tables.postings).where(*retire).values(status=terminal),
    )

    sweep = PollSweep(
        source=source, seen=seen, missed=missed, closed=closed, terminal_status=terminal
    )
    if missed or closed:
        logger.info(
            "%s %s seen=%d missed=%d closed=%d",
            EVENT,
            source,
            seen,
            missed,
            closed,
            extra={
                "event": EVENT,
                "source": source,
                "seen": seen,
                "missed": missed,
                "closed": closed,
            },
        )
    return sweep


# A 🔒 row says the posting closed, so it retires on one sighting rather than
# waiting out DEDUP_MISSED_POLL_LIMIT. Still subject to "confirmed against ATS":
# a list maintainer's padlock does not outrank the company's own board.
async def mark_locked_stale(session: AsyncSession, *, dedup_keys: Collection[str]) -> int:
    keys = sorted(set(dedup_keys))
    if not keys:
        return 0
    marked = await _rowcount(
        session,
        update(tables.postings)
        .where(
            tables.postings.c.dedup_key.in_(keys),
            tables.postings.c.status == "open",
            ~_has_ats_sighting(),
        )
        .values(status="stale"),
    )
    if marked:
        logger.info(
            "%s locked %d postings",
            EVENT,
            marked,
            extra={"event": EVENT, "locked_stale": marked},
        )
    return marked


# §8's catch-all: anything no poll has confirmed in DEDUP_STALE_AFTER_DAYS days.
# 'stale' not 'closed' -- nothing observed it gone, it just went quiet.
async def sweep_stale_by_age(session: AsyncSession) -> int:
    settings = get_settings()
    cutoff = timedelta(days=settings.dedup_stale_after_days)
    marked = await _rowcount(
        session,
        update(tables.postings)
        .where(
            tables.postings.c.status == "open",
            tables.postings.c.last_seen_at < func.now() - cutoff,
        )
        .values(status="stale"),
    )
    if marked:
        logger.info(
            "%s aged out %d postings",
            EVENT,
            marked,
            extra={"event": EVENT, "stale": marked, "after_days": settings.dedup_stale_after_days},
        )
    return marked
