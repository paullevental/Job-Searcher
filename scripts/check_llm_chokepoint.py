#!/usr/bin/env python3
# Rule 8: a model SDK imported outside agents/.../llm/ bypasses budget, max_tokens, timeout and retry cap.

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_FILES = (Path("agents/tests/test_llm_anthropic_provider.py"),)

BANNED_MODULES = ("anthropic", "openai", "cohere", "mistralai", "google.generativeai")
IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?P<mod>[\w.]+)|from\s+(?P<frm>[\w.]+)\s+import)", re.MULTILINE
)


def _scanned(root: Path) -> list[Path]:
    package = root / "agents" / "src" / "offerradar_agents"
    allowed_dir = package / "llm"
    allowed_files = {root / p for p in ALLOWED_FILES}
    paths = [
        *package.rglob("*.py"),
        *(root / "agents" / "tests").rglob("*.py"),
        *(root / "scripts").rglob("*.py"),
    ]
    return sorted(p for p in paths if allowed_dir not in p.parents and p not in allowed_files)


def main(root: Path = ROOT) -> int:
    package = root / "agents" / "src" / "offerradar_agents"
    if not (package / "llm").is_dir():
        print(f"missing {package / 'llm'}; chokepoint cannot be verified", file=sys.stderr)
        return 1

    violations: list[str] = []
    for path in _scanned(root):
        text = path.read_text(encoding="utf-8")
        for match in IMPORT_RE.finditer(text):
            module = match.group("mod") or match.group("frm")
            # Prefix match on the full dotted name, so "google.generativeai" is
            # banned without also banning "google.cloud.storage".
            if any(module == b or module.startswith(f"{b}.") for b in BANNED_MODULES):
                line = text[: match.start()].count("\n") + 1
                violations.append(f"{path.relative_to(root)}:{line}: imports {module!r}")

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
