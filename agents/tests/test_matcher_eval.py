from __future__ import annotations

import math
from pathlib import Path

import pytest

from offerradar_agents.matcher.eval import (
    RankingReport,
    RecordedChunk,
    RetrievalFixture,
    RetrievalQuery,
    RetrievalReport,
    evaluate_ranking,
    evaluate_retrieval,
    format_report,
    load_ranking_fixture,
    load_retrieval_fixture,
    sample_for_rating,
    split_holdout,
)
from offerradar_agents.matcher.rank import score_posting

FIXTURES = Path(__file__).parent / "fixtures"
RANKING_FILE = FIXTURES / "ranking_eval.example.json"
RETRIEVAL_FILE = FIXTURES / "retrieval_eval.example.json"

NOT_WRITTEN = pytest.mark.xfail(raises=NotImplementedError, strict=True)


def test_ranking_fixture_loads() -> None:
    fixture = load_ranking_fixture(RANKING_FILE)

    assert fixture.placeholder
    assert len(fixture.chunk_vectors) == 2
    assert [p.posting_id for p in fixture.postings] == [1, 2, 5, 6, 3, 4]
    assert [p.holdout for p in fixture.postings] == [False, False, False, False, True, True]
    assert fixture.postings[0].rating == 5.0


def test_retrieval_fixture_loads() -> None:
    fixture = load_retrieval_fixture(RETRIEVAL_FILE)

    assert fixture.placeholder
    assert [c.ordinal for c in fixture.chunks] == [0, 1, 2]
    assert fixture.queries[1].relevant_ordinals == frozenset({1, 2})


def test_ranking_loader_rejects_mixed_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        '{"embedding_model": "m", "chunks": [[1, 0]], '
        '"postings": [{"id": 1, "vector": [1, 0, 0], "rating": 3}]}'
    )

    with pytest.raises(ValueError, match="dimension"):
        load_ranking_fixture(path)


def test_retrieval_loader_rejects_unknown_ordinal(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        '{"embedding_model": "m", "chunks": [{"ordinal": 0, "vector": [1]}], '
        '"queries": [{"id": "q", "text": "t", "vector": [1], "relevant": [7]}]}'
    )

    with pytest.raises(ValueError, match="unknown chunk ordinal"):
        load_retrieval_fixture(path)


def test_sample_stays_inside_the_rank_window() -> None:
    ranked = list(range(1, 501))

    sample = sample_for_rating(ranked, n=50, seed=1)

    assert len(sample) == 50
    assert all(21 <= posting_id <= 200 for posting_id in sample)


def test_sample_has_no_duplicates() -> None:
    sample = sample_for_rating(list(range(1, 501)), n=180, seed=3)

    assert len(set(sample)) == 180


def test_sample_is_deterministic_per_seed() -> None:
    ranked = list(range(1, 301))

    assert sample_for_rating(ranked, n=30, seed=7) == sample_for_rating(ranked, n=30, seed=7)
    assert sample_for_rating(ranked, n=30, seed=7) != sample_for_rating(ranked, n=30, seed=8)


def test_sample_is_not_in_rank_order() -> None:
    sample = sample_for_rating(list(range(1, 301)), n=30, seed=7)

    assert sample != sorted(sample)


def test_sample_honours_a_custom_window() -> None:
    sample = sample_for_rating(list(range(1, 101)), lo=5, hi=10, n=6, seed=0)

    assert sorted(sample) == [5, 6, 7, 8, 9, 10]


def test_sample_rejects_more_than_the_pool() -> None:
    with pytest.raises(ValueError, match="pool"):
        sample_for_rating(list(range(1, 101)), n=100, seed=0)


def test_sample_rejects_a_bad_window() -> None:
    with pytest.raises(ValueError, match="rank range"):
        sample_for_rating([1, 2, 3], lo=5, hi=2, n=1, seed=0)


def test_holdout_split_is_disjoint_and_complete() -> None:
    ids = list(range(1, 101))

    tuning, holdout = split_holdout(ids, fraction=0.3, seed=5)

    assert not set(tuning) & set(holdout)
    assert sorted(tuning + holdout) == ids
    assert 15 <= len(holdout) <= 45


def test_holdout_membership_survives_new_ratings() -> None:
    _, before = split_holdout(range(1, 51), fraction=0.3, seed=5)
    tuning_after, after = split_holdout(range(1, 201), fraction=0.3, seed=5)

    assert set(before) <= set(after)
    assert not set(before) & set(tuning_after)


def test_holdout_split_is_deterministic_and_order_independent() -> None:
    ids = list(range(1, 101))

    assert split_holdout(ids, fraction=0.3, seed=5) == split_holdout(ids, fraction=0.3, seed=5)
    assert split_holdout(ids, fraction=0.3, seed=5) == split_holdout(
        ids[::-1], fraction=0.3, seed=5
    )


def test_holdout_split_edges() -> None:
    assert split_holdout([1, 2, 3], fraction=0, seed=0) == ([1, 2, 3], [])
    assert split_holdout([1, 2, 3], fraction=1, seed=0) == ([], [1, 2, 3])
    with pytest.raises(ValueError, match="fraction"):
        split_holdout([1], fraction=1.5, seed=0)


def test_format_report_marks_placeholders() -> None:
    ranking = format_report(RankingReport(4, False, 20, 0.5, 0.25, placeholder=True))
    retrieval = format_report(RetrievalReport(2, 5, 1.0))

    assert "PLACEHOLDER" in ranking
    assert "tuning" in ranking
    assert "PLACEHOLDER" not in retrieval
    assert "recall@5" in retrieval


@NOT_WRITTEN
@pytest.mark.parametrize(
    "similarities", [[0.2], [-1.0], [1.0], [0.1, 0.5, 0.9], [-0.5, 0.0, 0.5, 1.0]]
)
def test_score_posting_stays_in_range(similarities: list[float]) -> None:
    score = score_posting(similarities)

    assert isinstance(score, float)
    assert -1.0 <= score <= 1.0


@NOT_WRITTEN
@pytest.mark.parametrize("index", [0, 1, 2])
def test_score_posting_never_drops_when_one_similarity_rises(index: int) -> None:
    base = [0.1, 0.4, 0.7]
    raised = list(base)
    raised[index] += 0.2

    assert score_posting(raised) >= score_posting(base)


@NOT_WRITTEN
def test_score_posting_handles_a_single_chunk() -> None:
    assert math.isfinite(score_posting([0.3]))
    assert score_posting([0.6]) >= score_posting([0.3])


@NOT_WRITTEN
def test_evaluate_ranking_reports_in_range_metrics() -> None:
    report = evaluate_ranking(load_ranking_fixture(RANKING_FILE), k=2)

    assert report.rated == 4
    assert not report.holdout
    assert 0.0 <= report.recall_at_k <= 1.0
    assert -1.0 <= report.spearman <= 1.0
    assert report.placeholder


@NOT_WRITTEN
def test_evaluate_ranking_holdout_scores_only_holdout_rows() -> None:
    report = evaluate_ranking(load_ranking_fixture(RANKING_FILE), holdout=True, k=2)

    assert report.holdout
    assert report.rated == 2


@NOT_WRITTEN
def test_evaluate_ranking_rewards_ratings_that_follow_similarity() -> None:
    report = evaluate_ranking(load_ranking_fixture(RANKING_FILE), k=2)

    assert report.spearman > 0


@NOT_WRITTEN
def test_evaluate_retrieval_is_perfect_when_the_nearest_chunk_is_relevant() -> None:
    fixture = RetrievalFixture(
        embedding_model="m",
        chunks=(RecordedChunk(0, (1.0, 0.0)), RecordedChunk(1, (0.0, 1.0))),
        queries=(
            RetrievalQuery("a", "x", (0.9, 0.1), frozenset({0})),
            RetrievalQuery("b", "y", (0.1, 0.9), frozenset({1})),
        ),
    )

    report = evaluate_retrieval(fixture, k=1)

    assert report.queries == 2
    assert report.recall_at_k == 1.0


@NOT_WRITTEN
def test_evaluate_retrieval_recall_is_bounded() -> None:
    report = evaluate_retrieval(load_retrieval_fixture(RETRIEVAL_FILE), k=1)

    assert report.queries == 2
    assert 0.0 <= report.recall_at_k <= 1.0
