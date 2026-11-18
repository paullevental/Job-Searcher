from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from offerradar_agents.api.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """App with lifespan run.

    Creating the engine and Redis client opens no sockets, so this stays
    offline (AGENTS.md §2 rule 5). Tests that exercise /ready replace the
    probe functions rather than standing up real dependencies.
    """
    with TestClient(create_app()) as test_client:
        yield test_client
