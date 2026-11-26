from __future__ import annotations

import pytest

from offerradar_agents.config import Settings
from offerradar_agents.matcher.prefilter import (
    PostingFacts,
    PrefilterRules,
    passes,
    rules_from_settings,
)

OPEN = PrefilterRules(terms=(), locations=())
STRICT = PrefilterRules(terms=("Summer 2027",), locations=("Toronto",))


def facts(
    title: str = "Software Engineer Intern",
    *,
    location: str | None = "Toronto, ON",
    remote: bool = False,
    term: str | None = "Summer 2027",
    description: str | None = "Build things.",
) -> PostingFacts:
    return PostingFacts(title, location, remote, term, description)


@pytest.mark.parametrize(
    ("posting", "expected"),
    [
        (facts(), True),
        (facts(term="summer 2027"), True),
        (facts(term="Fall 2027"), False),
        (facts(term=None), True),
        (facts(location="Vancouver, BC"), False),
        (facts(location="TORONTO"), True),
        (facts(location=None), True),
        (facts(location="Vancouver, BC", remote=True), True),
        (facts("Senior Engineer"), False),
        (facts("Sr. Engineer"), False),
        (facts("Engineering Manager Intern"), False),
        (facts("Seniority-free Intern"), True),
        (facts(description="Requires an active Security Clearance."), False),
        (facts(description="Must be a U.S. citizen."), False),
        (facts(description="TS/SCI required"), False),
        (facts("Top Secret Intern", description=None), False),
        (facts(description="ITAR controlled"), False),
        (facts(description="I play the guitar"), True),
    ],
)
def test_strict_rules(posting: PostingFacts, expected: bool) -> None:
    assert passes(posting, STRICT) is expected


def test_empty_rules_admit_any_term_and_location() -> None:
    assert passes(facts(term="Fall 2027", location="Vancouver"), OPEN)


def test_rules_from_settings_reads_lists() -> None:
    settings = Settings(prefilter_terms="Summer 2027, Fall 2027", prefilter_locations="Toronto")
    assert rules_from_settings(settings) == PrefilterRules(
        terms=("Summer 2027", "Fall 2027"), locations=("Toronto",)
    )
