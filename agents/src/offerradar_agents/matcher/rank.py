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


# Paul writes this: one posting's cosine similarities to every resume chunk -> one score in [-1, 1].
def score_posting(chunk_similarities: Sequence[float]) -> float:
    raise NotImplementedError("the ranking formula is Paul's (AGENTS.md §11 M4)")


# Paul writes this: scores every open posting, upserts matches.similarity, returns the top k.
async def rank_postings(
    session: AsyncSession, *, resume_version: str, model: str, k: int = 20
) -> list[RankedPosting]:
    raise NotImplementedError("rank_postings is Paul's (AGENTS.md §11 M4)")


# Paul writes this: the k resume chunks nearest to `text`, most similar first.
async def retrieve_resume_context(
    session: AsyncSession,
    provider: EmbeddingProvider,
    text: str,
    *,
    resume_version: str,
    model: str,
    k: int = 5,
) -> list[RetrievedChunk]:
    raise NotImplementedError("retrieve_resume_context is Paul's (AGENTS.md §11 M4)")
