from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.db.tables import agent_runs


async def start_run(session: AsyncSession, budget_usd: Decimal) -> int:
    result = await session.execute(
        insert(agent_runs).values(budget_usd=budget_usd).returning(agent_runs.c.id)
    )
    return int(result.scalar_one())


async def reserve(session: AsyncSession, run_id: int, amount: Decimal, *, max_calls: int) -> bool:
    result = await session.execute(
        update(agent_runs)
        .where(
            agent_runs.c.id == run_id,
            agent_runs.c.status == "running",
            agent_runs.c.spent_usd + amount <= agent_runs.c.budget_usd,
            agent_runs.c.llm_call_count < max_calls,
        )
        .values(
            spent_usd=agent_runs.c.spent_usd + amount,
            llm_call_count=agent_runs.c.llm_call_count + 1,
        )
        .returning(agent_runs.c.id)
    )
    return result.first() is not None


async def settle(session: AsyncSession, run_id: int, reserved: Decimal, actual: Decimal) -> None:
    await session.execute(
        update(agent_runs)
        .where(agent_runs.c.id == run_id)
        .values(spent_usd=agent_runs.c.spent_usd - reserved + actual)
    )


async def finish_run(session: AsyncSession, run_id: int, status: str) -> None:
    await session.execute(
        update(agent_runs)
        .where(agent_runs.c.id == run_id, agent_runs.c.status == "running")
        .values(status=status, finished_at=func.now())
    )


async def mark_budget_exhausted(session: AsyncSession, run_id: int) -> None:
    await session.execute(
        update(agent_runs)
        .where(agent_runs.c.id == run_id, agent_runs.c.status == "running")
        .values(status="budget_exhausted", finished_at=func.now())
    )
