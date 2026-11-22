from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import redis.asyncio as redis
from protego import Protego

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.scout.blocklist import BannedDomainError, assert_allowed
from offerradar_agents.scout.retry import RetryPolicy

logger = logging.getLogger(__name__)

MAX_REDIRECTS: Final = 5

# 304 is a 3xx but carries no Location: it is a cache hit, not a redirect.
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})

OK: Final = 200
REDIRECTION: Final = 300
NOT_MODIFIED: Final = 304
CLIENT_ERROR: Final = 400
TOO_MANY_REQUESTS: Final = 429
SERVER_ERROR: Final = 500

# Dropped across origins: httpx does this itself, a hand-rolled walk must too.
SENSITIVE_HEADERS: Final = frozenset({"authorization", "cookie", "proxy-authorization"})

_ALLOW_ALL: Final = Protego.parse("")
# RFC 9309 2.3.1.4: 5xx or unreachable means disallow, so rule 3 fails closed.
_DISALLOW_ALL: Final = Protego.parse("User-agent: *\nDisallow: /\n")
_RATE_LIMIT_KEY_PREFIX: Final = "scout:ratelimit:"

# Reserves the slot and returns the wait; tokens go negative as queued debt.
_RESERVE_LUA: Final = """
local key = KEYS[1]
-- `now` is the caller's clock, not Redis TIME: one host, so the skew is zero.
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local state = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(state[1])
local updated_at = tonumber(state[2])
if tokens == nil or updated_at == nil then
  tokens = burst
  updated_at = now
end

local elapsed = now - updated_at
if elapsed < 0 then elapsed = 0 end
tokens = math.min(burst, tokens + elapsed * rate)
tokens = tokens - 1

local wait = 0
if tokens < 0 then
  wait = -tokens / rate
end

redis.call('HSET', key, 'tokens', tokens, 'updated_at', now)
redis.call('PEXPIRE', key, math.ceil((burst / rate + wait) * 1000) + 1000)
return tostring(wait)
"""


class RobotsDisallowedError(RuntimeError):
    pass


class RedirectLimitError(RuntimeError):
    pass


class SourceResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    text: str
    etag: str | None
    not_modified: bool

    def raise_for_status(self) -> None:
        if OK <= self.status_code < REDIRECTION or self.not_modified:
            return
        raise SourceResponseError(f"source answered {self.status_code}")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass
class _CachedRobots:
    rules: Protego
    expires_at: float


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _safe_target(url: str) -> str:
    parsed = httpx.URL(url)
    return f"{parsed.host}{parsed.path}"


def _retry_after_seconds(response: httpx.Response, cap: float) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(cap, max(0.0, float(raw)))
    except ValueError:
        pass
    try:
        deadline = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return min(cap, max(0.0, (deadline - datetime.now(UTC)).total_seconds()))


class ScoutHttpClient:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        redis_client: redis.Redis | None = None,
        policy: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._policy = policy
        self._sleep = sleep
        self._clock = clock
        if client is not None and client.follow_redirects:
            raise ValueError(
                "ScoutHttpClient needs follow_redirects=False: httpx would follow a hop "
                "before assert_allowed saw it, which defeats rule 2"
            )
        self._owns_client = client is None
        self._client = client if client is not None else self._build_client()

        self._reserve_script = (
            redis_client.register_script(_RESERVE_LUA) if redis_client is not None else None
        )
        # No Redis: per-process only, so rule 3 holds for one fetching process.
        self._buckets: dict[str, _Bucket] = {}

        self._robots: dict[str, _CachedRobots] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.scout_http_timeout_seconds,
            follow_redirects=False,  # rule 2: every hop is checked by hand
        )

    async def __aenter__(self) -> ScoutHttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResult:
        attempt = 0
        while True:
            try:
                result, retry_after = await self._follow(url, etag=etag, headers=headers)
            except (BannedDomainError, RobotsDisallowedError, RedirectLimitError):
                # A rule 2 or rule 3 refusal cannot change on a retry.
                raise
            except Exception as exc:
                policy = self._policy
                if (
                    policy is None
                    or attempt >= policy.max_retries
                    or not policy.should_retry_exception(exc)
                ):
                    raise
                logger.warning(
                    "scout request failed, retrying",
                    extra={
                        "target": _safe_target(url),
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                await self._sleep(policy.delay_for(attempt))
                attempt += 1
                continue

            policy = self._policy
            if policy is None or attempt >= policy.max_retries:
                return result
            if not policy.should_retry_status(result.status_code):
                return result
            logger.warning(
                "scout request returned a retryable status, retrying",
                extra={
                    "target": _safe_target(url),
                    "attempt": attempt,
                    "status": result.status_code,
                },
            )
            await self._sleep(policy.delay_for(attempt, retry_after))
            attempt += 1

    async def _follow(
        self,
        url: str,
        *,
        etag: str | None,
        headers: Mapping[str, str] | None,
    ) -> tuple[HttpResult, float | None]:
        current = url
        origin = _origin(url)
        carried: Mapping[str, str] = dict(headers or {})
        cap = self._settings.scout_retry_max_seconds
        for _ in range(MAX_REDIRECTS + 1):
            assert_allowed(current)
            response = await self._request(current, etag=etag, headers=carried)
            location = response.headers.get("location")
            if response.status_code not in REDIRECT_STATUSES or location is None:
                return self._to_result(response, etag), _retry_after_seconds(response, cap)
            current = str(httpx.URL(current).join(location))
            if _origin(current) != origin:
                # A hand-rolled walk loses httpx's cross-origin stripping (§9).
                carried = {
                    name: value
                    for name, value in carried.items()
                    if name.lower() not in SENSITIVE_HEADERS
                }
                origin = _origin(current)
        raise RedirectLimitError(f"more than {MAX_REDIRECTS} redirects from {_safe_target(url)}")

    async def _request(
        self,
        url: str,
        *,
        etag: str | None,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        user_agent = self._settings.scout_user_agent
        rules = await self._robots_for(url)
        if not rules.can_fetch(url, user_agent):
            raise RobotsDisallowedError(f"robots.txt disallows {_safe_target(url)}")
        await self._throttle(url, rules.crawl_delay(user_agent))
        return await self._client.get(url, headers=self._headers(etag, headers))

    def _headers(self, etag: str | None, extra: Mapping[str, str] | None) -> dict[str, str]:
        merged: dict[str, str] = dict(extra or {})
        if etag is not None:
            merged["If-None-Match"] = etag
        # Rule 3's User-Agent is a requirement, not a default a caller overrides.
        merged["User-Agent"] = self._settings.scout_user_agent
        return merged

    def _to_result(self, response: httpx.Response, sent_etag: str | None) -> HttpResult:
        if response.status_code == NOT_MODIFIED:
            # Only a 304 we asked for is a cache hit; the sent ETag still validates.
            return HttpResult(
                status_code=NOT_MODIFIED,
                text="",
                etag=response.headers.get("etag") or sent_etag,
                not_modified=sent_etag is not None,
            )
        return HttpResult(
            status_code=response.status_code,
            text=response.text,
            etag=response.headers.get("etag"),
            not_modified=False,
        )

    async def _robots_for(self, url: str) -> Protego:
        origin = _origin(url)
        cached = self._robots.get(origin)
        if cached is not None and self._clock() < cached.expires_at:
            return cached.rules

        lock = self._robots_locks.get(origin)
        if lock is None:
            lock = self._robots_locks[origin] = asyncio.Lock()
        async with lock:
            cached = self._robots.get(origin)
            if cached is not None and self._clock() < cached.expires_at:
                return cached.rules
            rules = await self._fetch_robots(origin)
            ttl = self._settings.scout_robots_cache_ttl_seconds
            self._robots[origin] = _CachedRobots(rules, self._clock() + ttl)
            return rules

    async def _fetch_robots(self, origin: str) -> Protego:
        current = f"{origin}/robots.txt"
        for _ in range(MAX_REDIRECTS + 1):
            assert_allowed(current)
            # An outbound request too, and it cannot gate on its own contents.
            await self._throttle(current, None)
            try:
                response = await self._client.get(
                    current, headers={"User-Agent": self._settings.scout_user_agent}
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "robots.txt unreachable, treating as disallow-all",
                    extra={"origin": origin, "error": type(exc).__name__},
                )
                return _DISALLOW_ALL

            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUSES and location is not None:
                current = str(httpx.URL(current).join(location))
                continue

            if response.status_code >= SERVER_ERROR:
                return _DISALLOW_ALL
            if response.status_code == TOO_MANY_REQUESTS:
                # 429 is a 4xx under RFC 9309, but crawling through it defeats rule 3.
                return _DISALLOW_ALL
            if response.status_code >= CLIENT_ERROR:
                # RFC 9309 2.3.1.3: an unavailable robots.txt means no restrictions.
                return _ALLOW_ALL
            return Protego.parse(response.text)

        logger.warning("robots.txt redirected too many times", extra={"origin": origin})
        return _DISALLOW_ALL

    async def _throttle(self, url: str, crawl_delay: float | None) -> None:
        host = httpx.URL(url).host
        rate = self._settings.scout_rate_limit_rps
        if crawl_delay:
            # A crawl-delay is a floor on the interval, so a ceiling on the rate.
            rate = min(rate, 1.0 / crawl_delay)
        burst = self._settings.scout_rate_limit_burst

        wait = await self._reserve(host, rate, burst)
        if wait > 0:
            await self._sleep(wait)

    async def _reserve(self, host: str, rate: float, burst: int) -> float:
        script = self._reserve_script
        if script is None:
            return self._reserve_in_process(host, rate, burst)
        raw = await script(
            keys=[f"{_RATE_LIMIT_KEY_PREFIX}{host}"], args=[rate, burst, self._clock()]
        )
        return float(raw)

    def _reserve_in_process(self, host: str, rate: float, burst: int) -> float:
        now = self._clock()
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = _Bucket(tokens=float(burst), updated_at=now)

        elapsed = max(0.0, now - bucket.updated_at)
        tokens = min(float(burst), bucket.tokens + elapsed * rate) - 1.0
        self._buckets[host] = _Bucket(tokens=tokens, updated_at=now)
        return -tokens / rate if tokens < 0 else 0.0
