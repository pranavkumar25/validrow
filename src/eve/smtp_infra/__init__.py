"""M2 · SMTP Infrastructure — the in-house mailbox-verification subsystem.

Composed of:
  * ``prober``       — the raw aiosmtplib conversation (never sends DATA)
  * ``providers``    — per-provider heuristics (Gmail/Outlook/Yahoo lie to probes)
  * ``rate_limiter`` — per-MX token bucket (don't hammer one provider)
  * ``ip_pool``      — egress IP reputation / rotation / cooldown / warm-up
  * ``blacklist``    — DNSBL monitoring + alerting
  * ``service``      — wires them into an async prober the pipeline can call

This module is the composition root: it owns the process-wide prober and the
background DNSBL scan that keeps that prober's IP pool honest.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from eve.config import get_settings
from eve.jobs.pipeline import AsyncProber, NullAsyncProber
from eve.smtp_infra.service import SmtpService

logger = logging.getLogger(__name__)

_prober: AsyncProber | None = None
_monitor_task: asyncio.Task | None = None


def get_async_prober() -> AsyncProber:
    """Return the configured prober. ``NullAsyncProber`` when SMTP is disabled."""
    global _prober
    if _prober is not None:
        return _prober
    s = get_settings()
    if not s.enable_smtp:
        return NullAsyncProber()
    _prober = SmtpService.from_settings(s)
    return _prober


def set_async_prober(prober: Optional[AsyncProber]) -> None:
    """Override the process-wide prober. ``None`` restores the configured one."""
    global _prober
    _prober = prober


async def _alert_listed(ip: str, zones: list[str]) -> None:
    """What a DNSBL hit does besides pulling the IP from rotation.

    ERROR rather than WARNING on purpose: a listed egress IP invalidates every
    verdict it produces, so this is the log line that should page someone. It
    reaches Sentry through the logging integration when a DSN is configured —
    there is no separate alerting channel to configure, and inventing one that
    silently drops messages would be worse than the log.
    """
    logger.error(
        "egress IP %s is listed on %s — pulled from rotation until its cooldown "
        "expires. Delist it, or remove it from EVE_SMTP_EGRESS_IPS.",
        ip,
        ", ".join(zones),
        extra={"egress_ip": ip, "dnsbl_zones": zones},
    )


def start_blacklist_monitor(settings=None) -> Optional[asyncio.Task]:
    """Start the periodic DNSBL scan of the egress pool. Idempotent.

    Returns the running task, or ``None`` when there is nothing to watch:

    * SMTP disabled — no probes leave this process at all.
    * ``EVE_DNSBL_ENABLED=false`` — someone monitors reputation elsewhere.
    * no ``EVE_SMTP_EGRESS_IPS`` — probes leave on the OS default route, which
      is not an address this process is entitled to cool down, and scanning it
      would report on a host that rotation cannot avoid anyway.

    Call it from every process that probes: the pool lives in memory, so the
    API and each worker each scan their own.
    """
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return _monitor_task

    s = settings or get_settings()
    if not (s.enable_smtp and s.dnsbl_enabled):
        return None

    prober = get_async_prober()
    if not isinstance(prober, SmtpService) or not prober.ip_pool.snapshot():
        return None

    from eve.smtp_infra.blacklist import BlacklistMonitor

    monitor = BlacklistMonitor(prober.ip_pool, zones=s.dnsbl_zone_list or None, alert=_alert_listed)
    _monitor_task = asyncio.create_task(monitor.run_forever(s.dnsbl_interval_seconds))
    logger.info(
        "DNSBL monitor started: %d egress IP(s) every %.0fs",
        len(prober.ip_pool.snapshot()),
        s.dnsbl_interval_seconds,
    )
    return _monitor_task


async def stop_blacklist_monitor() -> None:
    """Cancel the scan and wait for it to unwind. Safe to call when not running."""
    global _monitor_task
    task, _monitor_task = _monitor_task, None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


__all__ = [
    "get_async_prober",
    "set_async_prober",
    "start_blacklist_monitor",
    "stop_blacklist_monitor",
    "SmtpService",
]
