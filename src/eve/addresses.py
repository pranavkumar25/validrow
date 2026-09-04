"""The workspace read-model: every address this workspace has ever validated.

The engine writes CSVs; this store is what the product *reads*. It backs the
Contacts screen, the dashboard's recent-results table, the analytics rollups and
the export slices — anything that has to answer a question across jobs rather
than within one file.

Addresses are keyed by ``(workspace_id, normalized address)``, so re-running a
list updates the row in place rather than double-counting it. That is what makes
"every address across all completed jobs, de-duplicated" true — and scoping the
key to the workspace is what keeps it true once more than one tenant shares the
database. See :mod:`eve.tenancy`.

Runs on SQLite (aiosqlite) out of the box and on Postgres (asyncpg) by setting
``EVE_WORKSPACE_DB_URL`` — both are already hard dependencies, and the schema is
plain SQLAlchemy Core, so neither path needs a code change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from eve.config import get_settings
from eve.tenancy import current_workspace_id
from eve.trace import settled_layer
from eve.verdict import VERDICT_ORDER, Verdict, primary_verdict

# Sort keys the Contacts screen offers, mapped to (column, descending).
SORTS = {
    "recent": ("checked_at", True),
    "scoreUp": ("score", False),
    "scoreDown": ("score", True),
    "az": ("email", False),
}

#: The list types a job can be tagged with. Drives the dashboard tab strip.
LIST_TYPES = ["Cold outreach", "Newsletter", "Transactional", "Imports"]
DEFAULT_LIST_TYPE = "Imports"


@dataclass
class AddressRecord:
    """One validated address, denormalized for reading."""

    email: str
    domain: str
    status: str
    sub_status: str
    score: int
    job_id: str
    job_filename: str
    list_type: str = DEFAULT_LIST_TYPE
    checked_at: float = 0.0
    mx_found: bool = False
    is_catch_all: bool = False
    is_disposable: bool = False
    is_role: bool = False
    is_free: bool = False
    # Whether the mailbox probe actually ran. This is a *fact* about the run;
    # `settled_at` below is a derivation from it. Facts age well — derivations go
    # stale the moment the rule that produced them changes — so reporting
    # recomputes the layer from this rather than trusting the stored number.
    smtp_ran: bool = False
    settled_at: int = 6
    checks: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_verdict(
        cls,
        v: Verdict,
        *,
        job_id: str,
        job_filename: str,
        list_type: str = DEFAULT_LIST_TYPE,
        checked_at: Optional[float] = None,
    ) -> AddressRecord:
        status = v.status.value
        sub = v.sub_status.value
        smtp = (v.checks or {}).get("smtp")
        smtp_ran = smtp is not None and smtp.get("detail") != "smtp_disabled"
        return cls(
            email=v.normalized_email or v.email,
            domain=v.domain or "",
            status=status,
            sub_status=sub,
            score=int(v.score or 0),
            job_id=job_id,
            job_filename=job_filename,
            list_type=list_type,
            checked_at=checked_at if checked_at is not None else _now(),
            mx_found=bool(v.mx_found),
            is_catch_all=bool(v.is_catch_all),
            is_disposable=bool(v.is_disposable),
            is_role=bool(v.is_role),
            is_free=bool(v.is_free),
            smtp_ran=smtp_ran,
            settled_at=settled_layer(status, sub, smtp_ran=smtp_ran),
            checks=v.checks or {},
        )

    def as_row(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "domain": self.domain,
            "status": self.status,
            "sub_status": self.sub_status,
            "score": self.score,
            "job_id": self.job_id,
            "job_filename": self.job_filename,
            "list_type": self.list_type,
            "checked_at": self.checked_at,
            "checked_day": _day(self.checked_at),
            "mx_found": self.mx_found,
            "is_catch_all": self.is_catch_all,
            "is_disposable": self.is_disposable,
            "is_role": self.is_role,
            "is_free": self.is_free,
            "smtp_ran": self.smtp_ran,
            "settled_at": self.settled_at,
            "checks": self.checks,
        }


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def default_db_url() -> str:
    s = get_settings()
    if s.workspace_db_url:
        return s.workspace_db_url
    root = os.path.abspath(s.local_storage_dir)
    os.makedirs(root, exist_ok=True)
    return f"sqlite+aiosqlite:///{os.path.join(root, 'workspace.db')}"


class AddressStore:
    def __init__(self, url: Optional[str] = None):
        from sqlalchemy import (
            JSON,
            Boolean,
            Column,
            Float,
            Integer,
            MetaData,
            String,
            Table,
        )
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(url or default_db_url(), future=True)
        self._metadata = MetaData()
        self.t = Table(
            "addresses",
            self._metadata,
            # Identity is (workspace, normalized address): re-validating a list
            # refreshes the row instead of creating a duplicate, and two tenants
            # who validate the same mailbox each keep their own verdict.
            Column("workspace_id", String(64), primary_key=True),
            Column("email", String(320), primary_key=True),
            Column("domain", String(255), index=True),
            Column("status", String(32), index=True),
            Column("sub_status", String(64)),
            Column("score", Integer),
            Column("job_id", String(64), index=True),
            Column("job_filename", String(512)),
            Column("list_type", String(64), index=True),
            Column("checked_at", Float, index=True),
            Column("checked_day", String(10), index=True),
            Column("mx_found", Boolean),
            Column("is_catch_all", Boolean),
            Column("is_disposable", Boolean),
            Column("is_role", Boolean),
            Column("is_free", Boolean),
            Column("smtp_ran", Boolean),
            Column("settled_at", Integer),
            Column("checks", JSON),
        )

    async def init(self) -> None:
        """Bring the database to the current schema.

        Alembic owns the schema outright — there is no ``create_all`` fallback,
        so a fresh SQLite file and a long-lived Postgres arrive at the same
        place by replaying the same revisions.
        """
        from eve.migrations import run_migrations

        async with self._engine.connect() as conn:
            await conn.run_sync(run_migrations)
            await conn.commit()

    # --- writes ----------------------------------------------------------
    async def upsert_many(
        self, records: list[AddressRecord], *, workspace_id: Optional[str] = None
    ) -> None:
        """Insert or refresh a batch. Last write for an address wins."""
        if not records:
            return
        from sqlalchemy import and_, delete, insert

        ws = workspace_id or current_workspace_id()
        rows = [{**r.as_row(), "workspace_id": ws} for r in records]
        emails = [r["email"] for r in rows]
        async with self._engine.begin() as conn:
            # Portable upsert: clear the keys we are about to write, then insert.
            # Chunked so we stay well inside SQLite's variable limit.
            for i in range(0, len(emails), 400):
                chunk = emails[i : i + 400]
                await conn.execute(
                    delete(self.t).where(
                        and_(self.t.c.workspace_id == ws, self.t.c.email.in_(chunk))
                    )
                )
            await conn.execute(insert(self.t), rows)

    async def delete_by_job(self, job_id: str, *, workspace_id: Optional[str] = None) -> int:
        from sqlalchemy import and_, delete

        ws = workspace_id or current_workspace_id()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                delete(self.t).where(
                    and_(self.t.c.workspace_id == ws, self.t.c.job_id == job_id)
                )
            )
            return res.rowcount or 0

    # --- reads -----------------------------------------------------------
    def _filtered(
        self, *, verdicts=None, search=None, list_type=None, sub_reason=None, workspace_id=None
    ):
        from sqlalchemy import or_, select

        # Every read starts scoped. Filters are refinements on top of the
        # workspace, never a way to escape it.
        stmt = select(self.t).where(
            self.t.c.workspace_id == (workspace_id or current_workspace_id())
        )
        if verdicts:
            # Four display verdicts map onto six engine statuses.
            wanted = [s for s in _STATUSES if primary_verdict(s) in verdicts]
            stmt = stmt.where(self.t.c.status.in_(wanted))
        if search:
            like = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                or_(self.t.c.email.like(like), self.t.c.domain.like(like))
            )
        if list_type and list_type != "All lists":
            stmt = stmt.where(self.t.c.list_type == list_type)
        if sub_reason:
            stmt = stmt.where(self.t.c.sub_status == sub_reason)
        return stmt

    def _scope(self, workspace_id: Optional[str] = None):
        """The workspace predicate every aggregate has to carry.

        Aggregates that skip it would report one tenant's totals to another —
        a leak that shows up as wrong numbers rather than an error, so it is
        spelled out at each call site instead of being applied by convention.
        """
        return self.t.c.workspace_id == (workspace_id or current_workspace_id())

    async def query(
        self,
        *,
        verdicts: Optional[list[str]] = None,
        search: Optional[str] = None,
        list_type: Optional[str] = None,
        sub_reason: Optional[str] = None,
        sort: str = "recent",
        limit: int = 50,
        offset: int = 0,
        workspace_id: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """A page of addresses plus the total matching the same filters."""
        from sqlalchemy import func, select

        col_name, desc = SORTS.get(sort, SORTS["recent"])
        col = self.t.c[col_name]
        stmt = self._filtered(
            verdicts=verdicts,
            search=search,
            list_type=list_type,
            sub_reason=sub_reason,
            workspace_id=workspace_id,
        )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        page_stmt = stmt.order_by(col.desc() if desc else col.asc()).limit(limit).offset(offset)

        async with self._engine.connect() as conn:
            total = (await conn.execute(count_stmt)).scalar() or 0
            rows = (await conn.execute(page_stmt)).mappings().all()
        return [dict(r) for r in rows], int(total)

    async def stream(self, **filters) -> list[dict]:
        """Every matching row, for exports."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(self._filtered(**filters))).mappings().all()
        return [dict(r) for r in rows]

    async def is_empty(self, *, workspace_id: Optional[str] = None) -> bool:
        from sqlalchemy import func, select

        async with self._engine.connect() as conn:
            n = (
                await conn.execute(
                    select(func.count()).select_from(self.t).where(self._scope(workspace_id))
                )
            ).scalar() or 0
        return n == 0

    async def totals(
        self, *, list_type: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> dict[str, int]:
        """Address count per primary verdict."""
        from sqlalchemy import func, select

        stmt = (
            select(self.t.c.status, func.count())
            .where(self._scope(workspace_id))
            .group_by(self.t.c.status)
        )
        if list_type and list_type != "All lists":
            stmt = stmt.where(self.t.c.list_type == list_type)
        out = {v.value: 0 for v in VERDICT_ORDER}
        async with self._engine.connect() as conn:
            for status, n in (await conn.execute(stmt)).all():
                out[primary_verdict(status)] += int(n)
        return out

    async def headline(self, *, workspace_id: Optional[str] = None) -> dict[str, Any]:
        """Workspace KPIs: total, unique domains, average score."""
        from sqlalchemy import func, select

        scope = self._scope(workspace_id)
        async with self._engine.connect() as conn:
            total = (
                await conn.execute(select(func.count()).select_from(self.t).where(scope))
            ).scalar() or 0
            domains = (
                await conn.execute(
                    select(func.count(func.distinct(self.t.c.domain))).where(scope)
                )
            ).scalar() or 0
            avg = (await conn.execute(select(func.avg(self.t.c.score)).where(scope))).scalar()
        return {
            "total": int(total),
            "domains": int(domains),
            "avg_score": int(round(avg)) if avg is not None else None,
        }

    async def by_day(self, *, workspace_id: Optional[str] = None) -> dict[str, dict[str, int]]:
        """``{'2026-07-24': {'deliverable': 12, ...}}`` — the series source."""
        from sqlalchemy import func, select

        stmt = (
            select(self.t.c.checked_day, self.t.c.status, func.count())
            .where(self._scope(workspace_id))
            .group_by(self.t.c.checked_day, self.t.c.status)
        )
        out: dict[str, dict[str, int]] = {}
        async with self._engine.connect() as conn:
            for day, status, n in (await conn.execute(stmt)).all():
                if not day:
                    continue
                slot = out.setdefault(day, {v.value: 0 for v in VERDICT_ORDER})
                slot[primary_verdict(status)] += int(n)
        return out

    async def top_domains(
        self, limit: int = 8, *, workspace_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Busiest domains with their verdict mix — the Analytics table."""
        from sqlalchemy import and_, func, select

        scope = self._scope(workspace_id)
        counts = (
            select(self.t.c.domain, func.count().label("n"))
            .where(scope)
            .group_by(self.t.c.domain)
            .order_by(func.count().desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            tops = [(d, int(n)) for d, n in (await conn.execute(counts)).all() if d]
            if not tops:
                return []
            names = [d for d, _ in tops]
            mix_stmt = (
                select(self.t.c.domain, self.t.c.status, func.count())
                .where(and_(scope, self.t.c.domain.in_(names)))
                .group_by(self.t.c.domain, self.t.c.status)
            )
            mix_rows = (await conn.execute(mix_stmt)).all()

        mixes: dict[str, dict[str, int]] = {
            d: {v.value: 0 for v in VERDICT_ORDER} for d in names
        }
        for domain, status, n in mix_rows:
            mixes[domain][primary_verdict(status)] += int(n)
        return [{"domain": d, "count": n, "mix": mixes[d]} for d, n in tops]

    async def settled_breakdown(self, *, workspace_id: Optional[str] = None) -> dict[int, int]:
        """How many addresses each layer settled.

        Derived on read from (status, sub_status, smtp_ran) rather than read
        back from ``settled_at``, so a change to the settling rule takes effect
        immediately instead of waiting for every row to be re-validated.
        """
        from sqlalchemy import func, select

        stmt = (
            select(self.t.c.status, self.t.c.sub_status, self.t.c.smtp_ran, func.count())
            .where(self._scope(workspace_id))
            .group_by(self.t.c.status, self.t.c.sub_status, self.t.c.smtp_ran)
        )

        out: dict[int, int] = {}
        async with self._engine.connect() as conn:
            for status, sub, ran, n in (await conn.execute(stmt)).all():
                layer = settled_layer(status or "", sub or "", smtp_ran=bool(ran))
                out[layer] = out.get(layer, 0) + int(n)
        return out

    async def sub_reason_count(
        self, sub_status: str, *, workspace_id: Optional[str] = None
    ) -> int:
        from sqlalchemy import and_, func, select

        async with self._engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        select(func.count())
                        .select_from(self.t)
                        .where(
                            and_(
                                self._scope(workspace_id),
                                self.t.c.sub_status == sub_status,
                            )
                        )
                    )
                ).scalar()
                or 0
            )

    async def get(self, email: str, *, workspace_id: Optional[str] = None) -> Optional[dict]:
        from sqlalchemy import and_, select

        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(self.t).where(
                            and_(
                                self._scope(workspace_id),
                                self.t.c.email == email.lower(),
                            )
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None


_STATUSES = ["valid", "invalid", "risky", "unknown", "disposable", "spam_trap"]

_store: Optional[AddressStore] = None


def get_address_store() -> AddressStore:
    global _store
    if _store is None:
        _store = AddressStore()
    return _store


def set_address_store(store: Optional[AddressStore]) -> None:
    """Override the process-wide store (tests / explicit configuration)."""
    global _store
    _store = store
