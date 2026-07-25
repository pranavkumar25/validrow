"""The orchestrator: run the pipeline cheap->expensive and aggregate a Verdict.

    validate(email) -> Verdict

Short-circuits on hard failures (bad syntax, no MX) so we never spend an
expensive check when a cheap one already settled the answer.

Status semantics
----------------
* ``invalid``    — cannot receive mail (bad syntax, or no MX/A record).
* ``disposable`` — throwaway/temporary provider.
* ``risky``      — deliverable but low-value/uncertain: role account, or a
                   catch-all domain where the individual mailbox is unknowable.
* ``valid``      — mailbox confirmed to exist (requires SMTP probe, M2).
* ``unknown``    — domain is fine but the mailbox was not (or could not be)
                   probed. This is the honest default when SMTP is disabled.
"""
from __future__ import annotations

from eve.config import get_settings
from eve.layers import classify as classify_layer
from eve.layers import dns_mx
from eve.layers import normalize as normalize_layer
from eve.layers import syntax as syntax_layer
from eve.layers import typo as typo_layer
from eve.layers.smtp import NotImplementedProber, Prober, ProbeResult
from eve.verdict import Status, SubStatus, Verdict


def validate(
    email: str,
    *,
    enable_dns: bool | None = None,
    enable_smtp: bool | None = None,
    dns_timeout: float | None = None,
    prober: Prober | None = None,
) -> Verdict:
    settings = get_settings()
    enable_dns = settings.enable_dns if enable_dns is None else enable_dns
    enable_smtp = settings.enable_smtp if enable_smtp is None else enable_smtp
    dns_timeout = settings.dns_timeout if dns_timeout is None else dns_timeout

    v = Verdict(email=email or "")

    # --- Layer 1: syntax -------------------------------------------------
    syn = syntax_layer.check_syntax(email)
    v.record("syntax", {"valid": syn.valid, "error": syn.error})
    if not syn.valid:
        v.status = Status.INVALID
        v.sub_status = SubStatus.INVALID_SYNTAX
        v.score = 0
        return v

    local_part = syn.local_part or ""
    domain = syn.domain or ""
    v.domain = domain

    # --- Layer 2: normalize ---------------------------------------------
    norm = normalize_layer.normalize(local_part, domain)
    v.normalized_email = norm.normalized_email
    v.dedupe_key = norm.dedupe_key
    v.record("normalize", {"dedupe_key": norm.dedupe_key})

    # --- Layer 3: typo suggestion (advisory) ----------------------------
    suggestion = typo_layer.suggest_domain(domain)
    if suggestion and suggestion != domain:
        v.suggested_correction = f"{local_part}@{suggestion}"
    v.record("typo", {"suggested_domain": suggestion})

    # --- Layer 5: classification ----------------------------------------
    cls = classify_layer.classify(local_part, domain)
    v.is_disposable = cls.is_disposable
    v.is_role = cls.is_role
    v.is_free = cls.is_free
    if cls.is_free:
        v.tags.append("free_provider")
    else:
        v.tags.append("corporate")
    v.record(
        "classify",
        {"disposable": cls.is_disposable, "role": cls.is_role, "free": cls.is_free},
    )

    if cls.is_disposable:
        v.status = Status.DISPOSABLE
        v.sub_status = SubStatus.DISPOSABLE
        v.score = _score(v, base=15)
        return v

    # --- Layer 4: DNS / MX ----------------------------------------------
    mx_hosts: list[str] = []
    if enable_dns:
        mx = dns_mx.resolve_mx(domain, timeout=dns_timeout)
        v.mx_found = mx.mx_found
        mx_hosts = mx.mx_hosts
        v.record("dns_mx", {"mx_found": mx.mx_found, "mx_hosts": mx.mx_hosts, "error": mx.error})
        if not mx.mx_found:
            if mx.error in ("timeout", None) or (mx.error or "").startswith("dns_error"):
                v.status = Status.UNKNOWN
                v.sub_status = SubStatus.TIMEOUT if mx.error == "timeout" else SubStatus.DNS_ERROR
                v.score = _score(v, base=40)
            else:
                v.status = Status.INVALID
                v.sub_status = SubStatus.NO_MX
                v.score = _score(v, base=3)
            return v
    else:
        v.record("dns_mx", {"skipped": True})

    # --- Layer 6/7: SMTP mailbox + catch-all (M2) -----------------------
    if enable_smtp and mx_hosts:
        probe = (prober or NotImplementedProber()).probe(v.normalized_email or email, mx_hosts)
        v.record(
            "smtp",
            {"outcome": probe.outcome, "code": probe.smtp_code, "detail": probe.detail},
        )
        if probe.is_catch_all or probe.outcome in ("valid", "invalid", "catch_all"):
            apply_probe_outcome(v, probe)
            return v
        # outcome == "unknown" falls through to the no-SMTP aggregation below.

    return finalize_without_smtp(v)


def finalize_without_smtp(v: Verdict) -> Verdict:
    """Aggregate when the mailbox was not (or could not be) probed."""
    if v.is_role:
        v.status = Status.RISKY
        v.sub_status = SubStatus.ROLE_ACCOUNT
        v.score = _score(v, base=55)
    else:
        # Domain accepts mail; individual mailbox unverified -> honest "unknown".
        v.status = Status.UNKNOWN
        v.sub_status = SubStatus.OK
        v.score = _score(v, base=70)
    return v


def apply_probe_outcome(v: Verdict, probe: ProbeResult) -> Verdict:
    """Fold an SMTP probe result into a verdict (shared by engine + pipeline).

    Respects role accounts: a role mailbox that *exists* is still ``risky``, not
    a clean ``valid``, because it's not a person.
    """
    if probe.is_catch_all or probe.outcome == "catch_all":
        v.is_catch_all = True
        v.status = Status.RISKY
        v.sub_status = SubStatus.CATCH_ALL
        v.score = _score(v, base=50)
    elif probe.outcome == "valid":
        if v.is_role:
            v.status = Status.RISKY
            v.sub_status = SubStatus.ROLE_ACCOUNT
            v.score = _score(v, base=55)
        else:
            v.status = Status.VALID
            v.sub_status = SubStatus.OK
            v.score = _score(v, base=95)
    elif probe.outcome == "invalid":
        v.status = Status.INVALID
        v.sub_status = SubStatus.MAILBOX_NOT_FOUND
        v.score = _score(v, base=5)
    else:  # unknown
        detail = probe.detail or ""
        if "greylist" in detail:
            v.sub_status = SubStatus.GREYLISTED
        elif "antispam" in detail or "block" in detail:
            v.sub_status = SubStatus.ANTISPAM_BLOCK
        else:
            v.sub_status = SubStatus.UNKNOWN
        v.status = Status.RISKY if v.is_role else Status.UNKNOWN
        v.score = _score(v, base=55 if v.is_role else 60)
    return v


def _score(v: Verdict, base: int) -> int:
    """Nudge the base score by soft signals, clamped to 0..100."""
    score = base
    if v.suggested_correction:
        score -= 10  # a likely typo lowers confidence
    if v.is_role and v.status not in (Status.INVALID, Status.DISPOSABLE):
        score -= 5
    if v.is_free:
        score -= 2
    return max(0, min(100, score))
