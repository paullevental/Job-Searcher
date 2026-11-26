from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from offerradar_agents.config import get_settings
from offerradar_agents.db.session import build_engine, build_session_factory
from offerradar_agents.db.tables import llm_calls, postings


@dataclass(frozen=True)
class RunCost:
    calls: int
    total_cost: Decimal
    mean_cost: Decimal


@dataclass(frozen=True)
class CostComparison:
    calls: int
    open_postings: int
    funnel_cost: Decimal
    all_postings_cost: Decimal


async def run_cost(session: AsyncSession, run_id: int) -> RunCost:
    row = (
        await session.execute(
            select(
                func.count(llm_calls.c.id), func.coalesce(func.sum(llm_calls.c.cost_usd), 0)
            ).where(llm_calls.c.run_id == run_id)
        )
    ).one()
    calls, total = int(row[0]), Decimal(row[1])
    return RunCost(calls, total, total / calls if calls else Decimal(0))


async def open_posting_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count()).select_from(postings).where(postings.c.status == "open")
    )
    return int(count or 0)


def project(cost: RunCost, open_postings: int) -> CostComparison:
    return CostComparison(
        calls=cost.calls,
        open_postings=open_postings,
        funnel_cost=cost.total_cost,
        all_postings_cost=cost.mean_cost * open_postings,
    )


def format_comparison(comparison: CostComparison) -> str:
    return "\n".join(
        [
            "matcher cost per night",
            f"  funnel        ${comparison.funnel_cost:.6f}  ({comparison.calls} calls)",
            f"  all postings  ${comparison.all_postings_cost:.6f}"
            f"  ({comparison.open_postings} open)",
        ]
    )


async def _report(run_id: int) -> int:
    engine = build_engine(get_settings())
    try:
        async with build_session_factory(engine)() as session:
            comparison = project(await run_cost(session, run_id), await open_posting_count(session))
    except (SQLAlchemyError, OSError) as exc:
        print(f"database unreachable: {exc!r}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(format_comparison(comparison))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="offerradar_agents.matcher.cost_report")
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args(argv)
    return asyncio.run(_report(args.run_id))


if __name__ == "__main__":
    raise SystemExit(main())
