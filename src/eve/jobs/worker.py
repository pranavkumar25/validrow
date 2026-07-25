"""Arq worker entrypoint — the horizontal scale-out path for bulk jobs.

In M1 the API runs jobs **in-process** (see ``api/jobs.py``), which is enough for
local dev and moderate volume. For 1M-row throughput across many machines,
enqueue jobs to arq and run this worker — ideally pinned to the port-25-capable
egress hosts so SMTP probes originate from the managed IP pool.

Scale-out requires *shared* backends (so every worker sees the same state):
``SqlJobStore`` (Postgres) + ``S3ObjectStore`` + ``RedisKV``, plus the ``worker``
extra (``pip install -e '.[worker,postgres,s3,redis]'``).

Run with:  ``arq eve.jobs.worker.WorkerSettings``
"""
from __future__ import annotations


async def verify_file_job(ctx: dict, job_id: str) -> None:
    """Arq task: load a job and run the full pipeline."""
    from eve.jobs.pipeline import run_job
    from eve.jobs.store import get_job_store
    from eve.smtp_infra import get_async_prober
    from eve.storage import get_object_store

    job_store = get_job_store()
    job = await job_store.get(job_id)
    if job is None:
        return
    await run_job(
        job,
        store=get_object_store(),
        job_store=job_store,
        prober=get_async_prober(),
    )


class WorkerSettings:
    """Arq worker config. ``redis_settings`` is resolved from ``EVE_REDIS_URL``
    at deploy time (left to the operator so tests don't require Redis)."""

    functions = [verify_file_job]
    max_jobs = 20
