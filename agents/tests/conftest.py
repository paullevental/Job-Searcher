from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from offerradar_agents.api.app import create_app
from offerradar_agents.dedup.eval import evaluate, format_report, load_pairs, load_vectors

DEDUP_PAIRS = Path(__file__).parent / "fixtures/dedup_pairs.json"
DEDUP_VECTORS = Path(__file__).parent / "fixtures/dedup_pair_vectors.json"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


# §11 M3's "CI prints precision/recall". A print() inside a passing test is
# swallowed by -q; the terminal summary writes to the reporter instead.
def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not DEDUP_PAIRS.exists():
        return
    terminalreporter.write_line("")
    vectors = load_vectors(DEDUP_VECTORS).vectors if DEDUP_VECTORS.exists() else None
    terminalreporter.write_line(format_report(evaluate(load_pairs(DEDUP_PAIRS), vectors)))
