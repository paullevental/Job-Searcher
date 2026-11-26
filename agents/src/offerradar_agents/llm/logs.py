from __future__ import annotations

import logging


def quiet_sdk_loggers() -> None:
    for name in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
