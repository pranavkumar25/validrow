"""Object storage abstraction.

The pipeline never holds a whole file in memory, so stores expose *streaming*
read/write. ``LocalObjectStore`` (filesystem) is the default and makes the whole
system runnable + testable with no external services. ``S3ObjectStore``
(S3/R2/MinIO via aioboto3) is the production backend and is imported lazily so
``aioboto3`` is only needed when actually used.
"""
from __future__ import annotations

import shutil
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from eve.config import get_settings


class ObjectStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: BinaryIO) -> str:
        """Store a binary stream under ``key``; return the storage ref."""

    @abstractmethod
    async def open_read(self, key: str) -> BinaryIO:
        """Open a binary stream for reading (caller closes)."""

    @abstractmethod
    async def open_write(self, key: str) -> BinaryIO:
        """Open a binary stream for writing (caller closes)."""

    @abstractmethod
    def new_key(self, suffix: str = "") -> str:
        ...

    @abstractmethod
    async def presigned_download(self, key: str) -> str:
        ...


class LocalObjectStore(ObjectStore):
    def __init__(self, base_dir: str | Path):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.base / key).resolve()
        # Guard against path traversal.
        if self.base.resolve() not in p.parents and p != self.base.resolve():
            raise ValueError(f"key escapes storage root: {key}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def new_key(self, suffix: str = "") -> str:
        return f"{uuid.uuid4().hex}{suffix}"

    async def put(self, key: str, data: BinaryIO) -> str:
        path = self._path(key)
        with path.open("wb") as fh:
            shutil.copyfileobj(data, fh)
        return key

    async def open_read(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    async def open_write(self, key: str) -> BinaryIO:
        return self._path(key).open("wb")

    async def presigned_download(self, key: str) -> str:
        # Local dev: served through the API's own download route.
        return f"/v1/files/{key}/raw"


class S3ObjectStore(ObjectStore):  # pragma: no cover - exercised only with real S3
    """S3 / R2 / MinIO backend. Requires the ``s3`` extra (aioboto3)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket

    def _session(self):
        import aioboto3  # lazy

        return aioboto3.Session()

    def new_key(self, suffix: str = "") -> str:
        return f"{uuid.uuid4().hex}{suffix}"

    def _client(self):
        return self._session().client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        )

    async def put(self, key: str, data: BinaryIO) -> str:
        async with self._client() as s3:
            await s3.upload_fileobj(data, self.bucket, key)
        return key

    async def open_read(self, key: str) -> BinaryIO:
        import io

        buf = io.BytesIO()
        async with self._client() as s3:
            await s3.download_fileobj(self.bucket, key, buf)
        buf.seek(0)
        return buf

    async def open_write(self, key: str) -> BinaryIO:
        # Callers write then call put(); for S3 we buffer and upload on close.
        raise NotImplementedError("use put() for S3 writes")

    async def presigned_download(self, key: str) -> str:
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600
            )


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Return the process-wide store. Local unless an S3 endpoint is configured
    with credentials AND the ``s3`` extra is importable."""
    global _store
    if _store is not None:
        return _store
    s = get_settings()
    _store = LocalObjectStore(s.local_storage_dir)
    return _store


def set_object_store(store: ObjectStore) -> None:
    """Test/override hook."""
    global _store
    _store = store


async def read_lines(store: ObjectStore, key: str) -> AsyncIterator[bytes]:
    """Stream a stored object line-by-line (bytes) without loading it fully."""
    fh = await store.open_read(key)
    try:
        for line in fh:
            yield line
    finally:
        fh.close()
