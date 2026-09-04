"""Tenancy: every job and address belongs to a workspace.

The addresses primary key becomes ``(workspace_id, email)``. That is the whole
point of the revision — the address is de-duplicated *within* a workspace, so
two tenants validating the same mailbox each keep their own verdict instead of
overwriting one another.

Existing rows are adopted into the ``default`` workspace, which is what a
single-tenant deployment already was.

Revision ID: 0003_workspace_id
Revises: 0002_smtp_ran
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_workspace_id"
down_revision = "0002_smtp_ran"
branch_labels = None
depends_on = None

# Matches Settings.workspace_id's default. Hardcoded rather than imported: a
# migration has to mean the same thing in a year, whatever the config says then.
DEFAULT_WORKSPACE = "default"

_INDEXED = ("domain", "status", "job_id", "list_type", "checked_at", "checked_day")

# Column order of `addresses` as of 0002, used to copy rows on SQLite.
_ADDRESS_COLUMNS = [
    "email", "domain", "status", "sub_status", "score", "job_id", "job_filename",
    "list_type", "checked_at", "checked_day", "mx_found", "is_catch_all",
    "is_disposable", "is_role", "is_free", "smtp_ran", "settled_at", "checks",
]


def _addresses_table(name: str) -> sa.Table:
    return sa.Table(
        name,
        sa.MetaData(),
        sa.Column("workspace_id", sa.String(64), nullable=False, server_default=DEFAULT_WORKSPACE),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("domain", sa.String(255)),
        sa.Column("status", sa.String(32)),
        sa.Column("sub_status", sa.String(64)),
        sa.Column("score", sa.Integer),
        sa.Column("job_id", sa.String(64)),
        sa.Column("job_filename", sa.String(512)),
        sa.Column("list_type", sa.String(64)),
        sa.Column("checked_at", sa.Float),
        sa.Column("checked_day", sa.String(10)),
        sa.Column("mx_found", sa.Boolean),
        sa.Column("is_catch_all", sa.Boolean),
        sa.Column("is_disposable", sa.Boolean),
        sa.Column("is_role", sa.Boolean),
        sa.Column("is_free", sa.Boolean),
        sa.Column("smtp_ran", sa.Boolean),
        sa.Column("settled_at", sa.Integer),
        sa.Column("checks", sa.JSON),
        sa.PrimaryKeyConstraint("workspace_id", "email"),
    )


def upgrade() -> None:
    from eve.migrations._util import has_column

    bind = op.get_bind()

    if not has_column(bind, "jobs", "workspace_id"):
        op.add_column(
            "jobs",
            sa.Column(
                "workspace_id", sa.String(64), nullable=False, server_default=DEFAULT_WORKSPACE
            ),
        )
        op.create_index("ix_jobs_workspace_id", "jobs", ["workspace_id"])

    if has_column(bind, "addresses", "workspace_id"):
        return

    if bind.dialect.name == "sqlite":
        # SQLite cannot redefine a primary key in place: rebuild and swap.
        # Dropping the old table takes its indexes with it, so they are
        # recreated against the new one afterwards.
        _addresses_table("addresses_new").create(bind)
        cols = ", ".join(_ADDRESS_COLUMNS)
        op.execute(
            sa.text(
                f"INSERT INTO addresses_new (workspace_id, {cols}) "
                f"SELECT :ws, {cols} FROM addresses"
            ).bindparams(ws=DEFAULT_WORKSPACE)
        )
        op.drop_table("addresses")
        op.rename_table("addresses_new", "addresses")
        for col in _INDEXED:
            op.create_index(f"ix_addresses_{col}", "addresses", [col])
    else:
        op.add_column(
            "addresses",
            sa.Column(
                "workspace_id", sa.String(64), nullable=False, server_default=DEFAULT_WORKSPACE
            ),
        )
        pk = sa.inspect(bind).get_pk_constraint("addresses")
        if pk and pk.get("name"):
            op.drop_constraint(pk["name"], "addresses", type_="primary")
        op.create_primary_key("pk_addresses", "addresses", ["workspace_id", "email"])

    op.create_index("ix_addresses_workspace_id", "addresses", ["workspace_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrading would have to discard every workspace but one, silently "
        "merging their addresses. Restore from a backup instead."
    )
