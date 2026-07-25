"""Job domain models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


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
    spam_trap: int = 0

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
            "spam_trap": self.spam_trap,
        }


@dataclass
class Job:
    file_key: str
    filename: str
    mapping: Optional[ColumnMapping] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: JobStatus = JobStatus.PENDING
    counts: Counts = field(default_factory=Counts)
    webhook_url: Optional[str] = None
    error: Optional[str] = None
    # Output object keys, populated on completion.
    output_keys: dict[str, str] = field(default_factory=dict)  # segment -> key

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status.value,
            "counts": self.counts.as_dict(),
            "error": self.error,
            "outputs": list(self.output_keys.keys()),
            "mapping": None
            if not self.mapping
            else {
                "email": self.mapping.email,
                "first_name": self.mapping.first_name,
                "last_name": self.mapping.last_name,
            },
        }
