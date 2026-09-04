"""Turning a request into a workspace.

`eve.tenancy` always said this was the shape it was waiting for: *"when auth
lands, ``current_workspace_id`` becomes a request-scoped lookup (a ContextVar
set by middleware from the session or API key) and nothing else in the codebase
has to change."* This is that middleware, and nothing else did change — every
store still filters on the same column it always has.

Two credentials, one identity:

* **A session cookie**, for the web app. Opaque token, `HttpOnly`, checked
  against a hashed row so the database holds nothing you can sign in with.
* **An API key**, for `/v1/*`. Sent as `X-API-Key`, or as `Authorization:
  Bearer` for callers whose HTTP client makes that easier.

Failing to authenticate is not the same as being anonymous. With
``EVE_REQUIRE_AUTH`` off — the default — an unauthenticated request is served
against the configured workspace, exactly as before this existed. With it on, a
browser is redirected to the login screen and an API caller gets a 401 with a
JSON body, because redirecting a script to an HTML page is how you turn an auth
error into a confusing parse error.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from eve.auth import User, get_auth_store
from eve.config import get_settings
from eve.tenancy import reset_current_workspace_id, set_current_workspace_id

logger = logging.getLogger(__name__)

#: Reachable without credentials. Everything else is gated when auth is on.
#: `/health` is here because a load balancer cannot log in, and the API docs
#: because a 401 on `/docs` is how you make an API look broken.
PUBLIC_PATHS = frozenset({
    "/health",
    "/login",
    "/signup",
    "/logout",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
})

PUBLIC_PREFIXES = ("/static/",)

API_KEY_HEADER = "x-api-key"


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _presented_api_key(request: Request) -> str:
    """The API key on this request, from either header form."""
    key = request.headers.get(API_KEY_HEADER, "").strip()
    if key:
        return key
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def resolve_identity(request: Request) -> tuple[Optional[User], Optional[str]]:
    """Who is making this request, and the id of the API key they used.

    The key id comes back so rate limiting can count a *key* rather than an IP:
    two keys behind one NAT are two callers, and one key from a fleet of workers
    is one caller. Neither is true of the address.
    """
    store = get_auth_store()

    key = _presented_api_key(request)
    if key:
        user = await store.user_for_api_key(key)
        if user is not None:
            return user, await store.api_key_id_for(key)
        # A key that was presented and rejected is never treated as anonymous:
        # falling through to the cookie would let a revoked key ride someone
        # else's browser session.
        return None, None

    token = request.cookies.get(get_settings().session_cookie, "")
    if token:
        return await store.user_for_session(token), None
    return None, None


def _unauthenticated_response(request: Request):
    """A 401 for machines, a redirect for browsers."""
    path = request.url.path
    if path.startswith("/v1/"):
        return JSONResponse(
            {"detail": "authentication required: send an X-API-Key header"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve the caller, publish their workspace, and gate when required."""

    async def dispatch(self, request: Request, call_next):
        s = get_settings()

        user: Optional[User] = None
        api_key_id: Optional[str] = None
        try:
            user, api_key_id = await resolve_identity(request)
        except Exception:  # noqa: BLE001
            # A store that cannot be read must not authenticate anyone. With
            # auth off this is harmless; with it on, the gate below closes.
            logger.warning("could not resolve identity", exc_info=True)

        request.state.user = user
        request.state.api_key_id = api_key_id

        if s.require_auth and user is None and not _is_public(request.url.path):
            return _unauthenticated_response(request)

        # The ContextVar is set here, before the request is handled, so every
        # store called downstream filters on this caller's workspace. Unset for
        # an anonymous request, which leaves `current_workspace_id` on the
        # configured value — the pre-auth behaviour, preserved exactly.
        token = set_current_workspace_id(user.workspace_id if user else None)
        try:
            return await call_next(request)
        finally:
            reset_current_workspace_id(token)


def current_user(request: Request) -> Optional[User]:
    """The authenticated user, if any. Set by :class:`AuthMiddleware`."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> User:
    """FastAPI dependency for a route that has no meaning without an account."""
    from fastapi import HTTPException

    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user
