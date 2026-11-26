from __future__ import annotations

import re
from dataclasses import dataclass

from offerradar_agents.config import Settings

SENIORITY_WORDS = ("senior", "sr", "staff", "principal", "lead", "manager", "director", "head")
CLEARANCE_PHRASES = (
    "security clearance",
    "secret clearance",
    "ts/sci",
    "top secret",
    "u.s. citizen",
    "us citizen",
    "u.s. citizenship",
    "us citizenship",
    "itar",
)

_SENIORITY = re.compile(rf"\b(?:{'|'.join(map(re.escape, SENIORITY_WORDS))})\b", re.IGNORECASE)
_CLEARANCE = re.compile(rf"\b(?:{'|'.join(map(re.escape, CLEARANCE_PHRASES))})\b", re.IGNORECASE)


@dataclass(frozen=True)
class PrefilterRules:
    terms: tuple[str, ...]
    locations: tuple[str, ...]


@dataclass(frozen=True)
class PostingFacts:
    title: str
    location: str | None
    remote: bool
    term: str | None
    description: str | None


def rules_from_settings(settings: Settings) -> PrefilterRules:
    return PrefilterRules(
        terms=settings.prefilter_term_list, locations=settings.prefilter_location_list
    )


def passes(facts: PostingFacts, rules: PrefilterRules) -> bool:
    if (
        rules.terms
        and facts.term is not None
        and facts.term.casefold() not in {term.casefold() for term in rules.terms}
    ):
        return False
    if rules.locations and not facts.remote and facts.location is not None:
        location = facts.location.casefold()
        if not any(wanted.casefold() in location for wanted in rules.locations):
            return False
    if _SENIORITY.search(facts.title):
        return False
    return not _CLEARANCE.search(f"{facts.title}\n{facts.description or ''}")
