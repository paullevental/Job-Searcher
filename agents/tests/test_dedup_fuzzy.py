from __future__ import annotations

import pytest

from offerradar_agents.config import get_settings
from offerradar_agents.dedup.fuzzy import (
    classify,
    fuzzy_match,
    similarity_score,
    unmatched_tokens,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_identical_titles_score_one() -> None:
    assert similarity_score("software engineer intern", "software engineer intern") == 1.0


def test_score_is_symmetric() -> None:
    a, b = "backend engineer intern", "backend engineering intern"

    assert similarity_score(a, b) == similarity_score(b, a)


def test_unrelated_titles_score_below_review_threshold() -> None:
    score = similarity_score("frontend developer intern", "machine learning intern")

    assert score < get_settings().dedup_review_threshold


def test_score_is_bounded() -> None:
    assert 0.0 <= similarity_score("", "software engineer intern") <= 1.0


def test_classify_uses_both_thresholds() -> None:
    settings = get_settings()

    assert classify(1.0) == "merge"
    assert classify(settings.dedup_merge_threshold) == "merge"
    assert classify(settings.dedup_review_threshold) == "review"
    assert classify(settings.dedup_review_threshold - 0.01) == "distinct"


def test_abbreviation_is_missed_by_string_similarity() -> None:
    # Documents the M4 trigger: SequenceMatcher compares character runs, so an
    # abbreviation of a real duplicate scores as distinct. docs/TODO.md M3.
    score = similarity_score("swe intern", "software engineering intern")

    assert classify(score) == "distinct"


def test_an_inflected_word_is_not_an_unmatched_token() -> None:
    assert unmatched_tokens("software engineer intern", "software engineering intern") == ()
    assert unmatched_tokens("software developer intern", "software development intern") == ()


def test_a_leading_class_year_is_not_an_unmatched_token() -> None:
    # A trailing year is stripped in normalization; a leading one survives it.
    assert unmatched_tokens("2027 summer games intern", "summer games intern") == ()


def test_a_qualifier_on_one_side_is_unmatched() -> None:
    assert unmatched_tokens("ai software engineer intern", "software engineer intern") == ("ai",)
    assert unmatched_tokens("entry level software engineer", "entry level c software engineer") == (
        "c",
    )


def test_two_programme_names_are_not_inflections() -> None:
    # starlink/starship agree on 4 of 8 characters, below INFLECTION_PREFIX_RATIO.
    assert unmatched_tokens("engineer starlink", "engineer starship") == ("starlink", "starship")


def test_a_qualifier_demotes_a_merge_to_review() -> None:
    verdict, score = fuzzy_match("entry level software engineer", "entry level c software engineer")

    assert score >= get_settings().dedup_merge_threshold
    assert verdict == "review"


def test_an_inflection_still_merges() -> None:
    verdict, _ = fuzzy_match("software engineer intern", "software engineering intern")

    assert verdict == "merge"


def test_a_repeated_token_is_counted_not_set_matched() -> None:
    # Counter difference, not set difference: one "senior" must not cancel two.
    assert unmatched_tokens("senior senior engineer", "senior engineer") == ("senior",)
