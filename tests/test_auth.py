"""Accounts, sessions, API keys — and the workspace boundary they establish.

The point of this feature is not the login form. It is that `workspace_id`
stops being a configured claim and becomes something a request proves, so the
isolation the column was added for actually holds between two real accounts.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eve.addresses import AddressRecord, AddressStore, set_address_store
from eve.auth import (
    MIN_PASSWORD_LENGTH,
    AuthStore,
    api_key_display,
    hash_password,
    needs_rehash,
    new_api_key,
    password_problem,
    set_auth_store,
    verify_password,
)
from eve.config import Settings, set_settings

# Real hashing is ~180ms. These tests make dozens of accounts, so they run the
# KDF at a token cost — what is under test is the plumbing, and the cost is a
# constant the production path picks up from the module defaults.
FAST = {"iterations": 1_000, "n": 2 ** 10}


@pytest.fixture(autouse=True)
def _fast_hashing(monkeypatch):
    import eve.auth as auth

    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", FAST["iterations"])
    monkeypatch.setattr(auth, "SCRYPT_N", FAST["n"])
    real = auth.hash_password
    monkeypatch.setattr(
        auth, "hash_password", lambda pw, **kw: real(pw, n=FAST["n"], iterations=FAST["iterations"])
    )


@pytest.fixture
async def store(tmp_path):
    s = AuthStore(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    await s.init()
    set_auth_store(s)
    try:
        yield s
    finally:
        set_auth_store(None)


def _settings(**over) -> Settings:
    base = {"enable_dns": False, "enable_smtp": False, "require_auth": True, "open_signup": True}
    base.update(over)
    return Settings(**base)


# --- password hashing ----------------------------------------------------


def test_a_password_verifies_against_its_own_hash():
    h = hash_password("correct-horse-battery", iterations=1000, n=2 ** 10)
    assert verify_password("correct-horse-battery", h)
    assert not verify_password("correct-horse-batteryy", h)
    assert not verify_password("", h)


def test_the_hash_is_salted_so_two_identical_passwords_differ():
    a = hash_password("same-password-here", iterations=1000, n=2 ** 10)
    b = hash_password("same-password-here", iterations=1000, n=2 ** 10)
    assert a != b
    assert verify_password("same-password-here", a)
    assert verify_password("same-password-here", b)


def test_the_plaintext_never_appears_in_the_hash():
    h = hash_password("hunter2-hunter2-hunter2", iterations=1000, n=2 ** 10)
    assert "hunter2" not in h


@pytest.mark.parametrize(
    "stored",
    ["", "nonsense", "scrypt$bad", "md5$1$aa$bb", "pbkdf2$notanumber$aa$bb", "$$$$"],
)
def test_a_malformed_hash_is_a_failed_login_not_a_crash(stored):
    assert verify_password("anything", stored) is False


def test_a_weaker_stored_hash_is_flagged_for_upgrade():
    assert needs_rehash(hash_password("x" * 12, iterations=1_000, n=2 ** 10))
    assert needs_rehash("nonsense")


def test_password_length_is_the_only_rule():
    assert password_problem("x" * MIN_PASSWORD_LENGTH) is None
    assert password_problem("x" * (MIN_PASSWORD_LENGTH - 1))
    # No composition rules: a long passphrase of plain words is fine.
    assert password_problem("correct horse battery staple") is None


# --- accounts ------------------------------------------------------------


async def test_the_first_account_adopts_the_configured_workspace(store):
    """A deployment that ran without auth keeps the data it already has."""
    set_settings(Settings(workspace_id="default"))
    user = await store.create_user("first@acme.io", "a-long-enough-password")
    assert user.workspace_id == "default"


async def test_later_accounts_get_their_own_workspace(store):
    set_settings(Settings(workspace_id="default"))
    first = await store.create_user("first@acme.io", "a-long-enough-password")
    second = await store.create_user("second@acme.io", "a-long-enough-password")
    assert second.workspace_id not in ("default", first.workspace_id)


async def test_email_is_matched_without_case_or_padding(store):
    await store.create_user("  Jane@Acme.IO ", "a-long-enough-password")
    assert await store.authenticate("jane@acme.io", "a-long-enough-password")
    assert await store.authenticate("JANE@ACME.IO", "a-long-enough-password")
    with pytest.raises(ValueError):
        await store.create_user("JANE@acme.io", "a-long-enough-password")


async def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(store):
    await store.create_user("jane@acme.io", "a-long-enough-password")
    assert await store.authenticate("jane@acme.io", "wrong-password-here") is None
    assert await store.authenticate("nobody@acme.io", "wrong-password-here") is None


async def test_a_deactivated_account_cannot_sign_in(store):
    from sqlalchemy import update

    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    async with store._engine.begin() as conn:
        await conn.execute(update(store.users).where(store.users.c.id == user.id).values(is_active=False))
    assert await store.authenticate("jane@acme.io", "a-long-enough-password") is None


# --- sessions ------------------------------------------------------------


async def test_a_session_resolves_to_its_user_and_stops_at_logout(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    token = await store.start_session(user.id)

    assert (await store.user_for_session(token)).id == user.id
    await store.end_session(token)
    assert await store.user_for_session(token) is None


async def test_an_expired_session_is_not_accepted(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    token = await store.start_session(user.id, ttl_seconds=-1)
    assert await store.user_for_session(token) is None


async def test_the_session_table_holds_no_usable_token(store):
    """A leaked database must not be a set of live sessions."""
    from sqlalchemy import select

    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    token = await store.start_session(user.id)
    async with store._engine.connect() as conn:
        rows = (await conn.execute(select(store.sessions))).mappings().all()

    assert len(rows) == 1
    assert rows[0]["token_hash"] != token
    assert token not in str(dict(rows[0]))


async def test_signing_out_everywhere_ends_every_session(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    a, b = await store.start_session(user.id), await store.start_session(user.id)

    assert await store.end_all_sessions(user.id) == 2
    assert await store.user_for_session(a) is None
    assert await store.user_for_session(b) is None


async def test_expired_sessions_can_be_purged(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    await store.start_session(user.id, ttl_seconds=-1)
    live = await store.start_session(user.id)

    assert await store.purge_expired_sessions() == 1
    assert await store.user_for_session(live) is not None


# --- API keys ------------------------------------------------------------


async def test_an_api_key_resolves_to_its_user_until_revoked(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    key, row = await store.create_api_key(user.id, name="ci")

    assert key.startswith("eve_")
    assert (await store.user_for_api_key(key)).id == user.id
    assert await store.revoke_api_key(row["id"], user_id=user.id)
    assert await store.user_for_api_key(key) is None


async def test_the_key_table_holds_no_usable_key(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    key, row = await store.create_api_key(user.id)

    assert row["key_hash"] != key
    assert key not in str(row)
    # Only the prefix is kept, which identifies the key without being one.
    assert row["prefix"] == api_key_display(key)
    assert len(row["prefix"]) < len(key)


async def test_one_user_cannot_revoke_anothers_key(store):
    a = await store.create_user("a@acme.io", "a-long-enough-password")
    b = await store.create_user("b@other.io", "a-long-enough-password")
    key, row = await store.create_api_key(a.id)

    assert await store.revoke_api_key(row["id"], user_id=b.id) is False
    assert await store.user_for_api_key(key) is not None


async def test_a_revoked_key_is_kept_so_it_stays_identifiable(store):
    user = await store.create_user("jane@acme.io", "a-long-enough-password")
    _, row = await store.create_api_key(user.id, name="leaked")
    await store.revoke_api_key(row["id"])

    assert await store.list_api_keys(user.id) == []
    kept = await store.list_api_keys(user.id, include_revoked=True)
    assert [k["name"] for k in kept] == ["leaked"]


async def test_an_unknown_key_resolves_to_nobody(store):
    assert await store.user_for_api_key(new_api_key()) is None
    assert await store.user_for_api_key("") is None


# --- the workspace boundary ----------------------------------------------


async def test_two_accounts_do_not_see_each_others_addresses(tmp_path, store):
    """The whole point: identity now decides which rows a request can read."""
    addresses = AddressStore(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    await addresses.init()
    set_address_store(addresses)
    set_settings(_settings())

    alice = await store.create_user("alice@acme.io", "a-long-enough-password")
    bob = await store.create_user("bob@other.io", "a-long-enough-password")
    alice_key, _ = await store.create_api_key(alice.id)
    bob_key, _ = await store.create_api_key(bob.id)

    def _rec(email):
        return AddressRecord(
            email=email, domain=email.split("@")[1], status="valid", sub_status="ok",
            score=90, job_id="j1", job_filename="list.csv", checked_at=1000.0,
        )

    await addresses.upsert_many([_rec("lead@alice-corp.com")], workspace_id=alice.workspace_id)
    await addresses.upsert_many([_rec("lead@bob-corp.com")], workspace_id=bob.workspace_id)

    from eve.api.main import app

    with TestClient(app) as client:
        a = client.get("/v1/addresses", headers={"X-API-Key": alice_key}).json()
        b = client.get("/v1/addresses", headers={"X-API-Key": bob_key}).json()

    assert [r["email"] for r in a["rows"]] == ["lead@alice-corp.com"]
    assert [r["email"] for r in b["rows"]] == ["lead@bob-corp.com"]
    assert a["total"] == 1 and b["total"] == 1
    set_address_store(None)
