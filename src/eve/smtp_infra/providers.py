"""Provider-specific heuristics — where accuracy is won or lost.

Big providers defeat naive probing: Gmail/Outlook/Yahoo often accept every
``RCPT`` then bounce later, greylist, or throttle. For those we set
``trust_rcpt=False`` so a ``250`` is reported as ``unknown`` (honest) rather than
a false ``valid``. A hard ``5xx`` reject is still meaningful and mapped to
``invalid``.

This is a *living config table* — add rows without touching probe code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    name: str
    trust_rcpt: bool
    supports_catch_all_probe: bool = True
    note: str = ""


GENERIC = Strategy("generic", trust_rcpt=True)

# Match against the destination MX hostnames (substring) or the domain.
_TABLE: dict[str, Strategy] = {
    "google.com": Strategy("google", trust_rcpt=False, supports_catch_all_probe=False,
                           note="Gmail accepts-then-bounces; RCPT unreliable"),
    "googlemail.com": Strategy("google", trust_rcpt=False, supports_catch_all_probe=False),
    "outlook.com": Strategy("microsoft", trust_rcpt=False, supports_catch_all_probe=False,
                            note="O365/Outlook throttles + accept-all at RCPT"),
    "protection.outlook.com": Strategy("microsoft", trust_rcpt=False, supports_catch_all_probe=False),
    "hotmail.com": Strategy("microsoft", trust_rcpt=False, supports_catch_all_probe=False),
    "yahoodns.net": Strategy("yahoo", trust_rcpt=False, supports_catch_all_probe=False,
                             note="Yahoo/AOL unreliable RCPT"),
    "yahoo.com": Strategy("yahoo", trust_rcpt=False, supports_catch_all_probe=False),
    "icloud.com": Strategy("apple", trust_rcpt=False, supports_catch_all_probe=False),
    "me.com": Strategy("apple", trust_rcpt=False, supports_catch_all_probe=False),
    "pphosted.com": Strategy("proofpoint", trust_rcpt=False, note="Proofpoint gateway often accept-all"),
    "mimecast.com": Strategy("mimecast", trust_rcpt=False, note="Mimecast gateway often accept-all"),
}


def strategy_for(mx_hosts: list[str], domain: str) -> Strategy:
    hay = (" ".join(mx_hosts) + " " + domain).lower()
    for needle, strat in _TABLE.items():
        if needle in hay:
            return strat
    return GENERIC
