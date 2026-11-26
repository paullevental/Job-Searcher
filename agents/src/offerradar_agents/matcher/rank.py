from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.embeddings.provider import EmbeddingProvider


@dataclass(frozen=True)
class RankedPosting:
    posting_id: int
    similarity: float


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    kind: str
    ordinal: int
    text: str
    similarity: float


TOP_CHUNKS = 3


def score_posting(chunk_similarities: Sequence[float]) -> float:
    if not chunk_similarities:
        raise ValueError("a posting needs at least one chunk similarity")
    top = sorted(chunk_similarities, reverse=True)[:TOP_CHUNKS]
    return sum(top) / len(top)


# queries.py imports RetrievedChunk from here, so its helpers are imported inside the functions.
async def rank_postings(
    session: AsyncSession, *, resume_version: str, model: str, k: int = 20
) -> list[RankedPosting]:
    from offerradar_agents.matcher.queries import chunk_similarities, upsert_matches

    similarities = await chunk_similarities(session, resume_version=resume_version, model=model)
    scores = {pid: score_posting(values) for pid, values in similarities.items()}
    await upsert_matches(session, scores, resume_version=resume_version, model=model)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [RankedPosting(pid, score) for pid, score in ranked[:k]]


async def retrieve_resume_context(
    session: AsyncSession,
    provider: EmbeddingProvider,
    text: str,
    *,
    resume_version: str,
    model: str,
    k: int = 5,
) -> list[RetrievedChunk]:
    from offerradar_agents.matcher.queries import nearest_chunks

    [vector] = await provider.embed([text])
    return await nearest_chunks(session, vector, resume_version=resume_version, model=model, k=k)
