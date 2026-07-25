"""Layer 2 — normalization + canonical dedupe key.

Two outputs:
  * ``normalized_email`` — the address we display/store (lowercased, trimmed).
  * ``dedupe_key``       — a canonical key that collapses provider-equivalent
                           addresses so ``j.doe+sale@gmail.com`` and
                           ``jdoe@gmail.com`` count as one.
"""
from __future__ import annotations

from dataclasses import dataclass

# Providers where a "+tag" suffix is an alias for the same mailbox.
_PLUS_ALIAS_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "fastmail.com",
    "protonmail.com",
    "proton.me",
    "yahoo.com",
    "icloud.com",
}

# Providers where dots in the local part are ignored (Gmail's behaviour).
_DOT_INSENSITIVE_DOMAINS = {"gmail.com", "googlemail.com"}

# Domains that are really the same inbox.
_DOMAIN_ALIASES = {"googlemail.com": "gmail.com"}


@dataclass
class NormalizeResult:
    normalized_email: str
    dedupe_key: str
    domain: str


def normalize(local_part: str, domain: str) -> NormalizeResult:
    """Produce the display address and a canonical dedupe key."""
    local = local_part.strip()
    domain = domain.strip().lower().rstrip(".")

    # Canonical domain for keying (googlemail -> gmail).
    canon_domain = _DOMAIN_ALIASES.get(domain, domain)

    normalized_email = f"{local.lower()}@{domain}"

    key_local = local.lower()
    if canon_domain in _PLUS_ALIAS_DOMAINS:
        key_local = key_local.split("+", 1)[0]
    if canon_domain in _DOT_INSENSITIVE_DOMAINS:
        key_local = key_local.replace(".", "")

    dedupe_key = f"{key_local}@{canon_domain}"
    return NormalizeResult(
        normalized_email=normalized_email,
        dedupe_key=dedupe_key,
        domain=domain,
    )


def suggested_local(local_part: str) -> str | None:
    """Currently unused hook for future local-part corrections."""
    return None
