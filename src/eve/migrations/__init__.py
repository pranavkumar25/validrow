"""Schema migrations for the workspace database.

Alembic is the single source of truth for the schema: there is no
``create_all`` anywhere, so a fresh SQLite file and a year-old Postgres reach
head by replaying the same revisions.

Databases written before Alembic existed have tables but no ``alembic_version``
row, and may hold only part of the schema — the two stores create their own
tables independently. Rather than guess which revision such a database matches
and stamp it, revisions 0001-0003 check what is present before acting, so
replaying them from base is safe against any of those states. Revisions from
0004 on assume Alembic has been in charge and are written strictly.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SCRIPT_LOCATION = str(Path(__file__).parent)


def _config(connection):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", _SCRIPT_LOCATION)
    # Tell env.py to use the connection we already hold rather than opening a
    # second one — the caller may be mid-transaction on a temp SQLite file.
    cfg.attributes["connection"] = connection
    return cfg


def run_migrations(connection) -> None:
    """Bring the database behind ``connection`` (a *sync* Connection) to head.

    Called via ``await conn.run_sync(run_migrations)`` from the async stores.
    """
    from alembic import command

    command.upgrade(_config(connection), "head")
