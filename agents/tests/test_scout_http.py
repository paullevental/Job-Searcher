from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
import respx

from offerradar_agents.config import Settings
from offerradar_agents.scout.blocklist import BANNED_HOSTS, BannedDomainError
from offerradar_agents.scout.http import (
    RobotsDisallowedError,
    ScoutHttpClient,
    SourceResponseError,
)

HOST = "boards.example.test"
BASE = f"https://{HOST}"
JOBS_URL = f"{BASE}/v1/boards/acme/jobs"
ROBOTS_URL = f"{BASE}/robots.txt"

BANNED_HOST = sorted(BANNED_HOSTS)[0]


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "SCOUT_USER_AGENT": "OfferRadar-Test/0.1 (+tests)",
        "SCOUT_HTTP_TIMEOUT_SECONDS": 5.0,
        "SCOUT_RATE_LIMIT_RPS": 1000.0,
        "SCOUT_RATE_LIMIT_BURST": 100,
        "SCOUT_ROBOTS_CACHE_TTL_SECONDS": 3600,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class Recorder:
    def __init__(self, now: float = 1_000.0) -> None:
        self.slept: list[float] = []
        self.now = now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def clock(self) -> float:
        return self.now


@dataclass
class FakePolicy:
    max_retries: int = 2
    delays: tuple[float, ...] = (0.1, 0.2, 0.4)
    retry_statuses: frozenset[int] = frozenset({429, 500, 503})
    calls: list[tuple[int, float | None]] = field(default_factory=list)

    def should_retry_status(self, status_code: int) -> bool:
        return status_code in self.retry_statuses

    def should_retry_exception(self, exc: BaseException) -> bool:
        return isinstance(exc, httpx.TimeoutException)

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        self.calls.append((attempt, retry_after))
        return self.delays[attempt]


def allow_all_robots(router: respx.Router) -> None:
    router.get(ROBOTS_URL).respond(200, text="User-agent: *\nAllow: /\n")


def build_client(recorder: Recorder, **kwargs: object) -> ScoutHttpClient:
    settings = kwargs.pop("settings", None)
    return ScoutHttpClient(
        settings=settings if isinstance(settings, Settings) else make_settings(),
        sleep=recorder.sleep,
        clock=recorder.clock,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_allowed_get_returns_body_etag_and_json() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(200, json={"jobs": [{"id": 1}]}, headers={"ETag": '"v1"'})
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 200
    assert result.not_modified is False
    assert result.etag == '"v1"'
    assert result.json() == {"jobs": [{"id": 1}]}
    assert recorder.slept == []


async def test_user_agent_is_sent_and_cannot_be_overridden() -> None:
    recorder = Recorder()
    settings = make_settings()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder, settings=settings) as client:
            await client.get(
                JOBS_URL, headers={"User-Agent": "sneaky/9", "Accept": "application/json"}
            )

    sent = route.calls.last.request
    assert sent.headers["user-agent"] == settings.scout_user_agent
    assert sent.headers["accept"] == "application/json"


async def test_robots_disallow_raises_before_the_fetch() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).respond(200, text="User-agent: *\nDisallow: /v1/\n")
        jobs = router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder) as client:
            with pytest.raises(RobotsDisallowedError):
                await client.get(JOBS_URL)

    assert jobs.call_count == 0


async def test_missing_robots_txt_allows_the_fetch() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).respond(404)
        router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 200


async def test_server_error_on_robots_txt_disallows_the_fetch() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).respond(503)
        jobs = router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder) as client:
            with pytest.raises(RobotsDisallowedError):
                await client.get(JOBS_URL)

    assert jobs.call_count == 0


async def test_unreachable_robots_txt_disallows_the_fetch() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).mock(side_effect=httpx.ConnectError("refused"))
        jobs = router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder) as client:
            with pytest.raises(RobotsDisallowedError):
                await client.get(JOBS_URL)

    assert jobs.call_count == 0


async def test_robots_txt_is_fetched_once_per_origin() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        robots = router.get(ROBOTS_URL).respond(200, text="User-agent: *\nAllow: /\n")
        router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder) as client:
            await client.get(JOBS_URL)
            await client.get(JOBS_URL)

    assert robots.call_count == 1


async def test_banned_host_raises_before_any_request() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        async with build_client(recorder) as client:
            with pytest.raises(BannedDomainError):
                await client.get(f"https://{BANNED_HOST}/jobs/123")

    assert len(router.calls) == 0


async def test_redirect_to_banned_host_raises_and_is_not_followed() -> None:
    recorder = Recorder()
    banned_url = f"https://www.{BANNED_HOST}/jobs/123"
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(302, headers={"Location": banned_url})
        async with build_client(recorder) as client:
            with pytest.raises(BannedDomainError):
                await client.get(JOBS_URL)

    assert all(str(call.request.url) != banned_url for call in router.calls)


async def test_redirect_to_an_allowed_host_is_followed() -> None:
    recorder = Recorder()
    final_url = f"{BASE}/v2/boards/acme/jobs"
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(301, headers={"Location": "/v2/boards/acme/jobs"})
        router.get(final_url).respond(200, text='{"jobs": []}')
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 200
    assert result.json() == {"jobs": []}


async def test_304_is_a_success_distinct_from_an_empty_200() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL).respond(304)
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL, etag='"v1"')

    assert route.calls.last.request.headers["if-none-match"] == '"v1"'
    assert result.status_code == 304
    assert result.not_modified is True
    assert result.text == ""
    # The validator survives a 304 that omits the header.
    assert result.etag == '"v1"'


async def test_empty_200_is_not_reported_as_not_modified() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(200, text="[]")
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL, etag='"v1"')

    assert result.not_modified is False
    assert result.json() == []


async def test_retry_loop_uses_the_policy_delays_and_the_injected_sleep() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=2, delays=(0.1, 0.2, 0.4))
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL)
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(503),
            httpx.Response(200, text='{"ok": true}'),
        ]
        async with build_client(recorder, policy=policy) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 200
    assert route.call_count == 3
    assert policy.calls == [(0, None), (1, None)]
    assert recorder.slept == [0.1, 0.2]


async def test_retry_passes_retry_after_through_to_the_policy() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=1, delays=(0.1, 0.2))
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL)
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, text="{}"),
        ]
        async with build_client(recorder, policy=policy) as client:
            await client.get(JOBS_URL)

    assert policy.calls == [(0, 7.0)]


async def test_exhausted_retries_return_the_last_response_rather_than_raising() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=1, delays=(0.1, 0.2))
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL).respond(500)
        async with build_client(recorder, policy=policy) as client:
            result = await client.get(JOBS_URL)

    # §8: a failing source is an alert for the runner, not an exception here.
    assert result.status_code == 500
    assert route.call_count == 2
    assert recorder.slept == [0.1]


async def test_timeout_is_retried_when_the_policy_says_so() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=2, delays=(0.1, 0.2, 0.4))
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL)
        route.side_effect = [
            httpx.ConnectTimeout("too slow"),
            httpx.Response(200, text="{}"),
        ]
        async with build_client(recorder, policy=policy) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 200
    assert recorder.slept == [0.1]


async def test_non_retryable_status_is_returned_without_sleeping() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=3)
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL).respond(404)
        async with build_client(recorder, policy=policy) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 404
    assert route.call_count == 1
    assert recorder.slept == []


async def test_rule_violations_are_never_offered_to_the_retry_policy() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=3)
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).respond(200, text="User-agent: *\nDisallow: /\n")
        async with build_client(recorder, policy=policy) as client:
            with pytest.raises(RobotsDisallowedError):
                await client.get(JOBS_URL)

    assert policy.calls == []
    assert recorder.slept == []


async def test_rate_limiter_admits_a_burst_then_throttles() -> None:
    recorder = Recorder()
    settings = make_settings(SCOUT_RATE_LIMIT_RPS=2.0, SCOUT_RATE_LIMIT_BURST=3)
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder, settings=settings) as client:
            # The clock never advances: the burst of 3 is robots.txt plus two gets.
            for _ in range(4):
                await client.get(JOBS_URL)

    # Debt grows by one token per request at 2 tokens/second.
    assert recorder.slept == [0.5, 1.0]


async def test_crawl_delay_lowers_the_rate_below_the_configured_one() -> None:
    recorder = Recorder()
    settings = make_settings(SCOUT_RATE_LIMIT_RPS=10.0, SCOUT_RATE_LIMIT_BURST=1)
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).respond(200, text="User-agent: *\nAllow: /\nCrawl-delay: 4\n")
        router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder, settings=settings) as client:
            await client.get(JOBS_URL)

    # robots.txt took the only token, so the fetch waits a crawl-delay, not 1/10 s.
    assert recorder.slept == [4.0]


async def test_redis_script_is_registered_and_consulted() -> None:
    recorder = Recorder()
    calls: list[dict[str, object]] = []

    class FakeScript:
        async def __call__(self, keys: list[str], args: list[object]) -> bytes:
            calls.append({"keys": keys, "args": args})
            return b"0"

    class FakeRedis:
        def __init__(self) -> None:
            self.registered: list[str] = []

        def register_script(self, script: str) -> FakeScript:
            self.registered.append(script)
            return FakeScript()

    fake_redis = FakeRedis()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder, redis_client=fake_redis) as client:
            await client.get(JOBS_URL)

    assert len(fake_redis.registered) == 1
    assert "HMGET" in fake_redis.registered[0]
    assert [call["keys"] for call in calls] == [[f"scout:ratelimit:{HOST}"]] * 2
    assert recorder.slept == []


# --- status, 304 and credential handling -----------------------------------


async def test_server_error_is_rejected_by_raise_for_status() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(500, text="<html>upstream is down</html>")
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL)

    assert result.status_code == 500
    with pytest.raises(SourceResponseError):
        result.raise_for_status()


async def test_solicited_304_passes_raise_for_status() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(304)
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL, etag='"v1"')

    result.raise_for_status()
    assert result.not_modified is True


async def test_unsolicited_304_is_a_source_error_not_unchanged() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(304)
        async with build_client(recorder) as client:
            result = await client.get(JOBS_URL)

    assert result.not_modified is False
    with pytest.raises(SourceResponseError):
        result.raise_for_status()


async def test_credentials_are_dropped_when_a_redirect_crosses_origin() -> None:
    recorder = Recorder()
    elsewhere = "https://other.example.test/jobs"
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get("https://other.example.test/robots.txt").respond(404)
        router.get(JOBS_URL).respond(302, headers={"Location": elsewhere})
        final = router.get(elsewhere).respond(200, text="{}")
        async with build_client(recorder) as client:
            await client.get(
                JOBS_URL, headers={"Authorization": "Bearer t0ken", "Accept": "text/plain"}
            )

    sent = final.calls.last.request
    assert "authorization" not in sent.headers
    # Only credentials are dropped; the media type still has to arrive.
    assert sent.headers["accept"] == "text/plain"


async def test_credentials_survive_a_same_origin_redirect() -> None:
    recorder = Recorder()
    final_url = f"{BASE}/v2/boards/acme/jobs"
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        router.get(JOBS_URL).respond(301, headers={"Location": "/v2/boards/acme/jobs"})
        final = router.get(final_url).respond(200, text="{}")
        async with build_client(recorder) as client:
            await client.get(JOBS_URL, headers={"Authorization": "Bearer t0ken"})

    assert final.calls.last.request.headers["authorization"] == "Bearer t0ken"


async def test_an_injected_client_that_follows_redirects_is_rejected() -> None:
    async with httpx.AsyncClient(follow_redirects=True) as unsafe:
        with pytest.raises(ValueError, match="follow_redirects"):
            ScoutHttpClient(settings=make_settings(), client=unsafe)


async def test_retry_after_is_clamped_to_the_retry_window() -> None:
    recorder = Recorder()
    policy = FakePolicy(max_retries=1, delays=(0.1, 0.2))
    settings = make_settings(SCOUT_RETRY_MAX_SECONDS=30.0)
    with respx.mock(assert_all_called=False) as router:
        allow_all_robots(router)
        route = router.get(JOBS_URL)
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "86400"}),
            httpx.Response(200, text="{}"),
        ]
        async with build_client(recorder, policy=policy, settings=settings) as client:
            await client.get(JOBS_URL)

    # A day-long hint cannot be allowed to stall every source behind this one.
    assert policy.calls == [(0, 30.0)]


async def test_rate_limited_robots_txt_stops_the_fetch() -> None:
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS_URL).respond(429)
        jobs = router.get(JOBS_URL).respond(200, text="{}")
        async with build_client(recorder) as client:
            with pytest.raises(RobotsDisallowedError):
                await client.get(JOBS_URL)

    assert jobs.call_count == 0
