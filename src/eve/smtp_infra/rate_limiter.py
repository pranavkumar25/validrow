"""Per-MX token-bucket rate limiter.

Keyed by destination MX host so we spread probes over time and never hammer one
provider (the fast path to getting an egress IP blocked). Backed by the KV
abstraction, so it's in-process locally and Redis-backed in production.
"""
from __future__ import annotations

import asyncio
import time

from eve.kv import KV


class PerMxRateLimiter:
    def __init__(self, kv: KV, rate: float, capacity: float | None = None):
        self.kv = kv
        self.rate = max(0.1, rate)
        self.capacity = capacity if capacity is not None else max(1.0, rate)

    async def acquire(self, mx_host: str) -> None:
        """Block until a token is available for ``mx_host``."""
        key = f"ratelimit:mx:{mx_host.lower()}"
        for _ in range(10_000):  # safety bound
            wait = await self.kv.incr_float_window(
                key, time.monotonic(), self.rate, self.capacity
            )
            if wait <= 0.0:
                return
            await asyncio.sleep(min(wait, 5.0))
