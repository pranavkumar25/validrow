"""Layer 1 — RFC 5322 syntax validation (via `email-validator`)."""
from __future__ import annotations

from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email


@dataclass
class SyntaxResult:
    valid: bool
    local_part: str | None = None
    domain: str | None = None
    ascii_email: str | None = None
    error: str | None = None


def check_syntax(email: str) -> SyntaxResult:
    """Parse and validate the address per RFC 5322.

    Deliverability (DNS) is checked separately in Layer 4, so it's disabled here.
    Internationalized addresses are normalized to their ASCII/IDNA form.
    """
    if not email or not isinstance(email, str):
        return SyntaxResult(valid=False, error="empty")

    candidate = email.strip()
    try:
        info = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError as exc:
        return SyntaxResult(valid=False, error=str(exc))

    return SyntaxResult(
        valid=True,
        local_part=info.local_part,
        domain=(info.ascii_domain or info.domain).lower(),
        ascii_email=info.ascii_email or info.normalized,
    )
