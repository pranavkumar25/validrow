"""Object storage abstraction.

The pipeline never holds a whole file in memory, so stores expose *streaming*
read/write. ``LocalObjectStore`` (filesystem) is the default and makes the whole
system runnable + testable with no external services. ``S3ObjectStore``
(S3/R2/MinIO via aioboto3) is the production backend and is imported lazily so
``aioboto3`` is only needed when actually used.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from eve.config import get_settings

logger = logging.getLogger(__name__)


class ObjectStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: BinaryIO) -> str:
        """Store a binary stream under ``key``; return the storage ref."""

    @abstractmethod
    async def open_read(self, key: str) -> BinaryIO:
        """Open a binary stream for reading (caller closes)."""

    @abstractmethod
    def new_key(self, suffix: str = "") -> str:
        ...

    @abstractmethod
    async def presigned_download(self, key: str) -> str:
        ...

    async def delete(self, key: str) -> bool:
        """Remove an object. Returns False if it was already gone."""
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    def __init__(self, base_dir: str | Path):
        self.base = Path(base_dir)
        try:
            self.base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Same reasoning as default_db_url: name the setting that fixes it
            # rather than raising a bare PermissionError from inside a mkdir.
            raise RuntimeError(
                f"Cannot create the local storage directory {self.base}: {exc}. "
                "Uploads and result CSVs go to disk unless S3 is configured. On "
                "a read-only or ephemeral filesystem set EVE_S3_BUCKET, "
                "EVE_S3_ACCESS_KEY and EVE_S3_SECRET_KEY instead."
            ) from exc

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

    async def presigned_download(self, key: str) -> str:
        # Local dev: served through the API's own download route.
        return f"/v1/files/{key}/raw"

    async def delete(self, key: str) -> bool:
        try:
            self._path(key).unlink()
            return True
        except FileNotFoundError:
            return False


class S3ObjectStore(ObjectStore):  # pragma: no cover - exercised only with real S3
    """S3 / R2 / MinIO backend. Requires the ``s3`` extra (aioboto3).

    Note on ``open_read``: it buffers the object into memory rather than
    streaming it. Uploads are bounded by ``EVE_MAX_UPLOAD_BYTES`` so this is
    survivable, but it is the one place where the "flat memory" property of the
    pipeline does not hold on the S3 backend.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "",
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region

    def _session(self):
        import aioboto3  # lazy

        return aioboto3.Session()

    def new_key(self, suffix: str = "") -> str:
        return f"{uuid.uuid4().hex}{suffix}"

    def _client(self):
        kwargs = {
            "aws_access_key_id": self.access_key,
            "aws_secret_access_key": self.secret_key,
        }
        # Blank endpoint = real AWS S3; set it for R2/MinIO/Spaces.
        if self.endpoint:
            kwargs["endpoint_url"] = self.endpoint
        if self.region:
            kwargs["region_name"] = self.region
        return self._session().client("s3", **kwargs)

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

    async def presigned_download(self, key: str) -> str:
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600
            )

    async def delete(self, key: str) -> bool:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)
        return True


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Return the process-wide store.

    S3 when a bucket + credentials are configured and ``aioboto3`` imports;
    local disk otherwise. A misconfigured S3 setup raises rather than silently
    falling back to disk — on a multi-instance deploy that fallback would look
    like it worked and then lose every upload.
    """
    global _store
    if _store is not None:
        return _store
    s = get_settings()
    if s.s3_configured:
        try:
            import aioboto3  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "EVE_S3_BUCKET is set but aioboto3 could not be imported. "
                "It is a base dependency, so this is a broken environment "
                "rather than a missing extra: reinstall with pip install -e '.'"
            ) from exc
        logger.info("object store: s3 bucket=%s endpoint=%s", s.s3_bucket, s.s3_endpoint or "aws")
        _store = S3ObjectStore(
            endpoint=s.s3_endpoint,
            access_key=s.s3_access_key,
            secret_key=s.s3_secret_key,
            bucket=s.s3_bucket,
            region=s.s3_region,
        )
    else:
        logger.info("object store: local dir=%s", s.local_storage_dir)
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
