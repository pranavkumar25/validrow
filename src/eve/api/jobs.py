"""Bulk job endpoints (M1): create, status, download."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from eve.api.schemas import CreateJobRequest, JobResponse
from eve.jobs.models import ColumnMapping, Job
from eve.jobs.pipeline import run_job
from eve.jobs.store import get_job_store
from eve.smtp_infra import get_async_prober
from eve.storage import get_object_store

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])

# Hold references to background tasks so they aren't garbage-collected mid-run.
_running: set[asyncio.Task] = set()


@router.post("", response_model=JobResponse)
async def create_job(req: CreateJobRequest) -> JobResponse:
    store = get_object_store()
    job_store = get_job_store()

    try:
        await store.open_read(req.file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file_id not found") from exc

    job = Job(
        file_key=req.file_id,
        filename=req.file_id,
        mapping=ColumnMapping(
            email=req.mapping.email,
            first_name=req.mapping.first_name,
            last_name=req.mapping.last_name,
        ),
        webhook_url=req.webhook_url,
    )
    await job_store.create(job)

    task = asyncio.create_task(
        run_job(job, store=store, job_store=job_store, prober=get_async_prober())
    )
    _running.add(task)
    task.add_done_callback(_running.discard)

    return JobResponse(**job.as_dict())


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    job = await get_job_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse(**job.as_dict())


@router.get("/{job_id}/download")
async def download_job(
    job_id: str,
    segment: str = Query("cleaned", pattern="^(cleaned|valid|removed)$"),
) -> StreamingResponse:
    job = await get_job_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    key = job.output_keys.get(segment)
    if not key:
        raise HTTPException(status_code=409, detail=f"segment '{segment}' not ready")

    store = get_object_store()
    fh = await store.open_read(key)

    def _iter():
        try:
            while chunk := fh.read(65536):
                yield chunk
        finally:
            fh.close()

    filename = f"{job_id}_{segment}.csv"
    return StreamingResponse(
        _iter(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
