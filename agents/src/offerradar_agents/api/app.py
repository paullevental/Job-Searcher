"""FastAPI application for the agents service.

Two probes, deliberately different:

  GET /health  liveness  -- this process is up. No dependencies touched, so a
                            Postgres outage never makes Compose restart a
                            healthy process.
  GET /ready   readiness -- Postgres and Redis are actually reachable. This is
                            what `depends_on: condition: service_healthy` should
                            gate on, so "all services healthy" means something.

Run endpoints (POST /runs, POST /runs/{id}/resume) arrive in M6.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from offerradar_agents import __version__
from offerradar_agents.config import Settings, get_settings

logger = logging.getLogger(__name__)

SERVICE: Final = "agents"
PROBE_TIMEOUT_SECONDS: Final = 3.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app.state.engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": PROBE_TIMEOUT_SECONDS},
    )
    app.state.redis = redis.from_url(
        settings.redis_url,
        socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        socket_timeout=PROBE_TIMEOUT_SECONDS,
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


async def _check_postgres(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return "ok"


async def _check_redis(client: redis.Redis) -> str:
    await client.ping()
    return "ok"


def create_app() -> FastAPI:
    app = FastAPI(
        title="OfferRadar agents",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE, "version": __version__}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        checks: dict[str, str] = {}
        probes = (
            ("postgres", _check_postgres(app.state.engine)),
            ("redis", _check_redis(app.state.redis)),
        )
        for name, probe in probes:
            try:
                checks[name] = await probe
            except Exception as exc:  # noqa: BLE001 - report, never crash the probe
                checks[name] = f"error: {type(exc).__name__}"
                logger.warning("readiness probe failed", extra={"dependency": name})

        ok = all(value == "ok" for value in checks.values())
        body: dict[str, Any] = {
            "status": "ready" if ok else "not ready",
            "service": SERVICE,
            "checks": checks,
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    return app


app = create_app()
