from __future__ import annotations

import math

import pytest

from offerradar_agents.matcher.queries import _clamp


def test_clamp_pulls_float32_overshoot_back_into_range() -> None:
    assert _clamp(1.0000001) == 1.0
    assert _clamp(-1.0000001) == -1.0
    assert _clamp(0.42) == 0.42


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_clamp_rejects_non_finite_similarity(value: float) -> None:
    with pytest.raises(ValueError, match="zero norm"):
        _clamp(value)
