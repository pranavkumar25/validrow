"""Email Validation Engine (eve).

A layered email verification engine:

    1. syntax     — RFC 5322 parse
    2. normalize  — lowercase / trim / Gmail dot+plus collapse
    3. typo       — suggest corrections for fat-finger domains
    4. dns_mx     — does the domain accept mail?
    5. classify   — disposable / role / free vs corporate
    6. smtp        — mailbox existence probe        (added in M2)
    7. catch_all   — domain-accepts-everything probe (added in M2)

The public entrypoint is :func:`eve.engine.validate`.
"""

from eve.verdict import Status, SubStatus, Verdict

__all__ = ["Status", "SubStatus", "Verdict", "validate"]

from eve.engine import validate  # noqa: E402  (re-export after Verdict import)
