from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx
import redis.exceptions
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from offerradar_agents.scout import repository
from offerradar_agents.scout.alerts import AlertKind, source_alert
from offerradar_agents.scout.blocklist import BannedDomainError
from offerradar_agents.scout.http import (
    RedirectLimitError,
    RobotsDisallowedError,
    SourceResponseError,
)
from offerradar_agents.scout.models import SourceAdapter

# Every exception the fetch path can raise must be in one tuple or the other.
REFUSAL_ERRORS = (BannedDomainError, RobotsDisallowedError)
SOURCE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    SourceResponseError,
    RedirectLimitError,
    json.JSONDecodeError,
    redis.exceptions.RedisError,
)
# Caught around the transaction: the session needs a rollback afterwards.
WRITE_ERRORS = (SQLAlchemyError, LookupError)


class CursorStore(Protocol):
    @property
    def label(self) -> str: ...

    async def load(self, session: AsyncSession) -> str | None: ...

    async def save(self, session: AsyncSession, etag: str | None) -> None: ...


@dataclass(frozen=True)
class ListCursor:
    source: str
    repo: str

    @property
    def label(self) -> str:
        return self.repo

    async def load(self, session: AsyncSession) -> str | None:
        return await repository.source_cursor_etag(
            session, source=self.source, cursor_key=self.repo
        )

    async def save(self, session: AsyncSession, etag: str | None) -> None:
        await repository.upsert_source_cursor(
            session, source=self.source, cursor_key=self.repo, etag=etag
        )


@dataclass(frozen=True)
class AtsBoardCursor:
    board_id: int
    board_token: str

    @property
    def label(self) -> str:
        return self.board_token

    async def load(self, session: AsyncSession) -> str | None:
        return await repository.ats_board_etag(session, board_id=self.board_id)

    async def save(self, session: AsyncSession, etag: str | None) -> None:
        await repository.touch_ats_board(session, board_id=self.board_id, etag=etag)


@dataclass(frozen=True)
class ScoutTarget:
    adapter: SourceAdapter
    cursor: CursorStore


@dataclass(frozen=True)
class SourceOutcome:
    source: str
    label: str
    postings_seen: int = 0
    not_modified: bool = False
    alert: AlertKind | None = None


async def run_target(
    target: ScoutTarget,
    session: AsyncSession,
    *,
    run_id: str | None = None,
) -> SourceOutcome:
    source = target.adapter.source
    label = target.cursor.label
    try:
        collected = await target.adapter.collect()
    except REFUSAL_ERRORS as exc:
        source_alert(
            AlertKind.REFUSED, source, run_id=run_id, detail=f"{label}: {type(exc).__name__}"
        )
        return SourceOutcome(source, label, alert=AlertKind.REFUSED)
    except SOURCE_ERRORS as exc:
        source_alert(
            AlertKind.FAILED, source, run_id=run_id, detail=f"{label}: {type(exc).__name__}"
        )
        return SourceOutcome(source, label, alert=AlertKind.FAILED)

    if collected.not_modified:
        await target.cursor.save(session, collected.etag)
        return SourceOutcome(source, label, not_modified=True)

    if not collected.postings:
        source_alert(AlertKind.EMPTY, source, run_id=run_id, detail=f"{label}: 200 with no rows")
        await target.cursor.save(session, collected.etag)
        return SourceOutcome(source, label, alert=AlertKind.EMPTY)

    for posting in collected.postings:
        await repository.upsert_posting(session, posting)
    await target.cursor.save(session, collected.etag)
    return SourceOutcome(source, label, postings_seen=len(collected.postings))


async def run_scout(
    targets: list[ScoutTarget],
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str | None = None,
) -> list[SourceOutcome]:
    outcomes: list[SourceOutcome] = []
    for target in targets:
        source = target.adapter.source
        label = target.cursor.label
        try:
            async with session_factory() as session, session.begin():
                outcome = await run_target(target, session, run_id=run_id)
        except WRITE_ERRORS as exc:
            source_alert(
                AlertKind.FAILED, source, run_id=run_id, detail=f"{label}: {type(exc).__name__}"
            )
            outcome = SourceOutcome(source, label, alert=AlertKind.FAILED)
        outcomes.append(outcome)
    return outcomes
