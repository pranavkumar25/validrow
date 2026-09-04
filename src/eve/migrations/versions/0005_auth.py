"""Accounts, sessions and API keys.

Until this revision, ``workspace_id`` was declared by configuration — the
column was real but nothing proved which workspace a request belonged to.
These tables are what turns that declaration into an identity.

Nothing is backfilled. A deployment that has been running without auth has no
users, and the first account created adopts the configured workspace, so its
existing jobs and addresses are already its own.

Written strictly — Alembic has owned the schema since 0001.

Revision ID: 0005_auth
Revises: 0004_reprobes
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_auth"
down_revision = "0004_reprobes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        # Unique, and lowercased by the application at every boundary: an
        # address differing only in case is the same mailbox, and two accounts
        # for it is an account-takeover question nobody wants to answer later.
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float),
        sa.Column("last_login_at", sa.Float),
        sa.Column("is_active", sa.Boolean),
    )
    op.create_index("ix_users_workspace_id", "users", ["workspace_id"])

    op.create_table(
        "sessions",
        # The cookie carries the token; this carries its SHA-256. Reading the
        # table gives an attacker nothing they can log in with.
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float),
        sa.Column("expires_at", sa.Float),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        # Carried on the key itself rather than read through the user, so
        # revoking the key is the only way to end its access.
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("prefix", sa.String(32)),
        sa.Column("created_at", sa.Float),
        sa.Column("last_used_at", sa.Float),
        # Revoked rather than deleted, so a key seen in a log stays identifiable.
        sa.Column("revoked_at", sa.Float, nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_workspace_id", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_users_workspace_id", table_name="users")
    op.drop_table("users")
