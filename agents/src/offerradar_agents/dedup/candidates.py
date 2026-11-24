from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db import tables
from offerradar_agents.dedup.fuzzy import (
    FuzzyVerdict,
    flag_possible_duplicate,
    fuzzy_match,
    merge_postings,
    similarity_score,
)
from offerradar_agents.dedup.staleness import ATS_SOURCES
from offerradar_agents.embeddings.provider import EmbeddingProvider

logger = logging.getLogger(__name__)

EVENT = "dedup.candidates"


@dataclass(frozen=True)
class CandidatePosting:
    id: int
    company_id: int
    normalized_title: str
    first_seen_at: datetime
    possible_duplicate_of: int | None = None


@dataclass(frozen=True)
class DedupDecision:
    posting_id: int
    counterpart_id: int
    verdict: FuzzyVerdict
    score: float
    canonical_id: int
    duplicate_id: int


_COLUMNS = (
    tables.postings.c.id,
    tables.postings.c.company_id,
    tables.postings.c.normalized_title,
    tables.postings.c.first_seen_at,
    tables.postings.c.possible_duplicate_of,
)


def _row(row: Any) -> CandidatePosting:
    return CandidatePosting(
        id=int(row.id),
        company_id=int(row.company_id),
        normalized_title=str(row.normalized_title),
        first_seen_at=row.first_seen_at,
        possible_duplicate_of=row.possible_duplicate_of,
    )


async def load_open_posting(session: AsyncSession, posting_id: int) -> CandidatePosting | None:
    result = await session.execute(
        select(*_COLUMNS).where(
            tables.postings.c.id == posting_id,
            tables.postings.c.status == "open",
        )
    )
    row = result.one_or_none()
    return None if row is None else _row(row)


# An ATS board lists each requisition once, so two rows carrying a sighting from
# the same board are two requisitions however alike their titles read. Measured on
# a 4210-posting harvest: every merge-band pair sharing a board was a false
# positive (11 of 11) and every true duplicate was cross-source. See ADR-004.
def _shares_an_ats_board(exclude_id: int) -> ColumnElement[bool]:
    mine = tables.posting_sightings.alias("mine")
    theirs = tables.posting_sightings.alias("theirs")
    return (
        select(1)
        .select_from(mine.join(theirs, theirs.c.source == mine.c.source))
        .where(
            mine.c.posting_id == tables.postings.c.id,
            theirs.c.posting_id == exclude_id,
            mine.c.source.in_(sorted(ATS_SOURCES)),
        )
        .exists()
    )


# §8.3 scopes the fallback to same company, open postings only. A row already
# flagged is excluded: re-flagging it would overwrite an earlier pairing Paul has
# not seen yet.
async def open_siblings(
    session: AsyncSession, *, company_id: int, exclude_id: int
) -> tuple[CandidatePosting, ...]:
    result = await session.execute(
        select(*_COLUMNS)
        .where(
            tables.postings.c.company_id == company_id,
            tables.postings.c.status == "open",
            tables.postings.c.id != exclude_id,
            tables.postings.c.possible_duplicate_of.is_(None),
            ~_shares_an_ats_board(exclude_id),
        )
        .order_by(tables.postings.c.id)
    )
    return tuple(_row(row) for row in result.all())


# now() is transaction_timestamp() and frozen per transaction, so two postings
# upserted in one Scout run share first_seen_at exactly; id breaks the tie or the
# merge direction is whichever row the planner happened to return first.
def _canonical_first(
    a: CandidatePosting, b: CandidatePosting
) -> tuple[CandidatePosting, CandidatePosting]:
    if (a.first_seen_at, a.id) <= (b.first_seen_at, b.id):
        return a, b
    return b, a


# Embeds before any write: a caller catching EMBEDDING_ERRORS keeps a clean transaction.
async def resolve_duplicates(
    session: AsyncSession, provider: EmbeddingProvider, *, posting_id: int
) -> DedupDecision | None:
    subject = await load_open_posting(session, posting_id)
    if subject is None:
        return None
    # A flag means "awaiting Paul's review". Re-deciding nightly would either
    # redo settled work or repoint it at a different row he has not seen.
    if subject.possible_duplicate_of is not None:
        return None

    siblings = await open_siblings(session, company_id=subject.company_id, exclude_id=subject.id)
    if not siblings:
        return None

    titles = list(
        dict.fromkeys([subject.normalized_title, *(s.normalized_title for s in siblings)])
    )
    vectors = dict(zip(titles, await provider.embed(titles), strict=True))
    subject_vector = vectors[subject.normalized_title]
    score, best = max(
        ((similarity_score(subject_vector, vectors[s.normalized_title]), s) for s in siblings),
        key=lambda scored: scored[0],
    )

    verdict = fuzzy_match(subject.normalized_title, best.normalized_title, score=score)
    if verdict == "distinct":
        return None

    canonical, duplicate = _canonical_first(subject, best)
    if verdict == "merge":
        await merge_postings(session, duplicate_id=duplicate.id, canonical_id=canonical.id)
    else:
        await flag_possible_duplicate(session, posting_id=duplicate.id, counterpart_id=canonical.id)

    return DedupDecision(
        posting_id=subject.id,
        counterpart_id=best.id,
        verdict=verdict,
        score=score,
        canonical_id=canonical.id,
        duplicate_id=duplicate.id,
    )
