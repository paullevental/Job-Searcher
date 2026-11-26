from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from offerradar_agents.db.tables import companies, match_analyses, matches, postings
from offerradar_agents.llm.client import LLMClient
from offerradar_agents.llm.errors import BudgetExhaustedError, LLMResponseError
from offerradar_agents.llm.provider import LLMRequest, ProviderName
from offerradar_agents.matcher.resume import resume_version
from offerradar_agents.matcher.score_schema import SCORE_TOOL, ScoreResult

log = logging.getLogger(__name__)

PROMPTS_DIR: Final = Path(__file__).resolve().parents[3] / "prompts"
_TAG: Final = re.compile(r"<[^>]+>")
_SPACE: Final = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class ScoreSummary:
    scored: int
    already_scored: int
    failed: int
    remaining: int


@dataclass(frozen=True)
class ScoringSetup:
    provider: ProviderName
    model: str
    prompt_version: str
    resume_markdown: str
    max_tokens: int


def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


# Greenhouse descriptions arrive as escaped HTML.
def _plain(text: str) -> str:
    return _SPACE.sub(" ", _TAG.sub(" ", unescape(unescape(text)))).strip()


def posting_text(row: Any) -> str:
    lines = [
        f"Title: {row.title}",
        f"Company: {row.company}",
        f"Location: {row.location or 'unknown'}{' (remote)' if row.remote else ''}",
        f"Term: {row.term or 'unknown'}",
    ]
    if row.description:
        lines += ["", "Description:", _plain(row.description)]
    return "\n".join(lines)


# The memo key hashes exactly what the model saw, so a title edit re-scores too.
def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _posting_columns() -> Any:
    return select(
        postings.c.id,
        postings.c.title,
        companies.c.name.label("company"),
        postings.c.location,
        postings.c.remote,
        postings.c.term,
        postings.c.description,
    ).join(companies, companies.c.id == postings.c.company_id)


async def _selected_rows(session: AsyncSession) -> list[Any]:
    statement = (
        _posting_columns()
        .join(matches, matches.c.posting_id == postings.c.id)
        .where(matches.c.selected_for_analysis.is_(True), postings.c.status == "open")
        .order_by(postings.c.id)
    )
    return list(await session.execute(statement))


async def ranked_rows(session: AsyncSession) -> list[Any]:
    statement = (
        _posting_columns()
        .join(matches, matches.c.posting_id == postings.c.id)
        .where(postings.c.status == "open")
        .order_by(matches.c.similarity.desc(), postings.c.id)
    )
    return list(await session.execute(statement))


async def _selected_rows_by_id(session: AsyncSession, posting_ids: Sequence[int]) -> list[Any]:
    statement = (
        _posting_columns()
        .join(matches, matches.c.posting_id == postings.c.id)
        .where(matches.c.selected_for_analysis.is_(True), postings.c.id.in_(posting_ids))
        .order_by(postings.c.id)
    )
    return list(await session.execute(statement))


async def _stored_score(
    session: AsyncSession, posting_id: int, digest: str, setup: ScoringSetup, resume: str
) -> int | None:
    statement = select(match_analyses.c.score).where(
        match_analyses.c.posting_id == posting_id,
        match_analyses.c.description_hash == digest,
        match_analyses.c.prompt_version == setup.prompt_version,
        match_analyses.c.resume_version == resume,
        match_analyses.c.model == setup.model,
        match_analyses.c.provider == setup.provider,
    )
    stored = await session.scalar(statement)
    return None if stored is None else int(stored)


async def _score_rows(
    sessions: async_sessionmaker[AsyncSession],
    client: LLMClient,
    rows: Sequence[Any],
    *,
    run_id: int,
    setup: ScoringSetup,
    expected_hashes: dict[int, str] | None = None,
) -> tuple[ScoreSummary, dict[int, int | None]]:
    system = load_prompt(setup.prompt_version)
    resume = resume_version(setup.resume_markdown)
    scores: dict[int, int | None] = {}
    scored = already = failed = done = 0
    for row in rows:
        text = posting_text(row)
        digest = text_hash(text)
        if expected_hashes is not None and expected_hashes.get(row.id) != digest:
            log.warning("posting %s: text changed since it was rated; not scored", row.id)
            scores[row.id] = None
            failed += 1
            done += 1
            continue
        async with sessions() as session:
            stored = await _stored_score(session, row.id, digest, setup, resume)
        if stored is not None:
            scores[row.id] = stored
            already += 1
            done += 1
            continue
        request = LLMRequest(
            model=setup.model,
            system=system,
            cached_context=setup.resume_markdown,
            user=text,
            tool=SCORE_TOOL,
            max_tokens=setup.max_tokens,
        )
        try:
            response = await client.complete(
                run_id=run_id, agent="matcher", prompt_version=setup.prompt_version, request=request
            )
            result = ScoreResult.model_validate(response.tool_input)
        except BudgetExhaustedError:
            log.warning("run %s: budget or call cap reached", run_id)
            break
        except (LLMResponseError, ValidationError) as exc:
            log.warning("posting %s: no valid score: %s", row.id, type(exc).__name__)
            scores[row.id] = None
            failed += 1
            done += 1
            continue
        async with sessions() as session, session.begin():
            await session.execute(
                pg_insert(match_analyses)
                .values(
                    posting_id=row.id,
                    description_hash=digest,
                    prompt_version=setup.prompt_version,
                    resume_version=resume,
                    model=setup.model,
                    provider=setup.provider,
                    score=result.score,
                    matched_skills=result.matched_skills,
                    missing_skills=result.missing_skills,
                    why_fit=result.why_fit,
                )
                .on_conflict_do_nothing(constraint="uq_match_analyses_scoring_inputs")
            )
        scores[row.id] = result.score
        scored += 1
        done += 1
    summary = ScoreSummary(
        scored=scored, already_scored=already, failed=failed, remaining=len(rows) - done
    )
    return summary, scores


# Hard rule 11: the nightly path reads only postings selected at Gate 1.
async def score_selected(
    sessions: async_sessionmaker[AsyncSession],
    client: LLMClient,
    *,
    run_id: int,
    setup: ScoringSetup,
) -> ScoreSummary:
    async with sessions() as session:
        rows = await _selected_rows(session)
    summary, _ = await _score_rows(sessions, client, rows, run_id=run_id, setup=setup)
    return summary


# Eval postings still pass Gate 1: the caller selects them first, and a changed text is refused.
async def score_for_eval(
    sessions: async_sessionmaker[AsyncSession],
    client: LLMClient,
    expected_hashes: dict[int, str],
    *,
    run_id: int,
    setup: ScoringSetup,
) -> tuple[ScoreSummary, dict[int, int | None]]:
    async with sessions() as session:
        rows = await _selected_rows_by_id(session, sorted(expected_hashes))
    return await _score_rows(
        sessions, client, rows, run_id=run_id, setup=setup, expected_hashes=expected_hashes
    )
