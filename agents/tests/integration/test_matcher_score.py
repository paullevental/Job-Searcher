from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from offerradar_agents.config import Settings
from offerradar_agents.db.tables import companies, match_analyses, matches, postings
from offerradar_agents.llm.budget import start_run
from offerradar_agents.llm.client import LLMClient
from offerradar_agents.llm.errors import LLMResponseError, RetryableLLMError
from offerradar_agents.llm.fake import FakeLLMProvider
from offerradar_agents.llm.provider import LLMResponse, Usage
from offerradar_agents.matcher.score import (
    ScoringSetup,
    posting_text,
    ranked_rows,
    score_for_eval,
    score_selected,
    text_hash,
)
from offerradar_agents.matcher.score_schema import SCORE_TOOL

SETUP = ScoringSetup(
    provider="anthropic",
    model="claude-haiku-5-5",
    prompt_version="score_v1",
    resume_markdown="# Resume\n\n- Built a Python service.",
    max_tokens=400,
)


def _ok(score: int = 70) -> LLMResponse:
    return LLMResponse(
        tool_input={
            "score": score,
            "matched_skills": ["Python"],
            "missing_skills": [],
            "why_fit": "Fits.",
        },
        usage=Usage(1000, 100),
    )


@pytest.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE match_analyses, matches, postings, companies, agent_runs, llm_calls "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)


async def _seed(
    sessions: async_sessionmaker[AsyncSession], selected: set[str], slugs: list[str]
) -> dict[str, int]:
    ids: dict[str, int] = {}
    async with sessions() as session, session.begin():
        company = await session.execute(
            insert(companies).values(name="Acme", normalized_name="acme", source="manual")
        )
        company_id = int(company.inserted_primary_key[0])
        for slug in slugs:
            result = await session.execute(
                insert(postings).values(
                    company_id=company_id,
                    title=f"Intern {slug}",
                    normalized_title=slug,
                    canonical_url=f"https://acme.example/{slug}",
                    dedup_key=slug,
                )
            )
            ids[slug] = int(result.inserted_primary_key[0])
            await session.execute(
                insert(matches).values(
                    posting_id=ids[slug],
                    similarity=0.5,
                    embedding_model="nomic-embed-text",
                    resume_version="v1",
                    selected_for_analysis=slug in selected,
                )
            )
    return ids


async def _run(sessions: async_sessionmaker[AsyncSession], budget: str = "0.50") -> int:
    async with sessions() as session, session.begin():
        return await start_run(session, Decimal(budget))


def _client(
    sessions: async_sessionmaker[AsyncSession], provider: FakeLLMProvider, **overrides: object
) -> LLMClient:
    return LLMClient(
        sessions,
        provider,
        Settings().model_copy(update=overrides),
        allowed_tools=frozenset({SCORE_TOOL.name}),
        sleep=_no_sleep,
    )


async def _no_sleep(_: float) -> None:
    return None


async def _hashes(sessions: async_sessionmaker[AsyncSession]) -> dict[int, str]:
    async with sessions() as session:
        return {row.id: text_hash(posting_text(row)) for row in await ranked_rows(session)}


async def test_no_unselected_posting_reaches_stage_two(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed(sessions, {"b", "d"}, ["a", "b", "c", "d", "e"])
    provider = FakeLLMProvider(name="anthropic", responses=[_ok(), _ok()])

    summary = await score_selected(
        sessions, _client(sessions, provider), run_id=await _run(sessions), setup=SETUP
    )

    assert summary.scored == 2
    assert len(provider.requests) == 2
    sent = "\n".join(request.user for request in provider.requests)
    for slug in ("a", "c", "e"):
        assert f"Intern {slug}" not in sent
    async with sessions() as session:
        scored = set((await session.execute(select(match_analyses.c.posting_id))).scalars())
    assert scored == {ids["b"], ids["d"]}


async def test_second_run_is_memoized(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _seed(sessions, {"a"}, ["a"])
    provider = FakeLLMProvider(name="anthropic", responses=[_ok()])
    client = _client(sessions, provider)

    await score_selected(sessions, client, run_id=await _run(sessions), setup=SETUP)
    again = await score_selected(sessions, client, run_id=await _run(sessions), setup=SETUP)

    assert len(provider.requests) == 1
    assert again.already_scored == 1


async def test_other_provider_is_not_memoized(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _seed(sessions, {"a"}, ["a"])
    first = FakeLLMProvider(name="anthropic", responses=[_ok()])
    second = FakeLLMProvider(name="ollama", responses=[_ok(40)])

    await score_selected(
        sessions, _client(sessions, first), run_id=await _run(sessions), setup=SETUP
    )
    ollama_setup = ScoringSetup(**{**SETUP.__dict__, "provider": "ollama", "model": "local"})
    await score_selected(
        sessions, _client(sessions, second), run_id=await _run(sessions), setup=ollama_setup
    )

    assert len(second.requests) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(match_analyses)) == 2


async def test_invalid_output_is_counted_not_stored(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(sessions, {"a", "b"}, ["a", "b"])
    bad = LLMResponse(tool_input={"score": 101}, usage=Usage(10, 10))
    provider = FakeLLMProvider(name="anthropic", responses=[bad, LLMResponseError("no tool")])

    summary = await score_selected(
        sessions, _client(sessions, provider), run_id=await _run(sessions), setup=SETUP
    )

    assert (summary.scored, summary.failed) == (0, 2)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(match_analyses)) == 0


async def test_budget_exhaustion_stops_scoring(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _seed(sessions, {"a", "b", "c"}, ["a", "b", "c"])
    provider = FakeLLMProvider(name="anthropic", responses=[_ok(), _ok(), _ok()])

    summary = await score_selected(
        sessions,
        _client(sessions, provider, max_llm_calls_per_run=1),
        run_id=await _run(sessions),
        setup=SETUP,
    )

    assert len(provider.requests) == 1
    assert (summary.scored, summary.remaining) == (1, 2)


async def test_retry_cap_stops_the_run(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _seed(sessions, {"a", "b"}, ["a", "b"])
    provider = FakeLLMProvider(
        name="anthropic", responses=[RetryableLLMError("overloaded", status=529)] * 4
    )

    with pytest.raises(RetryableLLMError):
        await score_selected(
            sessions,
            _client(sessions, provider, llm_max_retries=3),
            run_id=await _run(sessions),
            setup=SETUP,
        )

    assert len(provider.requests) == 4


async def test_eval_scores_only_selected_postings(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await _seed(sessions, {"a"}, ["a", "b"])
    provider = FakeLLMProvider(name="anthropic", responses=[_ok(80)])

    _, scores = await score_for_eval(
        sessions,
        _client(sessions, provider),
        await _hashes(sessions),
        run_id=await _run(sessions),
        setup=SETUP,
    )

    assert scores == {ids["a"]: 80}
    assert len(provider.requests) == 1


async def test_eval_refuses_changed_text(sessions: async_sessionmaker[AsyncSession]) -> None:
    ids = await _seed(sessions, {"a"}, ["a"])
    provider = FakeLLMProvider(name="anthropic", responses=[_ok()])

    summary, scores = await score_for_eval(
        sessions,
        _client(sessions, provider),
        {ids["a"]: "hash-of-the-text-that-was-rated"},
        run_id=await _run(sessions),
        setup=SETUP,
    )

    assert scores == {ids["a"]: None}
    assert summary.failed == 1
    assert provider.requests == []


async def test_eval_reports_memoized_scores(sessions: async_sessionmaker[AsyncSession]) -> None:
    ids = await _seed(sessions, {"a"}, ["a"])
    provider = FakeLLMProvider(name="anthropic", responses=[_ok(80)])
    client = _client(sessions, provider)

    await score_selected(sessions, client, run_id=await _run(sessions), setup=SETUP)
    _, scores = await score_for_eval(
        sessions, client, await _hashes(sessions), run_id=await _run(sessions), setup=SETUP
    )

    assert scores == {ids["a"]: 80}
    assert len(provider.requests) == 1
