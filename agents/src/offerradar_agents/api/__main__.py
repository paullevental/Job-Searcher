"""Entry point: python -m offerradar_agents.api"""

from __future__ import annotations

import uvicorn

from offerradar_agents.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "offerradar_agents.api.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=settings.is_dev,
    )


if __name__ == "__main__":
    main()
