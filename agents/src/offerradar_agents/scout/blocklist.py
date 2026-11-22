from __future__ import annotations

from urllib.parse import urlsplit

BANNED_HOSTS: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "indeed.com",
        "jobright.ai",
        "joinhandshake.com",
        "wellfound.com",
        "angel.co",  # redirects to wellfound.com
    }
)


class BannedDomainError(RuntimeError):
    pass


def banned_host(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        return None
    for banned in BANNED_HOSTS:
        if host == banned or host.endswith(f".{banned}"):
            return banned
    return None


def assert_allowed(url: str) -> None:
    found = banned_host(url)
    if found is not None:
        raise BannedDomainError(f"rule 2 forbids requests to {found}")
