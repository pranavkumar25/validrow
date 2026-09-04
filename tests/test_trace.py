"""The layer trace must never assert evidence the engine did not gather.

These tests pin the property the product's credibility rests on: a layer that
did not run says so, and the layer credited with settling the verdict is the one
that actually did.
"""
from __future__ import annotations

import pytest

from eve.trace import build_trace, settled_layer, subtitle_for
from eve.verdict import Status, SubStatus, Verdict


def _rows(v: Verdict) -> list[dict]:
    return [r.as_dict() for r in build_trace(v)]


def test_trace_always_has_seven_layers_in_order():
    v = Verdict(email="a@b.com", domain="b.com", checks={"syntax": {"valid": True}})
    rows = _rows(v)
    assert [r["n"] for r in rows] == [1, 2, 3, 4, 5, 6, 7]
    assert [r["name"] for r in rows][:3] == ["Syntax", "Normalization", "Typo check"]


def test_bad_syntax_settles_at_layer_one_and_skips_the_rest():
    v = Verdict(
        email="not-an-email",
        status=Status.INVALID,
        sub_status=SubStatus.INVALID_SYNTAX,
        checks={"syntax": {"valid": False, "error": "missing @"}},
    )
    rows = _rows(v)
    assert rows[0]["st"] == "Fail"
    assert "missing @" in rows[0]["finding"]
    assert all(r["st"] == "Skipped" for r in rows[1:])
    assert all("settled at layer 1" in r["finding"] for r in rows[1:])
    assert settled_layer("invalid", "invalid_syntax") == 1


def test_no_mx_settles_at_layer_four():
    v = Verdict(
        email="a@nowhere.test",
        domain="nowhere.test",
        status=Status.INVALID,
        sub_status=SubStatus.NO_MX,
        checks={
            "syntax": {"valid": True},
            "typo": {"suggested_domain": None},
            "dns_mx": {"mx_found": False, "mx_hosts": [], "error": None},
        },
    )
    rows = _rows(v)
    assert rows[3]["st"] == "Fail"
    assert "No MX and no A fallback" in rows[3]["finding"]
    assert [r["st"] for r in rows[4:]] == ["Skipped"] * 3
    assert settled_layer("invalid", "no_mx") == 4


def test_disposable_settles_at_classification_and_dns_says_it_never_ran():
    """The engine short-circuits disposable domains before the MX lookup, so the
    DNS row must not claim a lookup happened."""
    v = Verdict(
        email="x@tempbox.io",
        domain="tempbox.io",
        status=Status.DISPOSABLE,
        sub_status=SubStatus.DISPOSABLE,
        is_disposable=True,
        checks={
            "syntax": {"valid": True},
            "typo": {"suggested_domain": None},
            "classify": {"disposable": True, "role": False, "free": False},
        },
    )
    rows = _rows(v)
    assert rows[3]["st"] == "Skipped" and "settled at layer 5" in rows[3]["finding"]
    assert rows[4]["st"] == "Fail"
    assert "disposable" in rows[4]["finding"].lower()
    assert settled_layer("disposable", "disposable") == 5


def test_smtp_disabled_never_fabricates_a_probe_result():
    v = Verdict(
        email="a@b.com",
        domain="b.com",
        status=Status.UNKNOWN,
        sub_status=SubStatus.OK,
        mx_found=True,
        checks={
            "syntax": {"valid": True},
            "typo": {"suggested_domain": None},
            "dns_mx": {"mx_found": True, "mx_hosts": ["mx.b.com"]},
            "classify": {"disposable": False, "role": False, "free": False},
        },
    )
    rows = _rows(v)
    assert rows[5]["st"] == "Skipped"
    assert "disabled" in rows[5]["finding"]
    assert rows[6]["st"] == "Skipped"
    # Nothing anywhere in the trace may look like a successful probe.
    assert not any("250" in r["finding"] for r in rows)


def test_catch_all_marks_acceptance_as_inconclusive():
    v = Verdict(
        email="a@catchall.com",
        domain="catchall.com",
        status=Status.RISKY,
        sub_status=SubStatus.CATCH_ALL,
        is_catch_all=True,
        mx_found=True,
        checks={
            "syntax": {"valid": True},
            "typo": {"suggested_domain": None},
            "dns_mx": {"mx_found": True, "mx_hosts": ["mx.catchall.com"]},
            "classify": {"disposable": False, "role": False, "free": False},
            "smtp": {"outcome": "catch_all", "code": 250, "detail": ""},
        },
    )
    rows = _rows(v)
    assert rows[6]["st"] == "Soft"
    assert "proves nothing" in rows[6]["finding"]
    assert settled_layer("risky", "catch_all") == 7


def test_dns_timeout_is_not_blamed_on_the_smtp_layer():
    """TIMEOUT is reported by both the resolver and the probe. With no probe in
    `checks`, the timeout belongs to DNS."""
    assert settled_layer("unknown", "timeout", smtp_ran=False) == 4
    assert settled_layer("unknown", "timeout", smtp_ran=True) == 6


def test_confirmed_mailbox_reads_as_a_pass():
    v = Verdict(
        email="a@b.com",
        domain="b.com",
        status=Status.VALID,
        sub_status=SubStatus.OK,
        mx_found=True,
        checks={
            "syntax": {"valid": True},
            "typo": {"suggested_domain": None},
            "dns_mx": {"mx_found": True, "mx_hosts": ["mx.b.com", "mx2.b.com"]},
            "classify": {"disposable": False, "role": False, "free": False},
            "smtp": {"outcome": "valid", "code": 250, "detail": ""},
        },
    )
    rows = _rows(v)
    assert rows[3]["finding"].startswith("2 records · mx.b.com")
    assert rows[5]["st"] == "Pass" and "RCPT confirmed" in rows[5]["finding"]


@pytest.mark.parametrize(
    "status,sub,expected",
    [
        ("valid", "ok", "Mailbox confirmed at b.com"),
        ("risky", "catch_all", "Domain accepts everything — acceptance is not proof"),
        ("unknown", "greylisted", "We could not confirm this mailbox. This is not a failure."),
        ("invalid", "mailbox_not_found", "This address will bounce"),
    ],
)
def test_subtitles_never_overstate(status, sub, expected):
    assert subtitle_for(status, sub, "b.com") == expected
