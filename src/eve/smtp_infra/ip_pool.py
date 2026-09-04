"""Egress IP pool manager — reputation, rotation, cooldown, warm-up.

Probing burns IP reputation; one aggressive run can blacklist an IP and poison
every future result. This manager rotates across healthy IPs, cools an IP that
starts getting tempfails/blocks, and caps daily volume for IPs still warming up.

When the pool is empty (local dev / tests) ``acquire()`` returns ``None`` and the
prober simply uses the OS default egress.

**The daily roll happens on read, not on a timer.** `promote_warmup` was written
to be "called once per day" by a scheduler that never existed, so `daily_count`
never reset: every IP went permanently unavailable after `warmup_daily_cap`
probes and `acquire()` silently returned `None` — falling back to the un-warmed,
un-rotated OS default route, which is the exact address rotation exists to
avoid. A background ticker would have fixed it only for processes that outlive a
day; deploying every afternoon would have reintroduced it. Checking the date
when an IP is taken cannot miss a boundary or be lost to a restart.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _utc_day() -> int:
    """Days since the epoch, UTC. The unit the daily cap is denominated in."""
    return int(datetime.now(timezone.utc).timestamp() // 86_400)


@dataclass
class IpState:
    ip: str
    daily_count: int = 0
    cooldown_until: float = 0.0
    reputation: float = 1.0
    warmup_stage: int = 0  # 0 = warming (daily cap applies); higher = warmer


class IpPool:
    def __init__(self, ips: list[str], warmup_daily_cap: int = 50, cooldown_seconds: float = 3600.0):
        self._ips = [IpState(ip=ip) for ip in ips if ip.strip()]
        self._i = 0
        self._cap = warmup_daily_cap
        self._cooldown = cooldown_seconds
        self._lock = asyncio.Lock()
        self._day = _utc_day()

    def _find(self, ip: str) -> Optional[IpState]:
        return next((s for s in self._ips if s.ip == ip), None)

    async def acquire(self) -> Optional[str]:
        """Return the next healthy egress IP, or None if none available/empty."""
        if not self._ips:
            return None
        async with self._lock:
            self._roll_day()
            now = time.monotonic()
            n = len(self._ips)
            for _ in range(n):
                st = self._ips[self._i % n]
                self._i += 1
                if st.cooldown_until > now:
                    continue
                if st.warmup_stage == 0 and st.daily_count >= self._cap:
                    continue
                st.daily_count += 1
                return st.ip
            return None  # everything cooling or capped

    async def report(self, ip: Optional[str], event: str) -> None:
        """Record a probe outcome. event: 'ok' | 'tempfail' | 'block'."""
        if not ip:
            return
        async with self._lock:
            st = self._find(ip)
            if not st:
                return
            if event == "block":
                st.reputation = max(0.0, st.reputation - 0.5)
                st.cooldown_until = time.monotonic() + self._cooldown
            elif event == "tempfail":
                st.reputation = max(0.0, st.reputation - 0.05)
            else:  # ok
                st.reputation = min(1.0, st.reputation + 0.01)

    async def cool(self, ip: str, seconds: Optional[float] = None) -> None:
        """Force an IP out of rotation (used by the blacklist monitor)."""
        async with self._lock:
            st = self._find(ip)
            if st:
                st.cooldown_until = time.monotonic() + (seconds or self._cooldown)

    def _roll_day(self) -> None:
        """Run the daily tick if the UTC day has turned. Caller holds the lock."""
        today = _utc_day()
        if today == self._day:
            return
        self._day = today
        self.promote_warmup()
        logger.info("egress pool: daily counters reset, warm-up advanced")

    def promote_warmup(self) -> None:
        """Warm-up tick: advance stage + reset daily counters.

        Called by :meth:`_roll_day` when the UTC day turns, and safe to call by
        hand. Real deployments ramp the cap by stage; here we simply graduate
        IPs out of the capped stage after a day of acceptable behaviour.
        """
        for st in self._ips:
            if st.reputation >= 0.8:
                st.warmup_stage += 1
            st.daily_count = 0

    def snapshot(self) -> list[IpState]:
        return list(self._ips)
