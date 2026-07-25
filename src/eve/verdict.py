"""Core result types shared by every layer and the orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Final, user-facing verdict for an address.

    Mirrors the industry convention (ZeroBounce/NeverBounce/Bouncer) so it maps
    cleanly onto a Smartlead-style UI.
    """

    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"
    DISPOSABLE = "disposable"
    SPAM_TRAP = "spam_trap"


class SubStatus(str, Enum):
    """Machine-readable reason behind the status."""

    OK = "ok"
    INVALID_SYNTAX = "invalid_syntax"
    NO_MX = "no_mx"
    DISPOSABLE = "disposable"
    ROLE_ACCOUNT = "role_account"
    CATCH_ALL = "catch_all"
    MAILBOX_NOT_FOUND = "mailbox_not_found"
    GREYLISTED = "greylisted"
    TIMEOUT = "timeout"
    DNS_ERROR = "dns_error"
    ANTISPAM_BLOCK = "antispam_block"
    UNKNOWN = "unknown"


@dataclass
class Verdict:
    """The full result of validating a single address.

    ``checks`` accumulates per-layer detail so a user can see *why* a verdict
    was reached; ``score`` is a 0-100 deliverability estimate for threshold UX.
    """

    email: str
    status: Status = Status.UNKNOWN
    sub_status: SubStatus = SubStatus.UNKNOWN
    score: int = 0

    normalized_email: str | None = None
    dedupe_key: str | None = None
    domain: str | None = None
    suggested_correction: str | None = None

    is_disposable: bool = False
    is_role: bool = False
    is_free: bool = False
    is_catch_all: bool = False
    mx_found: bool = False

    checks: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def record(self, layer: str, detail: Any) -> None:
        """Attach a layer's raw finding under ``checks[layer]``."""
        self.checks[layer] = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "status": self.status.value,
            "sub_status": self.sub_status.value,
            "score": self.score,
            "normalized_email": self.normalized_email,
            "dedupe_key": self.dedupe_key,
            "domain": self.domain,
            "suggested_correction": self.suggested_correction,
            "is_disposable": self.is_disposable,
            "is_role": self.is_role,
            "is_free": self.is_free,
            "is_catch_all": self.is_catch_all,
            "mx_found": self.mx_found,
            "tags": self.tags,
            "checks": self.checks,
        }
