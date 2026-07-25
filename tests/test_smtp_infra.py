"""Unit tests for the SMTP infra pieces (no network)."""
from __future__ import annotations

from eve.kv import InMemoryKV
from eve.smtp_infra.greylist import is_temporary, looks_greylisted
from eve.smtp_infra.ip_pool import IpPool
from eve.smtp_infra.prober import SmtpReply
from eve.smtp_infra.providers import GENERIC, strategy_for
from eve.smtp_infra.rate_limiter import PerMxRateLimiter
from eve.smtp_infra.service import SmtpService


def _svc() -> SmtpService:
    return SmtpService(
        port=25,
        helo="h",
        mail_from="m@h",
        timeout=5.0,
        rate_limiter=PerMxRateLimiter(InMemoryKV(), 100.0),
        ip_pool=IpPool([]),
    )


# --- provider heuristics + response mapping ------------------------------


def test_google_provider_not_trusted():
    assert strategy_for(["aspmx.l.google.com"], "gmail.com").trust_rcpt is False
    assert strategy_for(["mx.acme.io"], "acme.io").trust_rcpt is True


def test_map_outcomes():
    svc = _svc()
    google = strategy_for(["aspmx.l.google.com"], "gmail.com")
    # A 250 from an accept-all provider must NOT be reported as a confident valid.
    assert svc._map(SmtpReply(250, "OK"), google).outcome == "unknown"
    assert svc._map(SmtpReply(250, "OK"), google).detail == "provider_unreliable"
    assert svc._map(SmtpReply(250, "OK"), GENERIC).outcome == "valid"
    assert svc._map(SmtpReply(550, "No such user"), GENERIC).outcome == "invalid"
    assert svc._map(SmtpReply(451, "greylisted, try later"), GENERIC).outcome == "unknown"
    assert svc._map(SmtpReply(451, "greylisted, try later"), GENERIC).detail == "greylisted"
    assert svc._map(SmtpReply(554, "blocked by policy"), GENERIC).detail == "antispam_block"


# --- greylist ------------------------------------------------------------


def test_greylist_detection():
    assert is_temporary(451)
    assert is_temporary(250, "please try again later")
    assert not is_temporary(250, "OK")
    assert looks_greylisted(451, "greylisted")


# --- token-bucket rate limiter -------------------------------------------


async def test_token_bucket_throttles():
    kv = InMemoryKV()
    # capacity 1, refill 1/s: first token free, second must wait.
    first = await kv.incr_float_window("k", now=0.0, refill=1.0, capacity=1.0)
    second = await kv.incr_float_window("k", now=0.0, refill=1.0, capacity=1.0)
    assert first == 0.0
    assert second > 0.0
    # After enough time passes, a token is available again.
    later = await kv.incr_float_window("k", now=5.0, refill=1.0, capacity=1.0)
    assert later == 0.0


# --- IP pool -------------------------------------------------------------


async def test_ip_pool_rotation_and_cooldown():
    pool = IpPool(["1.1.1.1", "2.2.2.2"], warmup_daily_cap=1000)
    a = await pool.acquire()
    b = await pool.acquire()
    assert {a, b} == {"1.1.1.1", "2.2.2.2"}  # rotates across IPs

    await pool.report("1.1.1.1", "block")  # cooled out of rotation
    for _ in range(5):
        assert await pool.acquire() != "1.1.1.1"


async def test_ip_pool_warmup_cap():
    pool = IpPool(["1.1.1.1"], warmup_daily_cap=2)
    assert await pool.acquire() == "1.1.1.1"
    assert await pool.acquire() == "1.1.1.1"
    assert await pool.acquire() is None  # daily cap hit while warming


async def test_empty_ip_pool_returns_none():
    assert await IpPool([]).acquire() is None
