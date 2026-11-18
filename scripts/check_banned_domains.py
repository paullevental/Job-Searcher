#!/usr/bin/env python3
"""Fail if a banned job-board domain is referenced in source.

AGENTS.md §2 rule 2 bans LinkedIn, Indeed, Jobright, Handshake and Wellfound
outright, with one exception: the manual-add form, where Paul pastes a URL by
hand. That exception is STORE-ONLY. Nothing may fetch such a URL, so the
allowance is narrow and the path is listed explicitly below.

Two tiers of match, because a hook that cries wolf gets bypassed:

  Distinctive tokens are matched bare. "linkedin" and "jobright" are not words.
  Ambiguous tokens need a TLD. "indeed" is ordinary English and "handshake"
  appears in any discussion of TLS or TCP, so those only trip on "indeed.com"
  and "handshake.com". Handshake's real domain is joinhandshake.com, which is
  distinctive and so is matched bare.

This is a source grep and is defeated by string construction. The durable
control is egress filtering at the Docker/VPS layer; see docs/TODO.md.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DISTINCTIVE = ("linkedin", "jobright", "wellfound", "joinhandshake")
AMBIGUOUS = ("indeed", "handshake")
TLD = r"\.(?:com|ca|ai|io|org|net|co\.uk)"

PATTERN = re.compile(
    r"\b(?:" + "|".join(DISTINCTIVE) + r")\b"
    r"|\b(?:" + "|".join(AMBIGUOUS) + r")" + TLD,
    re.IGNORECASE,
)

# Paths allowed to name a banned domain, and only to store or display a
# hand-pasted URL -- never to request one.
ALLOWED_PATHS = (
    "web/src/features/manual-add/",
    "scripts/check_banned_domains.py",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/",
)

# Recorded HTTP fixtures are exactly where an accidental reference lands, so
# .json and .html are in scope alongside source.
SEARCH_SUFFIXES = {
    ".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".sql",
    ".yml", ".yaml", ".json", ".html", ".htm", ".txt",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "build", "dist", "__pycache__",
             ".gradle", ".ruff_cache", ".mypy_cache", ".pytest_cache"}


def is_allowed(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in ALLOWED_PATHS)


def main() -> int:
    violations: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SEARCH_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if is_allowed(rel):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = PATTERN.search(line)
            if match:
                violations.append(f"{rel}:{i}: references {match.group(0)!r}")

    if violations:
        print("Banned job-board domain referenced (AGENTS.md §2 rule 2):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print(f"ok: no banned job-board references "
          f"({', '.join(DISTINCTIVE)}; {', '.join(AMBIGUOUS)} with a TLD)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
