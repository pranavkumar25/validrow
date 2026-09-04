"""SmtpService — wires the M2 pieces into a single async prober.

Implements the pipeline's ``AsyncProber`` protocol:

    async def probe(email, mx_hosts, domain) -> ProbeResult

For each address it: picks the provider strategy, checks/creates the per-domain
catch-all verdict (cached, one probe per domain), acquires a per-MX rate token
and an egress IP, runs the RCPT probe, records IP health, and maps the SMTP
reply to a verdict outcome.
"""
from __future__ import annotations

import asyncio
import uuid

from eve.config import Settings
from eve.kv import get_kv
from eve.layers.smtp import ProbeResult
from eve.smtp_infra.greylist import is_temporary
from eve.smtp_infra.ip_pool import IpPool
from eve.smtp_infra.prober import SmtpReply, probe_rcpt
from eve.smtp_infra.providers import Strategy, strategy_for
from eve.smtp_infra.rate_limiter import PerMxRateLimiter

_PERMANENT_REJECT = {550, 551, 553, 554, 521, 501, 502}


class SmtpService:
    def __init__(
        self,
        *,
        port: int,
        helo: str,
        mail_from: str,
        timeout: float,
        rate_limiter: PerMxRateLimiter,
        ip_pool: IpPool,
        target_host: str = "",
        target_port: int = 0,
    ):
        self.port = port
        self.helo = helo
        self.mail_from = mail_from
        self.timeout = timeout
        self.rate_limiter = rate_limiter
        self.ip_pool = ip_pool
        # When set, every probe connects here instead of the domain's real MX.
        self.target_host = target_host
        self.target_port = target_port
        self._catch_all: dict[str, bool] = {}
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @classmethod
    def from_settings(cls, s: Settings) -> SmtpService:
        ips = s.smtp_egress_ip_list
        return cls(
            port=s.smtp_port,
            helo=s.smtp_helo_hostname,
            mail_from=s.smtp_mail_from,
            timeout=s.smtp_timeout,
            rate_limiter=PerMxRateLimiter(get_kv(), s.per_mx_rate),
            ip_pool=IpPool(ips, warmup_daily_cap=s.ip_warmup_daily_cap),
            target_host=s.smtp_target_host,
            target_port=s.smtp_target_port,
        )

    async def probe(self, email: str, mx_hosts: list[str], domain: str) -> ProbeResult:
        if not mx_hosts:
            return ProbeResult(outcome="unknown", detail="no_mx_hosts")
        strat = strategy_for(mx_hosts, domain)

        # Catch-all is decided once per domain (cached). A catch-all domain makes
        # every individual mailbox unverifiable -> risky.
        if strat.supports_catch_all_probe and await self._is_catch_all(domain, mx_hosts, strat):
            return ProbeResult(outcome="catch_all", is_catch_all=True, smtp_code=250, detail="catch_all")

        reply = await self._rcpt(email, mx_hosts, strat)
        return self._map(reply, strat)

    async def _rcpt(self, recipient: str, mx_hosts: list[str], strat: Strategy) -> SmtpReply:
        mx = mx_hosts[0]
        await self.rate_limiter.acquire(mx)  # rate-limit by real destination
        ip = await self.ip_pool.acquire()
        host = self.target_host or mx
        port = self.target_port or self.port
        reply = await probe_rcpt(
            host,
            recipient,
            port=port,
            helo=self.helo,
            mail_from=self.mail_from,
            timeout=self.timeout,
            source_ip=ip,
        )
        await self.ip_pool.report(ip, self._ip_event(reply))
        return reply

    @staticmethod
    def _ip_event(reply: SmtpReply) -> str:
        msg = (reply.message or "").lower()
        if reply.code in (421,) or "block" in msg or "blacklist" in msg or "spam" in msg:
            return "block"
        if reply.code == 0 or is_temporary(reply.code, reply.message):
            return "tempfail"
        return "ok"

    async def _get_lock(self, domain: str) -> asyncio.Lock:
        async with self._locks_guard:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = asyncio.Lock()
            return self._domain_locks[domain]

    async def _is_catch_all(self, domain: str, mx_hosts: list[str], strat: Strategy) -> bool:
        if domain in self._catch_all:
            return self._catch_all[domain]
        lock = await self._get_lock(domain)
        async with lock:
            if domain in self._catch_all:  # re-check under lock
                return self._catch_all[domain]
            fake = f"nonexistent-{uuid.uuid4().hex[:16]}@{domain}"
            reply = await self._rcpt(fake, mx_hosts, strat)
            is_ca = reply.code in (250, 251)
            self._catch_all[domain] = is_ca
            return is_ca

    def _map(self, reply: SmtpReply, strat: Strategy) -> ProbeResult:
        code = reply.code
        msg = (reply.message or "")[:160]
        if code in (250, 251):
            if not strat.trust_rcpt:
                # Provider accepts every RCPT — can't trust a 250 as "exists".
                return ProbeResult(outcome="unknown", smtp_code=code, detail="provider_unreliable")
            return ProbeResult(outcome="valid", smtp_code=code, detail=msg)
        if code in _PERMANENT_REJECT:
            low = msg.lower()
            if "block" in low or "spam" in low or "blacklist" in low or "policy" in low:
                return ProbeResult(outcome="unknown", smtp_code=code, detail="antispam_block")
            return ProbeResult(outcome="invalid", smtp_code=code, detail=msg)
        if is_temporary(code, reply.message):
            return ProbeResult(outcome="unknown", smtp_code=code, detail="greylisted")
        return ProbeResult(outcome="unknown", smtp_code=code, detail=msg or "unknown")
