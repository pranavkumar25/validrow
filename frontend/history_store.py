"""Local-disk history store for the Streamlit front-end.

A tiny, dependency-free stand-in for a real database. It persists every bulk
validation run to ``frontend/.eve_store/`` so the dashboard can show past
uploads, ongoing validations and aggregate analytics across sessions — no
login and no DB required (those get wired in later).

Layout::

    .eve_store/
        history.json          # index: list of run records
        <run_id>/cleaned.csv  # cached outputs (survive API restarts)
        <run_id>/valid.csv
        <run_id>/removed.csv
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

STORE_DIR = Path(__file__).resolve().parent / ".eve_store"
INDEX = STORE_DIR / "history.json"

# Count keys mirrored from the API's job "counts" dict.
COUNT_KEYS = [
    "total_rows", "unique_emails", "duplicates",
    "valid", "risky", "unknown", "invalid", "disposable", "spam_trap",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure() -> None:
    STORE_DIR.mkdir(exist_ok=True)
    if not INDEX.exists():
        INDEX.write_text("[]", encoding="utf-8")


def _read() -> list[dict[str, Any]]:
    _ensure()
    try:
        return json.loads(INDEX.read_text(encoding="utf-8") or "[]")
    except (json.JSONDecodeError, OSError):
        return []


def _write(records: list[dict[str, Any]]) -> None:
    _ensure()
    INDEX.write_text(json.dumps(records, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def list_runs() -> list[dict[str, Any]]:
    """All runs, newest first."""
    return sorted(_read(), key=lambda r: r.get("created_at", ""), reverse=True)


def get(run_id: str) -> Optional[dict[str, Any]]:
    for r in _read():
        if r["id"] == run_id:
            return r
    return None


def record_started(job_id: str, filename: str, mapping: dict[str, Any]) -> str:
    """Create a 'processing' record when a job is kicked off. Returns run_id."""
    recs = _read()
    run_id = uuid.uuid4().hex[:12]
    recs.append({
        "id": run_id,
        "job_id": job_id,
        "filename": filename,
        "mapping": mapping,
        "status": "processing",
        "counts": {},
        "created_at": _now(),
        "completed_at": None,
        "outputs": [],
    })
    _write(recs)
    return run_id


def update(run_id: str, *, counts: Optional[dict] = None, status: Optional[str] = None) -> None:
    recs = _read()
    for r in recs:
        if r["id"] == run_id:
            if counts is not None:
                r["counts"] = counts
            if status is not None:
                r["status"] = status
    _write(recs)


def complete(run_id: str, counts: dict[str, int], outputs: dict[str, bytes]) -> None:
    """Mark a run complete and cache its output CSVs to disk."""
    run_dir = STORE_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for seg, data in outputs.items():
        if data:
            (run_dir / f"{seg}.csv").write_bytes(data)
            saved.append(seg)
    recs = _read()
    for r in recs:
        if r["id"] == run_id:
            r["counts"] = counts
            r["status"] = "completed"
            r["completed_at"] = _now()
            r["outputs"] = saved
    _write(recs)


def output_bytes(run_id: str, segment: str) -> Optional[bytes]:
    p = STORE_DIR / run_id / f"{segment}.csv"
    return p.read_bytes() if p.exists() else None


def delete(run_id: str) -> None:
    _write([r for r in _read() if r["id"] != run_id])
    shutil.rmtree(STORE_DIR / run_id, ignore_errors=True)


def clear_all() -> None:
    for r in _read():
        shutil.rmtree(STORE_DIR / r["id"], ignore_errors=True)
    _write([])


# --------------------------------------------------------------------------- #
# Analytics helpers
# --------------------------------------------------------------------------- #
def invalid_total(counts: dict[str, int]) -> int:
    return counts.get("invalid", 0) + counts.get("disposable", 0) + counts.get("spam_trap", 0)


def valid_rate(counts: dict[str, int]) -> float:
    uniq = counts.get("unique_emails") or 0
    return (counts.get("valid", 0) / uniq) if uniq else 0.0


def aggregate() -> dict[str, Any]:
    """Roll every completed run into headline numbers + a per-day timeline."""
    runs = _read()
    completed = [r for r in runs if r.get("status") == "completed"]
    ongoing = [r for r in runs if r.get("status") in ("processing", "pending")]

    status_totals = {k: 0 for k in ["valid", "risky", "unknown", "invalid", "disposable", "spam_trap"]}
    verified = 0
    valid = 0
    by_date: dict[str, dict[str, int]] = {}

    for r in completed:
        c = r.get("counts", {})
        uniq = c.get("unique_emails", 0)
        verified += uniq
        valid += c.get("valid", 0)
        for k in status_totals:
            status_totals[k] += c.get(k, 0)
        day = (r.get("completed_at") or r.get("created_at") or "")[:10]
        if day:
            slot = by_date.setdefault(day, {"verified": 0, "valid": 0, "invalid": 0})
            slot["verified"] += uniq
            slot["valid"] += c.get("valid", 0)
            slot["invalid"] += invalid_total(c)

    return {
        "runs_total": len(runs),
        "runs_completed": len(completed),
        "ongoing": ongoing,
        "addresses_verified": verified,
        "valid_total": valid,
        "avg_valid_rate": (valid / verified) if verified else 0.0,
        "status_totals": status_totals,
        "by_date": by_date,
        "recent": sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True),
    }
