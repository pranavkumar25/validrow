"""Record whether the mailbox probe ran, and recompute the settling layer.

Rows written before the DNS-timeout fix credited that timeout to layer 6
instead of layer 4. ``smtp_ran`` is the underlying *fact*; ``settled_at`` is a
derivation from it, which reporting recomputes on read — so the backfill here
only matters to anything reading the stored column directly.

Revision ID: 0002_smtp_ran
Revises: 0001_baseline
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0002_smtp_ran"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from eve.migrations._util import has_column
    from eve.trace import settled_layer

    conn = op.get_bind()
    if has_column(conn, "addresses", "smtp_ran"):
        return  # database predates Alembic but already has this change

    op.add_column("addresses", sa.Column("smtp_ran", sa.Boolean, nullable=True))

    rows = conn.execute(
        sa.text("SELECT email, status, sub_status, checks FROM addresses")
    ).all()
    for email, status, sub, checks in rows:
        data = json.loads(checks) if isinstance(checks, str) else (checks or {})
        smtp = (data or {}).get("smtp")
        ran = smtp is not None and smtp.get("detail") != "smtp_disabled"
        conn.execute(
            sa.text(
                "UPDATE addresses SET smtp_ran = :ran, settled_at = :layer WHERE email = :email"
            ),
            {
                "ran": ran,
                "layer": settled_layer(status or "", sub or "", smtp_ran=ran),
                "email": email,
            },
        )


def downgrade() -> None:
    op.drop_column("addresses", "smtp_ran")
