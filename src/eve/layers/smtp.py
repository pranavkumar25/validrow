"""Layers 6 & 7 — SMTP mailbox probe + catch-all detection.

This is a *seam only* in M0. The real implementation (aiosmtplib over a
port-25 IP pool, per-MX rate limiting, catch-all cache, provider heuristics)
lands in milestone **M2 · SMTP Infrastructure**. The orchestrator accepts a
``prober`` implementing :class:`Prober` so M2 can plug in without touching the
engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ProbeResult:
    """Outcome of an SMTP mailbox probe."""

    # One of: "valid" | "invalid" | "unknown" | "catch_all"
    outcome: str
    is_catch_all: bool = False
    smtp_code: int = 0
    detail: str = ""


class Prober(Protocol):
    def probe(self, email: str, mx_hosts: list[str]) -> ProbeResult:  # pragma: no cover
        ...


class NotImplementedProber:
    """Default prober used until M2 exists — always returns ``unknown``."""

    def probe(self, email: str, mx_hosts: list[str]) -> ProbeResult:
        return ProbeResult(outcome="unknown", detail="smtp_probe_not_enabled")
