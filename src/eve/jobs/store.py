"""Job persistence.

``InMemoryJobStore`` is the default (single-process, zero deps) and is what the
tests and local demo use. ``SqlJobStore`` is the durable backend built on
SQLAlchemy Core (async) — it runs on SQLite (aiosqlite) for local/tests and on
Postgres (asyncpg) in production with no code change.
"""
from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from typing import Optional

from eve.jobs.models import ColumnMapping, Counts, Job, JobStatus


class JobStore(ABC):
    @abstractmethod
    async def create(self, job: Job) -> Job:
        ...

    @abstractmethod
    async def get(self, job_id: str) -> Optional[Job]:
        ...

    @abstractmethod
    async def save(self, job: Job) -> None:
        ...

    @abstractmethod
    async def list(self) -> builtins.list[Job]:
        ...


class InMemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    async def create(self, job: Job) -> Job:
        self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def save(self, job: Job) -> None:
        self._jobs[job.id] = job

    async def list(self) -> builtins.list[Job]:
        return list(self._jobs.values())


def _job_to_row(job: Job) -> dict:
    m = job.mapping
    return {
        "id": job.id,
        "filename": job.filename,
        "file_key": job.file_key,
        "status": job.status.value,
        "webhook_url": job.webhook_url,
        "error": job.error,
        "mapping": None
        if not m
        else {
            "email": m.email,
            "first_name": m.first_name,
            "last_name": m.last_name,
            "passthrough": m.passthrough,
        },
        "counts": job.counts.as_dict(),
        "output_keys": job.output_keys,
    }


def _row_to_job(row: dict) -> Job:
    m = row.get("mapping")
    mapping = (
        ColumnMapping(
            email=m["email"],
            first_name=m.get("first_name"),
            last_name=m.get("last_name"),
            passthrough=m.get("passthrough"),
        )
        if m
        else None
    )
    counts = Counts(**{k: v for k, v in (row.get("counts") or {}).items() if hasattr(Counts, k)})
    return Job(
        id=row["id"],
        filename=row["filename"],
        file_key=row["file_key"],
        status=JobStatus(row["status"]),
        mapping=mapping,
        counts=counts,
        webhook_url=row.get("webhook_url"),
        error=row.get("error"),
        output_keys=dict(row.get("output_keys") or {}),
    )


class SqlJobStore(JobStore):  # pragma: no cover - covered via aiosqlite in tests
    def __init__(self, url: str):
        from sqlalchemy import JSON, Column, MetaData, String, Table, Text
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(url, future=True)
        self._metadata = MetaData()
        self._table = Table(
            "jobs",
            self._metadata,
            Column("id", String(64), primary_key=True),
            Column("filename", String(512)),
            Column("file_key", String(256)),
            Column("status", String(32)),
            Column("webhook_url", String(1024), nullable=True),
            Column("error", Text, nullable=True),
            Column("mapping", JSON, nullable=True),
            Column("counts", JSON),
            Column("output_keys", JSON),
        )

    async def init(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(self._metadata.create_all)

    async def create(self, job: Job) -> Job:
        from sqlalchemy import insert

        async with self._engine.begin() as conn:
            await conn.execute(insert(self._table).values(**_job_to_row(job)))
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            res = await conn.execute(select(self._table).where(self._table.c.id == job_id))
            row = res.mappings().first()
        return _row_to_job(dict(row)) if row else None

    async def save(self, job: Job) -> None:
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._table).where(self._table.c.id == job.id).values(**_job_to_row(job))
            )

    async def list(self) -> builtins.list[Job]:
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            res = await conn.execute(select(self._table))
            return [_row_to_job(dict(r)) for r in res.mappings().all()]


_store: Optional[JobStore] = None


def get_job_store() -> JobStore:
    global _store
    if _store is None:
        _store = InMemoryJobStore()
    return _store


def set_job_store(store: JobStore) -> None:
    global _store
    _store = store
