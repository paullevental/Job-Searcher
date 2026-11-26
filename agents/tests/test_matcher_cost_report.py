from __future__ import annotations

from decimal import Decimal

from offerradar_agents.matcher.cost_report import RunCost, format_comparison, project


def test_project_is_decimal_exact() -> None:
    cost = RunCost(calls=3, total_cost=Decimal("0.030000"), mean_cost=Decimal("0.010000"))
    comparison = project(cost, 1000)
    assert comparison.funnel_cost == Decimal("0.030000")
    assert comparison.all_postings_cost == Decimal("10.000000")


def test_zero_calls_projects_zero() -> None:
    comparison = project(RunCost(0, Decimal(0), Decimal(0)), 500)
    assert comparison.all_postings_cost == Decimal(0)
    assert "$0.000000" in format_comparison(comparison)


def test_format_names_both_costs() -> None:
    text = format_comparison(project(RunCost(2, Decimal("0.5"), Decimal("0.25")), 40))
    assert "$0.500000  (2 calls)" in text
    assert "$10.000000  (40 open)" in text
