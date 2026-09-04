"""Alembic environment.

Two ways in:

* **Programmatic** (the normal path) — ``eve.migrations.run_migrations`` hands
  us a live connection via ``config.attributes["connection"]``. The stores call
  it from ``init()``, so a fresh checkout and a deployed upgrade take the same
  code path.
* **CLI** — ``alembic upgrade head`` with no connection supplied. We build one
  from ``EVE_WORKSPACE_DB_URL`` (or the default SQLite file), converting the
  async driver to its sync equivalent because the CLI has no event loop.
"""
from __future__ import annotations

from alembic import context

config = context.config
target_metadata = None  # migrations are explicit; no autogenerate


def _sync_url() -> str:
    """The workspace URL with any async driver swapped for its sync twin."""
    from eve.addresses import default_db_url

    url = default_db_url()
    for async_driver, sync_driver in (
        ("+aiosqlite", ""),
        ("+asyncpg", "+psycopg2"),
        ("+aiomysql", "+pymysql"),
    ):
        if async_driver in url:
            return url.replace(async_driver, sync_driver)
    return url


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER a primary key in place; batch mode rebuilds the
        # table instead, and is a no-op elsewhere.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return

    from sqlalchemy import create_engine

    engine = create_engine(_sync_url())
    try:
        with engine.connect() as conn:
            _run(conn)
            conn.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
