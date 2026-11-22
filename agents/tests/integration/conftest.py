from __future__ import annotations

import contextlib
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Set before the import. Ryuk's fixed name 409s on a retried start; the
# fixture stops its own container, so a crash can leave one for `docker rm`.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

from testcontainers.community.postgres import PostgresContainer

MIGRATIONS = Path(__file__).resolve().parents[3] / "workflow/src/main/resources/db/migration"

# Named rather than CASCADE-implied, so no unrelated table empties silently.
SCOUT_TABLES = (
    "companies",
    "ats_boards",
    "postings",
    "posting_sightings",
    "source_cursors",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # This hook sees every collected item, not only those below this file.
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.path).parents:
            item.add_marker(pytest.mark.integration)


def _wait_for_postgres(container: PostgresContainer, attempts: int = 40) -> str:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            url = container.get_connection_url()
            with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")):
                return url
        except Exception as exc:  # noqa: BLE001 - any failure here means "not ready yet"
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"Postgres container never became reachable: {last}")


def _apply_migrations(url: str) -> None:
    files = sorted(MIGRATIONS.glob("V*.sql"))
    if not files:
        raise RuntimeError(f"no migrations found under {MIGRATIONS}")
    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in files:
            conn.execute(path.read_text())


def _start_postgres(attempts: int = 3) -> tuple[PostgresContainer, str]:
    last: Exception | None = None
    for _ in range(attempts):
        container = PostgresContainer("pgvector/pgvector:pg16", driver="psycopg")
        try:
            container.start()
            return container, _wait_for_postgres(container)
        except Exception as exc:  # noqa: BLE001 - retry whatever stopped it coming up
            last = exc
            with contextlib.suppress(Exception):
                container.stop()
            time.sleep(2.0)
    raise RuntimeError(f"Postgres container would not start after {attempts} attempts: {last}")


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    container, url = _start_postgres()
    try:
        _apply_migrations(url)
        yield container
    finally:
        container.stop()


@pytest.fixture
async def engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    )
    created = create_async_engine(url)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(SCOUT_TABLES)} RESTART IDENTITY CASCADE"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
