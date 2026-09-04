"""The seven-layer trace: what each layer found, and which one settled it.

The product's central promise is that a verdict is always explainable and never
overstated. This module turns a :class:`~eve.verdict.Verdict` into the row-per-
layer trace the UI renders.

Two rules govern every line of this file:

1. **A finding is only ever read out of ``verdict.checks``.** If a layer did not
   run, its row says so — we never fabricate a plausible-sounding 250 for a
   probe that never happened.
2. **The engine's execution order is not the display order.** The engine runs
   classification *before* DNS (it is far cheaper, and a disposable domain
   settles the address without a lookup), while the product numbers DNS as
   layer 4 and classification as layer 5. When a layer was skipped because a
   later-numbered one settled the address, the row says exactly that rather
   than pretending the lookup succeeded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from eve.verdict import Status, SubStatus, Verdict

# --- Layer outcome states -------------------------------------------------- #
# `bg` tints the row for the two states a reader must not skim past: a proven
# failure, and an honest non-answer.
PASS = {"st": "Pass", "c": "#12B76A", "bg": "transparent"}
SOFT = {"st": "Soft", "c": "#F79009", "bg": "transparent"}
FAIL = {"st": "Fail", "c": "#F04438", "bg": "#FEF3F2"}
UNSURE = {"st": "Unknown", "c": "#A8A29E", "bg": "#F4F3EF"}
SKIPPED = {"st": "Skipped", "c": "#79756C", "bg": "transparent"}

LAYER_NAMES = {
    1: "Syntax",
    2: "Normalization",
    3: "Typo check",
    4: "DNS / MX",
    5: "Classification",
    6: "SMTP mailbox",
    7: "Catch-all",
}


@dataclass
class TraceRow:
    n: int
    name: str
    finding: str
    st: str
    c: str
    bg: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "name": self.name,
            "finding": self.finding,
            "st": self.st,
            "c": self.c,
            "bg": self.bg,
        }


def settled_layer(status: str, sub_status: str, *, smtp_ran: bool = True) -> int:
    """Which layer produced the verdict.

    Drives the "settled at layer N" copy and the dashboard's settled-by
    breakdown. Derived from the verdict alone so it stays consistent whether the
    caller has the full ``checks`` payload or only a stored row.
    """
    if sub_status == SubStatus.INVALID_SYNTAX.value:
        return 1
    if sub_status in (SubStatus.NO_MX.value, SubStatus.DNS_ERROR.value):
        return 4
    if sub_status == SubStatus.TIMEOUT.value and not smtp_ran:
        # TIMEOUT is reported by both the resolver and the mailbox probe. If the
        # probe never ran, the timeout was DNS's — attributing it to layer 6
        # would blame a layer that was never reached.
        return 4
    if sub_status == SubStatus.DISPOSABLE.value:
        return 5
    if sub_status == SubStatus.CATCH_ALL.value:
        return 7
    if sub_status == SubStatus.ROLE_ACCOUNT.value and not smtp_ran:
        # Nothing probed the mailbox; classification is what we actually know.
        return 5
    return 6


def _skip(n: int, settled_at: int) -> TraceRow:
    return TraceRow(n, LAYER_NAMES[n], f"Not run — settled at layer {settled_at}", **SKIPPED)


def build_trace(verdict: Verdict) -> list[TraceRow]:
    """Render the seven layers for one address, in product order."""
    checks = verdict.checks or {}
    status = verdict.status.value if isinstance(verdict.status, Status) else str(verdict.status)
    sub = (
        verdict.sub_status.value
        if isinstance(verdict.sub_status, SubStatus)
        else str(verdict.sub_status)
    )
    domain = verdict.domain or ""
    rows: list[TraceRow] = []

    def push(n: int, finding: str, state: dict) -> None:
        rows.append(TraceRow(n, LAYER_NAMES[n], finding, **state))

    # --- 1. Syntax --------------------------------------------------------
    syn = checks.get("syntax") or {}
    if syn.get("valid") is False or sub == SubStatus.INVALID_SYNTAX.value:
        err = syn.get("error")
        push(1, f"RFC 5322 reject — {err}" if err else "RFC 5322 reject", FAIL)
        for n in (2, 3, 4, 5, 6, 7):
            rows.append(_skip(n, 1))
        return rows
    push(1, "RFC 5322 valid", PASS)

    # --- 2. Normalization -------------------------------------------------
    norm = checks.get("normalize") or {}
    normalized = verdict.normalized_email or norm.get("dedupe_key")
    push(
        2,
        f"Lowercased and deduped to {normalized}" if normalized else "Lowercased and deduped",
        PASS,
    )

    # --- 3. Typo check ----------------------------------------------------
    typo = checks.get("typo") or {}
    suggested = typo.get("suggested_domain")
    if suggested and suggested != domain:
        push(3, f"Suggests {suggested}", SOFT)
    else:
        push(3, "No suggestion within edit distance 2", PASS)

    # --- 4. DNS / MX ------------------------------------------------------
    # The engine short-circuits disposable domains at classification, before it
    # ever looks up MX. Say so rather than inventing a lookup.
    dns = checks.get("dns_mx")
    settled = settled_layer(status, sub, smtp_ran="smtp" in checks)
    if dns is None:
        rows.append(_skip(4, 5) if settled == 5 else _skip(4, settled))
    elif dns.get("skipped"):
        push(4, "Not run — DNS resolution disabled in engine settings", SKIPPED)
    elif dns.get("mx_found"):
        hosts = dns.get("mx_hosts") or []
        head = hosts[0] if hosts else "no host reported"
        n_rec = len(hosts)
        push(4, f"{n_rec} record{'s' if n_rec != 1 else ''} · {head}", PASS)
    elif sub == SubStatus.NO_MX.value:
        push(4, f"No MX and no A fallback for {domain}", FAIL)
        for n in (5, 6, 7):
            rows.append(_skip(n, 4))
        return rows
    else:
        push(4, f"Lookup did not complete — {dns.get('error') or 'dns error'}", UNSURE)
        for n in (5, 6, 7):
            rows.append(_skip(n, 4))
        return rows

    # --- 5. Classification ------------------------------------------------
    cls = checks.get("classify") or {}
    is_disposable = cls.get("disposable", verdict.is_disposable)
    is_role = cls.get("role", verdict.is_role)
    is_free = cls.get("free", verdict.is_free)
    if is_disposable:
        push(5, "Domain is on the disposable-provider list", FAIL)
        for n in (6, 7):
            rows.append(_skip(n, 5))
        return rows
    if is_role:
        push(5, "Role account — shared inbox, not a person", SOFT)
    elif is_free:
        push(5, "Free provider mailbox · not corporate", SOFT)
    else:
        push(5, "Corporate · not role · not free", PASS)

    # --- 6. SMTP mailbox + 7. Catch-all -----------------------------------
    smtp = checks.get("smtp")
    if smtp is None:
        push(6, "Not run — SMTP probe disabled in engine settings", SKIPPED)
        push(7, "Not run — needs the SMTP probe", SKIPPED)
        return rows

    outcome = smtp.get("outcome")
    code = smtp.get("code")
    detail = smtp.get("detail") or ""
    if detail == "smtp_disabled":
        push(6, "Not run — SMTP probe disabled in engine settings", SKIPPED)
        push(7, "Not run — needs the SMTP probe", SKIPPED)
        return rows

    prefix = f"{code} " if code else ""
    if verdict.is_catch_all or outcome == "catch_all":
        push(6, f"{prefix}accepted".strip(), SOFT)
        push(7, "Domain accepts every recipient — acceptance proves nothing", SOFT)
    elif outcome == "valid":
        push(6, f"{prefix}accepted — RCPT confirmed at {domain}".strip(), PASS)
        rows.append(_skip(7, 6))
    elif outcome == "invalid":
        push(6, f"{prefix}recipient rejected — mailbox does not exist".strip(), FAIL)
        rows.append(_skip(7, 6))
    else:
        reason = {
            SubStatus.GREYLISTED.value: "greylisted, retry later",
            SubStatus.TIMEOUT.value: "no banner in time — connection timed out",
            SubStatus.ANTISPAM_BLOCK.value: "connection refused by antispam gateway",
        }.get(sub, detail or "no conclusive response")
        push(6, f"{prefix}{reason} — we will not guess".strip(), UNSURE)
        push(7, "Not run — probe never completed", SKIPPED)
    return rows


def trace_from_row(row: dict[str, Any]) -> list[TraceRow]:
    """Rebuild a trace from a stored address row (see :mod:`eve.addresses`)."""
    v = Verdict(
        email=row.get("email", ""),
        status=Status(row.get("status", "unknown")),
        sub_status=SubStatus(row.get("sub_status", "unknown")),
        score=int(row.get("score") or 0),
        normalized_email=row.get("email"),
        domain=row.get("domain"),
        is_disposable=bool(row.get("is_disposable")),
        is_role=bool(row.get("is_role")),
        is_free=bool(row.get("is_free")),
        is_catch_all=bool(row.get("is_catch_all")),
        mx_found=bool(row.get("mx_found")),
        checks=row.get("checks") or {},
    )
    return build_trace(v)


def subtitle_for(status: str, sub_status: str, domain: Optional[str]) -> str:
    """The one-line plain-English summary shown beside the score ring."""
    dom = domain or "this domain"
    if status == Status.VALID.value:
        return f"Mailbox confirmed at {dom}"
    if status == Status.RISKY.value:
        if sub_status == SubStatus.CATCH_ALL.value:
            return "Domain accepts everything — acceptance is not proof"
        if sub_status == SubStatus.ROLE_ACCOUNT.value:
            return "Shared inbox, deliverable but not a person"
        return "Deliverable, but send at your discretion"
    if status == Status.UNKNOWN.value:
        return "We could not confirm this mailbox. This is not a failure."
    if status == Status.DISPOSABLE.value or sub_status == SubStatus.DISPOSABLE.value:
        return "Throwaway domain — will not reach a person"
    return "This address will bounce"
