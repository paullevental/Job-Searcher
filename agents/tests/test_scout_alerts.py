from __future__ import annotations

import logging

import pytest

from offerradar_agents.scout.alerts import EVENT, AlertKind, source_alert


def test_source_alert_emits_one_structured_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        source_alert(AlertKind.FAILED, "lever", run_id="run-7", detail="503 after 4 retries")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.event == EVENT
    assert record.alert_kind == "failed"
    assert record.source == "lever"
    assert record.run_id == "run-7"


def test_not_modified_is_not_an_alert_kind() -> None:
    # A 304 as an AlertKind would alert on every night that caching works.
    assert {kind.value for kind in AlertKind} == {"failed", "empty", "refused"}
