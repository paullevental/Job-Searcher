from __future__ import annotations

from pathlib import Path

from offerradar_agents.dedup.eval import (
    EvalReport,
    LabeledPair,
    evaluate,
    format_report,
    load_pairs,
)

PAIRS_FILE = Path(__file__).parent / "fixtures/dedup_pairs.json"


def pair(title_a: str, title_b: str, *, duplicate: bool) -> LabeledPair:
    return LabeledPair(
        pair_id=f"{title_a}|{title_b}", title_a=title_a, title_b=title_b, duplicate=duplicate
    )


def test_identical_duplicate_counts_as_true_positive() -> None:
    report = evaluate((pair("data engineer intern", "data engineer intern", duplicate=True),))

    assert report.true_positives == 1
    assert report.precision == 1.0
    assert report.recall == 1.0


def test_identical_non_duplicate_counts_as_false_positive() -> None:
    report = evaluate((pair("data engineer intern", "data engineer intern", duplicate=False),))

    assert report.false_positives == 1
    assert report.precision == 0.0


def test_missed_duplicate_counts_as_false_negative() -> None:
    report = evaluate((pair("swe intern", "machine learning scientist", duplicate=True),))

    assert report.false_negatives == 1
    assert report.recall == 0.0


def test_review_band_is_not_a_positive_prediction() -> None:
    # "review" goes to Paul, so it is neither a true nor a false positive.
    report = evaluate(
        (pair("product manager intern", "product management intern", duplicate=True),)
    )

    assert report.review_band == 1
    assert report.true_positives == 0
    assert report.false_negatives == 1


def test_precision_and_recall_on_an_empty_denominator() -> None:
    empty = EvalReport(0, 0, 0, 0, 0, 0, 0)

    assert empty.precision == 1.0
    assert empty.recall == 1.0
    assert empty.total == 0


def test_the_labelled_set_is_real_not_placeholders() -> None:
    pairs = load_pairs(PAIRS_FILE)

    assert len(pairs) == 34
    assert all(not p.placeholder for p in pairs)
    assert all(p.sources != ("", "") for p in pairs)
    assert "PLACEHOLDERS" not in format_report(evaluate(pairs))


# A merge rewrites rows without asking, so a regression in precision is a
# data-loss bug; recall only costs Paul a duplicate in his queue.
def test_no_merge_on_the_labelled_set_is_wrong() -> None:
    report = evaluate(load_pairs(PAIRS_FILE))

    assert report.precision == 1.0, f"{report.false_positives} false merges"
    assert report.recall >= 0.77


def test_same_board_pairs_are_refused_before_scoring() -> None:
    report = evaluate(load_pairs(PAIRS_FILE))

    # Measured: every merge-band same-board pair in the harvest was a false
    # positive, so open_siblings drops them and the eval must mirror that.
    assert report.same_board == 15


def test_a_shared_ats_board_blocks_an_otherwise_identical_pair() -> None:
    same_board = LabeledPair(
        pair_id="x",
        title_a="systems engineer",
        title_b="systems engineer",
        duplicate=False,
        sources=("greenhouse", "greenhouse"),
    )
    report = evaluate((same_board,))

    assert report.same_board == 1
    assert report.true_negatives == 1
    assert report.false_positives == 0


def test_report_names_precision_and_recall() -> None:
    text = format_report(evaluate(load_pairs(PAIRS_FILE)))

    assert "precision" in text
    assert "recall" in text
