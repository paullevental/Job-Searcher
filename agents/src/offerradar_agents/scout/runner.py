from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx
import redis.exceptions
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from offerradar_agents.dedup.candidates import resolve_duplicates
from offerradar_agents.dedup.staleness import (
    apply_poll_result,
    mark_locked_stale,
    sweep_stale_by_age,
)
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

    # None for a list spanning many companies; set for a single company's board.
    # The staleness sweep scopes to it, or polling one board counts every other
    # board of the same provider as a miss.
    @property
    def company_id(self) -> int | None: ...

    async def load(self, session: AsyncSession) -> str | None: ...

    async def save(self, session: AsyncSession, etag: str | None) -> None: ...


@dataclass(frozen=True)
class ListCursor:
    source: str
    repo: str

    @property
    def label(self) -> str:
        return self.repo

    @property
    def company_id(self) -> int | None:
        return None

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
    board_company_id: int | None = None

    @property
    def label(self) -> str:
        return self.board_token

    @property
    def company_id(self) -> int | None:
        return self.board_company_id

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

    locked_keys = [posting.dedup_key for posting in collected.locked]

    if not collected.postings:
        # Only empty when nothing at all came back. A list whose rows have all gone
        # 🔒 is a source reporting closures, not a source that failed.
        if not locked_keys:
            source_alert(
                AlertKind.EMPTY, source, run_id=run_id, detail=f"{label}: 200 with no rows"
            )
            await target.cursor.save(session, collected.etag)
            return SourceOutcome(source, label, alert=AlertKind.EMPTY)
        await mark_locked_stale(session, dedup_keys=locked_keys)
        await target.cursor.save(session, collected.etag)
        return SourceOutcome(source, label)

    # Reached only past the 304 and empty returns above, which is what makes this
    # a real observation: apply_poll_result refuses an empty seen-set.
    seen_ids = [await repository.upsert_posting(session, posting) for posting in collected.postings]
    # Before the miss sweep: a 🔒 row is an observation, and retiring it first
    # keeps it out of the open-only candidate set the sweep counts misses over.
    await mark_locked_stale(session, dedup_keys=locked_keys)
    await apply_poll_result(
        session,
        source=source,
        seen_posting_ids=seen_ids,
        company_id=target.cursor.company_id,
    )
    # After the sweep: a merge sets status='merged', which would drop the row out
    # of the sweep's open-only candidate set.
    for posting_id in seen_ids:
        await resolve_duplicates(session, posting_id=posting_id)
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

    # Age-out is not per-source: a posting goes quiet across every source at once.
    # Its own transaction, so one source's write failure cannot skip it.
    try:
        async with session_factory() as session, session.begin():
            await sweep_stale_by_age(session)
    except WRITE_ERRORS as exc:
        source_alert(
            AlertKind.FAILED, "staleness", run_id=run_id, detail=f"age sweep: {type(exc).__name__}"
        )
    return outcomes
