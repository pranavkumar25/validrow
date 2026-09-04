"""The Validrow web app: server-rendered screens mounted on the engine's API.

There is no separate front-end process. The same uvicorn that serves ``/v1/*``
serves the UI, which means the app can never be out of step with the engine it
reports on — the class of bug the design's "engine unreachable" screen exists to
handle simply cannot arise for the local engine.

Every screen is a real URL: filters, sorting, paging and the wizard step all
live in the query string, so the back button works and any view can be linked.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from eve.addresses import DEFAULT_LIST_TYPE, LIST_TYPES, get_address_store
from eve.config import get_settings
from eve.web import views
from eve.web.views import APP_PREFIX

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

# The app hangs off /app; the landing page, auth screens and static files sit
# at the root, because those are the URLs a stranger arrives on.
router = APIRouter(prefix=APP_PREFIX, include_in_schema=False)
public = APIRouter(include_in_schema=False)

# How many recent single-checks to remember, and the cookie that holds them.
RECENT_COOKIE = "vr_recent"
RECENT_MAX = 5


def _initials(name: str) -> str:
    parts = [p for p in name.replace("-", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def workspace_identity(request: Request) -> dict[str, str]:
    """Who the sidebar says this workspace belongs to.

    Signed in, this is now a fact rather than a configured claim: the account's
    own name and address, and a menu that can sign it out.

    Signed out — which is still the default, since ``EVE_REQUIRE_AUTH`` is off —
    the original rule holds. Rendering a person there would be a claim the app
    cannot back up, so ``EVE_WORKSPACE_NAME`` / ``EVE_WORKSPACE_EMAIL`` are shown
    if set, and otherwise the block names the workspace and the engine serving
    it, both of which are true.
    """
    from eve.api.security import current_user

    user = current_user(request)
    if user is not None:
        name = user.name.strip() or user.email.split("@")[0]
        return {
            "name": name,
            "email": user.email,
            "initials": _initials(name),
            "signedIn": True,
        }

    s = get_settings()
    host = request.base_url.netloc or "local engine"
    name = s.workspace_name.strip() or "Local workspace"
    subtitle = s.workspace_email.strip() or host
    return {"name": name, "email": subtitle, "initials": _initials(name), "signedIn": False}


async def _render(
    request: Request, template: str, route: str, extra: dict, *, sb_q: str = ""
) -> HTMLResponse:
    ctx = await views.shell(route, sb_q=sb_q)
    ctx.update(extra)
    ctx["user"] = workspace_identity(request)
    ctx["engineUrl"] = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, template, ctx)


# --- Screens --------------------------------------------------------------- #
@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    tab: str = "All lists",
    q: str = "",
    grain: str = "Monthly",
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    if tab != "All lists" and tab not in LIST_TYPES:
        tab = "All lists"
    if grain not in ("Daily", "Weekly", "Monthly"):
        grain = "Monthly"
    data = await views.dashboard(tab=tab, q=q, grain=grain, page=page)
    return await _render(request, "dashboard.html", "dashboard", data, sb_q=q)


@router.get("/addresses", response_class=HTMLResponse)
async def addresses(
    request: Request,
    verdict: list[str] = Query(default_factory=list),
    q: str = "",
    sort: str = "recent",
    size: int = Query(50),
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    verdicts = [v for v in verdict if v in views.F.ORDER]
    if size not in (25, 50, 100):
        size = 50
    if sort not in ("recent", "scoreUp", "scoreDown", "az"):
        sort = "recent"
    data = await views.addresses(
        verdicts=verdicts, q=q, sort=sort, page=page, size=size
    )
    return await _render(request, "addresses.html", "addresses", data, sb_q=q)


@router.get("/addresses/detail", response_class=HTMLResponse)
async def address_detail(request: Request, email: str) -> HTMLResponse:
    """The expanded row, fetched on demand."""
    row = await get_address_store().get(email)
    if not row:
        raise HTTPException(status_code=404, detail="address not found")
    return templates.TemplateResponse(
        request,
        "_row_detail.html",
        {"row": views._address_row(row), "detail": views._detail(row)},
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request, grain: str = "Weekly") -> HTMLResponse:
    if grain not in ("Daily", "Weekly", "Monthly"):
        grain = "Weekly"
    return await _render(request, "analytics.html", "analytics", await views.analytics(grain=grain))


@router.get("/exports", response_class=HTMLResponse)
async def exports(
    request: Request,
    preset: str = "deliverable",
    columns: str = "full",
    custom: bool = False,
    v: list[str] = Query(default_factory=list),
    require_mx: bool = False,
    exclude_disposable: bool = False,
) -> HTMLResponse:
    data = await views.exports(
        preset=preset,
        columns=columns if columns in ("full", "email") else "full",
        custom_open=custom,
        custom_verdicts=[x for x in v if x in views.F.ORDER],
        require_mx=require_mx,
        exclude_disposable=exclude_disposable,
    )
    return await _render(request, "exports.html", "exports", data)


@router.get("/history", response_class=HTMLResponse)
async def history(request: Request) -> HTMLResponse:
    return await _render(request, "history.html", "history", await views.history())


@router.get("/history/{job_id}", response_class=HTMLResponse)
async def history_detail(request: Request, job_id: str) -> HTMLResponse:
    data = await views.history_detail(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return await _render(request, "hdetail.html", "hdetail", data)


@router.get("/settings", response_class=HTMLResponse)
async def settings_screen(request: Request) -> HTMLResponse:
    from eve.api.security import current_user
    from eve.auth import get_auth_store

    user = current_user(request)
    keys: list[dict] = []
    if user is not None:
        keys = [
            {
                "id": k["id"],
                "name": k["name"],
                "prefix": k["prefix"],
                "created": views._long_when(k["created_at"]),
                "used": views._when(k["last_used_at"]) if k["last_used_at"] else "Never used",
            }
            for k in await get_auth_store().list_api_keys(user.id)
        ]

    # Shown once, from the one-shot cookie the create route set, then cleared:
    # this is the only moment the plaintext exists anywhere we control.
    new_key = request.cookies.get("vr_new_key", "")
    resp = await _render(
        request,
        "settings.html",
        "settings",
        {
            "version": views.VERSION,
            "keys": keys,
            "newKey": new_key,
            "signedIn": user is not None,
            "authOn": get_settings().require_auth,
        },
    )
    if new_key:
        resp.delete_cookie("vr_new_key", path=APP_PREFIX + "/settings")
    return resp


@router.get("/how", response_class=HTMLResponse)
async def how(request: Request) -> HTMLResponse:
    return await _render(request, "how.html", "how", views.how_it_works())


# --- Single check ---------------------------------------------------------- #
def _read_recent(request: Request) -> list[dict]:
    import json

    try:
        return json.loads(request.cookies.get(RECENT_COOKIE) or "[]")[:RECENT_MAX]
    except (ValueError, TypeError):
        return []


@router.get("/single", response_class=HTMLResponse)
async def single(request: Request, email: str = "") -> HTMLResponse:
    import json

    recent = _read_recent(request)
    data = await views.single_check(email.strip() or None, recent)
    resp = await _render(request, "single.html", "single", data)

    if data.get("hasSc"):
        res = data["scRes"]
        entry = {
            "email": res["email"],
            "vlabel": res["vlabel"],
            "dot": res["dot"],
            "score": res["score"],
        }
        updated = [entry] + [r for r in recent if r.get("email") != entry["email"]]
        resp.set_cookie(
            RECENT_COOKIE,
            json.dumps(updated[:RECENT_MAX]),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="lax",
        )
    return resp


# --- Validate wizard ------------------------------------------------------- #
@router.get("/validate", response_class=HTMLResponse)
async def validate(
    request: Request,
    file: Optional[str] = None,
    name: Optional[str] = None,
    job: Optional[str] = None,
    step: int = 1,
) -> HTMLResponse:
    data = await views.validate_screen(
        file_id=file, filename=name, job_id=job, step=step
    )
    return await _render(request, "validate.html", "validate", data)


@router.post("/validate/upload")
async def validate_upload(file: UploadFile) -> RedirectResponse:
    """Store the upload, then hand off to the preview step."""
    from urllib.parse import quote

    from eve.storage import get_object_store

    store = get_object_store()
    key = store.new_key("_" + (file.filename or "upload.csv"))
    await store.put(key, file.file)
    name = file.filename or "upload.csv"
    return RedirectResponse(
        f"/validate?file={key}&name={quote(name)}&step=2", status_code=303
    )


@router.post("/validate/start")
async def validate_start(
    file_id: str = Form(...),
    filename: str = Form(""),
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    list_type: str = Form(DEFAULT_LIST_TYPE),
) -> RedirectResponse:
    import asyncio

    from eve.addresses import get_address_store as _addr
    from eve.jobs.models import ColumnMapping, Job
    from eve.jobs.pipeline import run_job
    from eve.jobs.store import get_job_store
    from eve.smtp_infra import get_async_prober
    from eve.storage import get_object_store

    job_store = get_job_store()
    job = Job(
        file_key=file_id,
        filename=filename or file_id,
        list_type=list_type if list_type in LIST_TYPES else DEFAULT_LIST_TYPE,
        mapping=ColumnMapping(
            email=email, first_name=first_name or None, last_name=last_name or None
        ),
    )
    await job_store.create(job)

    task = asyncio.create_task(
        run_job(
            job,
            store=get_object_store(),
            job_store=job_store,
            prober=get_async_prober(),
            addresses=_addr(),
        )
    )
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return RedirectResponse(f"{APP_PREFIX}/validate?job={job.id}", status_code=303)


_BACKGROUND: set = set()


# --- Actions --------------------------------------------------------------- #
@router.post("/exports/download")
async def exports_download(
    preset: str = Form("all"),
    columns: str = Form("full"),
    verdicts: list[str] = Form(default_factory=list),
    emails: list[str] = Form(default_factory=list),
    require_mx: str = Form(""),
    exclude_disposable: str = Form(""),
) -> Response:
    from eve.api.schemas import ExportRequest
    from eve.api.workspace import export_slice

    if preset in ("all", "custom"):
        chosen, named = verdicts, None
    elif preset in ("catchall", "disposable"):
        chosen, named = [], preset
    else:
        chosen, named = [preset], None

    return await export_slice(
        ExportRequest(
            verdicts=chosen,
            preset=named,
            emails=emails,
            columns=columns,
            require_mx=bool(require_mx),
            exclude_disposable=bool(exclude_disposable),
        )
    )


@router.post("/history/{job_id}/delete")
async def delete_job_action(job_id: str, confirm: str = Form("")) -> RedirectResponse:
    """Delete a run. The typed filename must match — the client gates the
    button, and this re-checks it, because a client-side guard is not a guard."""
    from eve.api.jobs import delete_job
    from eve.jobs.store import get_job_store

    job = await get_job_store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if confirm.strip() != job.filename:
        return RedirectResponse(f"{APP_PREFIX}/history/{job_id}", status_code=303)
    await delete_job(job_id, keep_addresses=True)
    return RedirectResponse(APP_PREFIX + "/history", status_code=303)


# --- Landing --------------------------------------------------------------- #
@public.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> Response:
    """The public front page.

    Signed-in visitors go straight to the app: showing the pitch to someone who
    already bought it wastes their click.
    """
    from eve.api.security import current_user
    from eve.web.landing import context

    if current_user(request) is not None:
        return RedirectResponse(APP_PREFIX + "/", status_code=303)

    ctx = await context(str(request.base_url).rstrip("/"))
    return templates.TemplateResponse(request, "landing.html", ctx)


# --- API reference --------------------------------------------------------- #
@public.get("/docs", response_class=HTMLResponse)
async def api_docs(request: Request) -> Response:
    """The API reference, rendered from the engine's own OpenAPI document.

    Public, and deliberately so: a 401 on the docs is how you make an API look
    broken to someone deciding whether to use it.
    """
    from eve.web.apidocs import context

    ctx = context(request.app.openapi(), str(request.base_url).rstrip("/"))
    return templates.TemplateResponse(request, "docs.html", ctx)


# --- Sign in / sign up ----------------------------------------------------- #
def _safe_next(raw: str) -> str:
    """Where to go after signing in.

    Only a path on this site. An open redirect on a login form is how a
    phishing page borrows your domain for its credibility, and ``//host`` is a
    protocol-relative URL, so checking for a leading ``/`` alone is not enough.
    """
    nxt = (raw or "").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        return "/"
    return nxt


def _auth_page(
    request: Request,
    mode: str,
    *,
    error: str = "",
    email: str = "",
    name: str = "",
    next_url: str = "/",
    status_code: int = 200,
) -> HTMLResponse:
    from eve.auth import MIN_PASSWORD_LENGTH

    s = get_settings()
    signup = mode == "signup"
    ctx = {
        "mode": mode,
        "heading": "Create your account" if signup else "Sign in",
        "subheading": (
            "One account, one workspace. Everything you validate stays inside it."
            if signup
            else "Validrow verifies your lists in-house — sign in to pick up where you left off."
        ),
        "action": "/signup" if signup else "/login",
        "submit": "Create account" if signup else "Sign in",
        "error": error,
        "notice": "",
        "email": email,
        "name": name,
        "next": next_url,
        "minPassword": MIN_PASSWORD_LENGTH,
        "altText": "",
        "altHref": "",
        "altLabel": "",
    }
    if signup:
        ctx.update(altText="Already have an account?", altHref="/login", altLabel="Sign in")
    elif s.open_signup:
        ctx.update(altText="No account?", altHref="/signup", altLabel="Create one")
    return templates.TemplateResponse(request, "auth.html", ctx, status_code=status_code)


def _sign_in(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie,
        token,
        max_age=int(s.session_ttl_seconds),
        httponly=True,          # never readable from JavaScript
        samesite="lax",         # survives a normal navigation, not a cross-site POST
        secure=s.session_cookie_secure,
        path="/",
    )


@public.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = APP_PREFIX + "/") -> Response:
    from eve.api.security import current_user
    from eve.auth import get_auth_store

    if current_user(request) is not None:
        return RedirectResponse(_safe_next(next), status_code=303)

    # A fresh install has nobody to sign in as. Send the first visitor to the
    # form that can actually help them rather than one that never will.
    if await get_auth_store().count_users() == 0:
        return RedirectResponse("/signup", status_code=303)
    return _auth_page(request, "login", next_url=_safe_next(next))


@public.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(APP_PREFIX + "/"),
) -> Response:
    from eve.auth import get_auth_store

    store = get_auth_store()
    user = await store.authenticate(email, password)
    if user is None:
        # One message for a wrong address and a wrong password alike: which of
        # the two it was is exactly what someone enumerating accounts wants.
        return _auth_page(
            request,
            "login",
            error="That email and password do not match an account.",
            email=email,
            next_url=_safe_next(next),
            status_code=401,
        )

    token = await store.start_session(user.id)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    _sign_in(resp, token)
    return resp


@public.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request, next: str = APP_PREFIX + "/") -> Response:
    from eve.api.security import current_user
    from eve.auth import get_auth_store

    if current_user(request) is not None:
        return RedirectResponse(_safe_next(next), status_code=303)

    s = get_settings()
    first = await get_auth_store().count_users() == 0
    if not (s.open_signup or first):
        return _auth_page(
            request,
            "login",
            error="This engine is not open for registration. Ask its owner for an account.",
            next_url=_safe_next(next),
            status_code=403,
        )

    page = _auth_page(request, "signup", next_url=_safe_next(next))
    return page


@public.post("/signup")
async def signup_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    name: str = Form(""),
    next: str = Form(APP_PREFIX + "/"),
) -> Response:
    from eve.auth import get_auth_store, password_problem

    s = get_settings()
    store = get_auth_store()
    first = await store.count_users() == 0
    if not (s.open_signup or first):
        return _auth_page(
            request,
            "login",
            error="This engine is not open for registration. Ask its owner for an account.",
            next_url=_safe_next(next),
            status_code=403,
        )

    problem = password_problem(password)
    if problem:
        return _auth_page(
            request, "signup", error=problem, email=email, name=name,
            next_url=_safe_next(next), status_code=400,
        )

    try:
        user = await store.create_user(email, password, name=name)
    except ValueError:
        return _auth_page(
            request,
            "signup",
            error="An account with that email already exists.",
            email=email,
            name=name,
            next_url=_safe_next(next),
            status_code=409,
        )

    token = await store.start_session(user.id)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    _sign_in(resp, token)
    return resp


@public.post("/logout")
@public.get("/logout")
async def logout(request: Request) -> Response:
    """Ends the session server-side, not just in the browser.

    Clearing the cookie alone would leave a working token behind for anyone who
    had already copied it.
    """
    from eve.auth import get_auth_store

    s = get_settings()
    token = request.cookies.get(s.session_cookie, "")
    if token:
        await get_auth_store().end_session(token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(s.session_cookie, path="/")
    return resp


# --- API keys -------------------------------------------------------------- #
@router.post("/settings/keys")
async def create_key(request: Request, name: str = Form("")) -> Response:
    from eve.api.security import require_user
    from eve.auth import get_auth_store

    user = require_user(request)
    key, row = await get_auth_store().create_api_key(user.id, name=name or "API key")
    # The plaintext exists only in this response. It is passed back through a
    # one-shot cookie rather than the URL, which would put a live credential in
    # the browser history and in any proxy log along the way.
    resp = RedirectResponse(APP_PREFIX + "/settings", status_code=303)
    resp.set_cookie(
        "vr_new_key", key, max_age=120, httponly=False,
        samesite="lax", path=APP_PREFIX + "/settings",
    )
    return resp


@router.post("/settings/keys/{key_id}/revoke")
async def revoke_key(request: Request, key_id: str) -> Response:
    from eve.api.security import require_user
    from eve.auth import get_auth_store

    user = require_user(request)
    await get_auth_store().revoke_api_key(key_id, user_id=user.id)
    return RedirectResponse(APP_PREFIX + "/settings", status_code=303)


def mount_web(app: FastAPI) -> None:
    """Attach the web app to an existing FastAPI instance."""
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(public)
    app.include_router(router)
    # Expose a couple of helpers to templates. APP is the prefix every in-app
    # link is built from, so the app can move without touching markup.
    templates.env.globals.update(settings=get_settings(), APP=APP_PREFIX)
