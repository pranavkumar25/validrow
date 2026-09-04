"""Request/response models for the API (Pydantic v2).

NB: These are Pydantic models, so their annotations ARE evaluated at runtime.
Use `typing` constructs (not PEP 604 `X | None`) so the models load on
Python 3.9 as well as 3.12. Ruff is configured with `keep-runtime-typing` to
avoid rewriting these back to the newer syntax.

Every public field carries a description. They are not decoration: `/docs` is
rendered from this app's own OpenAPI document, so a field described here is a
field described in the reference, and a field renamed here is renamed there.
The alternative is a second copy of the API written in prose, which is the copy
that goes stale.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class VerifyRequest(BaseModel):
    email: str = Field(
        ...,
        min_length=1,
        max_length=320,
        examples=["john.doe@gmail.com"],
        description="The address to verify. Parsed, normalised and probed as sent.",
    )
    # Optional per-request overrides (default: use server config).
    check_dns: Optional[bool] = Field(
        None,
        description="Override the engine's DNS setting for this call. With DNS "
        "off, nothing past layer 3 can run and the verdict comes back Unknown.",
    )
    check_smtp: Optional[bool] = Field(
        None,
        description="Override the engine's SMTP setting for this call. With the "
        "probe off, a deliverable-looking address is still reported Unknown "
        "rather than guessed at.",
    )


class VerifyResponse(BaseModel):
    #: A typo settled at layer 4: the domain resolves to nothing, and the
    #: correction is offered rather than applied.
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "email": "john@gmial.com",
        "status": "undeliverable",
        "sub_status": "no_mx",
        "score": 4,
        "normalized_email": "john@gmial.com",
        "dedupe_key": "john@gmial.com",
        "domain": "gmial.com",
        "suggested_correction": "john@gmail.com",
        "is_disposable": False,
        "is_role": False,
        "is_free": False,
        "is_catch_all": False,
        "mx_found": False,
        "tags": ["typo_suspected"],
        "checks": {"syntax": "ok", "normalize": "ok", "typo": "gmial.com -> gmail.com", "mx": "none"},
    }]})


    email: str = Field(..., description="The address exactly as it was sent.")
    status: str = Field(
        ...,
        examples=["undeliverable"],
        description="The verdict: deliverable, risky, unknown or undeliverable.",
    )
    sub_status: str = Field(
        ...,
        examples=["no_mx"],
        description="The reason under the verdict, such as no_mx, role_account "
        "or catch_all.",
    )
    score: int = Field(..., description="Confidence in the verdict, 0 to 100.")
    normalized_email: Optional[str] = Field(
        None,
        description="The address with provider-specific spelling collapsed: "
        "Gmail dots and plus tags removed, case folded.",
    )
    dedupe_key: Optional[str] = Field(
        None,
        description="The key two spellings of one mailbox share. Count this, "
        "not the address, to count people.",
    )
    domain: Optional[str] = Field(None, description="The domain part, lower-cased.")
    suggested_correction: Optional[str] = Field(
        None,
        examples=["john.doe@gmail.com"],
        description="A likely intended address when the domain looks misspelled. "
        "A suggestion, never an automatic substitution.",
    )
    is_disposable: bool = Field(
        ..., description="The domain is on the vendored disposable list."
    )
    is_role: bool = Field(
        ..., description="The local part reaches a desk rather than a person."
    )
    is_free: bool = Field(..., description="The domain is a free consumer provider.")
    is_catch_all: bool = Field(
        ...,
        description="The domain accepts every recipient, so its acceptance of "
        "this one proves nothing.",
    )
    mx_found: bool = Field(..., description="The domain published usable MX records.")
    tags: list[str] = Field(..., description="Flags raised along the way, as a list.")
    checks: dict[str, Any] = Field(
        ...,
        description="One entry per layer that ran, in order, with what it "
        "returned. This is the trace behind the verdict.",
    )


class HealthResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "ok", "version": "0.1.0", "smtp_enabled": True, "dns_enabled": True,
    }]})


    status: str = Field(..., description='"ok" while the process is serving.')
    version: str = Field(..., description="The engine version answering.")
    smtp_enabled: bool = Field(
        ..., description="Whether the mailbox probe is running on this engine."
    )
    dns_enabled: bool = Field(
        ..., description="Whether DNS and MX resolution is running on this engine."
    )


# --- File pipeline (M1) ---------------------------------------------------


class ColumnDetection(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "columns": ["email", "company", "signed_up"],
        "sample_rows": [{"email": "jane.doe@acme.io", "company": "Acme", "signed_up": "2026-01-14"}],
        "guessed_email": "email",
        "guessed_first_name": None,
        "guessed_last_name": None,
        "delimiter": ",",
    }]})


    columns: list[str] = Field(..., description="The header row, in file order.")
    sample_rows: list[dict[str, Any]] = Field(
        ..., description="The first few rows, for confirming the mapping."
    )
    guessed_email: Optional[str] = Field(
        None, description="The column that most likely holds the address."
    )
    guessed_first_name: Optional[str] = Field(None, description="A likely first-name column.")
    guessed_last_name: Optional[str] = Field(None, description="A likely last-name column.")
    delimiter: str = Field(
        ...,
        examples=[","],
        description="The delimiter found in the file. The output is written back "
        "with this one.",
    )


class FileUploadResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "file_id": "f_8s21kd_list.csv",
        "filename": "list.csv",
        "detection": {
            "columns": ["email", "company", "signed_up"],
            "sample_rows": [{"email": "jane.doe@acme.io", "company": "Acme", "signed_up": "2026-01-14"}],
            "guessed_email": "email",
            "guessed_first_name": None,
            "guessed_last_name": None,
            "delimiter": ",",
        },
    }]})


    file_id: str = Field(
        ..., description="The storage key to pass to POST /v1/jobs."
    )
    filename: str = Field(..., description="The name the file was uploaded under.")
    detection: ColumnDetection = Field(
        ..., description="What the header sniffer found, for mapping."
    )


class ColumnMappingIn(BaseModel):
    email: str = Field(
        ...,
        description="The column holding the address. The only mapping the engine "
        "needs; every other column is carried through untouched.",
    )
    first_name: Optional[str] = Field(None, description="Optional first-name column.")
    last_name: Optional[str] = Field(None, description="Optional last-name column.")


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "file_id": "f_8s21kd_list.csv",
        "mapping": {"email": "email"},
        "filename": "list.csv",
    }]})


    file_id: str = Field(..., description="A file_id returned by POST /v1/files.")
    mapping: ColumnMappingIn = Field(..., description="Which column holds the address.")
    webhook_url: Optional[str] = Field(
        None,
        description="Called once when the run finishes. Signed with HMAC-SHA256 "
        "when a webhook secret is configured.",
    )
    # Original upload name, so history shows the file the user recognises
    # rather than the storage key.
    filename: Optional[str] = Field(
        None, description="The name to show in History. Defaults to the storage key."
    )
    # Which kind of list this is; slices the workspace by campaign type.
    list_type: Optional[str] = Field(
        None, description="The list this run belongs to, for slicing the workspace."
    )


class JobResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "j_4c19be", "seq": 12, "filename": "list.csv", "status": "running",
        "list_type": "Cold outreach",
        "counts": {"deliverable": 1840, "risky": 402, "unknown": 311, "undeliverable": 297},
        "error": None, "outputs": [], "mapping": {"email": "email"},
        "phase": "smtp", "processed": 2850, "total": 4812, "domains_total": 731,
        "progress": 0.59, "created_at": 1788000000.0, "started_at": 1788000004.0,
        "finished_at": None, "duration": None,
    }]})


    id: str = Field(..., description="The job id, used on every other job route.")
    seq: int = Field(0, description="This workspace's own run number.")
    filename: str = Field(..., description="The file the run was started from.")
    status: str = Field(
        ..., examples=["running"], description="queued, running, done or failed."
    )
    list_type: str = Field("Imports", description="The list this run belongs to.")
    counts: dict[str, int] = Field(..., description="Rows settled, keyed by verdict.")
    error: Optional[str] = Field(None, description="Why the run failed, when it did.")
    outputs: list[str] = Field(
        ..., description="The segments ready to download: cleaned, valid, removed."
    )
    mapping: Optional[dict[str, Any]] = Field(
        None, description="The column mapping the run was started with."
    )
    # Live progress, so a client can render a truthful progress bar.
    phase: str = Field("queued", description="Which stage of the run is executing.")
    processed: int = Field(0, description="Rows settled so far.")
    total: int = Field(0, description="Rows in the file.")
    domains_total: int = Field(0, description="Distinct domains in the file.")
    progress: float = Field(0.0, description="Fraction complete, 0 to 1.")
    created_at: float = Field(0.0, description="Unix time the job was accepted.")
    started_at: Optional[float] = Field(None, description="Unix time work began.")
    finished_at: Optional[float] = Field(None, description="Unix time work ended.")
    duration: Optional[float] = Field(None, description="Seconds the run took.")


# --- Workspace read-model -------------------------------------------------


class AddressOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "email": "jane.doe@acme.io", "domain": "acme.io", "status": "valid",
        "sub_status": "mailbox_confirmed", "verdict": "deliverable", "score": 96,
        "job_id": "j_4c19be", "job_filename": "list.csv", "list_type": "Cold outreach",
        "checked_at": 1788000412.0, "settled_at": 6, "mx_found": True,
        "is_catch_all": False, "is_disposable": False, "is_role": False, "is_free": False,
    }]})


    email: str = Field(..., description="The address as it was validated.")
    domain: Optional[str] = Field(None, description="The domain part.")
    status: str = Field(..., description="The engine's status for this address.")
    sub_status: str = Field(..., description="The reason under the status.")
    verdict: str = Field(
        ...,
        description="The status rolled up to one of the four primary verdicts.",
    )
    score: int = Field(..., description="Confidence in the verdict, 0 to 100.")
    job_id: Optional[str] = Field(None, description="The run that produced this row.")
    job_filename: Optional[str] = Field(None, description="That run's file name.")
    list_type: Optional[str] = Field(None, description="The list the run belonged to.")
    checked_at: Optional[float] = Field(None, description="Unix time of the check.")
    settled_at: Optional[int] = Field(
        None, description="The layer, 1 to 7, that produced the verdict."
    )
    mx_found: bool = Field(False, description="The domain published usable MX records.")
    is_catch_all: bool = Field(False, description="The domain accepts every recipient.")
    is_disposable: bool = Field(False, description="The domain is a disposable provider.")
    is_role: bool = Field(False, description="The local part is a role account.")
    is_free: bool = Field(False, description="The domain is a free consumer provider.")


class AddressPage(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "rows": [{
            "email": "jane.doe@acme.io", "domain": "acme.io", "status": "valid",
            "sub_status": "mailbox_confirmed", "verdict": "deliverable", "score": 96,
            "job_id": "j_4c19be", "job_filename": "list.csv", "list_type": "Cold outreach",
            "checked_at": 1788000412.0, "settled_at": 6, "mx_found": True,
            "is_catch_all": False, "is_disposable": False, "is_role": False, "is_free": False,
        }],
        "total": 4812, "page": 1, "size": 50,
    }]})


    rows: list[AddressOut] = Field(..., description="This page of addresses.")
    total: int = Field(..., description="Addresses matching the filter, all pages.")
    page: int = Field(..., description="The 1-based page returned.")
    size: int = Field(..., description="Rows per page.")


class ExportRequest(BaseModel):

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "verdicts": ["undeliverable"], "columns": "email", "exclude_disposable": True,
    }]})

    """A slice of the workspace to take as CSV."""

    # Any of the four primary verdicts. Empty means every verdict.
    verdicts: list[str] = Field(
        default_factory=list,
        description="Any of the four primary verdicts. Empty means all of them.",
    )
    search: Optional[str] = Field(
        None, description="Substring match on the address or its domain."
    )
    list_type: Optional[str] = Field(None, description="Restrict to one list.")
    # Named preset ("catchall", "disposable") for the slices that are defined by
    # a sub-reason rather than a verdict.
    preset: Optional[str] = Field(
        None,
        description='A slice defined by a sub-reason rather than a verdict: '
        '"catchall" or "disposable".',
    )
    # Only these addresses, when exporting a hand-picked selection.
    emails: list[str] = Field(
        default_factory=list,
        description="Only these addresses, for exporting a hand-picked selection.",
    )
    # "full" carries verdict, sub-reason, score, MX and settling layer;
    # "email" is one column.
    columns: str = Field(
        "full",
        description='"full" carries the verdict, sub-reason, score, MX and '
        'settling layer; "email" is one column.',
    )
    require_mx: bool = Field(False, description="Drop rows whose domain has no MX.")
    exclude_disposable: bool = Field(False, description="Drop disposable domains.")
