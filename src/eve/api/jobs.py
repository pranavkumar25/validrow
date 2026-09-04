"""Bulk job endpoints (M1): create, status, download."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from eve.addresses import DEFAULT_LIST_TYPE, LIST_TYPES, get_address_store
from eve.api.schemas import CreateJobRequest, JobResponse
from eve.jobs.models import ColumnMapping, Job
from eve.jobs.queue import enqueue_job
from eve.jobs.store import get_job_store
from eve.ratelimit import RateLimit
from eve.storage import get_object_store

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, dependencies=[Depends(RateLimit())])
async def create_job(req: CreateJobRequest) -> JobResponse:
    store = get_object_store()
    job_store = get_job_store()

    try:
        await store.open_read(req.file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file_id not found") from exc

    list_type = req.list_type or DEFAULT_LIST_TYPE
    if list_type not in LIST_TYPES:
        raise HTTPException(
            status_code=422, detail=f"list_type must be one of {LIST_TYPES}"
        )

    job = Job(
        file_key=req.file_id,
        filename=req.filename or req.file_id,
        list_type=list_type,
        mapping=ColumnMapping(
            email=req.mapping.email,
            first_name=req.mapping.first_name,
            last_name=req.mapping.last_name,
        ),
        webhook_url=req.webhook_url,
    )
    await job_store.create(job)
    await enqueue_job(job)

    return JobResponse(**job.as_dict())


@router.get("", response_model=list[JobResponse])
async def list_jobs() -> list[JobResponse]:
    """Every run this workspace has done, newest first."""
    jobs = await get_job_store().list()
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [JobResponse(**j.as_dict()) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    job = await get_job_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse(**job.as_dict())


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str, keep_addresses: bool = Query(True)) -> None:
    """Delete a run and its cached outputs.

    The validated addresses stay in the workspace by default — they are facts
    about mailboxes, not artefacts of the run that discovered them. Pass
    ``keep_addresses=false`` to drop those too.
    """
    job_store = get_job_store()
    job = await job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    store = get_object_store()
    for key in job.output_keys.values():
        try:
            await store.delete(key)
        except (FileNotFoundError, NotImplementedError):
            pass
    if not keep_addresses:
        await get_address_store().delete_by_job(job_id)
    await job_store.delete(job_id)


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
