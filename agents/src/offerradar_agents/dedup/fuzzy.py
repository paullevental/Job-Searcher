from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final, Literal, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.config import get_settings
from offerradar_agents.db import tables

logger = logging.getLogger(__name__)

EVENT = "dedup.fuzzy"

FuzzyVerdict = Literal["merge", "review", "distinct"]

# Two tokens count as one word in two forms when they agree on this much of the
# longer one: engineer/engineering 0.73, development/developer 0.64, but
# starlink/starship only 0.50 and those are two vehicle programmes.
INFLECTION_PREFIX_RATIO: Final = 0.6


def similarity_score(vector_a: Sequence[float], vector_b: Sequence[float]) -> float:
    norms = math.hypot(*vector_a) * math.hypot(*vector_b)
    return 0.0 if norms == 0 else math.sumprod(vector_a, vector_b) / norms


def classify(score: float) -> FuzzyVerdict:
    settings = get_settings()
    if score >= settings.dedup_merge_threshold:
        return "merge"
    if score >= settings.dedup_review_threshold:
        return "review"
    return "distinct"


def _is_inflection(token_a: str, token_b: str) -> bool:
    shared = 0
    for char_a, char_b in zip(token_a, token_b, strict=False):
        if char_a != char_b:
            break
        shared += 1
    return shared / max(len(token_a), len(token_b)) >= INFLECTION_PREFIX_RATIO


def _is_class_year(token: str) -> bool:
    return token.isdigit() and len(token) == 4 and token.startswith("20")


# Tokens on one side only, after cancelling class years and inflections; a score barely sees one.
def unmatched_tokens(title_a: str, title_b: str) -> tuple[str, ...]:
    counts_a, counts_b = Counter(title_a.split()), Counter(title_b.split())
    only_a = [t for t in (counts_a - counts_b).elements() if not _is_class_year(t)]
    only_b = [t for t in (counts_b - counts_a).elements() if not _is_class_year(t)]
    for token in list(only_a):
        partner = next((other for other in only_b if _is_inflection(token, other)), None)
        if partner is not None:
            only_a.remove(token)
            only_b.remove(partner)
    return tuple(only_a + only_b)


# Caller scopes to same-company, open-only pairs (§8.3) and computes the score.
def fuzzy_match(title_a: str, title_b: str, *, score: float) -> FuzzyVerdict:
    verdict = classify(score)
    # A qualifier on one side only ("ai", "phd", "starlink") means two
    # requisitions. Demoted, not dropped: Paul still sees the pair.
    if verdict == "merge" and unmatched_tokens(title_a, title_b):
        verdict = "review"
    if verdict != "distinct":
        logger.info(
            "%s %s score=%.3f",
            EVENT,
            verdict,
            score,
            extra={"event": EVENT, "verdict": verdict, "score": score},
        )
    return verdict


async def merge_postings(session: AsyncSession, *, duplicate_id: int, canonical_id: int) -> None:
    if duplicate_id == canonical_id:
        raise ValueError("a posting cannot be merged into itself")

    statement = (
        update(tables.postings)
        .where(
            tables.postings.c.id == duplicate_id,
            tables.postings.c.status == "open",
        )
        .values(status="merged", merged_into_id=canonical_id)
    )
    result = cast("CursorResult[Any]", await session.execute(statement))
    if result.rowcount == 0:
        # Already merged, closed, or never existed -- a stale id must not silently no-op.
        raise LookupError(f"no open posting with id {duplicate_id} to merge")

    logger.info(
        "%s merge %s into %s",
        EVENT,
        duplicate_id,
        canonical_id,
        extra={
            "event": EVENT,
            "verdict": "merge",
            "duplicate_id": duplicate_id,
            "canonical_id": canonical_id,
        },
    )


# The row stays `open` and still reaches Matcher: V011's column is a Gate 1 hint,
# not a status transition.
async def flag_possible_duplicate(
    session: AsyncSession, *, posting_id: int, counterpart_id: int
) -> None:
    if posting_id == counterpart_id:
        raise ValueError("a posting cannot be a possible duplicate of itself")

    statement = (
        update(tables.postings)
        .where(
            tables.postings.c.id == posting_id,
            tables.postings.c.status == "open",
        )
        .values(possible_duplicate_of=counterpart_id)
    )
    result = cast("CursorResult[Any]", await session.execute(statement))
    if result.rowcount == 0:
        raise LookupError(f"no open posting with id {posting_id} to flag")

    logger.info(
        "%s review %s against %s",
        EVENT,
        posting_id,
        counterpart_id,
        extra={
            "event": EVENT,
            "verdict": "review",
            "posting_id": posting_id,
            "counterpart_id": counterpart_id,
        },
    )
