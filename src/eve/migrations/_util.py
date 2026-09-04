"""Inspection helpers shared by the adoption-tolerant early revisions.

Revisions 0001-0003 have to run against databases that predate Alembic and may
hold any subset of the schema — the address store and the job store create
their own tables and either one may have run alone. So those revisions check
what is actually there before acting. Revisions from 0004 on can assume Alembic
has been in charge from the start and should be written strictly.
"""
from __future__ import annotations

import sqlalchemy as sa


def has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return column in {c["name"] for c in insp.get_columns(table)}
