from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from offerradar_agents.config import Settings


def build_engine(settings: Settings, *, connect_timeout: float | None = None) -> AsyncEngine:
    # Bounds a readiness probe: without it /ready hangs instead of 503-ing.
    connect_args = {} if connect_timeout is None else {"timeout": connect_timeout}
    return create_async_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: a blocking refresh raises under asyncio.
    return async_sessionmaker(engine, expire_on_commit=False)
