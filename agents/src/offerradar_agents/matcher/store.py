from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db.tables import resume_chunks
from offerradar_agents.embeddings.provider import EmbeddingProvider
from offerradar_agents.matcher.resume import ResumeChunk, chunk_resume, resume_version


@dataclass(frozen=True)
class StoreResult:
    version: str
    chunk_count: int
    skipped: bool


def _validate(chunks: list[ResumeChunk]) -> None:
    if not chunks:
        raise ValueError("chunk_resume returned no chunks")
    ordinals = [chunk.ordinal for chunk in chunks]
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("chunk_resume returned duplicate ordinals")


# Caller owns the transaction; delete and insert must commit together.
async def store_resume(
    session: AsyncSession,
    provider: EmbeddingProvider,
    *,
    markdown: str,
    model: str,
) -> StoreResult:
    version = resume_version(markdown)
    stored = (
        (
            await session.execute(
                select(resume_chunks.c.embedding_model).where(resume_chunks.c.version == version)
            )
        )
        .scalars()
        .all()
    )
    if stored and all(m == model for m in stored):
        return StoreResult(version=version, chunk_count=len(stored), skipped=True)

    chunks = chunk_resume(markdown)
    _validate(chunks)
    vectors = await provider.embed([chunk.text for chunk in chunks])

    await session.execute(delete(resume_chunks).where(resume_chunks.c.version == version))
    await session.execute(
        insert(resume_chunks),
        [
            {
                "version": version,
                "kind": chunk.kind,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "embedding": vector,
                "embedding_model": model,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
    )
    return StoreResult(version=version, chunk_count=len(chunks), skipped=False)
