#!/usr/bin/env python3
"""Fail if code reads an environment variable that .env.example does not document.

AGENTS.md §9 requires .env.example to list every variable, and §12 makes keeping
it in sync part of the definition of done. "Keep it in sync" with no check drifts
within a milestone, so this is the check.

Direction matters. Reading an undocumented variable is an ERROR: a fresh clone
silently gets no value for it. Documenting a variable nothing reads yet is only
INFO -- .env.example legitimately carries vars consumed by docker-compose.yml
and by milestones not yet built.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "build", "dist", "__pycache__",
             ".gradle", ".ruff_cache", ".mypy_cache", ".pytest_cache", "scripts"}

# How each language names an env var. Spring reads ${VAR} from application.yml;
# pydantic-settings fields declare an explicit alias so the name is a literal
# this scan can see rather than an attribute name it would have to infer.
PATTERNS = (
    re.compile(r"""os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""os\.environ(?:\.get)?\(?\s*\[?\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""(?:validation_alias|alias)\s*=\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""\$\{([A-Z][A-Z0-9_]*)(?::[^}]*)?\}"""),
    re.compile(r"""import\.meta\.env\.([A-Z][A-Z0-9_]*)"""),
    re.compile(r"""process\.env(?:\.|\[["'])([A-Z][A-Z0-9_]*)"""),
)

SEARCH_SUFFIXES = {".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".yml", ".yaml"}


def documented() -> set[str]:
    if not ENV_EXAMPLE.exists():
        print(f"error: {ENV_EXAMPLE.name} is missing", file=sys.stderr)
        sys.exit(1)
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip())
    return keys


def referenced() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SEARCH_SUFFIXES:
            continue
        rel_parts = set(path.relative_to(ROOT).parts)
        if SKIP_DIRS & rel_parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1)
                line_no = text[: match.start()].count("\n") + 1
                found.setdefault(name, []).append(f"{rel}:{line_no}")
    return found


def main() -> int:
    docs = documented()
    refs = referenced()

    missing = {name: locs for name, locs in sorted(refs.items()) if name not in docs}
    unused = sorted(docs - set(refs))

    if unused:
        print("info: documented but not read by any code yet "
              "(docker-compose.yml and later milestones may consume these):")
        print(f"  {', '.join(unused)}")

    if missing:
        print("\nerror: code reads these but .env.example does not document them:",
              file=sys.stderr)
        for name, locs in missing.items():
            print(f"  {name}  <- {', '.join(locs[:4])}", file=sys.stderr)
        print("\nAdd them to .env.example with placeholder values "
              "(AGENTS.md §9, §12).", file=sys.stderr)
        return 1

    print(f"ok: all {len(refs)} env vars read by code are documented in .env.example")
    return 0


if __name__ == "__main__":
    sys.exit(main())
