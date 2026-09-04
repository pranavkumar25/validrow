"""Per-client rate limiting for the expensive endpoints.

Built on the same KV token bucket the SMTP prober uses, which means it is
per-process on the in-memory backend and genuinely shared once Redis is
configured — the distinction that matters the moment there is more than one
API instance behind a load balancer.

This is a coarse backstop against a single client hammering the API, not a
billing quota: it counts requests, not addresses validated. Metering belongs
with the workspace, once there is a workspace to bill.
"""
# NB: deliberately no `from __future__ import annotations`. FastAPI resolves a
# dependency's parameter types at import; with deferred annotations it fails to
# resolve `Request` and falls back to treating it as a query parameter, which
# turns every call into a 422.
import logging
import time

from fastapi import HTTPException, Request

from eve.config import get_settings
from eve.kv import get_kv
from eve.tenancy import current_workspace_id

logger = logging.getLogger(__name__)


def client_key(request: Request) -> str:
    """Who to count against.

    ``X-Forwarded-For`` is trusted only because this is expected to sit behind
    a proxy that sets it; exposed directly, a client could spoof it and get a
    fresh bucket per request. That is a deployment requirement, not a detail.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check(request: Request, cost: int = 1) -> None:
    """Raise 429 when the caller is over budget. No-op when disabled."""
    s = get_settings()
    limit = s.rate_limit_per_minute
    if limit <= 0:
        return

    key = f"ratelimit:api:{current_workspace_id()}:{client_key(request)}"
    kv = get_kv()
    for _ in range(max(1, cost)):
        wait = await kv.incr_float_window(key, time.monotonic(), limit / 60.0, float(limit))
        if wait > 0.0:
            logger.info("rate limited %s (retry in %.1fs)", key, wait)
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(max(1, int(wait) + 1))},
            )


class RateLimit:
    """FastAPI dependency: ``Depends(RateLimit())``, or ``RateLimit(cost=5)``."""

    def __init__(self, cost: int = 1):
        self.cost = cost

    async def __call__(self, request: Request) -> None:
        await check(request, self.cost)
