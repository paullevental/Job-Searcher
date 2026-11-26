from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from offerradar_agents.config import Settings
from offerradar_agents.db.tables import llm_calls
from offerradar_agents.llm.budget import mark_budget_exhausted, reserve, settle
from offerradar_agents.llm.cost import FREE, Prices, call_cost, prices_from_settings, reservation
from offerradar_agents.llm.errors import (
    BudgetExhaustedError,
    LLMResponseError,
    RetryableLLMError,
    ToolNotAllowedError,
)
from offerradar_agents.llm.provider import LLMProvider, LLMRequest, LLMResponse, Usage
from offerradar_agents.scout.retry import ExponentialBackoff


class LLMClient:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: LLMProvider,
        settings: Settings,
        *,
        allowed_tools: frozenset[str],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._settings = settings
        # Hard rule 1: only read-only tools the caller names may reach a model.
        self._allowed_tools = allowed_tools
        self._sleep = sleep
        self._backoff = ExponentialBackoff(
            base_seconds=settings.llm_retry_base_seconds,
            max_seconds=settings.llm_retry_max_seconds,
            max_retries=settings.llm_max_retries,
            rng=rng or random.Random(),  # noqa: S311
        )

    async def complete(
        self, *, run_id: int, agent: str, prompt_version: str, request: LLMRequest
    ) -> LLMResponse:
        if request.tool.name not in self._allowed_tools:
            raise ToolNotAllowedError(f"tool {request.tool.name!r} is not allowed")

        prices = FREE if self._provider.name == "ollama" else prices_from_settings(self._settings)
        reserved = reservation(request, prices)

        async with self._sessions() as session, session.begin():
            granted = await reserve(
                session, run_id, reserved, max_calls=self._settings.max_llm_calls_per_run
            )
            if not granted:
                await mark_budget_exhausted(session, run_id)
        if not granted:
            raise BudgetExhaustedError(f"run {run_id} has no budget or calls left")

        started = time.monotonic()
        try:
            response = await self._call_with_retries(request)
        except LLMResponseError as exc:
            await self._record(
                run_id, agent, prompt_version, request, exc.usage, started, prices, reserved
            )
            raise
        except Exception:
            await self._record(
                run_id, agent, prompt_version, request, None, started, prices, reserved
            )
            raise
        await self._record(
            run_id, agent, prompt_version, request, response.usage, started, prices, reserved
        )
        return response

    # A reply that was billed but unusable still lands in llm_calls at its real cost.
    async def _record(
        self,
        run_id: int,
        agent: str,
        prompt_version: str,
        request: LLMRequest,
        usage: Usage | None,
        started: float,
        prices: Prices,
        reserved: Decimal,
    ) -> None:
        cost = Decimal(0) if usage is None else call_cost(usage, prices)
        async with self._sessions() as session, session.begin():
            if usage is not None:
                await session.execute(
                    insert(llm_calls).values(
                        run_id=run_id,
                        agent=agent,
                        model=request.model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_read_tokens=usage.cache_read_tokens,
                        cost_usd=cost,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        prompt_version=prompt_version,
                    )
                )
            await settle(session, run_id, reserved, cost)

    async def _call_with_retries(self, request: LLMRequest) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return await self._provider.complete(request)
            except RetryableLLMError:
                if attempt >= self._settings.llm_max_retries:
                    raise
                await self._sleep(self._backoff.delay_for(attempt))
                attempt += 1
