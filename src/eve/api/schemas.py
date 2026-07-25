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


class JobResponse(BaseModel):
    id: str
    filename: str
    status: str
    counts: dict[str, int]
    error: Optional[str] = None
    outputs: list[str]
    mapping: Optional[dict[str, Any]] = None
