"""Outbound webhooks: telling the caller their job finished.

A job carries a ``webhook_url`` from the moment it is created. Delivering to it
is a separate concern from running it — a receiver that is down must not fail a
validation run that already succeeded, and must not hold a worker slot open
while we back off for minutes.

So delivery is detached from the pipeline, and *how* detached depends on what is
configured:

* **arq available** — delivery is its own queued task, retried durably. A
  receiver that is down for ten minutes still gets the callback.
* **otherwise** — a background task in this process with bounded retries. It
  does not survive a restart, and it says so in the log rather than pretending
  the callback is guaranteed.

Payloads are signed when ``EVE_WEBHOOK_SECRET`` is set, so a receiver can tell
our POST from anyone else's.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Optional

from eve.config import get_settings

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Eve-Signature"
TIMESTAMP_HEADER = "X-Eve-Timestamp"
DELIVERY_HEADER = "X-Eve-Delivery"
EVENT_HEADER = "X-Eve-Event"


def sign(body: bytes, timestamp: str, secret: str) -> str:
    """``sha256=<hex>`` over ``timestamp.body``.

    The timestamp is inside the signed material so a captured payload cannot be
    replayed later against a receiver that checks it.
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def job_payload(job) -> dict[str, Any]:
    """What a receiver gets. Mirrors the job read model plus the event name."""
    return {
        "event": "job.completed" if job.status.value == "completed" else "job.failed",
        "job": job.as_dict(),
        "workspace_id": job.workspace_id,
    }


async def deliver(
    url: str,
    payload: dict[str, Any],
    *,
    secret: str = "",
    timeout: Optional[float] = None,
    max_attempts: Optional[int] = None,
    delivery_id: Optional[str] = None,
) -> bool:
    """POST ``payload`` to ``url`` with retries. True if a 2xx came back.

    Retries on network errors and 5xx/429 only — a 4xx means the receiver
    understood us and said no, and repeating it just wastes both sides' time.
    """
    import httpx

    s = get_settings()
    secret = secret or s.webhook_secret
    timeout = s.webhook_timeout if timeout is None else timeout
    attempts = s.webhook_max_attempts if max_attempts is None else max_attempts
    delivery_id = delivery_id or uuid.uuid4().hex

    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: str(payload.get("event", "job.completed")),
        DELIVERY_HEADER: delivery_id,
        TIMESTAMP_HEADER: timestamp,
    }
    if secret:
        headers[SIGNATURE_HEADER] = sign(body, timestamp, secret)

    last = ""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                logger.info("webhook %s delivered to %s", delivery_id, url)
                return True
            last = f"HTTP {resp.status_code}"
            if resp.status_code < 500 and resp.status_code != 429:
                logger.warning(
                    "webhook %s rejected by %s (%s) — not retrying", delivery_id, url, last
                )
                return False
        except Exception as exc:  # noqa: BLE001 - any transport failure is retryable
            last = f"{type(exc).__name__}: {exc}"

        if attempt < attempts:
            backoff = min(60.0, 2.0 ** (attempt - 1))
            logger.info(
                "webhook %s to %s failed (%s); retry %d/%d in %.0fs",
                delivery_id, url, last, attempt + 1, attempts, backoff,
            )
            await asyncio.sleep(backoff)

    logger.error("webhook %s to %s gave up after %d attempts (%s)", delivery_id, url, attempts, last)
    return False


# Inline deliveries only: keep a reference so the task survives to completion.
_pending: set[asyncio.Task] = set()


async def dispatch_job_webhook(job) -> Optional[str]:
    """Schedule the callback for a finished job. Returns the backend, or None.

    Returns immediately — the caller is a worker that should move on to the
    next job, not sit through a receiver's backoff.
    """
    if not job.webhook_url:
        return None

    from eve.jobs.queue import resolve_backend

    payload = job_payload(job)
    if resolve_backend() == "arq":
        from eve.jobs.queue import get_pool

        pool = await get_pool()
        await pool.enqueue_job("deliver_webhook_job", job.webhook_url, payload)
        return "arq"

    logger.info(
        "webhook for job %s will be delivered in-process; a restart before it "
        "succeeds loses the callback (configure Redis for durable delivery)",
        job.id,
    )
    task = asyncio.create_task(deliver(job.webhook_url, payload))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return "inline"
