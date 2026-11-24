from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest
import respx

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.dedup.eval import (
    EvalReport,
    LabeledPair,
    RecordedVectors,
    derive_thresholds,
    evaluate,
    format_report,
    load_pairs,
    load_vectors,
)
from offerradar_agents.dedup.record import record
from offerradar_agents.embeddings.provider import DEFAULT_DOCUMENT_PREFIX, Vector

PAIRS_FILE = Path(__file__).parent / "fixtures/dedup_pairs.json"
VECTORS_FILE = Path(__file__).parent / "fixtures/dedup_pair_vectors.json"
EMBED_URL = "http://ollama.test:11434/api/embed"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def recorded() -> RecordedVectors:
    if not VECTORS_FILE.exists():
        if os.environ.get("CI"):
            pytest.fail("CI must gate the cosine thresholds: record dedup_pair_vectors.json")
        pytest.skip(
            "tests/fixtures/dedup_pair_vectors.json not recorded yet: "
            "run `make record-dedup-vectors` with Ollama up"
        )
    return load_vectors(VECTORS_FILE)


def pair(
    title_a: str,
    title_b: str,
    *,
    duplicate: bool,
    sources: tuple[str, str] = ("simplify", "greenhouse"),
) -> LabeledPair:
    return LabeledPair(
        pair_id=f"{title_a}|{title_b}",
        title_a=title_a,
        title_b=title_b,
        duplicate=duplicate,
        sources=sources,
    )


# A unit vector at the given cosine to [1, 0].
def at(cosine: float) -> Vector:
    return [cosine, math.sqrt(1 - cosine * cosine)]


ORIGIN: Vector = [1.0, 0.0]


def test_identical_duplicate_counts_as_true_positive() -> None:
    report = evaluate(
        (pair("data engineer intern", "data engineer intern", duplicate=True),),
        {"data engineer intern": ORIGIN},
    )

    assert report.true_positives == 1
    assert report.precision == 1.0
    assert report.recall == 1.0


def test_identical_non_duplicate_counts_as_false_positive() -> None:
    report = evaluate(
        (pair("data engineer intern", "data engineer intern", duplicate=False),),
        {"data engineer intern": ORIGIN},
    )

    assert report.false_positives == 1
    assert report.precision == 0.0


def test_missed_duplicate_counts_as_false_negative() -> None:
    report = evaluate(
        (pair("swe intern", "machine learning scientist", duplicate=True),),
        {"swe intern": ORIGIN, "machine learning scientist": [0.0, 1.0]},
    )

    assert report.false_negatives == 1
    assert report.recall == 0.0


def test_review_band_is_not_a_positive_prediction() -> None:
    # "review" goes to Paul, so it is neither a true nor a false positive.
    settings = get_settings()
    middle = (settings.dedup_merge_threshold + settings.dedup_review_threshold) / 2
    report = evaluate(
        (pair("product manager intern", "product management intern", duplicate=True),),
        {"product manager intern": ORIGIN, "product management intern": at(middle)},
    )

    assert report.review_band == 1
    assert report.true_positives == 0
    assert report.false_negatives == 1


def test_a_pair_without_recorded_vectors_is_unscored_not_distinct() -> None:
    report = evaluate((pair("data engineer intern", "data engineer", duplicate=True),))

    assert report.unscored == 1
    assert report.total == 0
    assert "UNSCORED" in format_report(report)


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


def test_same_board_pairs_are_refused_before_scoring() -> None:
    report = evaluate(load_pairs(PAIRS_FILE))

    # Measured: every merge-band same-board pair in the harvest was a false
    # positive, so open_siblings drops them and the eval must mirror that.
    assert report.same_board == 15


def test_a_shared_ats_board_blocks_an_otherwise_identical_pair() -> None:
    same_board = pair(
        "systems engineer",
        "systems engineer",
        duplicate=False,
        sources=("greenhouse", "greenhouse"),
    )
    report = evaluate((same_board,), {"systems engineer": ORIGIN})

    assert report.same_board == 1
    assert report.true_negatives == 1
    assert report.false_positives == 0


# A leading class year cancels in guard 2, so these pairs differ in string but
# not in tokens: the score alone decides them.
def scored(title: str, cosine: float, *, duplicate: bool) -> tuple[LabeledPair, dict[str, Vector]]:
    other = f"2027 {title}"
    return pair(title, other, duplicate=duplicate), {title: ORIGIN, other: at(cosine)}


def labelled(
    *entries: tuple[LabeledPair, dict[str, Vector]],
) -> tuple[tuple[LabeledPair, ...], dict[str, Vector]]:
    vectors: dict[str, Vector] = {}
    for _, entry_vectors in entries:
        vectors |= entry_vectors
    return tuple(p for p, _ in entries), vectors


def test_merge_threshold_is_the_lowest_duplicate_above_every_false_merge() -> None:
    pairs, vectors = labelled(
        scored("a", 0.99, duplicate=True),
        scored("b", 0.95, duplicate=True),
        scored("c", 0.93, duplicate=False),
        scored("d", 0.90, duplicate=True),
    )

    thresholds = derive_thresholds(pairs, vectors)

    assert thresholds.merge == pytest.approx(0.95)


def test_review_threshold_reaches_the_lowest_duplicate() -> None:
    pairs, vectors = labelled(
        scored("a", 0.99, duplicate=True),
        scored("c", 0.93, duplicate=False),
        scored("d", 0.70, duplicate=True),
    )

    assert derive_thresholds(pairs, vectors).review == pytest.approx(0.70)


def test_a_guard_2_demotion_does_not_constrain_the_merge_threshold() -> None:
    pairs, vectors = labelled(scored("a", 0.90, duplicate=True))
    blocked = pair("software engineer intern", "ai software engineer intern", duplicate=False)
    vectors |= {"software engineer intern": ORIGIN, "ai software engineer intern": at(0.99)}

    assert derive_thresholds((*pairs, blocked), vectors).merge == pytest.approx(0.90)


def test_a_same_board_pair_does_not_constrain_either_threshold() -> None:
    pairs, vectors = labelled(scored("a", 0.90, duplicate=True))
    same_board = pair("b", "2027 b", duplicate=False, sources=("greenhouse", "greenhouse"))
    vectors |= {"b": ORIGIN, "2027 b": at(0.99)}

    assert derive_thresholds((*pairs, same_board), vectors).merge == pytest.approx(0.90)


def test_no_merge_threshold_exists_when_a_false_merge_outscores_every_duplicate() -> None:
    pairs, vectors = labelled(
        scored("a", 0.90, duplicate=True),
        scored("c", 0.99, duplicate=False),
    )

    with pytest.raises(ValueError, match=r"precision 1\.0"):
        derive_thresholds(pairs, vectors)


def test_derived_thresholds_hold_the_precision_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    pairs, vectors = labelled(
        scored("a", 0.99, duplicate=True),
        scored("b", 0.95, duplicate=True),
        scored("c", 0.93, duplicate=False),
        scored("d", 0.70, duplicate=True),
    )
    thresholds = derive_thresholds(pairs, vectors)
    monkeypatch.setenv("DEDUP_MERGE_THRESHOLD", str(thresholds.merge))
    monkeypatch.setenv("DEDUP_REVIEW_THRESHOLD", str(thresholds.review))
    get_settings.cache_clear()

    report = evaluate(pairs, vectors)

    assert report.precision == 1.0
    assert (report.true_positives, report.false_negatives) == (2, 1)


def test_record_embeds_every_distinct_title_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pairs = load_pairs(PAIRS_FILE)
    titles = sorted({title for p in pairs for title in (p.title_a, p.title_b)})
    # Identical vectors: every cross-source pair scores 1.0, enough for derive_thresholds.
    body = {"embeddings": [[1.0, 0.0] for _ in titles]}
    settings = Settings(OLLAMA_URL="http://ollama.test:11434", EMBEDDING_BATCH_SIZE=len(titles))
    out = tmp_path / "vectors.json"

    with respx.mock(assert_all_called=True) as router:
        route = router.post(EMBED_URL).respond(200, json=body)
        record(pairs_path=PAIRS_FILE, out=out, settings=settings)

    sent = json.loads(route.calls.last.request.read())["input"]
    assert sent == [f"{DEFAULT_DOCUMENT_PREFIX}{title}" for title in titles]
    written = load_vectors(out)
    assert written.model == settings.embedding_model
    assert written.prefix == DEFAULT_DOCUMENT_PREFIX
    assert sorted(written.vectors) == titles
    assert "derived DEDUP_MERGE_THRESHOLD=1.0" in capsys.readouterr().out


# The recorded fixture must match what dedup embeds at runtime, or its scores
# measure a different similarity scale from the one the thresholds gate.
def test_recorded_vectors_match_the_runtime_model(recorded: RecordedVectors) -> None:
    assert recorded.model == Settings().embedding_model
    assert recorded.prefix == DEFAULT_DOCUMENT_PREFIX


# A merge rewrites rows without asking, so a regression in precision is a
# data-loss bug; recall only costs Paul a duplicate in his queue.
def test_no_merge_on_the_labelled_set_is_wrong(recorded: RecordedVectors) -> None:
    report = evaluate(load_pairs(PAIRS_FILE), recorded.vectors)

    assert report.unscored == 0, "re-record: a labelled title has no vector"
    assert report.precision == 1.0, f"{report.false_positives} false merges"
    assert report.recall >= 0.77


def test_report_names_precision_and_recall() -> None:
    text = format_report(evaluate(load_pairs(PAIRS_FILE)))

    assert "precision" in text
    assert "recall" in text
