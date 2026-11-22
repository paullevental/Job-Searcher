from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

EVENT = "scout.source_alert"


class AlertKind(StrEnum):
    FAILED = "failed"
    EMPTY = "empty"
    REFUSED = "refused"


def source_alert(
    kind: AlertKind,
    source: str,
    *,
    run_id: str | None = None,
    detail: str = "",
) -> None:
    fields: dict[str, Any] = {
        "event": EVENT,
        "alert_kind": str(kind),
        "source": source,
        "run_id": run_id,
        "detail": detail,
    }
    logger.warning("%s %s: %s", EVENT, source, kind, extra=fields)
