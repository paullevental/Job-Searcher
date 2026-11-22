from __future__ import annotations

from offerradar_agents.scout.normalize import (
    canonicalize_url,
    dedup_key,
    normalized_company_name,
    normalized_job_title,
    parse_term,
)

HEX64 = 64


def test_canonicalize_url_drops_tracking_params_and_fragment() -> None:
    canonical = canonicalize_url(
        "https://boards.greenhouse.io/acme/jobs/4012?utm_source=x&gh_src=abc&gh_jid=4012#apply"
    )

    assert "utm_source" not in canonical
    assert "gh_src" not in canonical
    assert "#" not in canonical
    assert "gh_jid=4012" in canonical


def test_canonicalize_url_lowercases_host_but_not_path() -> None:
    assert canonicalize_url("https://Boards.Greenhouse.IO/Acme/Jobs/Backend-Intern") == (
        "https://boards.greenhouse.io/Acme/Jobs/Backend-Intern"
    )


def test_canonicalize_url_is_idempotent() -> None:
    once = canonicalize_url("https://jobs.lever.co/acme/abc-123?utm_medium=feed")

    assert canonicalize_url(once) == once


def test_canonicalize_url_agrees_on_trailing_slash_variants() -> None:
    assert canonicalize_url("https://jobs.lever.co/acme/abc-123/") == canonicalize_url(
        "https://jobs.lever.co/acme/abc-123"
    )


def test_canonicalize_url_strips_the_default_port() -> None:
    assert canonicalize_url("https://jobs.lever.co:443/acme/abc-123") == canonicalize_url(
        "https://jobs.lever.co/acme/abc-123"
    )


def test_canonicalize_url_orders_surviving_params_stably() -> None:
    assert canonicalize_url("https://x.example/j?b=2&a=1") == canonicalize_url(
        "https://x.example/j?a=1&b=2"
    )


def test_normalized_company_name_collapses_legal_suffix_and_punctuation() -> None:
    assert normalized_company_name("Stripe, Inc.") == normalized_company_name("Stripe")
    assert normalized_company_name("stripe inc") == normalized_company_name("Stripe")


def test_normalized_company_name_applies_nfkc_before_comparing() -> None:
    # Without NFKC each opens a second row in a UNIQUE column.
    assert normalized_company_name("\uff33tripe") == normalized_company_name("Stripe")
    assert normalized_company_name("Acme\u00a0Labs") == normalized_company_name("Acme Labs")


def test_normalized_company_name_keeps_distinct_companies_distinct() -> None:
    assert normalized_company_name("Notstripe") != normalized_company_name("Stripe")


def test_normalized_job_title_removes_the_term() -> None:
    assert normalized_job_title("Software Engineer Intern (Summer 2026)") == normalized_job_title(
        "Software Engineer Intern"
    )


def test_normalized_job_title_keeps_distinct_specialisations_distinct() -> None:
    assert normalized_job_title("Software Engineer Intern, Backend") != normalized_job_title(
        "Software Engineer Intern, ML"
    )


def test_parse_term_reads_a_season_and_year_from_the_title() -> None:
    assert parse_term("Software Engineer Intern (Summer 2026)") == "Summer 2026"


def test_parse_term_returns_none_rather_than_guessing() -> None:
    assert parse_term("Software Engineer Intern") is None


def test_dedup_key_is_a_lowercase_sha256_hexdigest() -> None:
    key = dedup_key("stripe", "software engineer intern", "Summer 2026")

    assert len(key) == HEX64
    assert key == key.lower()
    assert all(char in "0123456789abcdef" for char in key)


def test_dedup_key_is_deterministic() -> None:
    first = dedup_key("stripe", "software engineer intern", "Summer 2026")
    second = dedup_key("stripe", "software engineer intern", "Summer 2026")

    assert first == second


def test_dedup_key_treats_a_missing_term_as_the_empty_string() -> None:
    assert dedup_key("stripe", "swe intern", None) == dedup_key("stripe", "swe intern", "")


def test_dedup_key_separator_cannot_be_forged_by_a_field_value() -> None:
    # ("a|b", "c") and ("a", "b|c") must not collide on a literal "|" join.
    assert dedup_key("a|b", "c", None) != dedup_key("a", "b|c", None)


def test_dedup_key_changes_with_the_term() -> None:
    assert dedup_key("stripe", "swe intern", "Summer 2026") != dedup_key(
        "stripe", "swe intern", "Fall 2026"
    )
