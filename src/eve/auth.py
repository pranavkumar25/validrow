"""Accounts, sessions and API keys — the thing every workspace boundary waited on.

Until now ``workspace_id`` came from configuration: the column was real, the
filtering was real, but *which* workspace a request belonged to was declared
rather than proven. This module is the proof. It resolves a request to a user,
and `eve.tenancy` publishes that user's workspace for the rest of the process.

Three deliberate choices:

**Password + email, not OAuth or magic links.** Both of those need something
outside the process — an OAuth client, or a way to send mail — and an auth
system that cannot be stood up without signing up for a third party is not one
you can run locally or test offline.

**Passwords are hashed with a standard-library KDF — scrypt where the build
provides it, PBKDF2-HMAC-SHA256 where it does not.** bcrypt and argon2 are the
usual answers and both are dependencies; scrypt is the same class of primitive
and ships with Python. It is not, however, always *present*: `hashlib.scrypt`
needs an OpenSSL that exposes it, and the macOS system Python is built against
LibreSSL, which does not. So the scheme is chosen at runtime and recorded in
the stored hash, and a hash made under the weaker scheme is upgraded the next
time its owner logs in. Neither branch adds a build dependency.

**Session tokens and API keys are stored hashed, and their plaintext is shown
exactly once.** They are high-entropy random strings rather than passwords, so
SHA-256 is the right hash for them: there is nothing to brute-force, and a fast
hash means the lookup on every request stays cheap. A leaked database yields no
usable credential either way.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from eve.addresses import default_db_url
from eve.config import get_settings

logger = logging.getLogger(__name__)

#: scrypt cost. 128 * N * r bytes of memory — 16 MB here, ~100ms per hash, which
#: is the point: it prices offline guessing out of reach while staying invisible
#: on a login. Raise N (never lower it) as hardware improves; stored hashes
#: record the parameters they were made with, so old ones keep verifying and are
#: upgraded on the next successful login.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024

#: PBKDF2 iterations, used where the build has no scrypt. Not memory-hard, so
#: it leans entirely on iteration count — this is the current OWASP figure for
#: PBKDF2-HMAC-SHA256 and costs ~180ms here.
PBKDF2_ITERATIONS = 600_000

#: Which KDF this build can actually offer. LibreSSL (macOS system Python)
#: builds hashlib without scrypt, so this is a runtime question, not a version
#: question — and answering it wrongly means every login raises AttributeError.
HAVE_SCRYPT = hasattr(hashlib, "scrypt")

#: Shown in full once, then only ever by prefix.
API_KEY_PREFIX = "eve_"
_API_KEY_DISPLAY = 12  # characters of the key kept for identifying it later

MIN_PASSWORD_LENGTH = 10


# --- password hashing ----------------------------------------------------


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM
    ).hex()


def _pbkdf2(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def hash_password(password: str, *, n: int = SCRYPT_N, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Hash a password, recording the scheme and cost in the returned string.

    ``scrypt$n$r$p$salt$hash`` or ``pbkdf2$iterations$salt$hash`` — self-describing
    so a stored hash keeps verifying after the defaults here change.
    """
    salt = os.urandom(16)
    if HAVE_SCRYPT:
        dk = _scrypt(password, salt, n, SCRYPT_R, SCRYPT_P)
        return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk}"
    return f"pbkdf2${iterations}${salt.hex()}${_pbkdf2(password, salt, iterations)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check against a stored hash. False on anything malformed.

    Verifies both schemes regardless of which one this build would *write*, so
    a database moved between a scrypt-capable host and a LibreSSL one keeps
    working in both directions.
    """
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt":
            _, n, r, p, salt_hex, hash_hex = parts
            if not HAVE_SCRYPT:
                logger.error(
                    "a password was hashed with scrypt but this build has none; "
                    "the account cannot sign in here"
                )
                return False
            computed = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p))
        elif parts[0] == "pbkdf2":
            _, iterations, salt_hex, hash_hex = parts
            computed = _pbkdf2(password, bytes.fromhex(salt_hex), int(iterations))
        else:
            return False
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed, hash_hex)


def needs_rehash(stored: str, *, n: int = SCRYPT_N, iterations: int = PBKDF2_ITERATIONS) -> bool:
    """Whether a stored hash is weaker than what this build would write now.

    Covers the upgrade that matters most: a database created on a host without
    scrypt, later run on one that has it, moves to scrypt as people log in.
    """
    parts = stored.split("$")
    try:
        if parts[0] == "scrypt":
            return not HAVE_SCRYPT or int(parts[1]) < n
        if parts[0] == "pbkdf2":
            return HAVE_SCRYPT or int(parts[1]) < iterations
    except (ValueError, IndexError):
        return True
    return True


def password_problem(password: str) -> Optional[str]:
    """Why this password is unacceptable, or ``None`` if it is fine.

    Length only. Composition rules (a digit, a symbol, a capital) push people
    towards `Password1!` and buy nothing a length floor does not.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


# --- tokens --------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def api_key_display(key: str) -> str:
    """The identifying fragment kept after the plaintext is discarded."""
    return key[:_API_KEY_DISPLAY]


def _now() -> float:
    return time.time()


@dataclass
class User:
    id: str
    email: str
    name: str
    workspace_id: str
    created_at: float = 0.0
    last_login_at: float = 0.0
    is_active: bool = True

    def as_identity(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name,
                "workspace_id": self.workspace_id}


def _user_from_row(row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"] or "",
        workspace_id=row["workspace_id"],
        created_at=row["created_at"] or 0.0,
        last_login_at=row["last_login_at"] or 0.0,
        is_active=bool(row["is_active"]),
    )


class AuthStore:
    """Users, sessions and API keys, in the same workspace database as everything else."""

    def __init__(self, url: Optional[str] = None):
        from sqlalchemy import Boolean, Column, Float, MetaData, String, Table
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(url or default_db_url(), future=True)
        self._metadata = MetaData()

        self.users = Table(
            "users",
            self._metadata,
            Column("id", String(64), primary_key=True),
            # Lowercased at every boundary: an address that differs only in case
            # is the same mailbox, and two accounts for it is an account-takeover
            # question nobody wants to answer later.
            Column("email", String(320), unique=True, index=True),
            Column("password_hash", String(255)),
            Column("name", String(200)),
            Column("workspace_id", String(64), index=True),
            Column("created_at", Float),
            Column("last_login_at", Float),
            Column("is_active", Boolean),
        )
        self.sessions = Table(
            "sessions",
            self._metadata,
            # The cookie holds the token; the table holds its hash. Reading this
            # table gives you nothing you can log in with.
            Column("token_hash", String(64), primary_key=True),
            Column("user_id", String(64), index=True),
            Column("created_at", Float),
            Column("expires_at", Float, index=True),
        )
        self.api_keys = Table(
            "api_keys",
            self._metadata,
            Column("id", String(64), primary_key=True),
            Column("key_hash", String(64), unique=True, index=True),
            Column("user_id", String(64), index=True),
            Column("workspace_id", String(64), index=True),
            Column("name", String(200)),
            Column("prefix", String(32)),
            Column("created_at", Float),
            Column("last_used_at", Float),
            Column("revoked_at", Float),
        )

    async def init(self) -> None:
        from eve.migrations import run_migrations

        async with self._engine.connect() as conn:
            await conn.run_sync(run_migrations)
            await conn.commit()

    # --- users -----------------------------------------------------------

    async def count_users(self) -> int:
        from sqlalchemy import func, select

        async with self._engine.connect() as conn:
            return int((await conn.execute(select(func.count()).select_from(self.users))).scalar() or 0)

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(self.users).where(self.users.c.email == email.strip().lower())))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def get_user(self, user_id: str) -> Optional[dict]:
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(self.users).where(self.users.c.id == user_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def create_user(
        self, email: str, password: str, *, name: str = "", workspace_id: Optional[str] = None
    ) -> User:
        """Register an account. Raises ``ValueError`` if the address is taken.

        The **first** account adopts the configured ``EVE_WORKSPACE_ID``, so a
        deployment that has been running without auth keeps every job and
        address it already has. Later accounts get a fresh workspace and see
        none of it — which is the isolation the column was added for.
        """
        from sqlalchemy import insert

        email = email.strip().lower()
        if await self.get_user_by_email(email):
            raise ValueError("an account with that email already exists")

        if workspace_id is None:
            first = await self.count_users() == 0
            workspace_id = get_settings().workspace_id if first else uuid.uuid4().hex

        user = User(
            id=uuid.uuid4().hex,
            email=email,
            name=name.strip(),
            workspace_id=workspace_id,
            created_at=_now(),
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(self.users),
                [{
                    "id": user.id,
                    "email": user.email,
                    "password_hash": hash_password(password),
                    "name": user.name,
                    "workspace_id": user.workspace_id,
                    "created_at": user.created_at,
                    "last_login_at": 0.0,
                    "is_active": True,
                }],
            )
        logger.info("account created for workspace %s", user.workspace_id)
        return user

    async def authenticate(self, email: str, password: str) -> Optional[User]:
        """Verify a login. ``None`` for a bad address, bad password or dead account.

        One outcome for all three: which of them it was is exactly what an
        attacker enumerating addresses wants to learn.
        """
        row = await self.get_user_by_email(email)
        if row is None:
            # Spend the same work anyway, so a missing account is not visibly
            # faster to probe than a wrong password.
            verify_password(password, hash_password("timing-equalizer"))
            return None
        if not row["is_active"] or not verify_password(password, row["password_hash"]):
            return None

        if needs_rehash(row["password_hash"]):
            await self.set_password(row["id"], password)
        await self._touch_login(row["id"])
        return _user_from_row(row)

    async def set_password(self, user_id: str, password: str) -> None:
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(self.users)
                .where(self.users.c.id == user_id)
                .values(password_hash=hash_password(password))
            )

    async def _touch_login(self, user_id: str) -> None:
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(self.users).where(self.users.c.id == user_id).values(last_login_at=_now())
            )

    # --- sessions --------------------------------------------------------

    async def start_session(self, user_id: str, *, ttl_seconds: Optional[float] = None) -> str:
        """Create a session and return the token to put in the cookie."""
        from sqlalchemy import insert

        ttl = ttl_seconds if ttl_seconds is not None else get_settings().session_ttl_seconds
        token = new_session_token()
        now = _now()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(self.sessions),
                [{
                    "token_hash": _token_hash(token),
                    "user_id": user_id,
                    "created_at": now,
                    "expires_at": now + ttl,
                }],
            )
        return token

    async def user_for_session(self, token: str) -> Optional[User]:
        from sqlalchemy import select

        if not token:
            return None
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(self.sessions).where(self.sessions.c.token_hash == _token_hash(token))
                    )
                )
                .mappings()
                .first()
            )
            if row is None or (row["expires_at"] or 0) <= _now():
                return None
            user = (
                (await conn.execute(select(self.users).where(self.users.c.id == row["user_id"])))
                .mappings()
                .first()
            )
        if user is None or not user["is_active"]:
            return None
        return _user_from_row(user)

    async def end_session(self, token: str) -> None:
        from sqlalchemy import delete

        if not token:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(self.sessions).where(self.sessions.c.token_hash == _token_hash(token))
            )

    async def end_all_sessions(self, user_id: str) -> int:
        """Sign a user out everywhere — what a password change should do."""
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            res = await conn.execute(delete(self.sessions).where(self.sessions.c.user_id == user_id))
            return res.rowcount or 0

    async def purge_expired_sessions(self) -> int:
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            res = await conn.execute(delete(self.sessions).where(self.sessions.c.expires_at <= _now()))
            return res.rowcount or 0

    # --- API keys --------------------------------------------------------

    async def create_api_key(self, user_id: str, *, name: str = "") -> tuple[str, dict]:
        """Mint a key. Returns ``(plaintext, row)`` — the plaintext is never stored."""
        from sqlalchemy import insert

        user = await self.get_user(user_id)
        if user is None:
            raise ValueError("no such user")

        key = new_api_key()
        row = {
            "id": uuid.uuid4().hex,
            "key_hash": _token_hash(key),
            "user_id": user_id,
            "workspace_id": user["workspace_id"],
            "name": (name or "API key").strip()[:200],
            "prefix": api_key_display(key),
            "created_at": _now(),
            "last_used_at": 0.0,
            "revoked_at": None,
        }
        async with self._engine.begin() as conn:
            await conn.execute(insert(self.api_keys), [row])
        return key, row

    async def user_for_api_key(self, key: str) -> Optional[User]:
        from sqlalchemy import select, update

        if not key:
            return None
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(self.api_keys).where(self.api_keys.c.key_hash == _token_hash(key))
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["revoked_at"]:
                return None
            user = (
                (await conn.execute(select(self.users).where(self.users.c.id == row["user_id"])))
                .mappings()
                .first()
            )
        if user is None or not user["is_active"]:
            return None

        async with self._engine.begin() as conn:
            await conn.execute(
                update(self.api_keys).where(self.api_keys.c.id == row["id"]).values(last_used_at=_now())
            )
        found = _user_from_row(user)
        # The key's own workspace wins: it was issued against that workspace and
        # revoking it must be the way to end its access, not moving the user.
        found.workspace_id = row["workspace_id"]
        return found

    async def api_key_id_for(self, key: str) -> Optional[str]:
        """The key's id, for rate-limiting a key rather than an IP."""
        from sqlalchemy import select

        if not key:
            return None
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(self.api_keys.c.id, self.api_keys.c.revoked_at).where(
                            self.api_keys.c.key_hash == _token_hash(key)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["revoked_at"]:
            return None
        return row["id"]

    async def list_api_keys(self, user_id: str, *, include_revoked: bool = False) -> list[dict]:
        from sqlalchemy import select

        q = select(self.api_keys).where(self.api_keys.c.user_id == user_id)
        if not include_revoked:
            q = q.where(self.api_keys.c.revoked_at.is_(None))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(q.order_by(self.api_keys.c.created_at.desc()))).mappings().all()
        return [dict(r) for r in rows]

    async def revoke_api_key(self, key_id: str, *, user_id: Optional[str] = None) -> bool:
        """Revoke rather than delete, so a key in a log stays identifiable."""
        from sqlalchemy import and_, update

        where = self.api_keys.c.id == key_id
        if user_id is not None:
            where = and_(where, self.api_keys.c.user_id == user_id)
        async with self._engine.begin() as conn:
            res = await conn.execute(update(self.api_keys).where(where).values(revoked_at=_now()))
            return bool(res.rowcount)


_store: Optional[AuthStore] = None


def get_auth_store() -> AuthStore:
    global _store
    if _store is None:
        _store = AuthStore()
    return _store


def set_auth_store(store: Optional[AuthStore]) -> None:
    """Override the process-wide store (tests / explicit configuration)."""
    global _store
    _store = store
