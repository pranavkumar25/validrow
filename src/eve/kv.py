"""Tiny async key-value abstraction used for progress counters, the domain
cache, and the per-MX rate limiter.

``InMemoryKV`` is the default so everything runs in one process with no Redis.
``RedisKV`` (lazy ``redis.asyncio``) is the multi-worker production backend —
same interface, so nothing else changes when you swap it in.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


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

        ``now`` is *advisory*. Callers pass a process-local monotonic reading,
        which is meaningless to any other process — two workers sharing a
        bucket have unrelated monotonic origins. Shared implementations must
        therefore ignore it and use their own authoritative clock; only the
        in-process backend can take the caller's word for the time.
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


# Atomic token bucket, evaluated server-side so concurrent workers cannot
# interleave a read and a write and both conclude they had a token.
#
# The clock is Redis's own (`TIME`), not the caller's: the bucket is shared
# across machines, and each machine's monotonic clock starts somewhere else.
#
# KEYS[1] = bucket key
# ARGV[1] = refill rate (tokens/sec), ARGV[2] = capacity (tokens)
# returns  = seconds to wait, as a string ("0" means a token was consumed)
_TOKEN_BUCKET_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local refill = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end

local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill)

local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = (1 - tokens) / refill
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- Drop idle buckets: once a full refill has elapsed the state is equivalent to
-- a fresh key, so keeping it around only leaks memory per MX host.
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill) + 60)
return tostring(wait)
"""


class RedisKV(KV):  # pragma: no cover - requires a live Redis
    """Redis-backed KV. Requires the ``redis`` extra."""

    def __init__(self, url: str):
        from redis import asyncio as aioredis  # lazy

        self._r = aioredis.from_url(url, decode_responses=True)
        self._bucket = self._r.register_script(_TOKEN_BUCKET_LUA)

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
        # `now` is ignored on purpose — see KV.incr_float_window. The script
        # reads Redis's clock, which every worker shares.
        wait = await self._bucket(keys=[key], args=[refill, capacity])
        return float(wait)


_kv: Optional[KV] = None


def get_kv() -> KV:
    """The process-wide KV.

    Redis when ``EVE_REDIS_URL`` is set, in-process otherwise. This matters
    beyond caching: the per-MX rate limiter lives here, so two workers on the
    in-process backend each get a *full* probe budget against the same
    provider — which is the fast path to getting an egress IP blocked.
    """
    global _kv
    if _kv is None:
        from eve.config import get_settings

        s = get_settings()
        if s.redis_configured:
            try:
                import redis  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "EVE_REDIS_URL is set but the 'redis' extra is not installed. "
                    "Run: pip install -e '.[redis]'"
                ) from exc
            logger.info("kv: redis")
            _kv = RedisKV(s.redis_url)
        else:
            logger.info("kv: in-process")
            _kv = InMemoryKV()
    return _kv


def set_kv(kv: KV) -> None:
    global _kv
    _kv = kv
