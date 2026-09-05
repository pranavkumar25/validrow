"""The database URL a managed provider actually hands you.

Neon, Supabase, Render, Railway and Heroku all issue libpq URLs carrying
``?sslmode=require``, and Neon adds ``channel_binding=require``. Those are
psycopg2 parameters; asyncpg has never accepted them, and SQLAlchemy forwards
unknown query parameters straight to the driver. So the URL that works in psql
and in every other tool fails here with

    TypeError: connect() got an unexpected keyword argument 'sslmode'

on the first connection, which is inside the startup migration, which is why it
presents as a process that will not boot rather than as a database problem.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from eve.addresses import async_engine


def _built(url: str) -> dict:
    """The arguments async_engine hands SQLAlchemy for this URL."""
    captured: dict = {}
    real = None

    def spy(resolved, **kwargs):
        captured["url"] = resolved
        captured["kwargs"] = kwargs
        return real(resolved, **kwargs)

    from sqlalchemy.ext.asyncio import create_async_engine

    real = create_async_engine
    with patch("sqlalchemy.ext.asyncio.create_async_engine", spy):
        async_engine(url)
    return captured


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@h.neon.tech/db?sslmode=require&channel_binding=require",
        "postgresql+asyncpg://u:p@h.supabase.co:5432/postgres?sslmode=require",
        "postgresql+asyncpg://u:p@h/db?sslmode=verify-full",
    ],
)
def test_libpq_parameters_never_reach_asyncpg(url: str) -> None:
    built = _built(url)
    query = dict(built["url"].query)
    assert "sslmode" not in query, "sslmode would be forwarded to asyncpg.connect()"
    assert "channel_binding" not in query


def test_requiring_tls_becomes_the_argument_asyncpg_understands() -> None:
    built = _built("postgresql+asyncpg://u:p@h/db?sslmode=require")
    assert built["kwargs"]["connect_args"]["ssl"] is True


def test_disabling_tls_is_honoured_rather_than_ignored() -> None:
    """Dropping the parameter without translating it would silently force TLS."""
    built = _built("postgresql+asyncpg://u:p@h/db?sslmode=disable")
    assert built["kwargs"]["connect_args"]["ssl"] is False


def test_a_url_with_no_ssl_parameter_is_left_alone() -> None:
    built = _built("postgresql+asyncpg://u:p@h/db")
    assert built["kwargs"].get("connect_args", {}) == {}


def test_sqlite_is_untouched() -> None:
    """The default backend must not grow Postgres connect arguments."""
    built = _built("sqlite+aiosqlite:///:memory:")
    assert built["kwargs"].get("connect_args", {}) == {}
    assert built["url"].drivername == "sqlite+aiosqlite"


def test_every_store_builds_its_engine_through_the_helper() -> None:
    """Four stores open connections; a URL fixed in one is fixed in none."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "eve"
    for module in ("addresses.py", "auth.py", "reprobe.py", "jobs/store.py"):
        text = (root / module).read_text()
        assert "async_engine(" in text, f"{module} does not use the helper"
        # The helper itself is the one place allowed to call SQLAlchemy directly.
        calls = text.count("create_async_engine(")
        expected = 1 if module == "addresses.py" else 0
        assert calls == expected, f"{module} calls create_async_engine directly"
