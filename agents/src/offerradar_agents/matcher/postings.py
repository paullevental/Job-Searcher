from __future__ import annotations

from sqlalchemy import bindparam, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from offerradar_agents.db.tables import companies, postings
from offerradar_agents.embeddings.errors import EMBEDDING_ERRORS, EmbeddingResponseError
from offerradar_agents.embeddings.provider import EmbeddingProvider
from offerradar_agents.scout.alerts import AlertKind, source_alert

DEFAULT_BATCH_SIZE = 64


def posting_embedding_text(
    *, title: str, company: str, location: str | None, term: str | None
) -> str:
    return " | ".join(part for part in (title, company, location, term) if part)


async def _embed_batch(
    session: AsyncSession, provider: EmbeddingProvider, *, model: str, batch_size: int
) -> int:
    rows = (
        await session.execute(
            select(
                postings.c.id,
                postings.c.title,
                postings.c.location,
                postings.c.term,
                companies.c.name,
            )
            .join(companies, companies.c.id == postings.c.company_id)
            .where(
                postings.c.status == "open",
                postings.c.embedding.is_(None) | postings.c.embedding_model.is_distinct_from(model),
            )
            .order_by(postings.c.id)
            .limit(batch_size)
        )
    ).all()
    if not rows:
        return 0
    vectors = await provider.embed(
        [
            posting_embedding_text(title=r.title, company=r.name, location=r.location, term=r.term)
            for r in rows
        ]
    )
    if len(vectors) != len(rows):
        raise EmbeddingResponseError(f"{len(vectors)} embeddings for {len(rows)} postings")
    await session.execute(
        update(postings)
        .where(postings.c.id == bindparam("b_id"))
        .values(embedding=bindparam("b_embedding"), embedding_model=model),
        [
            {"b_id": row.id, "b_embedding": vector}
            for row, vector in zip(rows, vectors, strict=True)
        ],
    )
    return len(rows)


async def embed_pending_postings(
    session_factory: async_sessionmaker[AsyncSession],
    provider: EmbeddingProvider,
    *,
    model: str,
    run_id: str | None = None,
    batch_size: int | None = None,
) -> int:
    size = DEFAULT_BATCH_SIZE if batch_size is None else batch_size
    if size < 1:
        raise ValueError("batch_size must be >= 1")
    total = 0
    try:
        while True:
            async with session_factory() as session, session.begin():
                done = await _embed_batch(session, provider, model=model, batch_size=size)
            if done == 0:
                return total
            total += done
    except (*EMBEDDING_ERRORS, SQLAlchemyError, OSError) as exc:
        source_alert(AlertKind.FAILED, "embeddings", run_id=run_id, detail=repr(exc))
        return total
