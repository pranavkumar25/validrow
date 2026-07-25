"""DNSBL (blacklist) monitoring + alerting.

Egress IP reputation is fragile — a listing on Spamhaus/Barracuda poisons every
result from that IP. This module checks IPs against DNSBLs and, on a hit, pulls
the IP from rotation (via the pool's cooldown) and fires an alert hook.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Callable, Optional

import dns.exception
import dns.resolver

DEFAULT_ZONES = ["zen.spamhaus.org", "b.barracudacentral.org", "bl.spamcop.net"]

AlertHook = Callable[[str, list[str]], Awaitable[None]]


def _reverse_ipv4(ip: str) -> str:
    return ".".join(reversed(ip.split(".")))


async def check_dnsbl(ip: str, zones: Optional[list[str]] = None, timeout: float = 5.0) -> list[str]:
    """Return the list of DNSBL zones that currently list ``ip`` (empty = clean)."""
    zones = zones or DEFAULT_ZONES
    if ip.count(".") != 3:  # IPv4 only for the reverse form
        return []
    rev = _reverse_ipv4(ip)
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout

    listed: list[str] = []
    for zone in zones:
        query = f"{rev}.{zone}"
        try:
            await asyncio.to_thread(resolver.resolve, query, "A")
            listed.append(zone)  # any A answer means "listed"
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except dns.exception.DNSException:
            continue
    return listed


class BlacklistMonitor:
    def __init__(self, ip_pool, zones: Optional[list[str]] = None, alert: Optional[AlertHook] = None):
        self.ip_pool = ip_pool
        self.zones = zones or DEFAULT_ZONES
        self.alert = alert

    async def scan_once(self) -> dict:
        """Check every pool IP once; cool + alert on listings. Returns ip->zones."""
        results: dict = {}
        for st in self.ip_pool.snapshot():
            listed = await check_dnsbl(st.ip, self.zones)
            if listed:
                results[st.ip] = listed
                await self.ip_pool.cool(st.ip)
                if self.alert:
                    await self.alert(st.ip, listed)
        return results

    async def run_forever(self, interval: float = 3600.0) -> None:  # pragma: no cover
        while True:
            await self.scan_once()
            await asyncio.sleep(interval)
