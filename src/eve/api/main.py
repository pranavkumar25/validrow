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

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from eve.addresses import get_address_store
from eve.api import files, jobs, workspace
from eve.api.schemas import HealthResponse, VerifyRequest, VerifyResponse
from eve.config import get_settings
from eve.engine import validate
from eve.jobs.store import get_job_store
from eve.observability import (
    configure_logging,
    describe_backends,
    init_sentry,
    warn_about_configuration,
)
from eve.ratelimit import RateLimit
from eve.smtp_infra import start_blacklist_monitor, stop_blacklist_monitor
from eve.web import mount_web

VERSION = "0.1.0"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s)
    init_sentry(s)

    # Migrate to head on boot so a fresh checkout just runs and a deploy picks
    # up schema changes without a separate step.
    await get_address_store().init()
    await get_job_store().init()

    # Say which backend each subsystem picked. Every one of them falls back to
    # a single-process default, and the failure mode is silent.
    for name, backend in describe_backends(s).items():
        logger.info("backend %s: %s", name, backend)
    warn_about_configuration(s)

    # Watch the egress IPs for DNSBL listings while this process is probing.
    # No-op unless SMTP is on and EVE_SMTP_EGRESS_IPS names IPs to watch.
    start_blacklist_monitor(s)
    try:
        yield
    finally:
        await stop_blacklist_monitor()


app = FastAPI(
    title="Email Validation Engine",
    version=VERSION,
    description="Layered email verification: syntax, normalize, typo, MX, classify, SMTP.",
    lifespan=lifespan,
)

_cors = get_settings().cors_origin_list
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(files.router)
app.include_router(jobs.router)
app.include_router(workspace.router)

# The Validrow web app. Mounted last so it takes "/" without shadowing the API.
mount_web(app)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status="ok",
        version=VERSION,
        smtp_enabled=s.enable_smtp,
        dns_enabled=s.enable_dns,
    )


@app.post(
    "/v1/verify",
    response_model=VerifyResponse,
    tags=["verify"],
    dependencies=[Depends(RateLimit())],
)
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
