"""Tiny async key-value abstraction used for progress counters, the domain
cache, and the per-MX rate limiter.

``InMemoryKV`` is the default so everything runs in one process with no Redis.
``RedisKV`` (lazy ``redis.asyncio``) is the multi-worker production backend —
same interface, so nothing else changes when you swap it in.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Optional


class KV(ABC):
    @abstractmethod
    async def incr(self, key: str, amount: int = 1) -> int:
        ...

    @abstractmethod
    async def get_int(self, key: str) -> int:
        ...

    @abstractmethod
    async def set_json(self, key: str, value: dict, ttl: Optional[float] = None) -> None:
        ...

    @abstractmethod
    async def get_json(self, key: str) -> Optional[dict]:
        ...

    @abstractmethod
    async def incr_float_window(self, key: str, now: float, refill: float, capacity: float) -> float:
        """Token-bucket helper: return the wait (seconds) before a token is free.

        0.0 means a token was consumed immediately.
        """


class InMemoryKV(KV):
    def __init__(self) -> None:
        self._ints: dict[str, int] = {}
        self._json: dict[str, tuple[Optional[float], dict]] = {}
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = asyncio.Lock()

    async def incr(self, key: str, amount: int = 1) -> int:
        async with self._lock:
            self._ints[key] = self._ints.get(key, 0) + amount
            return self._ints[key]

    async def get_int(self, key: str) -> int:
        return self._ints.get(key, 0)

    async def set_json(self, key: str, value: dict, ttl: Optional[float] = None) -> None:
        expires = time.monotonic() + ttl if ttl else None
        self._json[key] = (expires, value)

    async def get_json(self, key: str) -> Optional[dict]:
        entry = self._json.get(key)
        if not entry:
            return None
        expires, value = entry
        if expires is not None and expires < time.monotonic():
            self._json.pop(key, None)
            return None
        return value

    async def incr_float_window(
        self, key: str, now: float, refill: float, capacity: float
    ) -> float:
        async with self._lock:
            tokens, last = self._buckets.get(key, (capacity, now))
            # Refill based on elapsed time.
            tokens = min(capacity, tokens + (now - last) * refill)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return 0.0
            # Not enough — compute wait until one token accrues.
            wait = (1.0 - tokens) / refill
            self._buckets[key] = (tokens, now)
            return wait


class RedisKV(KV):  # pragma: no cover - requires a live Redis
    """Redis-backed KV. Requires the ``redis`` extra."""

    def __init__(self, url: str):
        from redis import asyncio as aioredis  # lazy

        self._r = aioredis.from_url(url, decode_responses=True)

    async def incr(self, key: str, amount: int = 1) -> int:
        return int(await self._r.incrby(key, amount))

    async def get_int(self, key: str) -> int:
        v = await self._r.get(key)
        return int(v) if v else 0

    async def set_json(self, key: str, value: dict, ttl: Optional[float] = None) -> None:
        import json

        await self._r.set(key, json.dumps(value), ex=int(ttl) if ttl else None)

    async def get_json(self, key: str) -> Optional[dict]:
        import json

        v = await self._r.get(key)
        return json.loads(v) if v else None

    async def incr_float_window(
        self, key: str, now: float, refill: float, capacity: float
    ) -> float:
        # A production impl would use a Lua script for atomicity; kept simple here.
        raise NotImplementedError("use a Lua token-bucket for Redis in production")


_kv: Optional[KV] = None


def get_kv() -> KV:
    global _kv
    if _kv is None:
        _kv = InMemoryKV()
    return _kv


def set_kv(kv: KV) -> None:
    global _kv
    _kv = kv
