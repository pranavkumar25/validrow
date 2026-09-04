"""Which workspace the current unit of work belongs to.

Every row in ``jobs`` and ``addresses`` carries a ``workspace_id``. There is no
authentication yet, so today that value comes from configuration and there is
exactly one workspace per process — but the column, the composite key and the
filtering are real.

That ordering is deliberate. Addresses are keyed by the normalized address so
re-running a list refreshes the row instead of duplicating it; without a
workspace in that key, two tenants validating ``john@acme.com`` would overwrite
each other and each would read the other's verdict. Adding the column later
means backfilling a key that is already wrong. Adding it now costs one
migration.

When auth lands, ``current_workspace_id`` becomes a request-scoped lookup
(a ContextVar set by middleware from the session or API key) and nothing else
in the codebase has to change.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from eve.config import get_settings

#: Set per-request once auth exists; falls back to configuration until then.
_current: ContextVar[Optional[str]] = ContextVar("eve_workspace_id", default=None)


def current_workspace_id() -> str:
    """The workspace to read and write. Never empty."""
    return _current.get() or get_settings().workspace_id or "default"


def set_current_workspace_id(workspace_id: Optional[str]):
    """Bind the workspace for the current context. Returns the reset token."""
    return _current.set(workspace_id)


def reset_current_workspace_id(token) -> None:
    _current.reset(token)
