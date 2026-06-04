"""Request dependencies: API-key auth and in-process rate limiting."""

from __future__ import annotations

import hmac
import threading
import time
from collections import OrderedDict, deque

from fastapi import Header, HTTPException, Request, status

from finoptic.config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> str | None:
    """Enforce ``X-API-Key`` when ``FINOPTIC_REQUIRE_AUTH`` is enabled."""
    settings = get_settings()
    if not settings.require_auth:
        return None
    # Constant-time comparison to avoid leaking key material via timing.
    if x_api_key and any(hmac.compare_digest(x_api_key, k) for k in settings.api_keys):
        return x_api_key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key"
    )


class RateLimiter:
    """Thread-safe sliding-window limiter with a bounded key space (LRU eviction)."""

    def __init__(self, per_minute: int, max_keys: int = 10_000) -> None:
        self.per_minute = per_minute
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            else:
                self._hits.move_to_end(key)  # mark as most-recently-used
            while dq and dq[0] <= now - window:
                dq.popleft()
            # Bound memory: evict the least-recently-used key (never the current one).
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            if len(dq) >= self.per_minute:
                return False
            dq.append(now)
            return True


_limiter: RateLimiter | None = None


def _get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().rate_limit_per_minute)
    return _limiter


def rate_limit(request: Request) -> None:
    """Reject callers exceeding the per-minute budget with HTTP 429.

    Keys on the client IP by default (a bounded space). Only when auth is required does it
    key on the API key — which is validated by ``require_api_key``, so it can't be an
    unbounded attacker-controlled value.
    """
    settings = get_settings()
    if settings.require_auth:
        key = request.headers.get("x-api-key") or "anon"
    else:
        key = request.client.host if request.client else "anon"
    if not _get_limiter().allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
        )
