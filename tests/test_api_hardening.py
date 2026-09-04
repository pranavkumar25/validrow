"""Upload cap and rate limiting."""
from __future__ import annotations

import httpx

from eve.api.main import app
from eve.config import Settings, set_settings

CSV = b"email,first_name\na@example.com,Ada\n"


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_upload_over_the_cap_is_rejected():
    set_settings(Settings(enable_dns=False, max_upload_bytes=64))
    big = b"email\n" + b"a@example.com\n" * 200

    async with await _client() as client:
        r = await client.post("/v1/files", files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413
    assert "upload limit" in r.json()["detail"]


async def test_upload_under_the_cap_still_works():
    set_settings(Settings(enable_dns=False, max_upload_bytes=1024))
    async with await _client() as client:
        r = await client.post("/v1/files", files={"file": ("small.csv", CSV, "text/csv")})
    assert r.status_code == 200
    assert r.json()["detection"]["guessed_email"] == "email"


async def test_rate_limit_is_off_by_default():
    set_settings(Settings(enable_dns=False))
    async with await _client() as client:
        for _ in range(8):
            r = await client.post("/v1/verify", json={"email": "a@example.com"})
            assert r.status_code == 200


async def test_rate_limit_returns_429_with_retry_after():
    set_settings(Settings(enable_dns=False, rate_limit_per_minute=3))
    async with await _client() as client:
        codes = [
            (await client.post("/v1/verify", json={"email": "a@example.com"})).status_code
            for _ in range(5)
        ]
        limited = await client.post("/v1/verify", json={"email": "a@example.com"})

    # The bucket starts full, so the first `limit` requests pass and the rest
    # are refused until it refills.
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
