from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from offerradar_agents.api import app as app_module


def test_health_reports_ok_without_touching_dependencies(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "agents"


def test_ready_reports_ready_when_both_dependencies_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(_: object) -> str:
        return "ok"

    monkeypatch.setattr(app_module, "_check_postgres", ok)
    monkeypatch.setattr(app_module, "_check_redis", ok)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "agents",
        "checks": {"postgres": "ok", "redis": "ok"},
    }


def test_ready_reports_503_when_postgres_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(_: object) -> str:
        return "ok"

    async def boom(_: object) -> str:
        raise ConnectionRefusedError("postgres is down")

    monkeypatch.setattr(app_module, "_check_postgres", boom)
    monkeypatch.setattr(app_module, "_check_redis", ok)

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not ready"
    assert body["checks"]["postgres"] == "error: ConnectionRefusedError"
    assert body["checks"]["redis"] == "ok"
