from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from offerradar_agents.config import Settings
from offerradar_agents.db.tables import agent_runs, llm_calls
from offerradar_agents.llm.budget import start_run
from offerradar_agents.llm.client import LLMClient
from offerradar_agents.llm.errors import (
    BudgetExhaustedError,
    LLMError,
    LLMResponseError,
    RetryableLLMError,
    ToolNotAllowedError,
)
from offerradar_agents.llm.fake import FakeLLMProvider
from offerradar_agents.llm.provider import LLMRequest, LLMResponse, ToolSpec, Usage
from offerradar_agents.matcher.score_schema import SCORE_TOOL

REQUEST = LLMRequest(
    model="m",
    system="s",
    cached_context="c",
    user="u",
    tool=SCORE_TOOL,
    max_tokens=100,
)
RESPONSE = LLMResponse(tool_input={"score": 1}, usage=Usage(1000, 100))


@pytest.fixture
async def factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE agent_runs, llm_calls RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _client(
    factory: async_sessionmaker[AsyncSession],
    provider: FakeLLMProvider,
    sleeps: Sleeps | None = None,
    **overrides: object,
) -> LLMClient:
    settings = Settings().model_copy(update=overrides)
    return LLMClient(
        factory,
        provider,
        settings,
        allowed_tools=frozenset({SCORE_TOOL.name}),
        sleep=sleeps or Sleeps(),
    )


async def _new_run(factory: async_sessionmaker[AsyncSession], budget: str) -> int:
    async with factory() as session, session.begin():
        return await start_run(session, Decimal(budget))


async def _run_row(factory: async_sessionmaker[AsyncSession], run_id: int) -> dict[str, object]:
    async with factory() as session:
        row = (await session.execute(select(agent_runs).where(agent_runs.c.id == run_id))).one()
    return dict(row._mapping)


async def _call(client: LLMClient, run_id: int) -> LLMResponse:
    return await client.complete(
        run_id=run_id, agent="matcher", prompt_version="v1", request=REQUEST
    )


async def test_budget_cap(factory: async_sessionmaker[AsyncSession]) -> None:
    provider = FakeLLMProvider("anthropic", [RESPONSE, RESPONSE])
    client = _client(factory, provider)
    run_id = await _new_run(factory, "0.0002")

    await _call(client, run_id)
    with pytest.raises(BudgetExhaustedError):
        await _call(client, run_id)

    row = await _run_row(factory, run_id)
    assert len(provider.requests) == 1
    assert row["status"] == "budget_exhausted"
    assert row["finished_at"] is not None
    assert row["spent_usd"] <= row["budget_usd"]  # type: ignore[operator]


async def test_call_cap(factory: async_sessionmaker[AsyncSession]) -> None:
    provider = FakeLLMProvider("anthropic", [RESPONSE] * 3)
    client = _client(factory, provider, max_llm_calls_per_run=2)
    run_id = await _new_run(factory, "5")

    await _call(client, run_id)
    await _call(client, run_id)
    with pytest.raises(BudgetExhaustedError):
        await _call(client, run_id)

    assert len(provider.requests) == 2
    assert (await _run_row(factory, run_id))["status"] == "budget_exhausted"


async def test_retry_cap_releases_reservation(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeLLMProvider("anthropic", [RetryableLLMError("503", status=503)] * 10)
    sleeps = Sleeps()
    client = _client(factory, provider, sleeps, llm_max_retries=3)
    run_id = await _new_run(factory, "5")

    with pytest.raises(RetryableLLMError):
        await _call(client, run_id)

    assert len(provider.requests) == 4
    assert len(sleeps.delays) == 3
    row = await _run_row(factory, run_id)
    assert row["spent_usd"] == 0
    assert row["status"] == "running"


async def test_non_retryable_error_is_not_retried(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeLLMProvider("anthropic", [LLMError("unauthorized", status=401), RESPONSE])
    sleeps = Sleeps()
    client = _client(factory, provider, sleeps)
    run_id = await _new_run(factory, "5")

    with pytest.raises(LLMError):
        await _call(client, run_id)

    assert len(provider.requests) == 1
    assert sleeps.delays == []
    assert (await _run_row(factory, run_id))["spent_usd"] == 0


async def test_call_is_logged_with_cost(factory: async_sessionmaker[AsyncSession]) -> None:
    provider = FakeLLMProvider("anthropic", [RESPONSE])
    client = _client(factory, provider)
    run_id = await _new_run(factory, "5")

    await _call(client, run_id)

    async with factory() as session:
        rows = (await session.execute(select(llm_calls))).all()
    assert len(rows) == 1
    assert rows[0].agent == "matcher"
    assert rows[0].prompt_version == "v1"
    assert rows[0].cost_usd == Decimal("0.000150")
    assert (await _run_row(factory, run_id))["spent_usd"] == Decimal("0.000150")


async def test_disallowed_tool_never_reaches_provider(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = FakeLLMProvider("anthropic", [RESPONSE])
    client = _client(factory, provider)
    run_id = await _new_run(factory, "5")
    bad = LLMRequest(
        model="m",
        system="s",
        cached_context="c",
        user="u",
        tool=ToolSpec(name="send_email", description="d", input_schema={}),
        max_tokens=100,
    )

    with pytest.raises(ToolNotAllowedError):
        await client.complete(run_id=run_id, agent="matcher", prompt_version="v1", request=bad)

    assert provider.requests == []


async def test_billed_unusable_reply_is_logged_at_cost(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    refused = LLMResponseError("refused", usage=Usage(1000, 100))
    client = _client(factory, FakeLLMProvider("anthropic", [refused]))
    run_id = await _new_run(factory, "0.50")

    with pytest.raises(LLMResponseError):
        await _call(client, run_id)

    async with factory() as session:
        cost = await session.scalar(select(llm_calls.c.cost_usd))
    assert cost == Decimal("0.000150")
    assert (await _run_row(factory, run_id))["spent_usd"] == Decimal("0.000150")
