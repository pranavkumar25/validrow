"""Pending re-probes for greylisted addresses.

A 4xx reply defers us rather than answering us, so the address is retried on a
schedule instead of being settled on the first reply. This table is the durable
record of that schedule: the retry survives a restart because the row does, not
because a timer does.

Written strictly — Alembic has owned the schema since 0001, so this table
cannot already exist.

Revision ID: 0004_reprobes
Revises: 0003_workspace_id
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_reprobes"
down_revision = "0003_workspace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reprobes",
        # Same identity as the address row this will update: a retry belongs to
        # one tenant's reading of one mailbox.
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column("job_id", sa.String(64)),
        sa.Column("attempt", sa.Integer),
        sa.Column("next_attempt_at", sa.Float),
        sa.Column("first_deferred_at", sa.Float),
        sa.Column("last_code", sa.Integer),
    )
    # The only hot query is "what is due now", ordered by when.
    op.create_index("ix_reprobes_next_attempt_at", "reprobes", ["next_attempt_at"])
    op.create_index("ix_reprobes_job_id", "reprobes", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_reprobes_job_id", table_name="reprobes")
    op.drop_index("ix_reprobes_next_attempt_at", table_name="reprobes")
    op.drop_table("reprobes")
