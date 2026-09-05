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

from eve.jobs.models import ColumnMapping, Counts, Job, JobStatus, Phase
from eve.tenancy import current_workspace_id


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

    async def delete(self, job_id: str) -> bool:
        raise NotImplementedError

    async def init(self) -> None:
        """Prepare the backend. A no-op for stores that need no schema."""
        return None


class InMemoryJobStore(JobStore):
    """Single-process store. Scoped by workspace so it behaves like the SQL one."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    async def create(self, job: Job) -> Job:
        if not job.seq:
            mine = [j.seq for j in self._jobs.values() if j.workspace_id == job.workspace_id]
            job.seq = max(mine, default=0) + 1
        self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        job = self._jobs.get(job_id)
        # A job id from another workspace reads as missing, not as forbidden —
        # confirming it exists would leak that another tenant has it.
        if job is None or job.workspace_id != current_workspace_id():
            return None
        return job

    async def save(self, job: Job) -> None:
        self._jobs[job.id] = job

    async def list(self) -> builtins.list[Job]:
        ws = current_workspace_id()
        return [j for j in self._jobs.values() if j.workspace_id == ws]

    async def delete(self, job_id: str) -> bool:
        if await self.get(job_id) is None:
            return False
        return self._jobs.pop(job_id, None) is not None


def _job_to_row(job: Job) -> dict:
    m = job.mapping
    return {
        "id": job.id,
        "workspace_id": job.workspace_id,
        "seq": job.seq,
        "filename": job.filename,
        "file_key": job.file_key,
        "status": job.status.value,
        "list_type": job.list_type,
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
        "phase": job.phase.value,
        "processed": job.processed,
        "total": job.total,
        "domains_total": job.domains_total,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
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
        workspace_id=row.get("workspace_id") or "default",
        seq=int(row.get("seq") or 0),
        filename=row["filename"],
        file_key=row["file_key"],
        status=JobStatus(row["status"]),
        list_type=row.get("list_type") or "Imports",
        mapping=mapping,
        counts=counts,
        webhook_url=row.get("webhook_url"),
        error=row.get("error"),
        output_keys=dict(row.get("output_keys") or {}),
        phase=Phase(row.get("phase") or Phase.QUEUED.value),
        processed=int(row.get("processed") or 0),
        total=int(row.get("total") or 0),
        domains_total=int(row.get("domains_total") or 0),
        created_at=float(row.get("created_at") or 0.0),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


class SqlJobStore(JobStore):  # pragma: no cover - covered via aiosqlite in tests
    def __init__(self, url: str):
        from sqlalchemy import JSON, Column, Float, Integer, MetaData, String, Table, Text

        from eve.addresses import async_engine

        self._engine = async_engine(url)
        self._metadata = MetaData()
        self._table = Table(
            "jobs",
            self._metadata,
            Column("id", String(64), primary_key=True),
            Column("workspace_id", String(64), index=True),
            Column("seq", Integer),
            Column("filename", String(512)),
            Column("file_key", String(256)),
            Column("status", String(32)),
            Column("list_type", String(64)),
            Column("webhook_url", String(1024), nullable=True),
            Column("error", Text, nullable=True),
            Column("mapping", JSON, nullable=True),
            Column("counts", JSON),
            Column("output_keys", JSON),
            Column("phase", String(32)),
            Column("processed", Integer),
            Column("total", Integer),
            Column("domains_total", Integer),
            Column("created_at", Float),
            Column("started_at", Float, nullable=True),
            Column("finished_at", Float, nullable=True),
        )

    async def init(self) -> None:
        from eve.migrations import run_migrations

        async with self._engine.connect() as conn:
            await conn.run_sync(run_migrations)
            await conn.commit()

    def _scope(self):
        return self._table.c.workspace_id == current_workspace_id()

    async def create(self, job: Job) -> Job:
        from sqlalchemy import func, insert, select

        async with self._engine.begin() as conn:
            if not job.seq:
                # The human-facing "#42" counts up within a workspace, so two
                # tenants both start at #1 instead of interleaving.
                top = (
                    await conn.execute(
                        select(func.max(self._table.c.seq)).where(
                            self._table.c.workspace_id == job.workspace_id
                        )
                    )
                ).scalar()
                job.seq = int(top or 0) + 1
            await conn.execute(insert(self._table).values(**_job_to_row(job)))
        return job

    async def delete(self, job_id: str) -> bool:
        from sqlalchemy import and_, delete

        async with self._engine.begin() as conn:
            res = await conn.execute(
                delete(self._table).where(and_(self._scope(), self._table.c.id == job_id))
            )
        return bool(res.rowcount)

    async def get(self, job_id: str) -> Optional[Job]:
        from sqlalchemy import and_, select

        async with self._engine.connect() as conn:
            res = await conn.execute(
                select(self._table).where(and_(self._scope(), self._table.c.id == job_id))
            )
            row = res.mappings().first()
        return _row_to_job(dict(row)) if row else None

    async def save(self, job: Job) -> None:
        from sqlalchemy import and_, update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._table)
                .where(
                    and_(
                        self._table.c.workspace_id == job.workspace_id,
                        self._table.c.id == job.id,
                    )
                )
                .values(**_job_to_row(job))
            )

    async def list(self) -> builtins.list[Job]:
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            res = await conn.execute(select(self._table).where(self._scope()))
            return [_row_to_job(dict(r)) for r in res.mappings().all()]


_store: Optional[JobStore] = None


def get_job_store() -> JobStore:
    """The process-wide job store.

    Defaults to the durable SQL backend on the workspace database, because the
    product shows run history: a job list that empties on restart would be a
    lie. Tests and embedders override this with ``set_job_store``.
    """
    global _store
    if _store is None:
        from eve.addresses import default_db_url

        _store = SqlJobStore(default_db_url())
    return _store


def set_job_store(store: JobStore) -> None:
    global _store
    _store = store
