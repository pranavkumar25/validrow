"""The view-model: real workspace data shaped into what each screen renders.

This is the Python port of the prototype's ``renderVals()``. Binding names are
kept identical to the design file so the templates read as a direct translation
of the markup.

The one structural difference from the prototype is that its data was invented
and always present. Here every number is a query result, which means empty and
partial states are load-bearing rather than decorative: a workspace with no runs
shows the designed empty states, and a workspace with one week of history hides
the period-over-period deltas instead of inventing a comparison.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from eve.addresses import LIST_TYPES, get_address_store
from eve.config import get_settings
from eve.jobs.models import Job, JobStatus, Phase
from eve.jobs.store import get_job_store
from eve.trace import subtitle_for, trace_from_row
from eve.verdict import SUB_REASON_LABEL, SubStatus, primary_verdict
from eve.web import format as F
from eve.web import series as S

VERSION = "0.1.0"

# --- Navigation ------------------------------------------------------------ #
NAV_ICONS = {
    "home": "M4 10.5L12 4l8 6.5V19a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 19z",
    "dashboard": "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    "validate": "M12 15V3M8 7l4-4 4 4M3 15v4a2 2 0 002 2h14a2 2 0 002-2v-4",
    "single": "M12 21a9 9 0 110-18 9 9 0 019 9v1.5a2.5 2.5 0 01-5 0V12a4 4 0 10-2.4 3.66",
    "addresses": "M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01",
    "analytics": "M3 21V10M9 21V3M15 21v-7M21 21V7",
    "exports": "M12 3v12M8 11l4 4 4-4M4 21h16",
    "history": "M3.5 12a8.5 8.5 0 108.5-8.5A8.5 8.5 0 005.2 6.6M12 7.5V12l3.2 2M3.5 4v3h3",
    "settings": (
        "M4 7h4M13 7h7M4 13h9M18 13h2M4 19h5M14 19h6M10.5 7a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0"
        "M15.5 13a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0M11 19a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0"
    ),
    "how": "M12 21a9 9 0 110-18 9 9 0 010 18M12 16.5v.01M12 14c0-1.8 2-2.1 2-3.9A2 2 0 0010 9.6",
}

ROUTE_PATHS = {
    "home": "/",
    "dashboard": "/dashboard",
    "validate": "/validate",
    "single": "/single",
    "addresses": "/addresses",
    "analytics": "/analytics",
    "exports": "/exports",
    "history": "/history",
    "settings": "/settings",
    "how": "/how",
}


def _nav(items, route: str) -> list[dict[str, Any]]:
    out = []
    for key, label, count in items:
        on = route == key or (key == "dashboard" and route == "home")
        out.append(
            {
                "label": label,
                "icon": NAV_ICONS[key],
                "href": ROUTE_PATHS[key],
                "count": count or "",
                "bg": F.BLUE_WASH if on else "transparent",
                "fg": F.BLUE_DARK if on else F.INK_2,
                "ic": F.BLUE_DARK if on else F.MUTED,
                "w": "500" if on else "400",
            }
        )
    return out


# --- Shared shell ---------------------------------------------------------- #
async def shell(route: str, *, sb_q: str = "") -> dict[str, Any]:
    """Sidebar, engine status and the banner — present on every screen."""
    settings = get_settings()
    jobs = await get_job_store().list()
    active = [j for j in jobs if j.status in (JobStatus.PENDING, JobStatus.PROCESSING)]

    smtp_on = settings.enable_smtp
    dns_on = settings.enable_dns
    # The design carries three engine states. Only two can occur here: the UI is
    # served by the engine, so "unreachable" would mean this page never rendered.
    #
    # DNS off is a real degradation — without an MX lookup nothing about a domain
    # can be proven, so layers 4-7 all fall away. The SMTP probe being off is the
    # documented default and produces honest Unknowns rather than wrong answers,
    # so it warrants the banner but not an amber engine.
    engine_status = "online" if dns_on else "degraded"

    ctx: dict[str, Any] = {
        "route": route,
        "navWork": _nav(
            [
                ("home", "Home", None),
                ("dashboard", "Dashboard", None),
                ("validate", "Validate list", str(len(active)) if active else None),
                ("single", "Single check", None),
            ],
            route,
        ),
        "navSpace": _nav(
            [
                ("addresses", "Contacts", None),
                ("analytics", "Analytics", None),
                ("exports", "Exports", None),
                ("history", "History", None),
            ],
            route,
        ),
        "navSystem": _nav([("how", "Support", None), ("settings", "Settings", None)], route),
        "sbQ": sb_q,
        "engineDot": {"online": F.GREEN, "degraded": F.AMBER}.get(engine_status, F.RED),
        "engineVer": f"Engine v{VERSION}",
        "engineFlags": f"DNS {'✓' if settings.enable_dns else '✗'} SMTP {'✓' if smtp_on else '✗'}",
        "engLabel": {"online": "Connected", "degraded": "Degraded"}.get(engine_status, "Unreachable"),
        "smtpOn": smtp_on,
        "smtpDot": F.GREEN if smtp_on else F.MUTED,
        "smtpLabel": "Enabled" if smtp_on else "Disabled",
        "dnsOn": settings.enable_dns,
        # Route flags the templates switch on.
        "isDash": route in ("dashboard", "home"),
        "isVal": route == "validate",
        "isSingle": route == "single",
        "isAddr": route == "addresses",
        "isAnalytics": route == "analytics",
        "isExports": route == "exports",
        "isHistory": route == "history",
        "isHDetail": route == "hdetail",
        "isSettings": route == "settings",
        "isHow": route == "how",
    }

    # The banner reports the most serious condition, not every one of them.
    if not dns_on:
        banner = {
            "title": "DNS resolution is off",
            "body": "Without an MX lookup no domain can be proven to accept mail, so the "
            "last four layers never run. Verdicts stop at classification.",
            "cta": "Engine settings",
            "bg": "#FFFAEB",
            "bd": "#FEDF89",
            "dot": F.AMBER,
        }
    elif not smtp_on:
        banner = {
            "title": "SMTP mailbox probe is off",
            "body": "Addresses that would be confirmable come back as Unknown, never as a "
            "fake Valid. Turn the probe on to settle them.",
            "cta": "Engine settings",
            "bg": "#FFFAEB",
            "bd": "#FEDF89",
            "dot": F.AMBER,
        }
    else:
        banner = None

    ctx.update(
        {
            "bannerOpen": banner is not None,
            "bannerBg": banner["bg"] if banner else "",
            "bannerBd": banner["bd"] if banner else "",
            "bannerDot": banner["dot"] if banner else "",
            "bannerTitle": banner["title"] if banner else "",
            "bannerBody": banner["body"] if banner else "",
            "bannerCta": banner["cta"] if banner else "",
        }
    )
    return ctx


# --- Helpers --------------------------------------------------------------- #
def _verdict_of(row: dict) -> dict[str, Any]:
    v = primary_verdict(row["status"])
    style = F.VERDICT_STYLE[v]
    return {
        "verdict": v,
        "vlabel": style["label"],
        "short": F.SHORT_LABEL[v],
        "dot": style["dot"],
        "wash": style["wash"],
        "ink": style["ink"],
    }


def _sub_label(row: dict) -> str:
    return SUB_REASON_LABEL.get(row.get("sub_status") or "", None) or ""


def _when(ts: Optional[float]) -> str:
    """``Jul 24 · 09:14`` — the timestamp format used across the tables."""
    if not ts:
        return "—"
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{d.strftime('%b')} {d.day} · {d.strftime('%H:%M')}"


def _day_only(ts: Optional[float]) -> str:
    if not ts:
        return "—"
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{d.strftime('%b')} {d.day}"


def _long_when(ts: Optional[float]) -> str:
    if not ts:
        return "—"
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{d.strftime('%b')} {d.day}, {d.year} · {d.strftime('%H:%M')}"


def _address_row(row: dict) -> dict[str, Any]:
    """One Contacts row, with everything the expanded detail needs."""
    v = _verdict_of(row)
    score = int(row.get("score") or 0)
    return {
        **row,
        **v,
        "sub": _sub_label(row),
        "score": score,
        "scoreW": f"{score}%",
        "scorePct": f"{score}%",
        "job": row.get("job_filename") or "—",
        "date": _when(row.get("checked_at")),
        "checked": _day_only(row.get("checked_at")),
        "segment": row.get("list_type") or "Imports",
        "segDot": F.BLUE if (row.get("list_type") or "") != "Imports" else F.MUTED_2,
        "full": row["email"],
    }


def _detail(row: dict) -> dict[str, Any]:
    v = _verdict_of(row)
    return {
        "score": int(row.get("score") or 0),
        "ringColor": v["dot"],
        "ringDash": F.ring_dash(row.get("score")),
        "subtitle": subtitle_for(
            row["status"], row.get("sub_status") or "", row.get("domain")
        ),
        "trace": [t.as_dict() for t in trace_from_row(row)],
    }


def _job_row(job: Job) -> dict[str, Any]:
    """One History row."""
    c = job.counts.as_dict()
    totals = _counts_to_verdicts(c)
    addr = c.get("unique_emails") or 0
    state = {
        JobStatus.COMPLETED: ("Completed", "#ECFDF3", "#027A48"),
        JobStatus.PROCESSING: ("Running", F.BLUE_WASH, F.BLUE_DARK),
        JobStatus.PENDING: ("Queued", F.SURFACE, F.MUTED),
        JobStatus.FAILED: ("Failed", "#FEF3F2", "#B42318"),
    }[job.status]
    running = job.status in (JobStatus.PROCESSING, JobStatus.PENDING)
    return {
        "job_id": job.id,
        "id": f"#{job.seq}",
        "file": job.filename,
        "rows": f"{F.fmt(c.get('total_rows') or 0)} rows",
        "addr": F.fmt(addr),
        "state": state[0],
        "tagBg": state[1],
        "tagFg": state[2],
        "date": _long_when(job.created_at),
        "dur": F.duration(job.duration) if job.status is JobStatus.COMPLETED else "—",
        "dr": "—" if (job.status is JobStatus.FAILED or not addr)
        else f"{F.jsround(totals['deliverable'] / addr * 100)}%",
        "running": running,
        "prog": F.fixed(job.progress * 100) + "%",
        "mix": F.mix(totals, only_present=False),
        "href": f"/validate?job={job.id}" if running else f"/history/{job.id}",
    }


def _counts_to_verdicts(counts: dict[str, int]) -> dict[str, int]:
    """Fold the engine's six statuses into the four displayed verdicts."""
    out = {k: 0 for k in F.ORDER}
    for status in ("valid", "risky", "unknown", "invalid", "disposable", "spam_trap"):
        out[primary_verdict(status)] += counts.get(status, 0)
    return out


# --- Dashboard ------------------------------------------------------------- #
async def dashboard(
    *, tab: str = "All lists", q: str = "", grain: str = "Monthly", page: int = 1
) -> dict[str, Any]:
    store = get_address_store()
    totals = await store.totals(list_type=tab)
    ws_totals = await store.totals()
    headline = await store.headline()
    by_day = await store.by_day()

    ws_total = sum(ws_totals.values())
    tab_total = sum(totals.values())

    # --- volume chart: this period vs the one before it -------------------
    this_buckets = S.build(by_day, grain)
    prev_buckets = S.build(by_day, grain, offset_periods=1)
    this_vals = [b.total for b in this_buckets]
    prev_vals = [b.total for b in prev_buckets]
    lo = min(this_vals + prev_vals + [0]) * 0.9
    hi = (max(this_vals + prev_vals) or 1) * 1.08

    vol_delta = S.delta(sum(this_vals), sum(prev_vals))
    rate_series = S.deliverable_rate(this_buckets)
    prev_rate = S.deliverable_rate(prev_buckets)
    rate_delta = S.delta(rate_series[-1] if rate_series else 0, prev_rate[-1] if prev_rate else 0)
    undeliv_this = sum(b.totals["undeliverable"] for b in this_buckets)
    undeliv_prev = sum(b.totals["undeliverable"] for b in prev_buckets)
    undeliv_delta = S.delta(undeliv_this, undeliv_prev)
    if undeliv_delta:  # fewer bounces is the good direction
        undeliv_delta["good"] = not undeliv_delta["up"]

    no_history = ws_total == 0 or not any(prev_vals)
    # A single populated bucket draws a near-vertical spike, which reads as a
    # broken chart rather than as "one day of data". Below two readings the
    # chart says so instead of drawing a shape that misleads.
    populated_buckets = sum(1 for v in this_vals if v)
    sparse = populated_buckets < 2

    # --- recent results table --------------------------------------------
    PAGE = 6
    start = (page - 1) * PAGE
    rows, matched = await store.query(
        list_type=tab, search=q or None, sort="recent", limit=PAGE, offset=start
    )
    fig_rows = []
    for r in rows:
        row = _address_row(r)
        row["email"] = F.mid_truncate(r["email"], 30)
        row["status"] = row["short"]
        row["aria"] = f"{r['email']} — {row['short']}, score {row['score']}"
        fig_rows.append(row)

    tabs = []
    for label in ["All lists"] + LIST_TYPES:
        active = tab == label
        tabs.append(
            {
                "label": label,
                "href": f"/dashboard?tab={label.replace(' ', '+')}",
                "bg": F.BLUE_WASH if active else "transparent",
                "fg": F.BLUE_DARK if active else F.INK_3,
                "w": "600" if active else "500",
                "ring": f"inset 0 -2px 0 0 {F.BLUE}" if active else "none",
                "on2": "true" if active else "false",
            }
        )

    jobs = await get_job_store().list()
    running_jobs = [j for j in jobs if j.status is JobStatus.PROCESSING]
    running = [
        {
            "file": j.filename,
            "pct": F.fixed(j.progress * 100) + "%",
            "phase": _phase_text(j),
            "elapsed": F.duration(j.duration),
            "href": f"/validate?job={j.id}",
        }
        for j in running_jobs
    ]

    stats = [
        {
            "label": "Total validated",
            "value": F.fmt(ws_total),
            "num": "32px",
            "delta": vol_delta,
            "series": this_vals,
        },
        {
            "label": "Deliverable rate",
            "value": F.pct(totals["deliverable"], tab_total) if tab_total else "—",
            "num": "24px",
            "delta": rate_delta,
            "series": rate_series,
        },
        {
            "label": "Undeliverable",
            "value": F.fmt(ws_totals["undeliverable"]),
            "num": "24px",
            "delta": undeliv_delta,
            "series": [b.totals["undeliverable"] for b in this_buckets],
        },
        {
            "label": "Avg. quality score",
            "value": str(headline["avg_score"]) if headline["avg_score"] is not None else "—",
            "num": "24px",
            "delta": None,
            "mix": True,
        },
    ]
    for s in stats:
        s["hasDelta"] = bool(s.get("delta")) and not no_history
        s["hasMix"] = bool(s.get("mix"))
        # Same rule as the volume chart: a sparkline through one reading is a
        # spike, not a trend.
        s["hasSpark"] = not s.get("mix") and not sparse
        s["line"] = F.smooth(s["series"], 309, 56) if s.get("series") else ""
        if s.get("delta"):
            s["deltaLabel"] = s["delta"]["pct"]
            s["deltaColor"] = F.GREEN_INK if s["delta"]["good"] else "#B42318"
            s["deltaUp"] = s["delta"]["up"]

    # Hover readout for every point, handed to the client as data.
    points = [
        {
            "label": b.label,
            "total": F.fmt(b.total),
            "rows": [
                {
                    "dot": F.VERDICT_STYLE[k]["dot"],
                    "label": F.SHORT_LABEL[k],
                    "value": F.fmt(b.totals[k]),
                }
                for k in F.ORDER
            ],
            "y": _chart_y(b.total, lo, hi, 248),
        }
        for b in this_buckets
    ]

    return {
        "tabs": tabs,
        "tab": tab,
        "aQ": q,
        "figRows": fig_rows,
        "figRowCount": f"{F.fmt(matched)} rows",
        "figShowing": (
            f"Showing {F.fmt(start + 1)}–{F.fmt(min(start + PAGE, matched))} of {F.fmt(matched)}"
            if matched
            else "No rows"
        ),
        "figPrevHref": _dash_href(tab, q, grain, page - 1) if page > 1 else None,
        "figNextHref": _dash_href(tab, q, grain, page + 1) if start + PAGE < matched else None,
        "figStats": stats,
        "donutFig": F.donut(ws_totals, short_labels=True),
        "donutTotal": F.fmt(ws_total),
        "heroMix": F.mix(ws_totals),
        "volValue": F.fmt(sum(this_vals)),
        "volDelta": vol_delta,
        "chartThis": F.curve(this_vals, 1048, 248, lo, hi),
        "chartArea": F.curve(this_vals, 1048, 248, lo, hi, area=True),
        "chartPrev": F.curve(prev_vals, 1048, 248, lo, hi),
        "chartPoints": points,
        "months": [{"l": b.label} for b in this_buckets],
        "volGrains": [
            {
                "label": g,
                "href": f"/dashboard?tab={tab.replace(' ', '+')}&grain={g}",
                "bg": F.WHITE if grain == g else "transparent",
                "fg": F.INK if grain == g else F.MUTED,
                "sh": "0 1px 2px rgba(16,24,40,0.06)" if grain == g else "none",
            }
            for g in S.GRAINS
        ],
        "showRunning": bool(running),
        "running": running,
        "noHistory": no_history,
        "volSparse": sparse,
        "dashEmpty": ws_total == 0,
        "dateRange": _range_label(this_buckets),
    }


def _dash_href(tab: str, q: str, grain: str, page: int) -> str:
    from urllib.parse import urlencode

    params = {"tab": tab, "grain": grain}
    if q:
        params["q"] = q
    if page > 1:
        params["page"] = page
    return "/dashboard?" + urlencode(params)


def _chart_y(value: float, lo: float, hi: float, h: float) -> float:
    span = (hi - lo) or 1
    return round(h - (value - lo) / span * h, 1)


def _range_label(buckets: list[S.Bucket]) -> str:
    """``Aug 1, 2025 – Jul 31, 2026`` — the year is only dropped when the whole
    range sits inside one year, otherwise the label reads as a 12-month span
    that starts and ends in the same year."""
    if not buckets:
        return "—"
    a, b = buckets[0].start, buckets[-1].end
    left = f"{a.strftime('%b')} {a.day}" + ("" if a.year == b.year else f", {a.year}")
    return f"{left} – {b.strftime('%b')} {b.day}, {b.year}"


def _phase_text(job: Job) -> str:
    """What the engine is doing right now, named rather than numbered."""
    if job.phase is Phase.READING:
        return "Reading and de-duplicating rows"
    if job.phase is Phase.RESOLVING:
        return f"Resolving {F.fmt(job.domains_total)} domains"
    if job.phase is Phase.VERIFYING:
        return f"Verifying {F.fmt(job.processed)} of {F.fmt(job.total)}"
    if job.phase is Phase.ASSEMBLING:
        return "Finalizing"
    if job.phase is Phase.DONE:
        return "Complete"
    return "Queued"


# --- Contacts / Addresses -------------------------------------------------- #
async def addresses(
    *,
    verdicts: list[str],
    q: str = "",
    sort: str = "recent",
    page: int = 1,
    size: int = 50,
    open_email: Optional[str] = None,
) -> dict[str, Any]:
    store = get_address_store()
    rows, total = await store.query(
        verdicts=verdicts or None,
        search=q or None,
        sort=sort,
        limit=size,
        offset=(page - 1) * size,
    )
    totals = await store.totals()
    headline = await store.headline()
    workspace_empty = await store.is_empty()

    a_rows = []
    for r in rows:
        row = _address_row(r)
        row["open"] = open_email == r["email"]
        row["detail"] = _detail(r) if row["open"] else None
        a_rows.append(row)

    start = (page - 1) * size
    chips = []
    for k in F.ORDER:
        on = k in verdicts
        style = F.VERDICT_STYLE[k]
        chips.append(
            {
                "label": style["label"],
                "dot": style["dot"],
                "bg": F.BLUE_WASH if on else F.WHITE,
                "bd": F.BLUE if on else F.LINE_2,
                "fg": F.BLUE_DARK if on else F.INK_3,
                "key": k,
                "on": on,
            }
        )

    sort_labels = {"recent": "Recent", "scoreUp": "Score ↑", "scoreDown": "Score ↓", "az": "A–Z"}

    def href(**over) -> str:
        from urllib.parse import urlencode

        base = {"verdict": verdicts, "q": q, "sort": sort, "size": size, "page": page}
        base.update(over)
        params: list[tuple[str, Any]] = []
        for v in base["verdict"]:
            params.append(("verdict", v))
        if base["q"]:
            params.append(("q", base["q"]))
        if base["sort"] != "recent":
            params.append(("sort", base["sort"]))
        if base["size"] != 50:
            params.append(("size", base["size"]))
        if base["page"] > 1:
            params.append(("page", base["page"]))
        return "/addresses" + ("?" + urlencode(params) if params else "")

    return {
        "aStats": [
            {"eyebrow": "Total addresses", "value": F.fmt(headline["total"])},
            {"eyebrow": "Deliverable", "value": F.fmt(totals["deliverable"])},
            {"eyebrow": "Unknown", "value": F.fmt(totals["unknown"])},
            {"eyebrow": "Unique domains", "value": F.fmt(headline["domains"])},
        ],
        "chipsAll": chips,
        "aRows": a_rows,
        "aQ": q,
        "aSort": sort,
        "aSize": size,
        "aPage": page,
        "aShowing": (
            f"Showing {F.fmt(start + 1)}–{F.fmt(min(start + size, total))} of {F.fmt(total)}"
            if total
            else "No rows"
        ),
        "aPageLabel": f"Page {page}",
        "sortLabel": sort_labels.get(sort, "Recent"),
        "sortOpts": [
            {"key": k, "label": v, "fg": F.BLUE if sort == k else F.INK_3}
            for k, v in sort_labels.items()
        ],
        "pageSizes": [
            {
                "label": str(n),
                "size": n,
                "bg": F.WHITE if size == n else "transparent",
                "fg": F.INK if size == n else F.MUTED,
            }
            for n in (25, 50, 100)
        ],
        "aEmptyNothing": workspace_empty,
        "aEmptyFilters": (not workspace_empty) and total == 0,
        "aHasRows": len(a_rows) > 0,
        "hasPrev": page > 1,
        "hasNext": start + size < total,
        # Toggling a chip resets to page 1 — otherwise a narrowed filter can
        # land you on a page that no longer exists.
        "chipHrefs": {
            k: href(
                verdict=[v for v in verdicts if v != k] if k in verdicts else verdicts + [k],
                page=1,
            )
            for k in F.ORDER
        },
        "sortHrefs": {k: href(sort=k, page=1) for k in sort_labels},
        "sizeHrefs": {n: href(size=n, page=1) for n in (25, 50, 100)},
        "prevHref": href(page=page - 1),
        "nextHref": href(page=page + 1),
    }


# --- Analytics ------------------------------------------------------------- #
def _grain_noun(grain: str) -> str:
    return {"Weekly": "weeks", "Monthly": "months"}.get(grain, "days")


async def analytics(*, grain: str = "Weekly") -> dict[str, Any]:
    store = get_address_store()
    totals = await store.totals()
    headline = await store.headline()
    by_day = await store.by_day()
    tops = await store.top_domains()

    total = sum(totals.values())
    buckets = S.build(by_day, grain)
    rates = S.deliverable_rate(buckets)
    wk = S.weekday_volume(by_day)
    heights = F.bar_heights([b["v"] for b in wk], 132)

    return {
        "anKpis": [
            {"eyebrow": "Addresses validated", "value": F.fmt(headline["total"])},
            {"eyebrow": "Deliverable rate", "value": F.pct(totals["deliverable"], total)},
            {"eyebrow": "Unique domains", "value": F.fmt(headline["domains"])},
            {
                "eyebrow": "Average score",
                "value": str(headline["avg_score"]) if headline["avg_score"] is not None else "—",
            },
        ],
        "ratePath": F.autoscale_polyline(rates, 720, 160),
        "rateGrains": [
            {
                "label": g,
                "href": f"/analytics?grain={g}",
                "bg": F.WHITE if grain == g else "transparent",
                "fg": F.INK if grain == g else F.MUTED,
                "sh": "0 1px 2px rgba(16,24,40,0.06)" if grain == g else "none",
            }
            for g in S.GRAINS
        ],
        "rateNote": f"{grain}, last {S.BUCKETS} {_grain_noun(grain)}",
        "wk": [{"d": b["d"], "v": F.fmt(b["v"]), "h": h} for b, h in zip(wk, heights)],
        "donutFig": F.donut(totals, short_labels=True),
        "donutTotal": F.fmt(total),
        "topDomains": [
            {
                "domain": t["domain"],
                "count": F.fmt(t["count"]),
                "dr": f"{F.jsround(t['mix']['deliverable'] / t['count'] * 100)}%" if t["count"] else "—",
                "drW": f"{F.jsround(t['mix']['deliverable'] / t['count'] * 100)}%" if t["count"] else "0%",
                "mix": F.mix(t["mix"]),
            }
            for t in tops
        ],
        "anEmpty": total == 0,
    }


# --- Exports --------------------------------------------------------------- #
PRESETS = [
    ("all", "All addresses", "Everything in the workspace"),
    ("deliverable", "Deliverable only", "Mailbox confirmed"),
    ("risky", "Risky", "Catch-all and role accounts"),
    ("undeliverable", "Undeliverable", "Will bounce — suppress these"),
    ("catchall", "Catch-all", "Domains that accept everything"),
    ("disposable", "Disposable", "Throwaway domains"),
]


async def exports(
    *, preset: str = "deliverable", columns: str = "full", custom_open: bool = False,
    custom_verdicts: Optional[list[str]] = None, require_mx: bool = False,
    exclude_disposable: bool = False,
) -> dict[str, Any]:
    store = get_address_store()
    totals = await store.totals()
    ws_total = sum(totals.values())
    catchall_n = await store.sub_reason_count(SubStatus.CATCH_ALL.value)
    disposable_n = await store.sub_reason_count(SubStatus.DISPOSABLE.value)
    counts = {
        "all": ws_total,
        "deliverable": totals["deliverable"],
        "risky": totals["risky"],
        "undeliverable": totals["undeliverable"],
        "catchall": catchall_n,
        "disposable": disposable_n,
    }

    preset_rows = [
        {
            "key": k,
            "label": label,
            "desc": desc,
            "n": F.fmt(counts[k]),
            "bd": F.BLUE if preset == k else F.LINE,
            "bg": F.BLUE_WASH if preset == k else F.WHITE,
        }
        for k, label, desc in PRESETS
    ]

    # Preview reflects the slice actually selected.
    if preset == "custom":
        sel = custom_verdicts or []
        rows, n = await store.query(verdicts=sel or None, limit=5, sort="recent")
        mix_totals = {k: totals[k] for k in (sel or F.ORDER)}
        label = "Custom"
    elif preset in ("catchall", "disposable"):
        sub = SubStatus.CATCH_ALL.value if preset == "catchall" else SubStatus.DISPOSABLE.value
        all_rows = await store.stream(sub_reason=sub)
        rows, n = all_rows[:5], len(all_rows)
        mix_totals = {"risky" if preset == "catchall" else "undeliverable": n}
        label = dict((k, lb) for k, lb, _ in PRESETS)[preset]
    else:
        sel = None if preset == "all" else [preset]
        rows, n = await store.query(verdicts=sel, limit=5, sort="recent")
        mix_totals = totals if preset == "all" else {preset: totals.get(preset, 0)}
        label = dict((k, lb) for k, lb, _ in PRESETS)[preset]

    jobs = await get_job_store().list()
    finished = [
        _job_row(j)
        for j in sorted(jobs, key=lambda x: x.created_at, reverse=True)
        if j.status is JobStatus.COMPLETED
    ]

    sel_v = custom_verdicts or []

    def href(**over) -> str:
        from urllib.parse import urlencode

        base = {
            "preset": preset,
            "columns": columns,
            "custom": custom_open,
            "v": sel_v,
            "require_mx": require_mx,
            "exclude_disposable": exclude_disposable,
        }
        base.update(over)
        params: list[tuple[str, Any]] = [
            ("preset", base["preset"]),
            ("columns", base["columns"]),
        ]
        if base["custom"]:
            params.append(("custom", "1"))
        for v in base["v"]:
            params.append(("v", v))
        if base["require_mx"]:
            params.append(("require_mx", "1"))
        if base["exclude_disposable"]:
            params.append(("exclude_disposable", "1"))
        return "/exports?" + urlencode(params)

    return {
        "presets": preset_rows,
        "exPreset": preset,
        "presetHrefs": {k: href(preset=k, custom=False) for k, _, _ in PRESETS},
        "customHref": href(preset="custom", custom=not custom_open),
        "verdictHrefs": {
            k: href(
                preset="custom",
                custom=True,
                v=[x for x in sel_v if x != k] if k in sel_v else sel_v + [k],
            )
            for k in F.ORDER
        },
        "toggleHrefs": {
            "require_mx": href(require_mx=not require_mx),
            "exclude_disposable": href(exclude_disposable=not exclude_disposable),
        },
        "columnHrefs": {c: href(columns=c) for c in ("full", "email")},
        "exPrev": {
            "label": label,
            "count": F.fmt(n),
            "mix": F.mix(mix_totals),
            "rows": [{"email": r["email"]} for r in rows],
        },
        "customOpen": custom_open,
        "customBg": F.BLUE_WASH if preset == "custom" else F.WHITE,
        "customBd": F.BLUE if preset == "custom" else F.LINE,
        "customState": "Editing" if custom_open else "Set up",
        "customVerdicts": [
            {
                "key": k,
                "label": F.SHORT_LABEL[k],
                "dot": F.VERDICT_STYLE[k]["dot"],
                "on": k in (custom_verdicts or []),
                "bg": F.BLUE_WASH if k in (custom_verdicts or []) else F.WHITE,
                "bd": F.BLUE if k in (custom_verdicts or []) else F.LINE_2,
                "fg": F.BLUE_DARK if k in (custom_verdicts or []) else F.INK_3,
            }
            for k in F.ORDER
        ],
        "customToggles": [
            {
                "key": "require_mx",
                "label": "Require MX",
                "note": "Drop rows whose domain has no mail exchanger",
                "on": require_mx,
                "trackBg": F.BLUE if require_mx else F.LINE_2,
                "knob": "16px" if require_mx else "0px",
            },
            {
                "key": "exclude_disposable",
                "label": "Exclude disposable",
                "note": "Drop throwaway domains even when they accept mail",
                "on": exclude_disposable,
                "trackBg": F.BLUE if exclude_disposable else F.LINE_2,
                "knob": "16px" if exclude_disposable else "0px",
            },
        ],
        "exCols": [
            {
                "key": key,
                "label": lbl,
                "bg": F.WHITE if columns == key else "transparent",
                "fg": F.INK if columns == key else F.MUTED,
                "sh": "0 1px 2px rgba(16,24,40,0.06)" if columns == key else "none",
            }
            for key, lbl in (("full", "Full detail"), ("email", "Email only"))
        ],
        "exColumns": columns,
        "hJobs": finished,
    }


# --- History --------------------------------------------------------------- #
async def history() -> dict[str, Any]:
    jobs = await get_job_store().list()
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return {"hJobs": [_job_row(j) for j in jobs], "hEmpty": not jobs}


async def history_detail(job_id: str) -> Optional[dict[str, Any]]:
    job = await get_job_store().get(job_id)
    if not job:
        return None
    c = job.counts.as_dict()
    totals = _counts_to_verdicts(c)
    addr = c.get("unique_emails") or 0
    return {
        "hd": {
            "job_id": job.id,
            "id": f"#{job.seq}",
            "file": job.filename,
            "date": _long_when(job.created_at),
            "dur": F.duration(job.duration),
            "rows": F.fmt(c.get("total_rows") or 0),
            "addr": F.fmt(addr),
            "mix": F.mix(totals),
            "cards": [
                {
                    "label": F.VERDICT_STYLE[k]["label"],
                    "dot": F.VERDICT_STYLE[k]["dot"],
                    "count": F.fmt(totals[k]),
                    "pct": f"{F.jsround(totals[k] / addr * 100)}%" if addr else "—",
                }
                for k in F.ORDER
            ],
            "failed": job.status is JobStatus.FAILED,
            "error": job.error,
            "outputs": list(job.output_keys.keys()),
        }
    }


# --- Validate -------------------------------------------------------------- #
def _steps(current: int) -> list[dict[str, Any]]:
    out = []
    for i, label in enumerate(["Upload", "Preview", "Map columns", "Validate"], start=1):
        done, cur = current > i, current == i
        out.append(
            {
                "label": label,
                "n": str(i),
                "bg": F.GREEN if done else F.WHITE,
                "bd": F.GREEN if done else (F.BLUE if cur else F.LINE),
                "fg": F.WHITE if done else (F.BLUE if cur else F.MUTED_2),
                "lfg": F.INK if cur else (F.INK_3 if done else F.MUTED),
                "ring": "0 0 0 3px rgba(53,171,255,0.16)" if cur else "none",
            }
        )
    return out


async def validate_screen(
    *,
    file_id: Optional[str] = None,
    filename: Optional[str] = None,
    job_id: Optional[str] = None,
    step: int = 1,
) -> dict[str, Any]:
    """One screen, five states: upload, preview, map, running, finished."""
    from eve.jobs import csv_io
    from eve.storage import get_object_store

    ctx: dict[str, Any] = {
        "vStep1": False,
        "vStep2": False,
        "vStep3": False,
        "vStep4": False,
        "vComplete": False,
        "vFailed": False,
        "fileId": file_id,
        "filename": filename,
        "listTypes": LIST_TYPES,
    }

    # --- a job is running or finished: that wins over the wizard ----------
    if job_id:
        job = await get_job_store().get(job_id)
        if job:
            ctx["vSteps"] = _steps(4)
            ctx["job"] = job.as_dict()
            ctx["jobId"] = job.id
            c = job.counts.as_dict()
            totals = _counts_to_verdicts(c)
            addr = c.get("unique_emails") or 0
            live_total = sum(totals.values()) or 1
            ctx["vCards"] = [
                {
                    "key": k,
                    "label": F.VERDICT_STYLE[k]["label"],
                    "dot": F.VERDICT_STYLE[k]["dot"],
                    "count": F.fmt(totals[k]),
                    "pct": f"{F.jsround(totals[k] / live_total * 100)}%" if sum(totals.values()) else "—",
                }
                for k in F.ORDER
            ]
            ctx["vFile"] = job.filename
            ctx["vPct"] = F.fixed(job.progress * 100) + "%"
            ctx["vPhase"] = _phase_text(job)
            ctx["vElapsed"] = F.duration(job.duration)

            if job.status is JobStatus.COMPLETED:
                ctx["vComplete"] = True
                ctx["doneMix"] = F.mix(totals)
                ctx["doneCards"] = ctx["vCards"]
                ctx["doneTitle"] = f"{job.filename} is clean"
                ctx["doneSub"] = f"{F.fmt(addr)} unique addresses · {F.duration(job.duration)}"
                ctx["downloads"] = [
                    {
                        "key": seg,
                        "label": lbl,
                        "href": f"/v1/jobs/{job.id}/download?segment={seg}",
                        "note": note,
                    }
                    for seg, lbl, note in (
                        (
                            "cleaned",
                            "Download cleaned list",
                            f"{F.fmt(c.get('total_rows') or 0)} rows",
                        ),
                        ("valid", "Deliverable only", f"{F.fmt(totals['deliverable'])} rows"),
                        (
                            "removed",
                            "Removed",
                            f"{F.fmt(c.get('duplicates', 0) + totals['undeliverable'])} rows",
                        ),
                    )
                    if seg in job.output_keys
                ]
            elif job.status is JobStatus.FAILED:
                ctx["vFailed"] = True
                ctx["vError"] = job.error or "The run stopped before it finished."
                ctx["vFailedAfter"] = (
                    f"Job failed after {F.fmt(job.processed)} of {F.fmt(job.total)} addresses"
                    if job.total
                    else "Job failed before any address was verified"
                )
                ctx["hasPartial"] = bool(job.output_keys)
            else:
                ctx["vStep4"] = True
            return ctx

    # --- the wizard -------------------------------------------------------
    if file_id and step >= 2:
        store = get_object_store()
        det = await csv_io.detect_columns(store, file_id)
        cols = det.columns
        ctx["columns"] = cols
        ctx["guessedEmail"] = det.guessed_email or (cols[0] if cols else "")
        ctx["guessedFirst"] = det.guessed_first_name
        ctx["guessedLast"] = det.guessed_last_name
        ctx["delimiterName"] = {
            ",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"
        }.get(det.delimiter, det.delimiter)
        ctx["lineEnding"] = "CRLF" if det.line_terminator == "\r\n" else "LF"

        stats = await csv_io.preview_stats(
            store, file_id, ctx["guessedEmail"], det.delimiter
        )
        ctx["totalRows"] = stats.total_rows
        ctx["uniqueEmails"] = stats.unique_emails
        ctx["emptyCsv"] = stats.total_rows == 0
        ctx["detectedLine"] = (
            f"Detected: {ctx['delimiterName']}-delimited · {F.fmt(stats.total_rows)} rows · "
            f"{len(cols)} columns · {ctx['lineEnding']}"
        )
        ctx["chargeLine"] = (
            f"{F.fmt(stats.total_rows)} rows · {F.fmt(stats.unique_emails)} unique addresses · "
            f"you'll be charged for {F.fmt(stats.unique_emails)}."
        )
        # Preview grid: the email column stays wide, the rest share the row.
        preview_cols = [ctx["guessedEmail"]] + [c for c in cols if c != ctx["guessedEmail"]]
        preview_cols = preview_cols[:5]
        ctx["previewCols"] = preview_cols
        ctx["previewGrid"] = "44px minmax(200px, 1.6fr) " + " ".join(
            ["1fr"] * max(0, len(preview_cols) - 1)
        )
        ctx["previewRows"] = [
            {"n": str(i + 1), "cells": [r.get(c, "") for c in preview_cols]}
            for i, r in enumerate(det.sample_rows)
        ]
        ctx["vSteps"] = _steps(step)
        ctx["vStep2"] = step == 2
        ctx["vStep3"] = step == 3
        return ctx

    ctx["vSteps"] = _steps(1)
    ctx["vStep1"] = True
    return ctx


# --- Single check ---------------------------------------------------------- #
async def single_check(email: Optional[str], recent: list[dict]) -> dict[str, Any]:
    """Run one address through the engine and lay out its trace."""
    import json

    from fastapi.concurrency import run_in_threadpool

    from eve.engine import validate as run_validate
    from eve.trace import build_trace

    ctx: dict[str, Any] = {
        "scQ": email or "",
        "hasSc": False,
        "scRecent": recent,
        "hasRecent": bool(recent),
        "typo": False,
    }
    if not email:
        return ctx

    v = await run_in_threadpool(run_validate, email)
    style = F.VERDICT_STYLE[primary_verdict(v.status)]
    sub = SUB_REASON_LABEL.get(v.sub_status.value) or ""
    ctx.update(
        {
            "hasSc": True,
            "scRes": {
                "email": v.normalized_email or v.email,
                "score": v.score,
                "ringColor": style["dot"],
                "ringDash": F.ring_dash(v.score),
                "vlabel": style["label"],
                "dot": style["dot"],
                "wash": style["wash"],
                "ink": style["ink"],
                "sub": sub,
                "subtitle": subtitle_for(v.status.value, v.sub_status.value, v.domain),
                "trace": [t.as_dict() for t in build_trace(v)],
            },
            "typo": bool(v.suggested_correction),
            "typoSuggestion": v.suggested_correction,
            "rawJson": json.dumps(v.to_dict(), indent=2),
        }
    )
    return ctx


# --- Static reference copy ------------------------------------------------- #
STAGES = [
    (
        "01",
        "Syntax",
        "We parse the address against RFC 5322 before anything touches the network. "
        "A malformed address short-circuits here, and the trace says so.",
    ),
    (
        "02",
        "Normalization",
        "Lowercasing, whitespace stripping, and provider-aware alias folding. Two rows "
        "that are the same mailbox collapse into one, and you are not charged twice.",
    ),
    (
        "03",
        "Typo detection",
        "Edit-distance against a corpus of the most common domains. We surface the "
        "suggestion. We never rewrite your data.",
    ),
    (
        "04",
        "DNS / MX",
        "MX lookup with A-record fallback. No mail exchanger and no fallback means the "
        "domain cannot receive mail at all — that is an undeliverable we can prove.",
    ),
    (
        "05",
        "Classification",
        "Corporate vs free provider, role account vs person, disposable vs durable. "
        "Classification never overrides evidence; it only moves the score.",
    ),
    (
        "06",
        "SMTP mailbox probe",
        "A real RCPT TO conversation with the mail exchanger. A 250 from a provider we "
        "trust is a confirmation. A 250 from a catch-all is not.",
    ),
    (
        "07",
        "Catch-all detection",
        "We probe a random address that cannot exist. If it is accepted, every result "
        "from that domain is risky — including the one you asked about.",
    ),
    (
        "08",
        "Scoring",
        "The layers combine into 0–100 with the verdict fixed by evidence, not by the "
        "number. A high score never upgrades an unknown into a deliverable.",
    ),
]

VERDICT_REFERENCE = {
    "deliverable": (
        "The mail exchanger confirmed this exact mailbox during a live SMTP conversation.",
        "We will never mark an address deliverable because it merely looks well-formed.",
    ),
    "risky": (
        "The address will probably accept mail, but acceptance does not prove a person is behind it.",
        "We will never quietly promote a catch-all domain to deliverable.",
    ),
    "unknown": (
        "The engine could not reach a conclusion — greylisting, a timeout, an antispam "
        "gateway, or a disabled probe.",
        "We will never dress an unknown up as a valid address to make a report look better.",
    ),
    "undeliverable": (
        "We have direct evidence this will bounce: a rejection, a missing domain, or a throwaway provider.",
        "We will never call a greylisted or timed-out address undeliverable.",
    ),
}


def how_it_works() -> dict[str, Any]:
    return {
        "stages": [{"n": n, "title": t, "body": b} for n, t, b in STAGES],
        "verdictRef": [
            {
                "label": F.VERDICT_STYLE[k]["label"],
                "dot": F.VERDICT_STYLE[k]["dot"],
                "wash": F.VERDICT_STYLE[k]["wash"],
                "ink": F.VERDICT_STYLE[k]["ink"],
                "def": VERDICT_REFERENCE[k][0],
                "never": VERDICT_REFERENCE[k][1],
            }
            for k in F.ORDER
        ],
    }
