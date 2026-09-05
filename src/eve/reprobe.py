"""Deferred re-probes for greylisted addresses.

A ``4xx`` reply is temporary. The mailbox may well exist — the receiver is
deferring us, which is exactly what greylisting is designed to do to a stranger.
Settling that address on the first answer is how a verifier reports ``unknown``
for mail that would have been delivered fine.

Real greylisters clear in roughly 5-15 minutes, which is far longer than a bulk
job can hold a row open. So the retry is *deferred*, and the durable record is
this table rather than a timer: a process that dies mid-wait loses nothing,
because the next process to poll finds the row still due. That is what makes
the schedule survive a worker restart on the inline backend as well as on arq.

**What a cleared retry updates.** The address row, and only the address row.
The job's cleaned / valid / removed CSVs are a snapshot taken when the job
finished, and a download must not change contents under someone who already
has it. This matches how the read-model already works elsewhere: `smtp_ran` is
the stored fact and the settling layer is recomputed from it on read, because
the read-model is the living record and the CSVs are an export of a moment.

After ``EVE_REPROBE_MAX_ATTEMPTS`` the address settles as ``unknown``, which is
where it already was — the retries can only improve on that, never worsen it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from eve.addresses import AddressRecord, get_address_store
from eve.config import get_settings
from eve.smtp_infra.greylist import RetryPolicy
from eve.tenancy import current_workspace_id

logger = logging.getLogger(__name__)

#: The probe detail that means "deferred, try again" (set by SmtpService._map).
GREYLISTED_DETAIL = "greylisted"


def is_greylisted(verdict_checks: Optional[dict]) -> bool:
    """Whether a verdict's SMTP check says the receiver deferred us."""
    smtp = (verdict_checks or {}).get("smtp") or {}
    return GREYLISTED_DETAIL in str(smtp.get("detail") or "")


def policy_from_settings(settings=None) -> RetryPolicy:
    """The retry schedule, from ``EVE_REPROBE_MAX_ATTEMPTS`` / ``_DELAYS``."""
    s = settings or get_settings()
    delays = [int(d) for d in s.reprobe_delay_list] or RetryPolicy().delays
    return RetryPolicy(max_attempts=s.reprobe_max_attempts, delays=delays)


def _now() -> float:
    return time.time()


class ReprobeStore:
    """The pending-retry table. One row per (workspace, address) awaiting a retry."""

    def __init__(self, url: Optional[str] = None):
        from sqlalchemy import Column, Float, Integer, MetaData, String, Table

        from eve.addresses import async_engine

        self._engine = async_engine(url)
        self._metadata = MetaData()
        self.t = Table(
            "reprobes",
            self._metadata,
            # Same identity as the address it will update, for the same reason:
            # two tenants deferring on the same mailbox retry independently.
            Column("workspace_id", String(64), primary_key=True),
            Column("email", String(320), primary_key=True),
            Column("job_id", String(64), index=True),
            Column("attempt", Integer),  # retries already made
            Column("next_attempt_at", Float, index=True),
            Column("first_deferred_at", Float),
            Column("last_code", Integer),
        )

    async def init(self) -> None:
        from eve.migrations import run_migrations

        async with self._engine.connect() as conn:
            await conn.run_sync(run_migrations)
            await conn.commit()

    # --- writes ----------------------------------------------------------

    async def schedule(
        self,
        email: str,
        *,
        workspace_id: Optional[str] = None,
        job_id: str = "",
        attempt: int = 0,
        last_code: int = 0,
        policy: Optional[RetryPolicy] = None,
        now: Optional[float] = None,
    ) -> Optional[float]:
        """Queue a retry. Returns when it is due, or ``None`` if attempts ran out."""
        pol = policy or policy_from_settings()
        delay = pol.next_delay(attempt)
        if delay < 0:
            return None

        from sqlalchemy import and_, delete, insert

        ws = workspace_id or current_workspace_id()
        ts = now if now is not None else _now()
        due = ts + delay
        row = {
            "workspace_id": ws,
            "email": email.lower(),
            "job_id": job_id,
            "attempt": attempt,
            "next_attempt_at": due,
            "first_deferred_at": ts,
            "last_code": last_code,
        }
        async with self._engine.begin() as conn:
            # Portable upsert, matching AddressStore: clear the key, then insert.
            await conn.execute(
                delete(self.t).where(
                    and_(self.t.c.workspace_id == ws, self.t.c.email == row["email"])
                )
            )
            await conn.execute(insert(self.t), [row])
        return due

    async def schedule_many(
        self,
        emails: list[tuple[str, int]],
        *,
        workspace_id: Optional[str] = None,
        job_id: str = "",
        policy: Optional[RetryPolicy] = None,
    ) -> int:
        """Queue first retries for a batch of ``(email, smtp_code)``. Returns the count."""
        scheduled = 0
        for email, code in emails:
            if await self.schedule(
                email, workspace_id=workspace_id, job_id=job_id, last_code=code, policy=policy
            ):
                scheduled += 1
        return scheduled

    async def claim_due(
        self, limit: int = 50, *, lease_seconds: float = 300.0, now: Optional[float] = None
    ) -> list[dict]:
        """Take up to ``limit`` rows that are due, leasing them against other pollers.

        The lease is a push of ``next_attempt_at`` into the future rather than a
        separate claim column: a poller that dies holding rows releases them when
        the lease expires, with no reaper to write and no state to get stuck in.
        """
        from sqlalchemy import and_, select, update

        ts = now if now is not None else _now()
        async with self._engine.begin() as conn:
            rows = (
                (
                    await conn.execute(
                        select(self.t)
                        .where(self.t.c.next_attempt_at <= ts)
                        .order_by(self.t.c.next_attempt_at)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            claimed = [dict(r) for r in rows]
            for r in claimed:
                await conn.execute(
                    update(self.t)
                    .where(
                        and_(
                            self.t.c.workspace_id == r["workspace_id"],
                            self.t.c.email == r["email"],
                        )
                    )
                    .values(next_attempt_at=ts + lease_seconds)
                )
        return claimed

    async def drop(self, email: str, *, workspace_id: Optional[str] = None) -> None:
        from sqlalchemy import and_, delete

        ws = workspace_id or current_workspace_id()
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(self.t).where(
                    and_(self.t.c.workspace_id == ws, self.t.c.email == email.lower())
                )
            )

    async def delete_by_job(self, job_id: str, *, workspace_id: Optional[str] = None) -> int:
        """Drop pending retries for a deleted job — nothing left to update."""
        from sqlalchemy import and_, delete

        ws = workspace_id or current_workspace_id()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                delete(self.t).where(
                    and_(self.t.c.workspace_id == ws, self.t.c.job_id == job_id)
                )
            )
            return res.rowcount or 0

    # --- reads -----------------------------------------------------------

    async def pending(self, *, workspace_id: Optional[str] = None) -> list[dict]:
        from sqlalchemy import select

        ws = workspace_id or current_workspace_id()
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(self.t)
                        .where(self.t.c.workspace_id == ws)
                        .order_by(self.t.c.next_attempt_at)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def pending_count(self, *, workspace_id: Optional[str] = None) -> int:
        return len(await self.pending(workspace_id=workspace_id))


_store: Optional[ReprobeStore] = None


def get_reprobe_store() -> ReprobeStore:
    global _store
    if _store is None:
        _store = ReprobeStore()
    return _store


def set_reprobe_store(store: Optional[ReprobeStore]) -> None:
    """Override the process-wide store (tests / explicit configuration)."""
    global _store
    _store = store


# --- running the retries -------------------------------------------------


async def run_due_reprobes(limit: Optional[int] = None, *, settings=None) -> dict[str, int]:
    """Re-probe every address whose retry is due. Returns a small tally.

    Each address is independent: one failure must not strand the rest of the
    batch, so a raising probe leaves that row leased and due again shortly.
    """
    s = settings or get_settings()
    store = get_reprobe_store()
    rows = await store.claim_due(limit or s.reprobe_batch)
    tally = {"claimed": len(rows), "resolved": 0, "retried": 0, "exhausted": 0, "failed": 0}
    if not rows:
        return tally

    pol = policy_from_settings(s)
    for row in rows:
        try:
            outcome = await _reprobe_one(row, settings=s, policy=pol)
            tally[outcome] += 1
        except Exception:  # noqa: BLE001 - one address must not sink the batch
            tally["failed"] += 1
            logger.warning("re-probe failed for %s", row.get("email"), exc_info=True)
    return tally


async def _reprobe_one(row: dict, *, settings, policy: RetryPolicy) -> str:
    """Re-probe one address. Returns 'resolved' | 'retried' | 'exhausted'."""
    # Imported here: the pipeline is what composes the prober, and it imports
    # this module to schedule retries in the first place.
    from eve.jobs.pipeline import _verify_email
    from eve.smtp_infra import get_async_prober

    email = row["email"]
    ws = row["workspace_id"]
    attempt = int(row.get("attempt") or 0) + 1

    verdict = await _verify_email(
        email,
        enable_dns=settings.enable_dns,
        enable_smtp=settings.enable_smtp,
        prober=get_async_prober(),
        dns_timeout=settings.dns_timeout,
    )

    store = get_reprobe_store()
    if is_greylisted(verdict.checks):
        smtp = (verdict.checks or {}).get("smtp") or {}
        due = await store.schedule(
            email,
            workspace_id=ws,
            job_id=row.get("job_id") or "",
            attempt=attempt,
            last_code=int(smtp.get("code") or 0),
            policy=policy,
        )
        if due is not None:
            logger.info("%s still deferred (attempt %d), retrying later", email, attempt)
            return "retried"
        # Out of attempts: the address keeps the unknown it already had.
        await store.drop(email, workspace_id=ws)
        logger.info("%s still deferred after %d attempts; settling unknown", email, attempt)
        await _update_address(email, verdict, workspace_id=ws, job_id=row.get("job_id") or "")
        return "exhausted"

    await store.drop(email, workspace_id=ws)
    await _update_address(email, verdict, workspace_id=ws, job_id=row.get("job_id") or "")
    logger.info("%s cleared greylisting on attempt %d: %s", email, attempt, verdict.status.value)
    return "resolved"


async def _update_address(email: str, verdict, *, workspace_id: str, job_id: str) -> None:
    """Fold a retry's verdict into the address row, keeping its job attribution.

    The address stays attributed to the job that first validated it — the retry
    is a later reading of the same address, not a new run of a different job.
    """
    addresses = get_address_store()
    existing = await addresses.get(email, workspace_id=workspace_id) or {}
    record = AddressRecord.from_verdict(
        verdict,
        job_id=existing.get("job_id") or job_id,
        job_filename=existing.get("job_filename") or "",
        list_type=existing.get("list_type") or "unknown",
    )
    await addresses.upsert_many([record], workspace_id=workspace_id)


# --- the background poller ----------------------------------------------

_runner_task: Optional[asyncio.Task] = None


async def _run_forever(settings) -> None:
    """Sweep until cancelled, against the settings the runner was started with.

    Carrying them rather than re-reading them per sweep keeps one process on one
    schedule for its lifetime, and is what lets a test drive the loop at all.
    """
    interval = settings.reprobe_poll_seconds
    while True:
        try:
            tally = await run_due_reprobes(settings=settings)
            if tally["claimed"]:
                logger.info("re-probe sweep: %s", tally)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the sweep runs for the process's life
            logger.exception("re-probe sweep failed; retrying in %.0fs", interval)
        await asyncio.sleep(interval)


def start_reprobe_runner(settings=None) -> Optional[asyncio.Task]:
    """Start the poller that runs due retries. Idempotent.

    Returns ``None`` when there is nothing it could ever do: with SMTP off no
    address is ever deferred, so there is nothing to retry.
    """
    global _runner_task
    if _runner_task is not None and not _runner_task.done():
        return _runner_task

    s = settings or get_settings()
    if not (s.enable_smtp and s.reprobe_enabled):
        return None

    _runner_task = asyncio.create_task(_run_forever(s))
    logger.info(
        "re-probe runner started: polling every %.0fs, %d attempts at %s",
        s.reprobe_poll_seconds,
        s.reprobe_max_attempts,
        ", ".join(f"{d}s" for d in s.reprobe_delay_list),
    )
    return _runner_task


async def stop_reprobe_runner() -> None:
    """Cancel the poller and wait for it to unwind. Safe when not running."""
    global _runner_task
    task, _runner_task = _runner_task, None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


__all__: list[str] = [
    "ReprobeStore",
    "get_reprobe_store",
    "set_reprobe_store",
    "is_greylisted",
    "policy_from_settings",
    "run_due_reprobes",
    "start_reprobe_runner",
    "stop_reprobe_runner",
]
