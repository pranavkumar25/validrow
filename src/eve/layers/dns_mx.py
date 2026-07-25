"""Layer 4 — DNS / MX resolution with an in-memory TTL cache.

Answers "can this domain receive mail?". Results are cached per-domain because
a large list is only tens of thousands of unique domains — this cache is the
performance keystone the architecture calls out. In M1+ this same shape is
backed by Redis + the ``domain_cache`` table.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import dns.exception
import dns.resolver

_DEFAULT_TTL = 3600.0  # seconds


@dataclass
class MxResult:
    domain: str
    mx_found: bool
    mx_hosts: list[str] = field(default_factory=list)
    error: str | None = None


class _TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, MxResult]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> MxResult | None:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires, value = entry
            if expires < time.monotonic():
                self._store.pop(key, None)
                return None
            return value

    def put(self, key: str, value: MxResult, ttl: float) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = _TTLCache()


def clear_cache() -> None:
    _cache.clear()


def resolve_mx(domain: str, timeout: float = 5.0, ttl: float = _DEFAULT_TTL) -> MxResult:
    """Resolve MX (with A/AAAA fallback per RFC 5321 §5). Cached per domain."""
    domain = domain.lower().strip().rstrip(".")
    cached = _cache.get(domain)
    if cached is not None:
        return cached

    result = _resolve_uncached(domain, timeout)
    _cache.put(domain, result, ttl)
    return result


def _resolve_uncached(domain: str, timeout: float) -> MxResult:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout

    try:
        answers = resolver.resolve(domain, "MX")
        hosts = sorted(
            (str(r.exchange).rstrip(".").lower(), int(r.preference)) for r in answers  # type: ignore[attr-defined]
        )
        mx_hosts = [h for h, _pref in sorted(hosts, key=lambda x: x[1])]
        mx_hosts = [h for h in mx_hosts if h]  # drop null MX ("."), if any
        if mx_hosts:
            return MxResult(domain=domain, mx_found=True, mx_hosts=mx_hosts)
        # Empty/`.` MX => explicitly refuses mail.
        return MxResult(domain=domain, mx_found=False, error="null_mx")
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        # No MX — fall back to A/AAAA (implicit MX).
        for rtype in ("A", "AAAA"):
            try:
                resolver.resolve(domain, rtype)
                return MxResult(domain=domain, mx_found=True, mx_hosts=[domain])
            except dns.exception.DNSException:
                continue
        return MxResult(domain=domain, mx_found=False, error="no_mx")
    except dns.exception.Timeout:
        return MxResult(domain=domain, mx_found=False, error="timeout")
    except dns.exception.DNSException as exc:
        return MxResult(domain=domain, mx_found=False, error=f"dns_error:{exc.__class__.__name__}")
