from __future__ import annotations

import pytest

from offerradar_agents.matcher.rank import score_posting


def test_score_is_mean_of_top_three() -> None:
    assert score_posting([0.1, 0.9, 0.5, 0.7, -1.0]) == pytest.approx(0.7)


def test_fewer_than_three_chunks_averages_what_exists() -> None:
    assert score_posting([0.2, 0.6]) == pytest.approx(0.4)
    assert score_posting([0.3]) == pytest.approx(0.3)


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        score_posting([])
