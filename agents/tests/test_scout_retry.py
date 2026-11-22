from __future__ import annotations

import random

import httpx
import pytest

from offerradar_agents.scout.retry import ExponentialBackoff


def policy(seed: int = 0) -> ExponentialBackoff:
    return ExponentialBackoff(
        base_seconds=1.0, max_seconds=30.0, max_retries=4, rng=random.Random(seed)
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_should_retry_status_accepts_rate_limit_and_server_errors(status: int) -> None:
    assert policy().should_retry_status(status) is True


@pytest.mark.parametrize("status", [200, 304, 400, 401, 403, 404, 422])
def test_should_retry_status_refuses_success_and_client_errors(status: int) -> None:
    assert policy().should_retry_status(status) is False


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("connect"),
        httpx.ReadTimeout("read"),
        httpx.ConnectError("refused"),
    ],
)
def test_should_retry_exception_accepts_transport_failures(exc: Exception) -> None:
    assert policy().should_retry_exception(exc) is True


def test_should_retry_exception_refuses_a_programming_error() -> None:
    assert policy().should_retry_exception(ValueError("bad field")) is False


@pytest.mark.parametrize("attempt", [0, 1, 2, 3])
def test_delay_for_stays_within_the_exponential_envelope(attempt: int) -> None:
    current = policy()
    ceiling = min(current.base_seconds * 2**attempt, current.max_seconds)

    delay = current.delay_for(attempt)

    assert 0.0 <= delay <= ceiling


def test_delay_for_respects_the_cap_at_high_attempts() -> None:
    current = policy()

    assert current.delay_for(20) <= current.max_seconds


def test_delay_for_is_reproducible_under_a_seeded_rng() -> None:
    first = [policy(seed=7).delay_for(attempt) for attempt in range(4)]
    second = [policy(seed=7).delay_for(attempt) for attempt in range(4)]

    assert first == second


def test_delay_for_actually_jitters() -> None:
    # Same attempt, different seeds: a formula that ignores rng returns one value.
    draws = {policy(seed=seed).delay_for(3) for seed in range(8)}

    assert len(draws) > 1


def test_retry_after_overrides_the_computed_delay() -> None:
    assert policy().delay_for(0, retry_after=12.5) == 12.5
