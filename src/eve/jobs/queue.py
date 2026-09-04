"""Where a bulk job actually runs.

Two backends, one call site:

* **arq** — the job id goes onto a Redis queue and a worker picks it up. The run
  survives an API restart, and probes originate from the worker hosts (the ones
  with port-25 egress), not from whatever machine served the upload.
* **inline** — the job runs as a task in the API process. Fine for local dev and
  low volume; a deploy mid-run orphans it, which is why it is not the default
  once Redis is configured.

``auto`` picks arq when Redis is configured and the ``worker`` extra is
installed, and inline otherwise — so a single-box deployment keeps working and
adding Redis changes behaviour without changing code.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from eve.config import get_settings
from eve.jobs.models import Job

logger = logging.getLogger(__name__)

# Inline runs only: hold a reference so the task is not garbage-collected
# mid-run. asyncio keeps only weak references to running tasks.
_running: set[asyncio.Task] = set()

_pool = None  # cached arq pool


def resolve_backend(settings=None) -> str:
    """Which backend a job would use right now: ``"arq"`` or ``"inline"``."""
    s = settings or get_settings()
    choice = (s.queue_backend or "auto").lower()
    if choice == "inline":
        return "inline"
    if choice == "arq":
        return "arq"
    if not s.redis_configured:
        return "inline"
    try:
        import arq  # noqa: F401
    except ImportError:
        logger.warning(
            "EVE_REDIS_URL is set but the 'worker' extra is missing; "
            "jobs will run in-process. Install with: pip install -e '.[worker]'"
        )
        return "inline"
    return "arq"


async def get_pool():
    """The shared arq pool. Requires the ``worker`` extra and Redis."""
    global _pool
    if _pool is None:
        from arq import create_pool

        _pool = await create_pool(redis_settings())
    return _pool


def redis_settings():
    from arq.connections import RedisSettings

    return RedisSettings.from_dsn(get_settings().redis_url)


async def enqueue_job(job: Job, settings=None) -> str:
    """Schedule ``job`` for execution. Returns the backend that took it."""
    backend = resolve_backend(settings)
    if backend == "arq":
        pool = await get_pool()
        # The workspace travels with the job: a worker has no request context
        # to infer it from, and the job store is workspace-scoped.
        await pool.enqueue_job("verify_file_job", job.id, job.workspace_id)
        logger.info("job %s queued on arq (workspace=%s)", job.id, job.workspace_id)
        return "arq"

    task = asyncio.create_task(run_job_now(job.id, job.workspace_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
    logger.info("job %s running in-process", job.id)
    return "inline"


async def run_job_now(job_id: str, workspace_id: Optional[str] = None) -> None:
    """Load a job and run the pipeline. The single entry point both backends use."""
    from eve.addresses import get_address_store
    from eve.jobs.pipeline import run_job
    from eve.jobs.store import get_job_store
    from eve.smtp_infra import get_async_prober
    from eve.storage import get_object_store
    from eve.tenancy import set_current_workspace_id

    if workspace_id:
        set_current_workspace_id(workspace_id)

    job_store = get_job_store()
    job = await job_store.get(job_id)
    if job is None:
        logger.warning("job %s vanished before it could run", job_id)
        return

    try:
        await run_job(
            job,
            store=get_object_store(),
            job_store=job_store,
            prober=get_async_prober(),
            addresses=get_address_store(),
        )
    except Exception:  # noqa: BLE001 - run_job already recorded it on the job
        logger.exception("job %s failed", job_id)

    # Notify either way: a failed run is exactly when the caller most needs to
    # hear from us. run_job mutates `job` in place, so this sees the outcome.
    from eve.webhooks import dispatch_job_webhook

    await dispatch_job_webhook(job)
