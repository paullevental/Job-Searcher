from __future__ import annotations

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer

from offerradar_agents.config import Settings
from offerradar_agents.db.session import build_engine
from offerradar_agents.db.tables import EMBEDDING_DIM, METADATA, companies, postings

COLUMNS_QUERY = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = :table_name"
)


@pytest.mark.parametrize("table_name", sorted(METADATA.tables))
async def test_declared_columns_match_database(engine: AsyncEngine, table_name: str) -> None:
    declared = {column.name for column in METADATA.tables[table_name].columns}
    async with engine.connect() as conn:
        result = await conn.execute(COLUMNS_QUERY, {"table_name": table_name})
        actual = {row[0] for row in result}

    assert actual, f"table {table_name!r} is declared in Python but absent from the database"

    only_in_python = sorted(declared - actual)
    only_in_postgres = sorted(actual - declared)
    assert not only_in_python and not only_in_postgres, (
        f"table {table_name!r} drifted from its migration: "
        f"declared in Python but not in Postgres: {only_in_python or 'none'}; "
        f"in Postgres but not declared in Python: {only_in_postgres or 'none'}"
    )


async def test_vector_round_trips_through_asyncpg(postgres_container: PostgresContainer) -> None:
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    vector = [(i - EMBEDDING_DIM / 2) / EMBEDDING_DIM for i in range(EMBEDDING_DIM)]
    engine = build_engine(Settings(database_url=url))
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE companies, postings RESTART IDENTITY CASCADE"))
            company_id = (
                await conn.execute(
                    insert(companies).values(name="Acme", normalized_name="acme", source="manual")
                )
            ).inserted_primary_key[0]
            posting_id = (
                await conn.execute(
                    insert(postings).values(
                        company_id=company_id,
                        title="SWE Intern",
                        normalized_title="swe intern",
                        canonical_url="https://acme.example/jobs/1",
                        dedup_key="acme|swe-intern|summer2027",
                        embedding=vector,
                        embedding_model="nomic-embed-text",
                    )
                )
            ).inserted_primary_key[0]

        async with engine.connect() as conn:
            result = await conn.execute(
                select(postings.c.embedding).where(postings.c.id == posting_id)
            )
            stored = result.scalar_one()
    finally:
        await engine.dispose()

    assert stored == pytest.approx(vector, rel=1e-6)
