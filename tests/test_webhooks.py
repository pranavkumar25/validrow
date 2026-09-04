"""Webhook signing and delivery.

The delivery loop is the part worth testing: it has to distinguish "the
receiver is down" (retry) from "the receiver said no" (don't), because getting
that backwards either drops callbacks or hammers a 400 forever.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from eve import webhooks
from eve.config import Settings, set_settings


def _mock_client(handler, calls):
    """Patch httpx.AsyncClient so `deliver` talks to `handler` instead of a socket."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            calls.append({"url": url, "content": content, "headers": headers})
            return handler(len(calls))

    return _Client


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "http://hook"))


def test_signature_covers_the_timestamp_and_body():
    body = b'{"event":"job.completed"}'
    sig = webhooks.sign(body, "1700000000", "s3cret")

    expected = hmac.new(b"s3cret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"
    # A different timestamp with the same body must not verify — otherwise a
    # captured payload replays forever.
    assert webhooks.sign(body, "1700000001", "s3cret") != sig


async def test_successful_delivery_signs_and_stops(monkeypatch):
    set_settings(Settings(webhook_secret="s3cret"))
    calls: list[dict] = []
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(lambda n: _response(200), calls))

    ok = await webhooks.deliver("http://hook", {"event": "job.completed", "job": {"id": "j1"}})

    assert ok is True
    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert headers[webhooks.EVENT_HEADER] == "job.completed"
    assert headers[webhooks.SIGNATURE_HEADER].startswith("sha256=")
    assert headers[webhooks.DELIVERY_HEADER]
    assert json.loads(calls[0]["content"])["job"]["id"] == "j1"


async def test_unsigned_when_no_secret_is_configured(monkeypatch):
    set_settings(Settings(webhook_secret=""))
    calls: list[dict] = []
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(lambda n: _response(200), calls))

    await webhooks.deliver("http://hook", {"event": "job.completed"})
    assert webhooks.SIGNATURE_HEADER not in calls[0]["headers"]


async def test_server_error_is_retried_then_succeeds(monkeypatch):
    set_settings(Settings(webhook_max_attempts=3))
    calls: list[dict] = []
    monkeypatch.setattr(
        httpx, "AsyncClient", _mock_client(lambda n: _response(200 if n >= 3 else 503), calls)
    )
    monkeypatch.setattr(webhooks.asyncio, "sleep", _no_sleep)

    assert await webhooks.deliver("http://hook", {"event": "job.completed"}) is True
    assert len(calls) == 3


async def test_client_error_is_not_retried(monkeypatch):
    set_settings(Settings(webhook_max_attempts=5))
    calls: list[dict] = []
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(lambda n: _response(410), calls))
    monkeypatch.setattr(webhooks.asyncio, "sleep", _no_sleep)

    # 410 means the receiver understood and refused. Repeating it four more
    # times helps nobody.
    assert await webhooks.deliver("http://hook", {"event": "job.completed"}) is False
    assert len(calls) == 1


async def test_429_is_retried(monkeypatch):
    set_settings(Settings(webhook_max_attempts=2))
    calls: list[dict] = []
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(lambda n: _response(429), calls))
    monkeypatch.setattr(webhooks.asyncio, "sleep", _no_sleep)

    assert await webhooks.deliver("http://hook", {"event": "job.completed"}) is False
    assert len(calls) == 2  # rate limiting is temporary, so it is worth retrying


async def test_gives_up_after_max_attempts(monkeypatch):
    set_settings(Settings(webhook_max_attempts=4))
    calls: list[dict] = []

    def _boom(n):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(_boom, calls))
    monkeypatch.setattr(webhooks.asyncio, "sleep", _no_sleep)

    assert await webhooks.deliver("http://hook", {"event": "job.completed"}) is False
    assert len(calls) == 4


async def test_no_url_means_no_delivery():
    from eve.jobs.models import Job

    assert await webhooks.dispatch_job_webhook(Job(file_key="k", filename="f.csv")) is None


def test_payload_names_the_event_from_the_job_status():
    from eve.jobs.models import Job, JobStatus

    job = Job(file_key="k", filename="f.csv")
    job.status = JobStatus.COMPLETED
    assert webhooks.job_payload(job)["event"] == "job.completed"

    job.status = JobStatus.FAILED
    payload = webhooks.job_payload(job)
    assert payload["event"] == "job.failed"
    assert payload["workspace_id"] == job.workspace_id


async def _no_sleep(_seconds):
    return None


@pytest.fixture(autouse=True)
def _quick_settings():
    yield
    set_settings(Settings())
