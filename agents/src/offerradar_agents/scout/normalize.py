from __future__ import annotations

import hashlib
import itertools
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Legal forms only -- "Group"/"Holdings"/"Labs" can distinguish real companies.
LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "ab",
        "ag",
        "as",
        "bv",
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "llp",
        "lp",
        "ltd",
        "nv",
        "oy",
        "plc",
        "pty",
        "sa",
    }
)

# Prefix match: a list of exact names goes stale when a source adds one.
TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_", "gh_src", "ref")

_SEASONS: frozenset[str] = frozenset({"spring", "summer", "fall", "autumn", "winter"})


def _fold(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(char if char.isalnum() or char.isspace() else " " for char in folded)
    return cleaned.split()


# Lowercase the host, not the path: paths are case-sensitive and a 404 follows.
def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    default_port = {"http": 80, "https": 443}.get(parts.scheme)
    netloc = parts.hostname or ""
    if parts.port is not None and parts.port != default_port:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path[:-1] if len(parts.path) > 1 and parts.path.endswith("/") else parts.path
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not any(key.startswith(prefix) for prefix in TRACKING_PARAM_PREFIXES)
    ]
    query = urlencode(sorted(kept))
    return urlunsplit((parts.scheme, netloc, path, query, ""))


# NFKC before casefold, and a legal suffix drops only as a whole trailing token.
def normalized_company_name(raw_name: str) -> str:
    tokens = _fold(raw_name)
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# Term removed: it is its own dedup_key field, so leaving it counts it twice.
def normalized_job_title(raw_title: str) -> str:
    tokens = _fold(raw_title)
    if (
        len(tokens) >= 2
        and tokens[-2] in _SEASONS
        and tokens[-1].isdigit()
        and len(tokens[-1]) == 4
    ):
        tokens = tokens[:-2]
    return " ".join(tokens)


# "{Season} {YYYY}" or None. No LLM in M2, so unparseable is None, never a guess.
def parse_term(title: str) -> str | None:
    words = title.split()
    for season_word, year_word in itertools.pairwise(words):
        season = season_word.strip("()").casefold()
        year = year_word.strip("()")
        if season in _SEASONS and year.isdigit() and len(year) == 4:
            return f"{season_word.strip('()')} {year}"
    return None


# sha256 of length-prefixed fields: a plain delimiter join lets "a|b","c" collide with "a","b|c".
def dedup_key(company: str, title: str, term: str | None) -> str:
    payload = bytearray()
    for field in (company, title, term or ""):
        data = field.encode("utf-8")
        payload += len(data).to_bytes(4, "big")
        payload += data
    return hashlib.sha256(payload).hexdigest()
