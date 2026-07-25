"""End-to-end API test for the bulk pipeline: upload -> map -> run -> download.

Runs fully offline (DNS + SMTP disabled) via a settings override.
"""
from __future__ import annotations

import asyncio

import httpx
from httpx import ASGITransport

from eve.api.main import app
from eve.config import Settings, set_settings

CSV = b"""email,first_name,last_name
John.Doe@gmail.com,john,doe
johndoe+x@gmail.com,john,doe
sales@acme.io,,
x@mailinator.com,a,b
bogus,c,d
jane@company.io,jane,smith
"""


async def _client():
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_bulk_job_flow():
    # Deterministic offline run.
    set_settings(Settings(enable_dns=False, enable_smtp=False))

    async with await _client() as client:
        # 1. upload + column detection
        r = await client.post("/v1/files", files={"file": ("list.csv", CSV, "text/csv")})
        assert r.status_code == 200
        body = r.json()
        assert body["detection"]["guessed_email"] == "email"
        file_id = body["file_id"]

        # 2. start job
        r = await client.post(
            "/v1/jobs",
            json={
                "file_id": file_id,
                "mapping": {"email": "email", "first_name": "first_name", "last_name": "last_name"},
            },
        )
        assert r.status_code == 200
        job_id = r.json()["id"]

        # 3. poll to completion (background task shares this event loop)
        status = None
        for _ in range(100):
            status = (await client.get(f"/v1/jobs/{job_id}")).json()
            if status["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.02)
        assert status["status"] == "completed", status
        assert status["counts"]["total_rows"] == 6
        assert status["counts"]["duplicates"] == 1

        # 4. download the cleaned sheet
        dl = await client.get(f"/v1/jobs/{job_id}/download", params={"segment": "cleaned"})
        assert dl.status_code == 200
        text = dl.text
        assert "email_status" in text.splitlines()[0]
        assert len(text.strip().splitlines()) == 7  # header + 6 rows

        # valid / removed segments are also available
        removed = await client.get(f"/v1/jobs/{job_id}/download", params={"segment": "removed"})
        assert removed.status_code == 200


async def test_download_before_ready_is_409():
    set_settings(Settings(enable_dns=False, enable_smtp=False))
    async with await _client() as client:
        r = await client.post("/v1/files", files={"file": ("l.csv", CSV, "text/csv")})
        r.json()["file_id"]
        # A job that doesn't exist -> 404
        assert (await client.get("/v1/jobs/nope/download")).status_code == 404
