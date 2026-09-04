"""The gate itself: who gets in, who gets redirected, and who gets a 401.

These drive the real app through a TestClient, so the middleware, the cookie
and the routes are all under test together — the parts where an auth bug
actually lives.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve.api.main import app
from eve.auth import get_auth_store
from eve.config import Settings, set_settings

PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def _fast_hashing(monkeypatch):
    import eve.auth as auth

    real = auth.hash_password
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1_000)
    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 10)
    monkeypatch.setattr(auth, "hash_password", lambda pw, **kw: real(pw, n=2 ** 10, iterations=1_000))


def _configure(**over) -> Settings:
    """Settings for this test. The storage dir already points at a temp path.

    ``session_cookie_secure`` is off here because TestClient speaks plain HTTP
    and a Secure cookie is correctly refused over it. The production default is
    on, and `test_the_cookie_is_marked_secure_when_configured` covers that.
    """
    base = {
        "enable_dns": False,
        "enable_smtp": False,
        "require_auth": True,
        "open_signup": True,
        "session_cookie_secure": False,
    }
    base.update(over)
    s = Settings(**base)
    set_settings(s)
    return s


@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


async def _make_user(email: str = "jane@acme.io") -> None:
    await get_auth_store().init()
    await get_auth_store().create_user(email, PASSWORD, name="Jane")


def _login(client, email: str = "jane@acme.io", password: str = PASSWORD, **extra):
    return client.post("/login", data={"email": email, "password": password, **extra})


# --- the gate ------------------------------------------------------------


async def test_a_browser_is_redirected_to_the_login_screen(client):
    _configure()
    await _make_user()

    r = client.get("/dashboard")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2Fdashboard"


async def test_the_original_destination_survives_the_login(client):
    _configure()
    await _make_user()

    r = client.get("/addresses?verdict=risky&page=2")
    assert r.headers["location"] == "/login?next=%2Faddresses%3Fverdict%3Drisky%26page%3D2"

    r = _login(client, next="/addresses?verdict=risky&page=2")
    assert r.status_code == 303
    assert r.headers["location"] == "/addresses?verdict=risky&page=2"


async def test_an_api_caller_gets_a_401_not_a_login_page(client):
    """Redirecting a script to HTML turns an auth error into a parse error."""
    _configure()
    await _make_user()

    r = client.post("/v1/verify", json={"email": "a@b.com"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")
    assert "X-API-Key" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/health", "/login", "/signup", "/openapi.json"])
async def test_public_paths_stay_reachable(client, path):
    _configure()
    await _make_user()
    assert client.get(path).status_code in (200, 303)


async def test_nothing_is_gated_when_auth_is_off(client):
    """The default. Turning auth on is opt-in; off must behave as it always did."""
    _configure(require_auth=False)
    assert client.get("/dashboard").status_code == 200
    assert client.post("/v1/verify", json={"email": "a@b.com"}).status_code == 200


# --- signing in ----------------------------------------------------------


async def test_a_good_password_starts_a_session(client):
    _configure()
    await _make_user()

    r = _login(client)
    assert r.status_code == 303
    cookie = r.cookies.get("vr_session")
    assert cookie

    assert client.get("/dashboard").status_code == 200


async def test_the_session_cookie_is_not_readable_from_javascript(client):
    _configure()
    await _make_user()

    header = _login(client).headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


async def test_the_cookie_is_marked_secure_when_configured(client):
    """The shipped default: the session cookie never crosses plain HTTP."""
    _configure(session_cookie_secure=True)
    await _make_user()

    assert "secure" in _login(client).headers["set-cookie"].lower()


async def test_a_bad_password_says_nothing_useful(client):
    _configure()
    await _make_user()

    r = _login(client, password="wrong-password-here")
    assert r.status_code == 401
    body = r.text
    assert "do not match an account" in body
    # The same sentence for an unknown address: no account enumeration.
    r2 = _login(client, email="nobody@acme.io", password="wrong-password-here")
    assert "do not match an account" in r2.text
    assert not client.cookies.get("vr_session")


@pytest.mark.parametrize("evil", ["//evil.example.com", "https://evil.example.com", "javascript:x"])
async def test_login_will_not_bounce_you_off_site(client, evil):
    """An open redirect on a login form is how a phishing page borrows a domain."""
    _configure()
    await _make_user()

    r = _login(client, next=evil)
    assert r.headers["location"] == "/"


async def test_signing_out_kills_the_session_server_side(client):
    _configure()
    await _make_user()

    token = _login(client).cookies.get("vr_session")
    assert await get_auth_store().user_for_session(token) is not None

    r = client.post("/logout")
    assert r.status_code == 303
    # Not merely cleared in the browser — a copied token is dead too.
    assert await get_auth_store().user_for_session(token) is None


# --- signing up ----------------------------------------------------------


async def test_the_first_account_can_always_be_created(client):
    """A fresh install has to be able to bootstrap itself."""
    _configure(open_signup=False)

    r = client.post("/signup", data={"email": "first@acme.io", "password": PASSWORD, "name": "First"})
    assert r.status_code == 303
    assert r.cookies.get("vr_session")


async def test_registration_closes_after_the_first_account(client):
    _configure(open_signup=False)
    await _make_user()

    assert client.get("/signup").status_code == 403
    r = client.post("/signup", data={"email": "second@acme.io", "password": PASSWORD})
    assert r.status_code == 403
    assert "not open for registration" in r.text


async def test_open_signup_lets_anyone_register(client):
    _configure(open_signup=True)
    await _make_user()

    r = client.post("/signup", data={"email": "second@acme.io", "password": PASSWORD})
    assert r.status_code == 303


async def test_a_short_password_is_refused(client):
    _configure()

    r = client.post("/signup", data={"email": "a@b.com", "password": "short"})
    assert r.status_code == 400
    assert "at least" in r.text
    assert await get_auth_store().count_users() == 0


async def test_a_duplicate_address_is_refused(client):
    _configure(open_signup=True)
    await _make_user()

    r = client.post("/signup", data={"email": "JANE@acme.io", "password": PASSWORD})
    assert r.status_code == 409
    assert "already exists" in r.text


async def test_login_sends_a_fresh_install_to_signup(client):
    """There is nobody to sign in as yet, so offer the form that can help."""
    _configure()

    r = client.get("/login")
    assert r.status_code == 303
    assert r.headers["location"] == "/signup"


# --- API keys ------------------------------------------------------------


async def test_a_key_authenticates_the_json_api(client):
    _configure()
    await _make_user()
    _login(client)

    r = client.post("/settings/keys", data={"name": "ci"})
    assert r.status_code == 303
    key = r.cookies.get("vr_new_key")
    assert key and key.startswith("eve_")

    client.cookies.clear()  # no session — the key alone must carry it
    assert client.post("/v1/verify", json={"email": "a@b.com"}, headers={"X-API-Key": key}).status_code == 200
    assert client.post(
        "/v1/verify", json={"email": "a@b.com"}, headers={"Authorization": f"Bearer {key}"}
    ).status_code == 200


async def test_a_revoked_key_does_not_fall_back_to_a_session(client):
    """A rejected credential is not the same as presenting none."""
    _configure()
    await _make_user()
    _login(client)
    key = client.post("/settings/keys", data={"name": "ci"}).cookies.get("vr_new_key")

    user = await get_auth_store().get_user_by_email("jane@acme.io")
    row = (await get_auth_store().list_api_keys(user["id"]))[0]
    await get_auth_store().revoke_api_key(row["id"])

    # The browser session is still valid, but the request presented a dead key.
    r = client.post("/v1/verify", json={"email": "a@b.com"}, headers={"X-API-Key": key})
    assert r.status_code == 401


async def test_an_unknown_key_is_rejected(client):
    _configure()
    await _make_user()

    r = client.post("/v1/verify", json={"email": "a@b.com"}, headers={"X-API-Key": "eve_not-a-key"})
    assert r.status_code == 401


async def test_creating_a_key_needs_an_account(client):
    _configure(require_auth=False)  # open engine, but a key still belongs to someone
    assert client.post("/settings/keys", data={"name": "ci"}).status_code == 401


async def test_the_plaintext_key_is_shown_once_then_cleared(client):
    _configure()
    await _make_user()
    _login(client)
    key = client.post("/settings/keys", data={"name": "ci"}).cookies.get("vr_new_key")

    first = client.get("/settings")
    assert key in first.text
    assert "not shown again" in first.text

    client.cookies.delete("vr_new_key", path="/settings")
    assert key not in client.get("/settings").text
