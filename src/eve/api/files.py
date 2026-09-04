"""File upload + column detection endpoints (M1)."""
from __future__ import annotations

from typing import BinaryIO

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from eve.api.schemas import ColumnDetection, FileUploadResponse
from eve.config import get_settings
from eve.jobs import csv_io
from eve.ratelimit import RateLimit
from eve.storage import get_object_store

router = APIRouter(prefix="/v1/files", tags=["files"])


class UploadTooLarge(Exception):
    pass


class _CappedReader:
    """Wraps the upload stream and refuses to yield more than ``limit`` bytes.

    The cap has to be enforced during the copy, not from a Content-Length
    header: the header is the client's claim about the body, and a chunked
    upload has none at all.
    """

    def __init__(self, wrapped: BinaryIO, limit: int):
        self._wrapped = wrapped
        self._limit = limit
        self._seen = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._wrapped.read(size)
        self._seen += len(chunk)
        if self._seen > self._limit:
            raise UploadTooLarge(self._limit)
        return chunk


@router.post("", response_model=FileUploadResponse, dependencies=[Depends(RateLimit())])
async def upload_file(file: UploadFile) -> FileUploadResponse:
    """Store an uploaded CSV and return detected columns for mapping."""
    store = get_object_store()
    limit = get_settings().max_upload_bytes
    key = store.new_key("_" + (file.filename or "upload.csv"))

    try:
        await store.put(key, _CappedReader(file.file, limit))
    except UploadTooLarge:
        # Don't leave the partial object behind to be paid for and never read.
        try:
            await store.delete(key)
        except (FileNotFoundError, NotImplementedError):
            pass
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds the {limit // (1024 * 1024)} MB upload limit",
        ) from None

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
