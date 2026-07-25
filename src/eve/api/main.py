"""FastAPI application.

Surface:
    GET  /health
    POST /v1/verify                      (real-time single address)
    POST /v1/files                       (upload CSV -> detect columns)
    POST /v1/jobs                        (start bulk verification)
    GET  /v1/jobs/{id}                   (status + counts)
    GET  /v1/jobs/{id}/download          (cleaned | valid | removed CSV)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from eve.api import files, jobs
from eve.api.schemas import HealthResponse, VerifyRequest, VerifyResponse
from eve.config import get_settings
from eve.engine import validate

VERSION = "0.1.0"

app = FastAPI(
    title="Email Validation Engine",
    version=VERSION,
    description="Layered email verification: syntax, normalize, typo, MX, classify, SMTP.",
)

app.include_router(files.router)
app.include_router(jobs.router)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status="ok",
        version=VERSION,
        smtp_enabled=s.enable_smtp,
        dns_enabled=s.enable_dns,
    )


@app.post("/v1/verify", response_model=VerifyResponse, tags=["verify"])
async def verify(req: VerifyRequest) -> VerifyResponse:
    """Validate a single address in real time.

    Runs the (blocking, DNS-touching) engine in a threadpool so the event loop
    stays free.
    """
    verdict = await run_in_threadpool(
        validate,
        req.email,
        enable_dns=req.check_dns,
        enable_smtp=req.check_smtp,
    )
    return VerifyResponse(**verdict.to_dict())
