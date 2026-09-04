"""Arq worker entrypoint — the horizontal scale-out path for bulk jobs.

The API enqueues to this worker whenever Redis is configured (see
``eve.jobs.queue``); without Redis it runs jobs in its own process instead.
Running the worker separately is what makes a job survive an API deploy, and
lets probes originate from the port-25-capable egress hosts rather than from
whichever machine served the upload.

Scale-out needs *shared* backends so every worker sees the same state:
Postgres (``EVE_WORKSPACE_DB_URL``), S3/R2 (``EVE_S3_*``) and Redis
(``EVE_REDIS_URL``) — the last one also makes the per-MX rate limiter shared,
without which each worker gets a full probe budget against the same provider.

    pip install -e '.[worker,postgres,s3,redis]'
    arq eve.jobs.worker.WorkerSettings
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def verify_file_job(ctx: dict, job_id: str, workspace_id: Optional[str] = None) -> None:
    """Arq task: load a job and run the full pipeline."""
    from eve.jobs.queue import run_job_now

    await run_job_now(job_id, workspace_id)


async def deliver_webhook_job(ctx: dict, url: str, payload: dict[str, Any]) -> bool:
    """Arq task: deliver one webhook, retried by arq if it raises.

    ``deliver`` already retries internally with backoff; returning False rather
    than raising keeps a receiver that is permanently down from occupying a
    worker slot indefinitely.
    """
    from eve.webhooks import deliver

    return await deliver(url, payload)


async def startup(ctx: dict) -> None:
    """Prepare shared state once per worker process."""
    from eve.addresses import get_address_store
    from eve.config import get_settings
    from eve.jobs.store import get_job_store
    from eve.observability import configure_logging, init_sentry
    from eve.smtp_infra import start_blacklist_monitor

    s = get_settings()
    configure_logging(s)
    init_sentry(s)
    await get_address_store().init()
    await get_job_store().init()
    # The worker is where probes actually originate, so it watches its own
    # egress IPs. The pool lives in memory: each process scans the IPs it uses.
    start_blacklist_monitor(s)
    logger.info("worker ready (env=%s)", s.env)


async def shutdown(ctx: dict) -> None:
    """Unwind the background scan so the process can exit promptly."""
    from eve.smtp_infra import stop_blacklist_monitor

    await stop_blacklist_monitor()


def _redis_settings():
    """Resolved at import: arq reads ``WorkerSettings.redis_settings`` directly.

    A worker with no Redis has nothing to pull from, so this fails loudly here
    rather than silently connecting to a localhost that is not there.
    """
    from eve.config import get_settings
    from eve.jobs.queue import redis_settings

    if not get_settings().redis_configured:
        raise RuntimeError(
            "EVE_REDIS_URL must be set to run the worker — it is the queue the "
            "API enqueues to. Without it the API runs jobs in-process and no "
            "worker is needed."
        )
    return redis_settings()


class WorkerSettings:
    """Arq worker config. Redis is resolved from ``EVE_REDIS_URL`` at import."""

    functions = [verify_file_job, deliver_webhook_job]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 20
    # A bulk run can legitimately take hours; arq's 300s default would kill it
    # partway and leave the job stuck in "processing".
    job_timeout = 60 * 60 * 6
    redis_settings = _redis_settings()
