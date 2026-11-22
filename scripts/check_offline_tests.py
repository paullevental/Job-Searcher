#!/usr/bin/env python3
"""Fail if a test could open a socket.

AGENTS.md §2 rule 5 forbids live network calls in tests. Rules 2, 3 and 8 each
have a guard; this one gives rule 5 the same teeth. A test module that reaches
the HTTP client without respx intercepting the transport would hit the real
source, so the check is: anything importing ScoutHttpClient or an adapter must
also import respx. Tests under tests/integration/ are exempt -- they talk to a
container on localhost, which respx would block.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "agents" / "tests"
EXEMPT_DIR = TESTS / "integration"

# Importing any of these means the module can reach the network.
NETWORK_IMPORTS = (
    "offerradar_agents.scout.http",
    "offerradar_agents.scout.github_readme",
    "offerradar_agents.scout.greenhouse",
    "offerradar_agents.scout.lever",
    "offerradar_agents.scout.ouckah",
    "offerradar_agents.scout.simplify",
    "offerradar_agents.scout.assemble",
)


def main() -> int:
    violations: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        if EXEMPT_DIR in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        reaches_network = any(module in text for module in NETWORK_IMPORTS)
        if reaches_network and "respx" not in text:
            violations.append(str(path.relative_to(ROOT)))

    if violations:
        print("Tests can reach the network with nothing intercepting it:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print("\nMock the transport with respx (AGENTS.md §2 rule 5).", file=sys.stderr)
        return 1

    print("ok: every offline test that can reach the network mocks the transport")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
