from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db.tables import matches, postings, resume_chunks
from offerradar_agents.matcher.rank import RetrievedChunk

UPSERT_BATCH = 2000


# float32 rounding can push an identical pair past 1.0; NaN means a zero-norm vector.
def _clamp(similarity: float) -> float:
    if not math.isfinite(similarity):
        raise ValueError(f"non-finite similarity {similarity}: a stored embedding has zero norm")
    return max(-1.0, min(1.0, similarity))


async def chunk_similarities(
    session: AsyncSession, *, resume_version: str, model: str
) -> dict[int, list[float]]:
    similarity = literal(1.0) - postings.c.embedding.cosine_distance(resume_chunks.c.embedding)
    statement = (
        select(postings.c.id, similarity.label("similarity"))
        .select_from(
            postings.join(
                resume_chunks,
                (resume_chunks.c.version == resume_version)
                & (resume_chunks.c.embedding_model == model)
                & resume_chunks.c.embedding.is_not(None),
            )
        )
        .where(
            postings.c.status == "open",
            postings.c.embedding_model == model,
            postings.c.embedding.is_not(None),
        )
        .order_by(postings.c.id, resume_chunks.c.ordinal)
    )
    grouped: dict[int, list[float]] = defaultdict(list)
    for posting_id, value in await session.execute(statement):
        grouped[posting_id].append(_clamp(float(value)))
    return dict(grouped)


async def nearest_chunks(
    session: AsyncSession,
    query_vector: Sequence[float],
    *,
    resume_version: str,
    model: str,
    k: int,
) -> list[RetrievedChunk]:
    distance = resume_chunks.c.embedding.cosine_distance(list(query_vector))
    statement = (
        select(
            resume_chunks.c.id,
            resume_chunks.c.kind,
            resume_chunks.c.ordinal,
            resume_chunks.c.text,
            (literal(1.0) - distance).label("similarity"),
        )
        .where(
            resume_chunks.c.version == resume_version,
            resume_chunks.c.embedding_model == model,
            resume_chunks.c.embedding.is_not(None),
        )
        .order_by(distance, resume_chunks.c.ordinal)
        .limit(k)
    )
    return [
        RetrievedChunk(
            chunk_id=chunk_id,
            kind=kind,
            ordinal=ordinal,
            text=text,
            similarity=_clamp(float(value)),
        )
        for chunk_id, kind, ordinal, text, value in await session.execute(statement)
    ]


# selected_for_analysis is absent from the update set: Gate 1 owns it.
async def upsert_matches(
    session: AsyncSession, scores: Mapping[int, float], *, resume_version: str, model: str
) -> int:
    for posting_id, score in scores.items():
        if not math.isfinite(score) or not -1.0 <= score <= 1.0:
            raise ValueError(f"similarity {score!r} for posting {posting_id} is outside [-1, 1]")
    rows = [
        {
            "posting_id": posting_id,
            "similarity": score,
            "embedding_model": model,
            "resume_version": resume_version,
        }
        for posting_id, score in scores.items()
    ]
    for start in range(0, len(rows), UPSERT_BATCH):
        insert = pg_insert(matches).values(rows[start : start + UPSERT_BATCH])
        await session.execute(
            insert.on_conflict_do_update(
                index_elements=[matches.c.posting_id],
                set_={
                    "similarity": insert.excluded.similarity,
                    "embedding_model": insert.excluded.embedding_model,
                    "resume_version": insert.excluded.resume_version,
                },
            )
        )
    return len(rows)
