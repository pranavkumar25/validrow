"""M2 · SMTP Infrastructure — the in-house mailbox-verification subsystem.

Composed of:
  * ``prober``       — the raw aiosmtplib conversation (never sends DATA)
  * ``providers``    — per-provider heuristics (Gmail/Outlook/Yahoo lie to probes)
  * ``rate_limiter`` — per-MX token bucket (don't hammer one provider)
  * ``ip_pool``      — egress IP reputation / rotation / cooldown / warm-up
  * ``blacklist``    — DNSBL monitoring + alerting
  * ``service``      — wires them into an async prober the pipeline can call
"""
from __future__ import annotations

from eve.config import get_settings
from eve.jobs.pipeline import AsyncProber, NullAsyncProber
from eve.smtp_infra.service import SmtpService

_prober: AsyncProber | None = None


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


def set_async_prober(prober: AsyncProber) -> None:
    global _prober
    _prober = prober


__all__ = ["get_async_prober", "set_async_prober", "SmtpService"]
