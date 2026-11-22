from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

import httpx


class RetryPolicy(Protocol):
    # A property, not an attribute: settable excludes frozen implementations.
    @property
    def max_retries(self) -> int: ...

    def should_retry_status(self, status_code: int) -> bool: ...

    def should_retry_exception(self, exc: BaseException) -> bool: ...

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float: ...


@dataclass(frozen=True)
class ExponentialBackoff:
    base_seconds: float
    max_seconds: float
    max_retries: int
    rng: random.Random = field(default_factory=random.Random)

    def should_retry_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def should_retry_exception(self, exc: BaseException) -> bool:
        return isinstance(exc, httpx.TimeoutException | httpx.ConnectError)

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return retry_after
        ceiling = min(self.max_seconds, self.base_seconds * 2**attempt)
        return self.rng.uniform(0.0, ceiling)
