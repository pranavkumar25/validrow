"""Job domain models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from eve.tenancy import current_workspace_id


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Phase(str, Enum):
    """Which pass of the pipeline is running, for the live progress readout.

    The pipeline makes two streaming passes over the file with a resolve and a
    verify stage between them; the UI names the stage rather than showing a bare
    percentage, so a long MX-resolution stall reads as work rather than a hang.
    """

    QUEUED = "queued"
    READING = "reading"
    RESOLVING = "resolving"
    VERIFYING = "verifying"
    ASSEMBLING = "assembling"
    DONE = "done"


@dataclass
class ColumnMapping:
    """Which CSV columns mean what. ``email`` is required."""

    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    # Columns to carry through untouched (defaults to "all original columns").
    passthrough: Optional[list[str]] = None


@dataclass
class Counts:
    total_rows: int = 0
    unique_emails: int = 0
    duplicates: int = 0
    valid: int = 0
    invalid: int = 0
    risky: int = 0
    unknown: int = 0
    disposable: int = 0

    def bump(self, status: str) -> None:
        if hasattr(self, status):
            setattr(self, status, getattr(self, status) + 1)

    def as_dict(self) -> dict[str, int]:
        return {
            "total_rows": self.total_rows,
            "unique_emails": self.unique_emails,
            "duplicates": self.duplicates,
            "valid": self.valid,
            "invalid": self.invalid,
            "risky": self.risky,
            "unknown": self.unknown,
            "disposable": self.disposable,
        }


@dataclass
class Job:
    file_key: str
    filename: str
    mapping: Optional[ColumnMapping] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Which workspace owns this run. Captured at construction from the current
    # context so a job can never be created without one.
    workspace_id: str = field(default_factory=current_workspace_id)
    status: JobStatus = JobStatus.PENDING
    counts: Counts = field(default_factory=Counts)
    webhook_url: Optional[str] = None
    error: Optional[str] = None
    # Output object keys, populated on completion.
    output_keys: dict[str, str] = field(default_factory=dict)  # segment -> key

    # --- Presentation / progress ----------------------------------------
    # Short human-facing number ("#42"), assigned by the store on create. The
    # uuid stays the real key; this is only ever shown to a person.
    seq: int = 0
    # Which list this is, so the workspace can be sliced by campaign type.
    list_type: str = "Imports"
    # Live progress. `processed`/`total` count unique addresses, not source rows,
    # because that is what the user is charged for and what takes the time.
    phase: Phase = Phase.QUEUED
    processed: int = 0
    total: int = 0
    # Distinct domains in the file, so the resolve phase can name its work.
    domains_total: int = 0
    created_at: float = field(default_factory=_now)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def progress(self) -> float:
        """0.0-1.0. Reports 1.0 for a finished job even if totals never landed."""
        if self.status is JobStatus.COMPLETED:
            return 1.0
        if not self.total:
            return 0.0
        return max(0.0, min(1.0, self.processed / self.total))

    @property
    def duration(self) -> Optional[float]:
        """Seconds the run took, or has been running for."""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else _now()
        return max(0.0, end - self.started_at)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "seq": self.seq,
            "filename": self.filename,
            "status": self.status.value,
            "list_type": self.list_type,
            "counts": self.counts.as_dict(),
            "error": self.error,
            "outputs": list(self.output_keys.keys()),
            "phase": self.phase.value,
            "processed": self.processed,
            "total": self.total,
            "domains_total": self.domains_total,
            "progress": self.progress,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": self.duration,
            "mapping": None
            if not self.mapping
            else {
                "email": self.mapping.email,
                "first_name": self.mapping.first_name,
                "last_name": self.mapping.last_name,
            },
        }
