"""Integration tests for the SMTP prober + service against a real (fake) server."""
from __future__ import annotations

from eve.kv import InMemoryKV
from eve.smtp_infra.ip_pool import IpPool
from eve.smtp_infra.prober import probe_rcpt
from eve.smtp_infra.rate_limiter import PerMxRateLimiter
from eve.smtp_infra.service import SmtpService
from smtp_helpers import RecordingHandler, start_server


def _service(port: int) -> SmtpService:
    return SmtpService(
        port=port,
        helo="verifier.local",
        mail_from="verify@verifier.local",
        timeout=10.0,
        rate_limiter=PerMxRateLimiter(InMemoryKV(), rate=100.0),
        ip_pool=IpPool([]),  # empty -> OS default egress
    )


async def test_prober_valid_invalid_and_never_data():
    handler = RecordingHandler(valid={"good@example.com"})
    controller, port = start_server(handler)
    try:
        good = await probe_rcpt("127.0.0.1", "good@example.com", port=port, timeout=10)
        bad = await probe_rcpt("127.0.0.1", "nobody@example.com", port=port, timeout=10)
    finally:
        controller.stop()

    assert good.code == 250
    assert bad.code == 550
    # The core guarantee: we never transmit a message body.
    assert good.sent_data is False and bad.sent_data is False
    assert handler.data_received is False


async def test_service_maps_valid_and_invalid():
    handler = RecordingHandler(valid={"good@example.com"})
    controller, port = start_server(handler)
    try:
        svc = _service(port)
        good = await svc.probe("good@example.com", ["127.0.0.1"], "example.com")
        bad = await svc.probe("bad@example.com", ["127.0.0.1"], "example.com")
    finally:
        controller.stop()

    assert good.outcome == "valid"
    assert bad.outcome == "invalid"
    assert handler.data_received is False


async def test_service_detects_catch_all():
    handler = RecordingHandler(catch_all=True)
    controller, port = start_server(handler)
    try:
        svc = _service(port)
        res = await svc.probe("whoever@example.com", ["127.0.0.1"], "example.com")
    finally:
        controller.stop()

    assert res.is_catch_all is True
    assert res.outcome == "catch_all"
