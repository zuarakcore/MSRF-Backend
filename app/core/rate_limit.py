"""Redis-backed rate limiting (moving window).

Two ways to use it:

* `RateLimit(...)` as a route dependency, keyed by client IP or user.
* `enforce(...)` inside a route or service when the key comes from the body
  (for example login attempts per email address).

The client IP is `request.client.host`. Behind a proxy, run uvicorn with
`--proxy-headers --forwarded-allow-ips=<proxy IPs>` so that value is the real client;
never parse X-Forwarded-For here, or attackers can spoof their way around limits.
"""

import logging
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import Request
from limits import RateLimitItem, parse
from limits.aio.strategies import MovingWindowRateLimiter
from limits.storage import storage_from_string

from app.core.config import get_settings
from app.core.errors import RateLimited, ServiceUnavailable

logger = logging.getLogger(__name__)

KeyFunc = Callable[[Request], str]


@lru_cache
def _limiter() -> MovingWindowRateLimiter:
    # implementation="redispy": use the `redis` package already installed (default is coredis).
    storage: Any = storage_from_string("async+" + get_settings().REDIS_URL, implementation="redispy")
    return MovingWindowRateLimiter(storage)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def enforce(scope: str, rate: str, *identifiers: str, fail_open: bool = False) -> None:
    """Count one hit for `identifiers` under `scope`; raise RateLimited when over `rate`.

    fail_open=False (default) returns 503 if Redis is unreachable. Use it for login and
    public forms, where an outage must not disable brute-force protection.
    """
    if not get_settings().RATE_LIMIT_ENABLED:
        return
    item: RateLimitItem = parse(rate)
    try:
        limiter = _limiter()
        allowed = await limiter.hit(item, scope, *identifiers)
        if allowed:
            return
        stats = await limiter.get_window_stats(item, scope, *identifiers)
    except Exception as exc:
        logger.error("Rate limiter unavailable", extra={"scope": scope, "error": str(exc)})
        if fail_open:
            return
        raise ServiceUnavailable() from exc
    raise RateLimited(retry_after=int(stats.reset_time - time.time()) + 1)


class RateLimit:
    """Route dependency: `dependencies=[Depends(RateLimit("refresh", "30/minute"))]`."""

    def __init__(self, scope: str, rate: str, *, key: KeyFunc = client_ip, fail_open: bool = False) -> None:
        self.scope = scope
        self.rate = rate
        self.key = key
        self.fail_open = fail_open

    # Must be `async def`: FastAPI inspects the callable, and a sync __call__ returning a
    # coroutine would run in a threadpool and never be awaited (the limit silently vanishes).
    async def __call__(self, request: Request) -> None:
        await enforce(self.scope, self.rate, self.key(request), fail_open=self.fail_open)
