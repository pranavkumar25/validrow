"""Workspace endpoints: addresses, analytics rollups and export slices.

These read the workspace read-model (:mod:`eve.addresses`) rather than the
per-job CSVs, because every question here spans jobs: "every address we have
ever validated, de-duplicated", "which domains dominate this workspace", "give
me the undeliverables to suppress".
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from eve.addresses import get_address_store
from eve.api.schemas import AddressOut, AddressPage, ExportRequest
from eve.verdict import SubStatus, primary_verdict

router = APIRouter(prefix="/v1", tags=["workspace"])

# Export presets that are defined by a sub-reason rather than by a verdict.
_PRESET_SUB = {
    "catchall": SubStatus.CATCH_ALL.value,
    "disposable": SubStatus.DISPOSABLE.value,
}

_FULL_COLUMNS = [
    "email",
    "domain",
    "verdict",
    "status",
    "sub_status",
    "score",
    "mx_found",
    "settled_at_layer",
    "source_job",
    "checked_at",
]


def _to_out(row: dict) -> AddressOut:
    return AddressOut(
        email=row["email"],
        domain=row.get("domain"),
        status=row["status"],
        sub_status=row.get("sub_status") or "unknown",
        verdict=primary_verdict(row["status"]),
        score=int(row.get("score") or 0),
        job_id=row.get("job_id"),
        job_filename=row.get("job_filename"),
        list_type=row.get("list_type"),
        checked_at=row.get("checked_at"),
        settled_at=row.get("settled_at"),
        mx_found=bool(row.get("mx_found")),
        is_catch_all=bool(row.get("is_catch_all")),
        is_disposable=bool(row.get("is_disposable")),
        is_role=bool(row.get("is_role")),
        is_free=bool(row.get("is_free")),
    )


@router.get("/addresses", response_model=AddressPage)
async def list_addresses(
    verdict: list[str] = Query(default_factory=list),
    q: Optional[str] = None,
    list_type: Optional[str] = None,
    sort: str = "recent",
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
) -> AddressPage:
    """Addresses across every run in this workspace, de-duplicated.

    One row per mailbox rather than one per appearance, so an address that
    arrived in three lists is counted once and carries the most recent verdict
    it was given.
    """
    rows, total = await get_address_store().query(
        verdicts=verdict or None,
        search=q,
        list_type=list_type,
        sort=sort,
        limit=size,
        offset=(page - 1) * size,
    )
    return AddressPage(
        rows=[_to_out(r) for r in rows], total=total, page=page, size=size
    )


@router.get("/analytics")
async def analytics() -> dict:
    """Everything the Analytics screen reports, in one round-trip."""
    store = get_address_store()
    headline = await store.headline()
    return {
        "headline": headline,
        "totals": await store.totals(),
        "by_day": await store.by_day(),
        "top_domains": await store.top_domains(),
        "settled_by_layer": await store.settled_breakdown(),
    }


@router.post("/exports")
async def export_slice(req: ExportRequest) -> StreamingResponse:
    """Stream a filtered slice of the workspace as CSV."""
    store = get_address_store()
    rows = await store.stream(
        verdicts=req.verdicts or None,
        search=req.search,
        list_type=req.list_type,
        sub_reason=_PRESET_SUB.get(req.preset or ""),
    )

    if req.emails:
        wanted = {e.strip().lower() for e in req.emails}
        rows = [r for r in rows if r["email"] in wanted]
    if req.require_mx:
        rows = [r for r in rows if r.get("mx_found")]
    if req.exclude_disposable:
        rows = [r for r in rows if not r.get("is_disposable")]

    email_only = req.columns == "email"
    header = ["email"] if email_only else _FULL_COLUMNS

    def _iter():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        for r in rows:
            if email_only:
                writer.writerow([r["email"]])
            else:
                writer.writerow(
                    [
                        r["email"],
                        r.get("domain") or "",
                        primary_verdict(r["status"]),
                        r["status"],
                        r.get("sub_status") or "",
                        r.get("score") or 0,
                        r.get("mx_found") or False,
                        r.get("settled_at") or "",
                        r.get("job_filename") or "",
                        r.get("checked_day") or "",
                    ]
                )
            if buf.tell() > 32768:
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate(0)
        if buf.tell():
            yield buf.getvalue().encode("utf-8")

    name = (req.preset or "-".join(req.verdicts) or "all") + "-addresses.csv"
    return StreamingResponse(
        _iter(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
