from __future__ import annotations

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.scout import repository
from offerradar_agents.scout.greenhouse import GreenhouseAdapter
from offerradar_agents.scout.http import ScoutHttpClient
from offerradar_agents.scout.lever import LeverAdapter
from offerradar_agents.scout.models import SourceAdapter
from offerradar_agents.scout.ouckah import OuckahAdapter
from offerradar_agents.scout.retry import ExponentialBackoff
from offerradar_agents.scout.runner import (
    AtsBoardCursor,
    ListCursor,
    ScoutTarget,
    SourceOutcome,
    run_scout,
)
from offerradar_agents.scout.simplify import SimplifyAdapter

ATS_PROVIDERS = ("greenhouse", "lever")


def build_retry_policy(settings: Settings) -> ExponentialBackoff:
    return ExponentialBackoff(
        base_seconds=settings.scout_retry_base_seconds,
        max_seconds=settings.scout_retry_max_seconds,
        max_retries=settings.scout_max_retries,
    )


def build_http_client(
    *,
    settings: Settings | None = None,
    redis_client: redis.Redis | None = None,
) -> ScoutHttpClient:
    resolved = settings if settings is not None else get_settings()
    return ScoutHttpClient(
        settings=resolved,
        redis_client=redis_client,
        policy=build_retry_policy(resolved),
    )


async def build_targets(
    session: AsyncSession,
    *,
    http: ScoutHttpClient,
    settings: Settings | None = None,
) -> list[ScoutTarget]:
    resolved = settings if settings is not None else get_settings()
    targets: list[ScoutTarget] = []

    for repo in resolved.simplify_repos:
        cursor = ListCursor(source=SimplifyAdapter.source, repo=repo)
        etag = await cursor.load(session)
        targets.append(
            ScoutTarget(
                adapter=SimplifyAdapter(http, repo=repo, etag=etag, settings=resolved),
                cursor=cursor,
            )
        )

    for repo in resolved.ouckah_repos:
        cursor = ListCursor(source=OuckahAdapter.source, repo=repo)
        etag = await cursor.load(session)
        targets.append(
            ScoutTarget(
                adapter=OuckahAdapter(http, repo=repo, etag=etag, settings=resolved),
                cursor=cursor,
            )
        )

    for board in await repository.list_ats_boards(session, providers=ATS_PROVIDERS):
        board_cursor = AtsBoardCursor(
            board_id=board.id,
            board_token=board.board_token,
            board_company_id=board.company_id,
        )
        board_etag = await board_cursor.load(session)
        adapter: SourceAdapter
        if board.provider == GreenhouseAdapter.source:
            adapter = GreenhouseAdapter(http, board_token=board.board_token, etag=board_etag)
        else:
            adapter = LeverAdapter(http, company=board.board_token, etag=board_etag)
        targets.append(ScoutTarget(adapter=adapter, cursor=board_cursor))

    return targets


async def collect_all(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings | None = None,
    redis_client: redis.Redis | None = None,
    run_id: str | None = None,
) -> list[SourceOutcome]:
    resolved = settings if settings is not None else get_settings()
    async with build_http_client(settings=resolved, redis_client=redis_client) as http:
        async with session_factory() as session:
            targets = await build_targets(session, http=http, settings=resolved)
        return await run_scout(targets, session_factory, run_id=run_id)
