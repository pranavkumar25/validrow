"""Greylisting / temporary-failure handling.

A ``4xx`` reply is temporary — the mailbox may well exist, the server is just
deferring us. We must NOT report ``invalid`` for these; instead the address is
scheduled for a delayed re-probe and, only after retries are exhausted, settled
to ``unknown``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_TEMP_HINTS = ("greylist", "grey list", "graylist", "try again", "deferred", "temporarily", "later")


def is_temporary(code: int, message: str = "") -> bool:
    if 400 <= code < 500:
        return True
    return any(h in (message or "").lower() for h in _TEMP_HINTS)


def looks_greylisted(code: int, message: str = "") -> bool:
    return code in (421, 450, 451) or any(h in (message or "").lower() for h in _TEMP_HINTS[:4])


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    # Delay before each re-probe (seconds). Real greylisters clear in ~5-15 min.
    delays: list[int] = field(default_factory=lambda: [900, 1800, 3600])

    def next_delay(self, attempt: int) -> int:
        if attempt >= self.max_attempts:
            return -1  # give up -> settle unknown
        return self.delays[min(attempt, len(self.delays) - 1)]
