"""File upload + column detection endpoints (M1)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from eve.api.schemas import ColumnDetection, FileUploadResponse
from eve.jobs import csv_io
from eve.storage import get_object_store

router = APIRouter(prefix="/v1/files", tags=["files"])


@router.post("", response_model=FileUploadResponse)
async def upload_file(file: UploadFile) -> FileUploadResponse:
    """Store an uploaded CSV and return detected columns for mapping."""
    store = get_object_store()
    key = store.new_key("_" + (file.filename or "upload.csv"))
    await store.put(key, file.file)

    det = await csv_io.detect_columns(store, key)
    return FileUploadResponse(
        file_id=key,
        filename=file.filename or key,
        detection=ColumnDetection(
            columns=det.columns,
            sample_rows=det.sample_rows,
            guessed_email=det.guessed_email,
            guessed_first_name=det.guessed_first_name,
            guessed_last_name=det.guessed_last_name,
            delimiter=det.delimiter,
        ),
    )


@router.get("/{key}/raw")
async def download_raw(key: str) -> StreamingResponse:
    """Serve a stored object (local-dev stand-in for a presigned URL)."""
    store = get_object_store()
    try:
        fh = await store.open_read(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    def _iter():
        try:
            while chunk := fh.read(65536):
                yield chunk
        finally:
            fh.close()

    return StreamingResponse(_iter(), media_type="text/csv")
