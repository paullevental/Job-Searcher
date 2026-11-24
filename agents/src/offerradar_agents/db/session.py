from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from offerradar_agents.config import Settings


# Identity text codec: pgvector.sqlalchemy.Vector already converts to/from vector text syntax.
async def _register_vector_codec(connection: Any) -> None:
    await connection.set_type_codec(
        "vector",
        schema="public",
        encoder=lambda value: value,
        decoder=lambda value: value,
        format="text",
    )


def build_engine(settings: Settings, *, connect_timeout: float | None = None) -> AsyncEngine:
    # Bounds a readiness probe: without it /ready hangs instead of 503-ing.
    connect_args = {} if connect_timeout is None else {"timeout": connect_timeout}
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, connect_args=connect_args
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        dbapi_connection.run_async(_register_vector_codec)

    return engine


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: a blocking refresh raises under asyncio.
    return async_sessionmaker(engine, expire_on_commit=False)
