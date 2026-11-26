from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from offerradar_agents.matcher.eval import split_holdout
from offerradar_agents.matcher.score_eval import (
    ScoredRun,
    evaluate,
    format_report,
    load_ratings,
    load_scored,
    spearman,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _run(scores: dict[int, int | None]) -> ScoredRun:
    return ScoredRun(prompt_version="v1", provider="p", model="m", scores=scores)


def test_spearman_perfect_and_reversed() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_ties_use_average_ranks() -> None:
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486832981)


def test_spearman_undefined_cases_are_nan() -> None:
    assert math.isnan(spearman([1], [1]))
    assert math.isnan(spearman([1, 2, 3], [5, 5, 5]))


def test_failed_counts_null_and_missing() -> None:
    ratings = {i: 3 for i in range(1, 7)}
    run = _run({1: None, 2: 50, 3: 60})
    report = evaluate(ratings, run, holdout=False, fraction=0.0)
    assert report.rated == 6
    assert report.failed == 4


def test_mae_and_spearman_over_scored_rows() -> None:
    ratings = {1: 5, 2: 3, 3: 1}
    report = evaluate(ratings, _run({1: 90, 2: 50, 3: 10}), holdout=False, fraction=0.0)
    assert report.mae == pytest.approx((10 + 0 + 10) / 3)
    assert report.spearman == pytest.approx(1.0)


def test_holdout_membership_stable_when_more_ratings_added() -> None:
    small = {i: 3 for i in range(1, 30)}
    large = {i: 3 for i in range(1, 60)}
    _, held_small = split_holdout(list(small), fraction=0.3, seed=0)
    _, held_large = split_holdout(list(large), fraction=0.3, seed=0)
    assert set(held_small) <= set(held_large)
    run = _run({i: 50 for i in large})
    assert evaluate(large, run, holdout=True).rated == len(held_large)


def _write(tmp_path: Path, postings: list[dict[str, int]]) -> Path:
    path = tmp_path / "ratings.json"
    path.write_text(json.dumps({"postings": postings}), encoding="utf-8")
    return path


def test_load_ratings_rejects_rating_six(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"outside 1\.\.5"):
        load_ratings(_write(tmp_path, [{"posting_id": 1, "rating": 6}]))


def test_load_ratings_rejects_duplicate_ids(tmp_path: Path) -> None:
    rows = [{"posting_id": 1, "rating": 2}, {"posting_id": 1, "rating": 3}]
    with pytest.raises(ValueError, match="duplicate"):
        load_ratings(_write(tmp_path, rows))


def test_example_fixtures_load_and_report() -> None:
    ratings = load_ratings(FIXTURES / "score_eval.example.json")
    run = load_scored(FIXTURES / "score_eval_run.example.json")
    assert run.scores[4] is None
    text = format_report(evaluate(ratings, run, holdout=False, fraction=0.0))
    assert "failed     1" in text
