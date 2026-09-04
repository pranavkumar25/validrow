"""The DNSBL monitor, and the conditions under which it actually runs.

No test here resolves a real name: `check_dnsbl` is patched out. What is being
asserted is the wiring — that a listing cools the IP and alerts, and that the
monitor starts in exactly the configurations where there is something to watch.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import eve.smtp_infra as smtp_infra
from eve.config import Settings
from eve.smtp_infra import blacklist as bl
from eve.smtp_infra.ip_pool import IpPool
from eve.smtp_infra.service import SmtpService


@pytest.fixture(autouse=True)
async def _no_leaked_monitor():
    """Every test leaves the module-level prober and monitor as it found them."""
    yield
    await smtp_infra.stop_blacklist_monitor()
    smtp_infra.set_async_prober(None)


def _settings(**over) -> Settings:
    base = {
        "enable_smtp": True,
        "smtp_egress_ips": "203.0.113.10,203.0.113.11",
        "dnsbl_interval_seconds": 0.01,
    }
    base.update(over)
    return Settings(**base)


# --- scan_once -----------------------------------------------------------


async def test_listed_ip_is_cooled_and_alerted(monkeypatch):
    pool = IpPool(["203.0.113.10", "203.0.113.11"])
    listings = {"203.0.113.10": ["zen.spamhaus.org"]}

    async def fake_check(ip, zones=None, timeout=5.0):
        return listings.get(ip, [])

    monkeypatch.setattr(bl, "check_dnsbl", fake_check)

    alerts = []

    async def alert(ip, zones):
        alerts.append((ip, zones))

    result = await bl.BlacklistMonitor(pool, alert=alert).scan_once()

    assert result == listings
    assert alerts == [("203.0.113.10", ["zen.spamhaus.org"])]

    # Cooled means out of rotation: acquire() only ever returns the clean one.
    assert {await pool.acquire() for _ in range(4)} == {"203.0.113.11"}


async def test_clean_pool_changes_nothing(monkeypatch):
    pool = IpPool(["203.0.113.10"])

    async def fake_check(ip, zones=None, timeout=5.0):
        return []

    monkeypatch.setattr(bl, "check_dnsbl", fake_check)

    assert await bl.BlacklistMonitor(pool).scan_once() == {}
    assert pool.snapshot()[0].cooldown_until == 0.0
    assert await pool.acquire() == "203.0.113.10"


async def test_a_failed_scan_does_not_end_the_loop(monkeypatch):
    """One resolver failure must not take monitoring down until the next deploy."""
    calls = []

    async def flaky(ip, zones=None, timeout=5.0):
        calls.append(ip)
        if len(calls) == 1:
            raise RuntimeError("resolver exploded")
        return []

    monkeypatch.setattr(bl, "check_dnsbl", flaky)

    monitor = bl.BlacklistMonitor(IpPool(["203.0.113.10"]))
    task = asyncio.create_task(monitor.run_forever(0.01))
    deadline = time.monotonic() + 2.0
    while len(calls) < 3 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 3, "the loop stopped after the failure"


# --- start/stop wiring ---------------------------------------------------


async def test_monitor_runs_when_smtp_and_egress_ips_are_configured(monkeypatch):
    seen = []

    async def fake_check(ip, zones=None, timeout=5.0):
        seen.append(ip)
        return []

    monkeypatch.setattr(bl, "check_dnsbl", fake_check)

    s = _settings()
    smtp_infra.set_async_prober(SmtpService.from_settings(s))
    task = smtp_infra.start_blacklist_monitor(s)

    assert task is not None
    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert set(seen) <= {"203.0.113.10", "203.0.113.11"} and seen

    await smtp_infra.stop_blacklist_monitor()
    assert task.cancelled() or task.done()


async def test_start_is_idempotent(monkeypatch):
    async def fake_check(ip, zones=None, timeout=5.0):
        return []

    monkeypatch.setattr(bl, "check_dnsbl", fake_check)

    s = _settings()
    smtp_infra.set_async_prober(SmtpService.from_settings(s))
    first = smtp_infra.start_blacklist_monitor(s)
    assert smtp_infra.start_blacklist_monitor(s) is first


@pytest.mark.parametrize(
    "over, why",
    [
        ({"enable_smtp": False}, "no probes leave this process"),
        ({"dnsbl_enabled": False}, "monitoring is someone else's job"),
        ({"smtp_egress_ips": ""}, "probes leave on the OS default route"),
    ],
)
async def test_monitor_does_not_run_when_there_is_nothing_to_watch(over, why):
    s = _settings(**over)
    if s.enable_smtp:
        smtp_infra.set_async_prober(SmtpService.from_settings(s))
    assert smtp_infra.start_blacklist_monitor(s) is None, why


async def test_stop_is_safe_when_nothing_is_running():
    await smtp_infra.stop_blacklist_monitor()  # must not raise


def test_configured_zones_override_the_defaults():
    assert Settings(dnsbl_zones="").dnsbl_zone_list == []
    assert Settings(dnsbl_zones=" bl.example.org , dbl.example.net ").dnsbl_zone_list == [
        "bl.example.org",
        "dbl.example.net",
    ]
