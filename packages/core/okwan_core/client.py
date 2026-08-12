"""Rate-limited, retrying async HTTP client shared by all connectors."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .errors import RateLimitedError, UpstreamError


@dataclass(frozen=True, slots=True)
class RateLimitProfile:
    """Client-side throttle + retry policy for one upstream API."""

    requests_per_second: float = 10.0
    burst: int = 5
    max_attempts: int = 4


class _TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity, self._tokens + (now - self._updated) * self._rate
            )
            self._updated = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitedError):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, UpstreamError):
        return exc.status >= 500
    return False


class OkwanClient:
    """Thin wrapper over httpx.AsyncClient: throttle, retry, error mapping."""

    def __init__(
        self, base_url: str, auth: httpx.Auth, rate_limit: RateLimitProfile
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, auth=auth, timeout=30.0)
        self._bucket = _TokenBucket(rate_limit.requests_per_second, rate_limit.burst)
        self._profile = rate_limit

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._profile.max_attempts),
            wait=wait_exponential(multiplier=0.5, max=20),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                await self._bucket.acquire()
                resp = await self._client.request(method, path, json=json, params=params)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                    await asyncio.sleep(retry_after)
                    raise RateLimitedError(retry_after=retry_after)
                if resp.status_code >= 400:
                    raise UpstreamError(status=resp.status_code, body=resp.text[:2000])
                return resp.json() if resp.content else {}
        raise RuntimeError("unreachable")  # pragma: no cover

    async def get(self, path: str, **kw: Any) -> dict[str, Any]:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> dict[str, Any]:
        return await self.request("POST", path, **kw)

    async def aclose(self) -> None:
        await self._client.aclose()
