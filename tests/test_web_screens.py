"""Every screen renders, empty and populated, with nothing left unresolved.

The templates are a direct translation of the design file, so the failure mode
worth guarding is a binding the view-model stopped supplying: Jinja renders it
as nothing and the screen quietly loses a column. These tests assert on the copy
and structure the design specifies.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from eve.addresses import AddressRecord, AddressStore, set_address_store
from eve.api.main import app

SCREENS = [
    ("/app/", ["Dashboard", "Validation volume", "Verdict mix", "Recent results"]),
    ("/app/dashboard", ["QUALITY SCORE", "SEGMENT", "This period", "Previous", "All lists"]),
    # "Rows per page" is deliberately absent until there are rows to page.
    ("/app/addresses", ["Every address across all completed jobs, de-duplicated.",
                        "Total addresses", "Unique domains"]),
    ("/app/analytics", ["Deliverable rate over time", "Volume by weekday", "Top domains"]),
    ("/app/exports", ["Slice the workspace and take the CSV.", "Full detail", "Email only",
                      "Export CSV"]),
    ("/app/history", ["Every job this workspace has run."]),
    ("/app/settings", ["Engine connection", "Engine status", "SMTP mailbox probe"]),
    ("/app/how", ["Seven layers, in a fixed order", "What each verdict means"]),
    ("/app/validate", ["Drop a CSV, or browse", "Upload", "Preview", "Map columns"]),
    ("/app/single", ["One address, the full seven-layer trace."]),
]

# Unresolved template syntax, or a prototype attribute that was never converted.
LEFTOVERS = re.compile(r'\{\{|\{%|<sc-(?:for|if)|style-hover="')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_LOCAL_STORAGE_DIR", str(tmp_path))
    store = AddressStore(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    set_address_store(store)
    with TestClient(app) as c:
        yield c
    set_address_store(None)


@pytest.fixture
def populated(client, tmp_path):
    """A workspace with one address per verdict.

    Seeded through a short-lived store on its own event loop, so the app's
    engine (bound to the TestClient's loop) is never touched from outside it.
    """
    import asyncio

    url = f"sqlite+aiosqlite:///{tmp_path}/ws.db"
    rows = [
        AddressRecord("ok@acme.io", "acme.io", "valid", "ok", 92, "j1", "list.csv",
                      checked_at=1785000000.0, mx_found=True, settled_at=6,
                      checks={"syntax": {"valid": True},
                              "dns_mx": {"mx_found": True, "mx_hosts": ["mx.acme.io"]},
                              "classify": {"disposable": False, "role": False, "free": False},
                              "smtp": {"outcome": "valid", "code": 250, "detail": ""}}),
        AddressRecord("info@acme.io", "acme.io", "risky", "role_account", 50, "j1",
                      "list.csv", checked_at=1785000000.0, is_role=True, settled_at=5),
        AddressRecord("who@other.com", "other.com", "unknown", "greylisted", 60, "j1",
                      "list.csv", checked_at=1785000000.0, settled_at=6),
        AddressRecord("no@gone.test", "gone.test", "invalid", "no_mx", 3, "j1",
                      "list.csv", checked_at=1785000000.0, settled_at=4,
                      checks={"syntax": {"valid": True},
                              "dns_mx": {"mx_found": False, "mx_hosts": [], "error": None}}),
    ]

    async def _seed():
        s = AddressStore(url)
        await s.init()
        await s.upsert_many(rows)

    asyncio.new_event_loop().run_until_complete(_seed())
    return client


@pytest.mark.parametrize("path,fragments", SCREENS)
def test_screen_renders_when_the_workspace_is_empty(client, path, fragments):
    r = client.get(path)
    assert r.status_code == 200
    for f in fragments:
        assert f in r.text, f"{path} lost {f!r}"
    assert not LEFTOVERS.search(r.text), f"{path} has unresolved template syntax"


def test_empty_workspace_shows_the_designed_empty_states(client):
    assert "Nothing validated yet" in client.get("/app/addresses").text
    assert "No runs yet" in client.get("/app/history").text


def test_one_period_of_data_says_so_instead_of_drawing_a_spike(populated):
    """All the seeded addresses land in one bucket. A spline through a single
    reading looks like a broken chart, so the card states the limit instead."""
    html = populated.get("/app/dashboard").text
    assert "Not enough history yet" in html
    assert 'id="volchart"' not in html


def test_empty_workspace_hides_period_deltas_rather_than_inventing_them(client):
    """With no prior period there is no honest comparison to show."""
    html = client.get("/app/dashboard").text
    assert "Total validated" in html
    # The delta chip markup only appears when a real comparison exists.
    assert "#027A48" not in html.split("Validation volume")[0].split("Total validated")[1][:400]


@pytest.mark.parametrize("path,fragments", SCREENS)
def test_screen_renders_with_data(populated, path, fragments):
    r = populated.get(path)
    assert r.status_code == 200
    for f in fragments:
        assert f in r.text
    assert not LEFTOVERS.search(r.text)


def test_contacts_shows_every_verdict_with_its_sub_reason(populated):
    html = populated.get("/app/addresses").text
    for expected in ("ok@acme.io", "Deliverable", "Risky", "Role account",
                     "Unknown", "Greylisted", "Undeliverable", "No MX record"):
        assert expected in html
    # Paging controls appear once there is something to page.
    assert "Rows per page" in html
    assert "Showing 1–4 of 4" in html


def test_verdict_filter_narrows_the_list(populated):
    only_risky = populated.get("/app/addresses?verdict=risky").text
    assert "info@acme.io" in only_risky
    assert "ok@acme.io" not in only_risky


def test_filters_that_match_nothing_show_the_filtered_empty_state(populated):
    html = populated.get("/app/addresses?q=zzzznomatch").text
    assert "No addresses match these filters" in html
    assert "Nothing validated yet" not in html


def test_row_detail_returns_the_seven_layer_trace(populated):
    r = populated.get("/app/addresses/detail", params={"email": "no@gone.test"})
    assert r.status_code == 200
    assert "DNS / MX" in r.text and "No MX and no A fallback" in r.text
    # Layers after the one that settled it must say they did not run.
    assert r.text.count("settled at layer 4") == 3


def test_row_detail_404s_for_an_unknown_address(populated):
    assert populated.get("/app/addresses/detail", params={"email": "nope@nope.com"}).status_code == 404


def test_single_check_renders_a_real_trace(client):
    html = client.get("/app/single", params={"email": "not-an-email"}).text
    assert "Layer trace" in html
    assert "RFC 5322 reject" in html
    assert "Undeliverable" in html


def test_unknown_job_404s(client):
    assert client.get("/app/history/deadbeef").status_code == 404


def test_export_streams_a_filtered_csv(populated):
    r = populated.post("/app/exports/download", data={"preset": "risky", "columns": "email"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "email"
    assert lines[1:] == ["info@acme.io"]


def test_export_full_detail_carries_the_settling_layer(populated):
    r = populated.post("/app/exports/download", data={"preset": "all", "columns": "full"})
    header = r.text.splitlines()[0]
    assert "settled_at_layer" in header and "sub_status" in header and "verdict" in header


def test_sidebar_names_the_workspace_rather_than_a_signed_in_person(client):
    """There is no auth, so an unconfigured install must not claim an identity
    it cannot back up. It names the workspace and the engine serving it."""
    html = client.get("/app/dashboard").text
    assert "Local workspace" in html
    assert "testserver" in html  # the engine host, which is true
    assert "LW" in html  # initials derived from the name, not invented


def test_sidebar_shows_a_configured_owner_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_LOCAL_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("EVE_WORKSPACE_NAME", "Pranav Kumar")
    monkeypatch.setenv("EVE_WORKSPACE_EMAIL", "pranav@validrow.io")
    from eve.config import Settings, set_settings

    set_settings(Settings())
    store = AddressStore(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    set_address_store(store)
    try:
        with TestClient(app) as c:
            html = c.get("/app/dashboard").text
        assert "Pranav Kumar" in html
        assert "pranav@validrow.io" in html
        assert "PK" in html
        assert "Local workspace" not in html
    finally:
        set_address_store(None)
        set_settings(Settings(_env_file=None))


def test_account_chevron_opens_a_menu_of_real_destinations(client):
    """The design's chevron implies a menu; it now has one, and every entry
    goes somewhere that exists."""
    html = client.get("/app/dashboard").text
    assert "data-acct-menu" in html
    assert "Engine settings" in html and "API docs" in html
    assert client.get("/app/settings").status_code == 200
    assert client.get("/docs").status_code == 200


def test_static_assets_are_self_hosted(client):
    """The app must render with no network access."""
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/InterVariable.woff2").status_code == 200
    assert "fonts.googleapis.com" not in client.get("/app/dashboard").text


# --- Landing page and the /app split --------------------------------------- #


def test_the_root_serves_a_landing_page_not_the_app(client):
    """A stranger arrives at "/". The app lives under /app."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Clean your list without" in r.text
    # It is the pitch, not the shell: no sidebar, no workspace.
    assert "Local workspace" not in r.text
    assert 'href="/signup"' in r.text and 'href="/login"' in r.text


def test_the_landing_page_matches_the_products_own_palette(client):
    """The verdict colours on the pitch are the ones the app renders."""
    from eve.web import format as F

    html = client.get("/").text
    for key in F.ORDER:
        style = F.VERDICT_STYLE[key]
        assert style["label"] in html
        assert style["dot"] in html, f"{key} dot colour missing"


def test_the_landing_page_names_the_seven_layers_in_order(client):
    html = client.get("/").text
    for n in range(1, 8):
        assert f">{n}</span>" in html, f"layer {n} missing"
    assert "never DATA" in html  # the probe promise, stated to strangers too


def test_every_app_screen_lives_under_the_prefix(client):
    """The prefix is one constant; nothing should answer on the old paths."""
    for bare in ("/dashboard", "/addresses", "/settings", "/validate", "/history"):
        assert client.get(bare).status_code == 404, f"{bare} still answers at the root"
        assert client.get("/app" + bare).status_code == 200


@pytest.mark.parametrize("path,_fragments", SCREENS)
def test_in_app_links_are_all_prefixed(client, path, _fragments):
    """A link that lost its prefix 404s in production, not in a unit test.

    Every screen, not just one: the first pass of this caught the dashboard's
    tab links and missed Analytics' grain links, which had the same defect.
    """
    import re

    html = client.get(path).text
    hrefs = set(re.findall(r'(?:href|action)="(/[^"]*)"', html))
    allowed_root = {"/login", "/signup", "/logout", "/docs", "/"}
    for href in hrefs:
        if href in allowed_root or href.startswith(("/static/", "/v1/")):
            continue
        assert href.startswith("/app/"), f"{path} links to {href}, which is not under /app"
