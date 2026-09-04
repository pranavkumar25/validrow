"""Core result types shared by every layer and the orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Final, user-facing verdict for an address.

    Mirrors the industry convention (ZeroBounce/NeverBounce/Bouncer) so it maps
    cleanly onto a Smartlead-style UI.

    There is deliberately no ``spam_trap``. It was declared here and counted
    everywhere downstream, but no layer could ever emit it, so every spam-trap
    figure the product showed was a structural zero. Detecting one needs a list
    of seed addresses, and a trap only works while its addresses are secret —
    which is why no such list is published, for sale or otherwise. A status
    that cannot be reached is worse than an absent one: it invites the reader
    to conclude a list is trap-free when nothing looked. Re-add it the day a
    real feed exists; it is one line here and a mapping below.
    """

    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"
    DISPOSABLE = "disposable"


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


class PrimaryVerdict(str, Enum):
    """The four verdicts the product surfaces, in display order.

    The engine emits five :class:`Status` values; ``disposable`` is not a
    separate verdict but a *reason* an address is undeliverable, so it folds
    into ``UNDELIVERABLE`` and surfaces its specificity as sub-reason text.
    Nothing about the underlying statuses changes — exports, filters and API
    payloads still see all five.
    """

    DELIVERABLE = "deliverable"
    RISKY = "risky"
    UNKNOWN = "unknown"
    UNDELIVERABLE = "undeliverable"


#: Display order, used by every mix bar, donut and legend.
VERDICT_ORDER = [
    PrimaryVerdict.DELIVERABLE,
    PrimaryVerdict.RISKY,
    PrimaryVerdict.UNKNOWN,
    PrimaryVerdict.UNDELIVERABLE,
]

_VERDICT_OF = {
    Status.VALID: PrimaryVerdict.DELIVERABLE,
    Status.RISKY: PrimaryVerdict.RISKY,
    Status.UNKNOWN: PrimaryVerdict.UNKNOWN,
    Status.INVALID: PrimaryVerdict.UNDELIVERABLE,
    Status.DISPOSABLE: PrimaryVerdict.UNDELIVERABLE,
}


def primary_verdict(status: Status | str) -> str:
    """Map an engine status onto one of the four primary verdicts."""
    try:
        return _VERDICT_OF[Status(status)].value
    except ValueError:
        return PrimaryVerdict.UNKNOWN.value


#: Human sub-reason for each machine sub_status, shown as secondary text under
#: the verdict. ``None`` means the verdict alone says everything there is to say.
SUB_REASON_LABEL = {
    SubStatus.OK.value: None,
    SubStatus.INVALID_SYNTAX.value: "Invalid syntax",
    SubStatus.NO_MX.value: "No MX record",
    SubStatus.DISPOSABLE.value: "Disposable domain",
    SubStatus.ROLE_ACCOUNT.value: "Role account",
    SubStatus.CATCH_ALL.value: "Catch-all domain",
    SubStatus.MAILBOX_NOT_FOUND.value: "Mailbox not found",
    SubStatus.GREYLISTED.value: "Greylisted",
    SubStatus.TIMEOUT.value: "SMTP timeout",
    SubStatus.DNS_ERROR.value: "DNS error",
    SubStatus.ANTISPAM_BLOCK.value: "Antispam block",
    SubStatus.UNKNOWN.value: None,
}


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
