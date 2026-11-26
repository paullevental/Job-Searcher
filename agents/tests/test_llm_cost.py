from __future__ import annotations

from decimal import Decimal

from offerradar_agents.config import Settings
from offerradar_agents.llm.cost import FREE, Prices, call_cost, prices_from_settings, reservation
from offerradar_agents.llm.provider import LLMRequest, Usage
from offerradar_agents.matcher.score_schema import SCORE_TOOL

PRICES = Prices(Decimal("3"), Decimal("15"), Decimal("0.3"), Decimal("3.75"))


def _request() -> LLMRequest:
    return LLMRequest(
        model="m",
        system="You score postings.",
        cached_context="resume " * 500,
        user="posting " * 200,
        tool=SCORE_TOOL,
        max_tokens=400,
    )


def test_call_cost_is_exact_decimal() -> None:
    usage = Usage(1000, 200, cache_read_tokens=5000, cache_write_tokens=2000)
    assert call_cost(usage, PRICES) == Decimal("0.015")


def test_free_costs_nothing() -> None:
    assert call_cost(Usage(10_000, 500, 100, 100), FREE) == 0
    assert reservation(_request(), FREE) == 0


def test_reservation_bounds_actual_cost() -> None:
    request = _request()
    worst = Usage(len(request.cached_context + request.user + request.system) // 3, 400)
    assert reservation(request, PRICES) >= call_cost(worst, PRICES)


def test_prices_from_settings() -> None:
    prices = prices_from_settings(Settings())
    assert prices.input == Settings().llm_price_input_per_mtok
