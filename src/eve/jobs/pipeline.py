"""The bulk verification pipeline.

Two streaming passes over the file with O(unique-emails) memory:

  * **split**    — stream rows, run cheap layers 1-3 inline, dedupe emails &
                   domains, collect the unique work-list.
  * **resolve**  — resolve each unique domain's MX exactly once (warms the cache).
  * **verify**   — verify each unique email (layers 4-7) with bounded concurrency.
  * **assemble** — stream rows again, join verdicts back by dedupe key, and emit
                   cleaned / valid_only / removed CSVs.

Duplicates and undeliverable rows are re-derived on the assembly pass, so we
never hold the whole sheet in memory.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from eve.addresses import AddressRecord, AddressStore
from eve.config import Settings, get_settings
from eve.engine import apply_probe_outcome, validate
from eve.jobs import csv_io
from eve.jobs.models import Job, JobStatus, Phase
from eve.jobs.store import JobStore
from eve.layers import dns_mx
from eve.layers.normalize import normalize
from eve.layers.smtp import ProbeResult
from eve.layers.syntax import check_syntax
from eve.storage import ObjectStore
from eve.verdict import Status, SubStatus, Verdict

logger = logging.getLogger(__name__)


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


# Statuses that go to the "removed" pile (do-not-send).
_REMOVED_STATUSES = {Status.INVALID, Status.DISPOSABLE, Status.SPAM_TRAP}

# DNS/validation is I/O-bound (network). asyncio.to_thread caps at ~CPU+4
# threads, which serializes thousands of MX lookups; give this work its own
# wide pool so resolution actually runs concurrently.
_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=64, thread_name_prefix="eve-io")

_VERDICT_COLUMNS = [
    "email_status",
    "sub_status",
    "score",
    "normalized_email",
    "suggested_correction",
    "is_disposable",
    "is_role",
    "is_free",
    "is_catch_all",
    "mx_found",
    "duplicate_of",
]


class AsyncProber(Protocol):
    async def probe(self, email: str, mx_hosts: list[str], domain: str) -> ProbeResult:  # noqa: E704
        ...


class NullAsyncProber:
    """Used when SMTP is disabled — never confirms a mailbox."""

    async def probe(self, email: str, mx_hosts: list[str], domain: str) -> ProbeResult:
        return ProbeResult(outcome="unknown", detail="smtp_disabled")


@dataclass
class _Work:
    email: str  # canonical (normalized) address to verify
    domain: str
    valid_syntax: bool


def _dedupe_key(raw: str) -> tuple[str, _Work]:
    """Map a raw cell value to a canonical dedupe key + work item."""
    syn = check_syntax(raw)
    if not syn.valid:
        key = (raw or "").strip().lower()
        return key, _Work(email=raw, domain="", valid_syntax=False)
    norm = normalize(syn.local_part or "", syn.domain or "")
    return norm.dedupe_key, _Work(email=norm.normalized_email, domain=syn.domain or "", valid_syntax=True)


async def run_job(
    job: Job,
    *,
    store: ObjectStore,
    job_store: JobStore,
    prober: Optional[AsyncProber] = None,
    settings: Optional[Settings] = None,
    addresses: Optional[AddressStore] = None,
) -> Job:
    """Run one bulk verification.

    ``addresses`` is the workspace read-model the product reports from. Pass
    ``None`` (the default in tests) to run verification without it.
    """
    settings = settings or get_settings()
    prober = prober or NullAsyncProber()
    assert job.mapping is not None, "job requires a column mapping"
    email_col = job.mapping.email

    job.status = JobStatus.PROCESSING
    job.phase = Phase.READING
    job.started_at = _now()
    await job_store.save(job)

    try:
        detection = await csv_io.detect_columns(store, job.file_key)
        delimiter = detection.delimiter
        line_terminator = detection.line_terminator
        original_columns = detection.columns

        # --- split -------------------------------------------------------
        unique: dict[str, _Work] = {}
        domains: set[str] = set()
        total_rows = 0
        async for row in csv_io.iter_rows(store, job.file_key, delimiter):
            total_rows += 1
            raw = (row.get(email_col) or "").strip()
            key, work = _dedupe_key(raw)
            if key not in unique:
                unique[key] = work
                if work.domain:
                    domains.add(work.domain)
        job.counts.total_rows = total_rows
        job.counts.unique_emails = len(unique)
        job.counts.duplicates = total_rows - len(unique)
        # Progress is measured in unique addresses — that is the work, and it is
        # what the user is charged for.
        job.total = len(unique)
        job.domains_total = len(domains)
        await job_store.save(job)

        # --- resolve (once per domain, in parallel) ----------------------
        job.phase = Phase.RESOLVING
        await job_store.save(job)
        if settings.enable_dns and domains:
            loop = asyncio.get_running_loop()
            rsem = asyncio.Semaphore(max(1, settings.verify_concurrency))

            async def _resolve(d: str) -> None:
                async with rsem:
                    await loop.run_in_executor(
                        _IO_EXECUTOR, functools.partial(dns_mx.resolve_mx, d, settings.dns_timeout)
                    )

            await asyncio.gather(*(_resolve(d) for d in domains))

        # --- verify (bounded concurrency, streamed progress) -------------
        job.phase = Phase.VERIFYING
        verdicts: dict[str, Verdict] = {}
        sem = asyncio.Semaphore(max(1, settings.verify_concurrency))

        async def _verify_one(key: str, work: _Work) -> None:
            async with sem:
                v = await _verify_email(
                    work.email,
                    enable_dns=settings.enable_dns,
                    enable_smtp=settings.enable_smtp,
                    prober=prober,
                    dns_timeout=settings.dns_timeout,
                )
            verdicts[key] = v
            job.counts.bump(v.status.value)
            job.processed += 1

        items = list(unique.items())
        batch = max(1, settings.chunk_size)
        for i in range(0, len(items), batch):
            chunk = items[i : i + batch]
            await asyncio.gather(*(_verify_one(k, w) for k, w in chunk))
            await job_store.save(job)  # stream progress after each chunk
            # Land results as they finish, so a job that fails part-way still
            # leaves behind everything it did prove.
            chunk_verdicts = [verdicts[k] for k, _ in chunk if k in verdicts]
            await _record_addresses(job, chunk_verdicts, addresses)
            await _schedule_reprobes(job, chunk_verdicts, settings, addresses)

        # --- assemble ----------------------------------------------------
        job.phase = Phase.ASSEMBLING
        await job_store.save(job)
        await _assemble(
            job, store, delimiter, line_terminator, original_columns, unique, verdicts
        )

        job.status = JobStatus.COMPLETED
        job.phase = Phase.DONE
        job.finished_at = _now()
        await job_store.save(job)
    except Exception as exc:  # noqa: BLE001 - surface any failure on the job
        job.status = JobStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = _now()
        await job_store.save(job)
        raise

    return job


async def _record_addresses(job: Job, verdicts: list[Verdict], addresses) -> None:
    """Write a batch of verdicts into the workspace read-model.

    Best-effort: the read-model powers reporting, so a failure to write it must
    never lose a validation run that already succeeded. The CSVs remain the
    system of record.
    """
    if addresses is None or not verdicts:
        return
    try:
        records = [
            AddressRecord.from_verdict(
                v,
                job_id=job.id,
                job_filename=job.filename,
                list_type=job.list_type,
            )
            for v in verdicts
            if (v.normalized_email or v.email)
        ]
        # Explicit rather than ambient: on a worker there is no request context
        # to infer the workspace from, so the job carries it.
        await addresses.upsert_many(records, workspace_id=job.workspace_id)
    except Exception:  # noqa: BLE001 - reporting must not break verification
        logger.warning("could not write addresses for job %s", job.id, exc_info=True)


async def _schedule_reprobes(
    job: Job, verdicts: list[Verdict], settings: Settings, addresses: Optional[AddressStore]
) -> None:
    """Queue a deferred retry for every address the receiver deferred.

    Greylisters clear in minutes, not milliseconds, so this cannot be a retry
    inside the run — the row would only collect a second 4xx and the job would
    stall behind it. The address keeps the honest ``unknown`` it has now, and
    the retry updates it later if the mailbox turns out to exist.

    Tied to ``addresses`` for the same reason the retry exists: the address row
    is the only thing a cleared greylist updates, so a run that is not keeping
    the read-model has nothing to schedule a retry *for*.

    Best-effort, like the read-model write: failing to schedule a retry must
    never lose a verification run that already succeeded.
    """
    if addresses is None or not verdicts:
        return
    if not (settings.enable_smtp and settings.reprobe_enabled):
        return
    from eve.reprobe import get_reprobe_store, is_greylisted

    pending = [
        (
            v.normalized_email or v.email,
            int(((v.checks or {}).get("smtp") or {}).get("code") or 0),
        )
        for v in verdicts
        if is_greylisted(v.checks) and (v.normalized_email or v.email)
    ]
    if not pending:
        return
    try:
        count = await get_reprobe_store().schedule_many(
            pending, workspace_id=job.workspace_id, job_id=job.id
        )
        logger.info("job %s: %d greylisted address(es) queued for re-probe", job.id, count)
    except Exception:  # noqa: BLE001 - scheduling must not break verification
        logger.warning("could not schedule re-probes for job %s", job.id, exc_info=True)


async def _verify_email(
    email: str, *, enable_dns: bool, enable_smtp: bool, prober: AsyncProber, dns_timeout: float
) -> Verdict:
    # Layers 1-5 + DNS (cache hit after the resolve pass). Run off the loop.
    loop = asyncio.get_running_loop()
    v = await loop.run_in_executor(
        _IO_EXECUTOR,
        functools.partial(validate, email, enable_dns=enable_dns, enable_smtp=False, dns_timeout=dns_timeout),
    )
    if not enable_smtp or v.status in (Status.INVALID, Status.DISPOSABLE):
        return v
    if enable_dns:
        if not v.mx_found:
            return v  # domain can't receive mail — nothing to probe
        mx_hosts = dns_mx.resolve_mx(v.domain or "", timeout=dns_timeout).mx_hosts  # cached
    else:
        # DNS off (e.g. SMTP demo mode): the prober's target host decides where
        # to connect, so a synthetic MX is fine.
        mx_hosts = [v.domain or ""]
    probe = await prober.probe(email, mx_hosts, v.domain or "")
    v.record("smtp", {"outcome": probe.outcome, "code": probe.smtp_code, "detail": probe.detail})
    if probe.is_catch_all or probe.outcome in ("valid", "invalid", "catch_all"):
        apply_probe_outcome(v, probe)
    elif "greylist" in (probe.detail or ""):
        # A 4xx defers us rather than answering us. The status stays whatever
        # the cheaper layers concluded — naming the reason is what lets the
        # address be picked up for a re-probe instead of read as settled.
        v.sub_status = SubStatus.GREYLISTED
    return v


async def _assemble(
    job: Job,
    store: ObjectStore,
    delimiter: str,
    line_terminator: str,
    original_columns: list[str],
    unique: dict[str, _Work],
    verdicts: dict[str, Verdict],
) -> None:
    header = list(original_columns) + _VERDICT_COLUMNS
    fmt = {"delimiter": delimiter, "line_terminator": line_terminator}
    cleaned = csv_io.CsvWriter(store, store.new_key("_cleaned.csv"), header, **fmt)
    valid_only = csv_io.CsvWriter(store, store.new_key("_valid.csv"), header, **fmt)
    removed = csv_io.CsvWriter(store, store.new_key("_removed.csv"), header, **fmt)

    seen: set[str] = set()
    m = job.mapping
    async for row in csv_io.iter_rows(store, job.file_key, delimiter):
        raw = (row.get(m.email) or "").strip()
        key, _ = _dedupe_key(raw)
        v = verdicts.get(key)

        # Bonus hygiene: tidy the mapped name columns.
        out = dict(row)
        for col in (m.first_name, m.last_name):
            if col and out.get(col):
                out[col] = out[col].strip().title()

        is_dup = key in seen
        seen.add(key)
        duplicate_of = unique[key].email if (is_dup and key in unique) else ""

        if v is not None:
            out.update(
                {
                    "email_status": v.status.value,
                    "sub_status": v.sub_status.value,
                    "score": v.score,
                    "normalized_email": v.normalized_email or "",
                    "suggested_correction": v.suggested_correction or "",
                    "is_disposable": v.is_disposable,
                    "is_role": v.is_role,
                    "is_free": v.is_free,
                    "is_catch_all": v.is_catch_all,
                    "mx_found": v.mx_found,
                    "duplicate_of": duplicate_of,
                }
            )
            status = v.status
        else:
            out.update({"email_status": "invalid", "sub_status": "empty", "duplicate_of": duplicate_of})
            status = Status.INVALID

        cleaned.write(out)
        if is_dup or status in _REMOVED_STATUSES:
            removed.write(out)
        else:
            valid_only.write(out)

    await cleaned.close()
    await valid_only.close()
    await removed.close()
    job.output_keys = {
        "cleaned": cleaned.key,
        "valid": valid_only.key,
        "removed": removed.key,
    }
