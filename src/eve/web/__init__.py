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

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter(include_in_schema=False)

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

    There is no auth, so a name and address rendered here would be a claim the
    app cannot back up. Configure ``EVE_WORKSPACE_NAME`` / ``EVE_WORKSPACE_EMAIL``
    and they are shown as given; leave them unset — the default — and the block
    names the workspace and the engine serving it, both of which are true.
    """
    s = get_settings()
    host = request.base_url.netloc or "local engine"
    name = s.workspace_name.strip() or "Local workspace"
    subtitle = s.workspace_email.strip() or host
    return {"name": name, "email": subtitle, "initials": _initials(name)}


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
    # engineUrl is supplied by _render for every screen.
    return await _render(request, "settings.html", "settings", {"version": views.VERSION})


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
    return RedirectResponse(f"/validate?job={job.id}", status_code=303)


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
        return RedirectResponse(f"/history/{job_id}", status_code=303)
    await delete_job(job_id, keep_addresses=True)
    return RedirectResponse("/history", status_code=303)


def mount_web(app: FastAPI) -> None:
    """Attach the web app to an existing FastAPI instance."""
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(router)
    # Expose a couple of helpers to templates.
    templates.env.globals.update(settings=get_settings())
