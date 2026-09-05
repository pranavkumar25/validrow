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


def test_every_page_shares_one_favicon(client):
    """The mark is an asset, not four data URIs that can drift apart."""
    icon = client.get("/static/favicon.svg")
    assert icon.status_code == 200
    body = icon.text
    # It follows the tab strip: a black mark on a dark chrome is an empty tab.
    assert "prefers-color-scheme: dark" in body
    assert "#0A0A0A" in body and "#0000FF" in body
    assert client.get("/static/apple-touch-icon.png").status_code == 200

    for path in ("/", "/docs", "/login", "/app/dashboard"):
        html = client.get(path).text
        assert 'href="/static/favicon.svg"' in html, f"{path} has no favicon"
        assert "data:image/svg+xml" not in html, f"{path} still inlines an icon"


# --- Landing page and the /app split --------------------------------------- #


def _create_account(client, email: str) -> None:
    """One real account, created the way a visitor creates one.

    Through the app rather than through the store: the store the app resolved at
    startup is bound to that boot's settings and to the TestClient's event loop,
    so a second one opened here would write to a different file and the page
    would go on reporting zero accounts.

    The cookie is dropped afterwards, because the visitor these tests are about
    is a stranger, and a signed-in one is redirected to the app.
    """
    r = client.post(
        "/signup",
        data={"email": email, "password": "a-long-enough-password", "name": "Seed"},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"signup returned {r.status_code}"
    client.cookies.clear()


def test_the_root_serves_a_landing_page_not_the_app(client):
    """A stranger arrives at "/". The app lives under /app."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Seven layers between your list and a bounce." in r.text
    # It is the pitch, not the shell: no sidebar, no workspace.
    assert "Local workspace" not in r.text
    assert 'href="/signup"' in r.text and 'href="/login"' in r.text


def test_the_landing_page_leaves_no_placeholder_unresolved(client):
    """Its context is large; a missing key renders as blank, not as an error."""
    html = client.get("/").text
    assert not LEFTOVERS.search(html)
    for marker in ("--blue:", "4,812 rows", "Free for the first 100 accounts."):
        assert marker in html, f"landing page lost {marker!r}"
    # Percentages that do not sum to 100 are the first sign of a careless tool,
    # and this page is selling care with numbers.
    import re

    shares = [int(x) for x in re.findall(r">(\d+)%<", html)]
    assert shares and sum(shares) == 100, f"verdict mix sums to {sum(shares)}"


def test_the_landing_page_matches_the_products_own_palette(client):
    """The verdict colours on the pitch are the ones the app renders."""
    from eve.web import format as F

    html = client.get("/").text
    for key in F.ORDER:
        style = F.VERDICT_STYLE[key]
        assert style["label"] in html
        assert style["dot"] in html, f"{key} dot colour missing"
    # The palette is emitted from format.py, not retyped in the stylesheet.
    assert f"--blue: {F.BLUE}" in html
    assert f"--ink: {F.INK}" in html


def test_the_landing_page_names_the_seven_layers_in_order(client):
    from eve.web.landing import LAYERS

    html = client.get("/").text
    for i, (name, _detail) in enumerate(LAYERS, start=1):
        assert f'<span class="n">{i}</span>' in html, f"layer {i} missing"
        assert name in html, f"layer {name!r} missing"
    assert "never DATA" in html  # the probe promise, stated to strangers too
    # The count is spelled out in two headlines. Both read it from the list.
    assert "Seven layers, cheapest first." in html


def test_the_landing_page_quotes_the_disposable_list_it_ships(client):
    """The count in the copy is the file's, not a number typed once and left."""
    from eve.layers.classify import list_sizes

    assert f"{list_sizes()['disposable']:,} disposable domains" in client.get("/").text


def test_the_landing_page_states_the_configured_offer(client):
    """The two numbers of the free tier come from settings, not from the copy."""
    from eve.config import get_settings

    s = get_settings()
    html = client.get("/").text
    assert f"{s.free_monthly_addresses:,} addresses a month" in html
    assert f"first {s.founding_accounts:,} accounts" in html


def test_the_landing_page_counts_the_founding_places_that_are_left(client):
    """The counter is accounts, which the auth store knows exactly.

    A number on a pitch page that nothing derives is the kind of claim this
    product exists to argue against, so it is asserted against a real count.
    """
    from eve.config import Settings, set_settings

    set_settings(Settings(open_signup=True))
    # Nobody has signed up: there is nothing to count down, so it states the offer.
    assert "Free for the first 100 accounts" in client.get("/").text

    _create_account(client, "jane@acme.io")
    html = client.get("/").text
    assert "99 of 100 free accounts left" in html
    assert "99 of 100 places open" in html


def test_a_closed_engine_makes_no_offer_it_cannot_honour(client):
    """Self-hosted, registration closed, one account already created.

    Counting down free places to a visitor who cannot register would be a lie
    the signup route would then have to refuse.
    """
    from eve.config import Settings, set_settings

    # Created while the engine still allows a first account, which is the
    # bootstrap every self-hosted install goes through.
    _create_account(client, "owner@acme.io")
    set_settings(Settings(open_signup=False))
    html = client.get("/").text
    assert "This engine is not open for registration." in html
    assert "free accounts left" not in html


def test_the_landing_copy_carries_no_em_dashes(client):
    """A house rule for the public page, and one a later edit could forget."""
    html = client.get("/").text
    assert "\u2014" not in html and "\u2013" not in html


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


# --- The API reference ------------------------------------------------------ #


def test_the_docs_page_is_ours_and_not_swagger(client):
    """`/docs` is a rendered page, not the CDN-loaded Swagger UI.

    Swagger pulls its bundle from jsdelivr, which is the wrong dependency for an
    engine whose whole claim is that it runs on infrastructure you control.
    """
    html = client.get("/docs").text
    assert "swagger" not in html.lower()
    assert "cdn.jsdelivr.net" not in html and "unpkg.com" not in html
    assert "API reference" in html
    # The machine-readable document stays exactly where it was.
    assert client.get("/openapi.json").status_code == 200


def test_the_docs_page_lists_every_route_the_engine_serves(client):
    """The page is generated, so a new route appears on it without an edit.

    Asserted against the OpenAPI document rather than a hard-coded list: a list
    typed here would be the second copy this page exists to avoid.
    """
    from eve.api.main import app

    html = client.get("/docs").text
    spec = app.openapi()
    documented = 0
    for path, item in spec["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                assert path in html, f"{method.upper()} {path} missing from /docs"
                documented += 1
    assert documented >= 10, "the API got smaller, or the spec is not being read"


def test_the_docs_page_carries_the_field_descriptions_from_the_models(client):
    """A description written on a Pydantic field is the one the reader sees."""
    from eve.api.schemas import VerifyRequest, VerifyResponse

    html = client.get("/docs").text
    for model in (VerifyRequest, VerifyResponse):
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"
            assert name in html, f"{model.__name__}.{name} missing from /docs"
    assert "The address to verify" in html


def test_the_docs_samples_are_built_from_the_schemas(client):
    """The request sample carries required fields, and no optional ones.

    An optional field filled with a type placeholder is not an illustration, it
    is an instruction: `"check_dns": false` would read as "turn DNS off".
    """
    html = client.get("/docs").text
    assert "john.doe@gmail.com" in html  # VerifyRequest.email's own example
    assert '"check_dns"' not in html.split("REQUEST BODY")[0]
    # The response sample is the model's authored example, not placeholders.
    assert "suggested_correction" in html and "john@gmail.com" in html
    assert '"string"' not in html, "a type placeholder leaked into a sample"


def test_the_docs_page_reads_this_engines_own_limits(client):
    """The numbers on the page are settings, not copy."""
    from eve.config import get_settings

    s = get_settings()
    html = client.get("/docs").text
    assert f"{s.max_upload_bytes // (1024 * 1024)} MB" in html


def test_the_docs_page_needs_no_network(client):
    """It renders with no external stylesheet, script or font."""
    import re

    html = client.get("/docs").text
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert not external, f"/docs reaches out to {external}"


def test_every_endpoint_carries_prose(client):
    """A reference where half the endpoints say nothing is half a reference.

    Asserted on the OpenAPI document, so the fix is a docstring on the handler
    rather than a paragraph typed into the page.
    """
    from eve.api.main import app

    spec = app.openapi()
    bare = [
        f"{m.upper()} {path}"
        for path, item in spec["paths"].items()
        for m, op in item.items()
        if m in ("get", "post", "put", "patch", "delete")
        and not (op.get("description") or "").strip()
    ]
    assert not bare, f"no description on: {bare}"


def test_each_endpoint_shows_three_languages(client):
    """cURL, Python and JavaScript, all generated from the one body."""
    html = client.get("/docs").text
    for label in ("cURL", "Python", "JavaScript"):
        assert html.count(f">{label}</label>") >= 12, f"{label} missing from some blocks"
    # Shaped like code somebody could run, not like a schema dump. The samples
    # are syntax-highlighted, so they are read as text rather than as markup:
    # `import` arrives wrapped in the span that colours it.
    import re

    code = re.sub(r"<[^>]+>", "", html)
    assert "import requests" in code and "r.raise_for_status()" in code
    assert "await fetch(" in code and "JSON.stringify(" in code
    assert "new FormData()" in code  # the upload endpoint, which takes no JSON


def test_the_docs_page_has_no_duplicate_element_ids(client):
    """The quickstart renders the same operation the reference does.

    Sharing ids there is not cosmetic: two radio groups with one name are one
    group, so the first tab block loses its checked input and shows no code at
    all, and a label points at the other block's radio.
    """
    import re
    from collections import Counter

    ids = re.findall(r'\sid="([^"]+)"', client.get("/docs").text)
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate ids on /docs: {dupes}"


@pytest.mark.parametrize("path,_fragments", SCREENS)
def test_script_navigation_is_prefixed_too(client, path, _fragments):
    """`onclick="location.href='...'"` is a link the href test cannot see.

    Fourteen buttons across eight screens kept a root path through the move to
    /app and answered 404: Dismiss's neighbour on the Dashboard banner, every
    "Validate a list" empty-state button, the back link on a run, and the whole
    of the upload wizard's navigation. The original test read href and action
    attributes only, so none of them was covered.
    """
    import re

    html = client.get(path).text
    targets = re.findall(r"location\.href\s*=\s*'(/[^']*)'", html)
    allowed_root = {"/login", "/signup", "/logout", "/docs", "/"}
    for t in targets:
        if t in allowed_root or t.startswith(("/static/", "/v1/")):
            continue
        assert t.startswith("/app/"), f"{path} navigates to {t}, which is not under /app"


def test_the_scripts_own_paths_are_prefixed(client):
    """app.js fetches and navigates too, and it is not markup the tests read.

    The Contacts row expander asked /addresses/detail and a finished job sent
    the browser to /validate; both 404 since the move. The prefix now reaches
    the script from the tag that loads it, so there is one definition of it.
    """
    js = client.get("/static/app.js").text
    literals = re.findall(r"""(?:fetch|location\.href\s*=)\s*\(?\s*'(/[^']*)'""", js)
    for lit in literals:
        assert lit.startswith(("/v1/", "/static/")), (
            f"app.js uses the bare path {lit}; build it from the APP prefix instead"
        )
    assert "data-app-prefix" in client.get("/app/dashboard").text


def test_hidden_actually_hides(client):
    """`hidden` is display:none from the UA sheet, and an inline display beats it.

    Three elements are written as `hidden style="...display: flex"`, so the
    selection bar stood open with nothing selected and a Contacts row's detail
    panel was expanded before it was clicked. One rule in base.html settles it,
    and it has to stay.
    """
    assert "[hidden] { display: none !important; }" in client.get("/app/dashboard").text


# The properties each responsive class owns. A screen that sets one of these
# inline takes it back off the stylesheet, and the media query silently stops
# applying: this is the invariant that lets the responsive rules work with no
# !important, so it is asserted rather than remembered.
OWNED_PROPERTIES = {
    "vr-cards": ["grid-template-columns"],
    "vr-kpis": ["grid-template-columns"],
    "vr-split": ["grid-template-columns"],
    "vr-bar": ["flex-wrap", "height"],
    "vr-head": ["flex-wrap"],
    "vr-head-main": ["flex"],
    "vr-note": ["flex-wrap", "align-items"],
    "vr-note-text": ["flex"],
    "vr-tabs": ["flex-wrap", "overflow"],
    "vr-grow": ["width", "min-width"],
    "vr-sidebar": ["width", "flex", "position", "height"],
}


def test_responsive_classes_still_own_their_properties():
    """No screen sets inline what its responsive class is supposed to control."""
    import re
    from pathlib import Path

    offenders = []
    for tpl in sorted(Path("src/eve/web/templates").glob("*.html")):
        html = tpl.read_text()
        for cls, props in OWNED_PROPERTIES.items():
            for m in re.finditer(
                r'class="[^"]*\b' + cls + r'\b[^"]*"[^>]*?style="([^"]*)"'
                r'|style="([^"]*)"[^>]*?class="[^"]*\b' + cls + r'\b[^"]*"',
                html,
            ):
                style = m.group(1) or m.group(2) or ""
                for decl in style.split(";"):
                    prop = decl.split(":", 1)[0].strip()
                    if prop in props:
                        offenders.append(f"{tpl.name}: .{cls} sets {prop} inline")
    assert not offenders, "\n".join(offenders)


def test_the_responsive_rules_need_no_important():
    """The whole point of the class split, pinned.

    The five `!important` that remain are the hover rules the design shipped
    with and the `[hidden]` fix, both of which override an inline *value* rather
    than a layout decision. The responsive block adds none.
    """
    import re
    from pathlib import Path

    css = Path("src/eve/web/templates/base.html").read_text()
    # Start at the comment that opens the block, not inside it, or the comment
    # stripper below has no "/*" to match and leaves the first one in.
    start = css.rindex("/*", 0, css.index("Responsive shell."))
    block = css[start:css.index("</style>")]
    # The comments explain why there is no !important, so they say the word.
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    lines = [ln.strip() for ln in block.splitlines() if "!important" in ln]
    assert not lines, "responsive rules regained !important:\n" + "\n".join(lines)


def test_phone_controls_clear_the_touch_floor(client):
    """Controls run from 18px to 34px tall; a finger wants more than that.

    `min-height` is what makes this possible without editing every screen: an
    inline `height: 24px` does not stop a stylesheet raising the minimum.
    """
    css = client.get("/app/dashboard").text
    assert ".vr-pad a[href]:not(.row)" in css and "min-height: 38px" in css
    assert ".vr-topbar a { min-height: 44px" in css
