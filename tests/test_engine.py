"""Orchestrator tests. DNS is disabled for determinism (offline)."""
from eve import Status, SubStatus, validate
from eve.layers.smtp import ProbeResult


def v(email, **kw):
    kw.setdefault("enable_dns", False)
    kw.setdefault("enable_smtp", False)
    return validate(email, **kw)


def test_invalid_syntax_short_circuits():
    r = v("not-an-email")
    assert r.status is Status.INVALID
    assert r.sub_status is SubStatus.INVALID_SYNTAX
    assert r.score == 0


def test_disposable():
    r = v("throwaway@mailinator.com")
    assert r.status is Status.DISPOSABLE
    assert r.is_disposable


def test_role_is_risky():
    r = v("sales@acme.io")
    assert r.status is Status.RISKY
    assert r.sub_status is SubStatus.ROLE_ACCOUNT
    assert r.is_role


def test_good_address_unknown_without_smtp():
    r = v("john@acme.io")
    assert r.status is Status.UNKNOWN
    assert r.sub_status is SubStatus.OK
    assert 0 < r.score <= 100


def test_typo_produces_suggestion():
    r = v("john@gmial.com")
    assert r.suggested_correction == "john@gmail.com"


def test_dedupe_key_present():
    r = v("J.Doe+x@gmail.com")
    assert r.dedupe_key == "jdoe@gmail.com"


def test_smtp_prober_injection_valid():
    class OkProber:
        def probe(self, email, mx_hosts):
            return ProbeResult(outcome="valid", smtp_code=250)

    # Force DNS + SMTP on, but stub MX so no network is needed.
    from eve.layers import dns_mx
    from eve.layers.dns_mx import MxResult

    dns_mx.clear_cache()
    dns_mx._cache.put("acme.io", MxResult("acme.io", True, ["mx.acme.io"]), ttl=60)

    r = validate("john@acme.io", enable_dns=True, enable_smtp=True, prober=OkProber())
    assert r.status is Status.VALID
    assert r.sub_status is SubStatus.OK
    assert r.score >= 90


def test_smtp_prober_catch_all_is_risky():
    class CatchAll:
        def probe(self, email, mx_hosts):
            return ProbeResult(outcome="catch_all", is_catch_all=True, smtp_code=250)

    from eve.layers import dns_mx
    from eve.layers.dns_mx import MxResult

    dns_mx.clear_cache()
    dns_mx._cache.put("catchall.io", MxResult("catchall.io", True, ["mx.catchall.io"]), ttl=60)

    r = validate("anyone@catchall.io", enable_dns=True, enable_smtp=True, prober=CatchAll())
    assert r.status is Status.RISKY
    assert r.sub_status is SubStatus.CATCH_ALL
    assert r.is_catch_all
