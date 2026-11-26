from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from offerradar_agents.config import Settings
from offerradar_agents.llm.provider import LLMRequest, Usage

MTOK = Decimal(1_000_000)
MICRO = Decimal("0.000001")


# USD per million tokens.
@dataclass(frozen=True)
class Prices:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


FREE = Prices(Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def prices_from_settings(settings: Settings) -> Prices:
    return Prices(
        input=settings.llm_price_input_per_mtok,
        output=settings.llm_price_output_per_mtok,
        cache_read=settings.llm_price_cache_read_per_mtok,
        cache_write=settings.llm_price_cache_write_per_mtok,
    )


# Rounded up to the column's 6 places so reserve and settle never disagree by a rounding step.
def _micro(amount: Decimal) -> Decimal:
    return amount.quantize(MICRO, rounding=ROUND_CEILING)


def call_cost(usage: Usage, prices: Prices) -> Decimal:
    total = (
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + usage.cache_read_tokens * prices.cache_read
        + usage.cache_write_tokens * prices.cache_write
    )
    return _micro(total / MTOK)


# One token is at least one byte, so the byte count bounds the input tokens from above.
def reservation(request: LLMRequest, prices: Prices) -> Decimal:
    schema = json.dumps(request.tool.input_schema)
    text = request.system + request.cached_context + request.user + schema
    input_bound = len(text.encode())
    total = input_bound * max(prices.input, prices.cache_write) + request.max_tokens * prices.output
    return _micro(total / MTOK)
