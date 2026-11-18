#!/usr/bin/env python3
"""Fail if a model SDK is imported outside the shared LLM client.

AGENTS.md §2 rule 8 and §8 "Providers" require every LLM call to go through
agents/src/offerradar_agents/llm/. A stray `import anthropic` elsewhere bypasses
the budget check, max_tokens, the timeout and the retry cap at the same time,
so rules 8 and 10 share this one chokepoint.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "agents" / "src" / "offerradar_agents"
ALLOWED_DIR = PACKAGE / "llm"

BANNED_MODULES = ("anthropic", "openai", "cohere", "mistralai", "google.generativeai")
IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?P<mod>[\w.]+)|from\s+(?P<frm>[\w.]+)\s+import)", re.MULTILINE
)


def main() -> int:
    violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if not ALLOWED_DIR in path.parents:
            text = path.read_text(encoding="utf-8")
            for match in IMPORT_RE.finditer(text):
                module = match.group("mod") or match.group("frm")
                # Prefix match on the full dotted name, so "google.generativeai" is
                # banned without also banning "google.cloud.storage".
                if any(module == b or module.startswith(f"{b}.") for b in BANNED_MODULES):
                    line = text[: match.start()].count("\n") + 1
                    rel = path.relative_to(ROOT)
                    violations.append(f"{rel}:{line}: imports {module!r}")

    if violations:
        print("Model SDK imported outside the shared LLM client:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nMove the call behind offerradar_agents.llm (AGENTS.md §2 rule 8, §8).",
            file=sys.stderr,
        )
        return 1

    print("ok: no model SDK imported outside offerradar_agents/llm/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
