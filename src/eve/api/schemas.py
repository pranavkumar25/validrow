"""Request/response models for the API (Pydantic v2).

NB: These are Pydantic models, so their annotations ARE evaluated at runtime.
Use `typing` constructs (not PEP 604 `X | None`) so the models load on
Python 3.9 as well as 3.12. Ruff is configured with `keep-runtime-typing` to
avoid rewriting these back to the newer syntax.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class VerifyRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=320, examples=["john.doe@gmail.com"])
    # Optional per-request overrides (default: use server config).
    check_dns: Optional[bool] = None
    check_smtp: Optional[bool] = None


class VerifyResponse(BaseModel):
    email: str
    status: str
    sub_status: str
    score: int
    normalized_email: Optional[str] = None
    dedupe_key: Optional[str] = None
    domain: Optional[str] = None
    suggested_correction: Optional[str] = None
    is_disposable: bool
    is_role: bool
    is_free: bool
    is_catch_all: bool
    mx_found: bool
    tags: list[str]
    checks: dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    version: str
    smtp_enabled: bool
    dns_enabled: bool


# --- File pipeline (M1) ---------------------------------------------------


class ColumnDetection(BaseModel):
    columns: list[str]
    sample_rows: list[dict[str, Any]]
    guessed_email: Optional[str]
    guessed_first_name: Optional[str]
    guessed_last_name: Optional[str]
    delimiter: str


class FileUploadResponse(BaseModel):
    file_id: str
    filename: str
    detection: ColumnDetection


class ColumnMappingIn(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class CreateJobRequest(BaseModel):
    file_id: str
    mapping: ColumnMappingIn
    webhook_url: Optional[str] = None
    # Original upload name, so history shows the file the user recognises
    # rather than the storage key.
    filename: Optional[str] = None
    # Which kind of list this is; slices the workspace by campaign type.
    list_type: Optional[str] = None


class JobResponse(BaseModel):
    id: str
    seq: int = 0
    filename: str
    status: str
    list_type: str = "Imports"
    counts: dict[str, int]
    error: Optional[str] = None
    outputs: list[str]
    mapping: Optional[dict[str, Any]] = None
    # Live progress, so a client can render a truthful progress bar.
    phase: str = "queued"
    processed: int = 0
    total: int = 0
    domains_total: int = 0
    progress: float = 0.0
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration: Optional[float] = None


# --- Workspace read-model -------------------------------------------------


class AddressOut(BaseModel):
    email: str
    domain: Optional[str] = None
    status: str
    sub_status: str
    verdict: str
    score: int
    job_id: Optional[str] = None
    job_filename: Optional[str] = None
    list_type: Optional[str] = None
    checked_at: Optional[float] = None
    settled_at: Optional[int] = None
    mx_found: bool = False
    is_catch_all: bool = False
    is_disposable: bool = False
    is_role: bool = False
    is_free: bool = False


class AddressPage(BaseModel):
    rows: list[AddressOut]
    total: int
    page: int
    size: int


class ExportRequest(BaseModel):
    """A slice of the workspace to take as CSV."""

    # Any of the four primary verdicts. Empty means every verdict.
    verdicts: list[str] = Field(default_factory=list)
    search: Optional[str] = None
    list_type: Optional[str] = None
    # Named preset ("catchall", "disposable") for the slices that are defined by
    # a sub-reason rather than a verdict.
    preset: Optional[str] = None
    # Only these addresses, when exporting a hand-picked selection.
    emails: list[str] = Field(default_factory=list)
    # "full" carries verdict, sub-reason, score, MX and settling layer;
    # "email" is one column.
    columns: str = "full"
    require_mx: bool = False
    exclude_disposable: bool = False
