"""Baseline: the jobs and addresses tables as they stood before Alembic.

Adopt-or-create. Databases written before Alembic have these tables already,
and may have only one of the two, because the address store and the job store
each create their own on first use. So each table is created only if absent,
which makes this revision safe to replay against any of those states.

Revision ID: 0001_baseline
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from eve.migrations._util import has_table

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    if not has_table(bind, "jobs"):
        _create_jobs()
    if not has_table(bind, "addresses"):
        _create_addresses()


def _create_jobs() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("seq", sa.Integer),
        sa.Column("filename", sa.String(512)),
        sa.Column("file_key", sa.String(256)),
        sa.Column("status", sa.String(32)),
        sa.Column("list_type", sa.String(64)),
        sa.Column("webhook_url", sa.String(1024), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("mapping", sa.JSON, nullable=True),
        sa.Column("counts", sa.JSON),
        sa.Column("output_keys", sa.JSON),
        sa.Column("phase", sa.String(32)),
        sa.Column("processed", sa.Integer),
        sa.Column("total", sa.Integer),
        sa.Column("domains_total", sa.Integer),
        sa.Column("created_at", sa.Float),
        sa.Column("started_at", sa.Float, nullable=True),
        sa.Column("finished_at", sa.Float, nullable=True),
    )


def _create_addresses() -> None:
    op.create_table(
        "addresses",
        sa.Column("email", sa.String(320), primary_key=True),
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
        sa.Column("settled_at", sa.Integer),
        sa.Column("checks", sa.JSON),
    )
    for col in ("domain", "status", "job_id", "list_type", "checked_at", "checked_day"):
        op.create_index(f"ix_addresses_{col}", "addresses", [col])


def downgrade() -> None:
    op.drop_table("addresses")
    op.drop_table("jobs")
