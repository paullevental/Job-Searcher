from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from offerradar_agents.db.tables import METADATA

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
