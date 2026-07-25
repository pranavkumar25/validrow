"""Validrow — SaaS front-end (Streamlit, pure Python).

Redesigned to the Figma "Dashboard v3" system: a warm cream canvas, a bright
sky-blue primary, an Inter type scale, soft 1px-shadow cards, and status shown
as coloured dot + label. A multi-page dashboard client of the FastAPI service:

    • Dashboard   – headline stat cards (with sparklines), validation volume,
                    recent verified results
    • Validate    – upload a CSV → stepper → map columns → validate → results
    • Single      – real-time single-address verdict + verification breakdown
    • Contacts    – every verified address across all uploads, searchable
    • Analytics   – deliverable rate, status mix donut, weekday volume, top domains
    • Exports     – download filtered slices + every run's ready outputs
    • History     – every past upload, with per-run analytics + downloads
    • How it works – the verification pipeline, layer by layer (via Support)

There is no login or database yet (those get linked later): run history is
persisted to local disk via ``history_store`` so the analytics survive page
reloads and API restarts.

Run:  streamlit run frontend/app.py
(The API must be running: uvicorn eve.api.main:app --port 8000)
"""
from __future__ import annotations

import html
import io
import time
from contextlib import contextmanager

import altair as alt
import history_store as store
import pandas as pd
import requests
import streamlit as st

DEFAULT_API = "http://127.0.0.1:8000"

# --------------------------------------------------------------------------- #
# Design tokens
# --------------------------------------------------------------------------- #
# Colours that Streamlit can theme natively live in .streamlit/config.toml and
# are mirrored here (and in the :root block) for the custom HTML layer. Change
# a value in config.toml first, then mirror it — never the other way round.
#
# Neutral ramp — 12 steps. Text steps are contrast-verified against #ffffff,
# #f6f7f9 (canvas) and #f1f2f5 (hover surface); the worst case is listed.
N0 = "#ffffff"       # elevated surface
N50 = "#f6f7f9"      # page canvas
N100 = "#f1f2f5"     # hover / inset surface
N150 = "#ecedf1"     # divider inside a component
N200 = "#e3e5ea"     # structural border
N300 = "#d3d6dd"     # border, hover/emphasis
N400 = "#b0b4bd"     # disabled text and marks only
N500 = "#878b95"     # decorative marks only — never text (3.05:1)
N600 = "#6c707a"     # tertiary text            (4.43:1)
N700 = "#5b5f68"     # secondary text           (5.72:1)
N800 = "#3a3c44"     # body text                (9.82:1)
N900 = "#14151a"     # headings / primary numerals

BLUE = "#1560d0"     # single accent — 5.81:1 against white, so labels pass AA
BLUE_HOVER = "#124fac"
BLUE_SOFT = "#eaf3ff"

# --- Status taxonomy -------------------------------------------------------- #
# Four primary verdicts, one colour each, one shape (a small filled dot plus a
# text label). The label always carries the meaning; colour only reinforces it.
#
# The engine emits six status values. `disposable` and `spam_trap` are not
# separate verdicts — they are *reasons* an address is undeliverable — so they
# resolve to the Undeliverable colour and label and surface their specificity as
# secondary grey sub-reason text. Nothing about the underlying values changes:
# STATUS_ORDER, the API payloads, the export columns and every filter still see
# all six keys.
PRIMARY = {
    "deliverable":   {"c": "#16744a", "soft": "#eef7f2", "label": "Deliverable"},
    "risky":         {"c": "#9a6410", "soft": "#f9f3e9", "label": "Risky"},
    "undeliverable": {"c": "#c43c2f", "soft": "#fbeeec", "label": "Undeliverable"},
    "unknown":       {"c": N600,      "soft": N100,      "label": "Unknown"},
}
# engine status -> primary verdict
VERDICT_OF = {
    "valid": "deliverable",
    "risky": "risky",
    "invalid": "undeliverable",
    "disposable": "undeliverable",
    "spam_trap": "undeliverable",
    "unknown": "unknown",
}
# The extra specificity carried by the two non-verdict statuses, shown as
# secondary text rather than as its own colour.
SUB_REASON = {"disposable": "Disposable domain", "spam_trap": "Spam trap"}


def verdict(status) -> str:
    """Map an engine status onto one of the four primary verdicts."""
    return VERDICT_OF.get(str(status), "unknown")


def status_color(status) -> str:
    return PRIMARY[verdict(status)]["c"]


def status_soft(status) -> str:
    return PRIMARY[verdict(status)]["soft"]


def status_label(status) -> str:
    return PRIMARY[verdict(status)]["label"]


# Kept as dict-shaped views so existing call sites keep working.
STATUS_COLORS = {k: status_color(k) for k in VERDICT_OF}
STATUS_DOT = STATUS_COLORS
STATUS_SOFT = {k: status_soft(k) for k in VERDICT_OF}
STATUS_LABEL = {k: status_label(k) for k in VERDICT_OF}
STATUS = {k: {"txt": status_color(k), "dot": status_color(k),
              "soft": status_soft(k), "label": status_label(k)} for k in VERDICT_OF}
STATUS_ORDER = ["valid", "risky", "unknown", "invalid", "disposable", "spam_trap"]
# Chart/legend order: the four verdicts, best to worst, no duplicates.
VERDICT_ORDER = ["deliverable", "risky", "unknown", "undeliverable"]

# --- Chart neutrals --------------------------------------------------------- #
# One ramp for every chart on every screen. (Two ramps used to be in play — a
# cool one and a warm leftover — so charts side by side disagreed.)
AX_LABEL = N600      # axis labels are text: must pass 4.5:1
AX_LINE = N200
AX_GRID = N150

# Inline SVG icons (Lucide-style) — no emoji as structural icons.
IC = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1.5"/>'
            '<rect x="14" y="3" width="7" height="7" rx="1.5"/>'
            '<rect x="14" y="14" width="7" height="7" rx="1.5"/>'
            '<rect x="3" y="14" width="7" height="7" rx="1.5"/>',
    "bolt": '<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "checkcircle": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "minus": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>',
    "gauge": '<path d="M12 14 8 8"/><circle cx="12" cy="13" r="9"/>',
    "bar": '<path d="M3 3v18h18"/><rect x="7" y="11" width="3" height="7" rx="1"/>'
           '<rect x="12.5" y="7" width="3" height="11" rx="1"/>'
           '<rect x="18" y="13" width="3" height="5" rx="1"/>',
    "spark": '<path d="M3 17l6-6 4 4 8-8"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "trendup": '<path d="M7 17 17 7"/><path d="M9 7h8v8"/>',
    "trenddown": '<path d="M7 7 17 17"/><path d="M17 9v8H9"/>',
    "download": '<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "upload": '<path d="M12 21V9m0 0 4 4m-4-4-4 4"/><path d="M4 7V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "layers": '<path d="m12 2 9 5-9 5-9-5 9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="m9 12 2 2 4-4"/>',
    "filter": '<path d="M3 4h18l-7 8v6l-4 2v-8L3 4z"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/>'
                '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 '
                '0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 '
                '0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 '
                '1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 '
                '1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 '
                '0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 '
                '1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "life": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>'
            '<path d="m4.9 4.9 4.2 4.2M14.9 14.9l4.2 4.2M14.9 9.1l4.2-4.2M4.9 19.1l4.2-4.2"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/>'
              '<path d="M21 12H9"/>',
    "chevdown": '<path d="m6 9 6 6 6-6"/>',
    "dots": '<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/>'
            '<circle cx="12" cy="19" r="1.4"/>',
    "code": '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/>',
    "pie": '<path d="M12 3a9 9 0 1 0 9 9h-9V3z"/><path d="M14 3.5a7 7 0 0 1 6.5 6.5H14V3.5z"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1.2"/>'
            '<circle cx="3.5" cy="12" r="1.2"/><circle cx="3.5" cy="18" r="1.2"/>',
}


def icon(name: str, size: int = 18, stroke: float = 1.9, cls: str = "") -> str:
    return (
        f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{IC.get(name, "")}</svg>'
    )


st.set_page_config(
    page_title="Validrow",
    # An inline SVG check-circle rather than an emoji, so the favicon matches
    # the one icon set used everywhere else.
    page_icon=(
        "data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
        "stroke='%231560d0' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'"
        "%3E%3Ccircle cx='12' cy='12' r='9'/%3E%3Cpath d='m8.5 12 2.5 2.5 4.5-5'/%3E%3C/svg%3E"
    ),
    layout="wide",
    initial_sidebar_state="expanded",
)


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


# --------------------------------------------------------------------------- #
# CSS
# --------------------------------------------------------------------------- #
def inject_css() -> None:
    # Inter is self-hosted via .streamlit/config.toml [[theme.fontFaces]] (served
    # from frontend/static/), so Streamlit applies it natively to every widget.
    st.markdown(
        """
        <style>
          /* ============================================================
             Validrow design system
             ------------------------------------------------------------
             Every value below is a token. Nothing in this stylesheet may
             hard-code a colour, size, radius, shadow or duration — if a
             component needs a value that isn't here, add the token.
             Colours that config.toml can set are mirrored, not redefined.
             ============================================================ */
          :root {
            /* --- accent: one colour, three states -------------------- */
            --blue:#1560d0; --blue-hover:#124fac; --blue-soft:#eaf3ff;

            /* --- neutral ramp: 12 steps ------------------------------ */
            --n0:#ffffff;   --n50:#f6f7f9;  --n100:#f1f2f5; --n150:#ecedf1;
            --n200:#e3e5ea; --n300:#d3d6dd; --n400:#b0b4bd; --n500:#878b95;
            --n600:#6c707a; --n700:#5b5f68; --n800:#3a3c44; --n900:#14151a;

            /* --- named roles (use these, not the ramp steps) --------- */
            --text-1:var(--n900);   /* headings, primary numerals        */
            --text-2:var(--n800);   /* body                    9.8:1    */
            --text-3:var(--n700);   /* secondary               5.7:1    */
            --text-4:var(--n600);   /* tertiary, captions      4.4:1    */
            --text-disabled:var(--n400);
            --mark:var(--n500);     /* decorative icons — never text    */
            --divider:var(--n150);  /* line inside a component          */
            --border:var(--n200);   /* line around a component          */
            --border-hover:var(--n300);
            --surface:var(--n0);    /* card / elevated                  */
            --surface-hover:var(--n50);
            --surface-inset:var(--n100);
            --canvas:var(--n50);

            /* --- status: four verdicts, one colour each -------------- */
            --st-ok:#16744a;   --st-ok-soft:#eef7f2;
            --st-risk:#9a6410; --st-risk-soft:#f9f3e9;
            --st-bad:#c43c2f;  --st-bad-soft:#fbeeec;
            --st-unk:var(--n600); --st-unk-soft:var(--n100);

            /* --- spacing: 4px scale --------------------------------- */
            --s1:4px;  --s2:8px;  --s3:12px; --s4:16px; --s5:20px;
            --s6:24px; --s8:32px; --s10:40px; --s12:48px; --s16:64px;

            /* --- type scale: 8 named styles ------------------------- */
            --fs-display:30px; --fw-display:640; --tr-display:-.022em;
            --fs-title:24px;   --fw-title:640;   --tr-title:-.018em;
            --fs-heading:15px; --fw-heading:620; --tr-heading:-.008em;
            --fs-body:14px;    --fw-body:400;
            --fs-sm:13px;
            --fs-label:12px;   --fw-label:550;
            --fs-caption:11px; --fw-caption:560; --tr-caption:.04em;
            --lh-tight:1.2; --lh-snug:1.35; --lh-body:1.55;

            /* --- radii: 3 values ------------------------------------ */
            --r-sm:6px;    /* inputs, buttons, small marks              */
            --r-md:10px;   /* cards, panels                             */
            --r-pill:999px;

            /* --- elevation: 3 levels, border-led -------------------- */
            --e0:none;                                    /* border only */
            --e1:0 1px 2px rgba(20,21,26,.04);            /* resting card */
            --e2:0 4px 12px -2px rgba(20,21,26,.07),
                 0 1px 2px rgba(20,21,26,.04);            /* overlay     */
            --ring:0 0 0 3px rgba(21,96,208,.28);         /* focus       */

            /* --- motion: one duration set, one curve ---------------- */
            --t-fast:120ms; --t:200ms; --t-slow:300ms;
            --ease:cubic-bezier(.2,0,.13,1);

            /* Tabular figures ON globally. This app sells numeric
               precision; proportional digits made columns of numbers
               fail to align. Opt *out* per-element if ever needed. */
            font-feature-settings:'liga' 1,'calt' 1,'ss01' 1,'cv11' 1,'tnum' 1;
            -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale;
            text-rendering:optimizeLegibility;
          }

          .stApp{ background:var(--canvas); }
          .block-container{ padding:var(--s8) var(--s10) var(--s16); max-width:1240px; }
          #MainMenu, header[data-testid="stHeader"], footer{ visibility:hidden; height:0; }
          .num{ font-variant-numeric:tabular-nums; }
          ::selection{ background:var(--blue-soft); }
          * { scrollbar-width:thin; scrollbar-color:var(--n300) transparent; }
          *::-webkit-scrollbar{ width:9px; height:9px; }
          *::-webkit-scrollbar-thumb{ background:var(--n300); border-radius:var(--r-pill);
            border:2px solid transparent; background-clip:content-box; }
          *::-webkit-scrollbar-thumb:hover{ background:var(--n400); background-clip:content-box; }

          h1{ font-weight:var(--fw-title) !important; letter-spacing:var(--tr-title);
            font-size:var(--fs-title) !important; color:var(--text-1);
            line-height:var(--lh-tight); }
          h2{ font-weight:var(--fw-heading) !important; letter-spacing:var(--tr-heading);
            color:var(--text-1); }
          h3, h4, h5{ font-weight:var(--fw-heading) !important;
            letter-spacing:var(--tr-heading); color:var(--text-1); }
          a{ text-decoration:none; }

          /* Focus: visible on every interactive surface, keyboard only.
             Never `outline:none` on its own. */
          a:focus-visible, button:focus-visible, summary:focus-visible,
          [role="button"]:focus-visible, input:focus-visible, select:focus-visible{
            outline:2px solid var(--blue); outline-offset:2px; border-radius:var(--r-sm); }

          /* ---------- Sidebar ---------- */
          section[data-testid="stSidebar"]{ width:264px !important; }
          section[data-testid="stSidebar"] > div{ padding-top:var(--s5); }
          .brand{ display:flex; align-items:center; gap:var(--s2); padding:0 var(--s2) var(--s1); }
          .brand .mark{ width:24px; height:24px; border-radius:var(--r-sm); flex:0 0 24px;
            display:grid; place-items:center; color:var(--n0); background:var(--blue); }
          .brand-word{ font-size:var(--fs-heading); font-weight:640; letter-spacing:var(--tr-title);
            line-height:1; color:var(--text-1); }
          .brand-word b{ color:var(--blue); font-weight:640; }
          .searchbox{ display:flex; align-items:center; gap:var(--s2);
            border:1px solid var(--border); background:var(--surface); border-radius:var(--r-sm);
            padding:var(--s2) var(--s3); margin:var(--s5) var(--s2) var(--s1);
            transition:border-color var(--t-fast) var(--ease); }
          .searchbox:hover{ border-color:var(--border-hover); }
          .searchbox svg{ color:var(--mark); flex:0 0 16px; }
          .searchbox .ph{ color:var(--text-4); font-size:var(--fs-sm); }
          .searchbox .kbd{ margin-left:auto; color:var(--text-4); font-size:var(--fs-caption);
            font-weight:var(--fw-label); border:1px solid var(--border); border-radius:var(--r-sm);
            padding:1px var(--s1); background:var(--surface-inset); }

          /* One definition of "small uppercase label" — was four. */
          .eyebrow, .side-label{ font-size:var(--fs-caption); font-weight:var(--fw-caption);
            letter-spacing:var(--tr-caption); text-transform:uppercase; color:var(--text-4); }
          .side-label{ margin:var(--s5) var(--s3) var(--s2); }

          .nav{ position:relative; display:flex; align-items:center; gap:var(--s3);
            padding:var(--s2) var(--s3); border-radius:var(--r-sm); color:var(--text-2) !important;
            font-size:var(--fs-sm); font-weight:500; text-decoration:none !important;
            margin:1px var(--s2);
            transition:background var(--t-fast) var(--ease), color var(--t-fast) var(--ease); }
          .nav:hover{ background:var(--surface-inset); color:var(--text-1) !important; }
          .nav-btn{ display:inline-flex !important; width:auto; }
          .nav-out{ border:1px solid var(--border); background:var(--surface); }
          .nav.active{ background:var(--blue-soft); color:var(--blue) !important; font-weight:600; }
          .nav.active::before{ content:""; position:absolute; left:calc(-1 * var(--s2)); top:50%;
            transform:translateY(-50%); width:3px; height:18px; border-radius:0 3px 3px 0;
            background:var(--blue); }
          .nav svg{ color:var(--mark); flex:0 0 18px; transition:color var(--t-fast) var(--ease); }
          .nav:hover svg{ color:var(--text-3); }
          .nav.active svg{ color:var(--blue); }
          .nav .badge-n{ margin-left:auto; background:var(--surface-inset); color:var(--text-3);
            border-radius:var(--r-pill); font-size:var(--fs-caption); font-weight:var(--fw-label);
            padding:1px var(--s2); min-width:19px; text-align:center; }
          .nav.active .badge-n{ background:var(--n0); color:var(--blue); }
          .side-foot{ border-top:1px solid var(--divider); margin:var(--s2) var(--s3) 0;
            padding:var(--s3) var(--s1) var(--s1); display:flex; align-items:center; gap:var(--s3); }
          .avatar{ width:32px; height:32px; flex:0 0 32px; border-radius:var(--r-pill); display:grid;
            place-items:center; color:var(--text-3); font-weight:600; font-size:var(--fs-label);
            background:var(--surface-inset); border:1px solid var(--border); }
          .side-foot .nm{ font-size:var(--fs-sm); font-weight:600; color:var(--text-1);
            line-height:var(--lh-tight); }
          .side-foot .em{ font-size:var(--fs-label); color:var(--text-4); }
          .side-foot .lo{ margin-left:auto; color:var(--mark); display:inline-flex;
            transition:color var(--t-fast) var(--ease); }
          .side-foot .lo:hover{ color:var(--text-3); }

          /* ---------- Pills (non-status meta only) ---------- */
          .pill{ display:inline-flex; align-items:center; gap:var(--s1);
            border-radius:var(--r-pill); padding:2px var(--s2); font-size:var(--fs-caption);
            font-weight:var(--fw-label); }
          .p-ok{ background:var(--st-ok-soft); color:var(--st-ok); }
          .p-off{ background:var(--surface-inset); color:var(--text-3); }
          .p-err{ background:var(--st-bad-soft); color:var(--st-bad); }
          .p-info{ background:var(--blue-soft); color:var(--blue); }
          .p-warn{ background:var(--st-risk-soft); color:var(--st-risk); }

          .dot{ width:7px; height:7px; border-radius:var(--r-pill); flex:0 0 7px;
            display:inline-block; }
          @keyframes pulse{ 0%,100%{ opacity:1; } 50%{ opacity:.45; } }
          .dot-live{ position:relative; animation:pulse 2s var(--ease) infinite; }

          /* ---------- Page header ---------- */
          .phead{ display:flex; align-items:flex-start; gap:var(--s4); margin:0 0 var(--s6); }
          .phead h1{ margin:0; }
          .phead .sub{ color:var(--text-3); font-size:var(--fs-body); margin-top:var(--s2);
            max-width:76ch; line-height:var(--lh-body); }
          .phead .sp{ margin-left:auto; display:flex; align-items:center; gap:var(--s2);
            flex-wrap:wrap; justify-content:flex-end; }

          /* ---------- Buttons: one height app-wide (34px) ---------- */
          .chip-btn{ display:inline-flex; align-items:center; gap:var(--s2); height:34px;
            box-sizing:border-box; border:1px solid var(--border); background:var(--surface);
            border-radius:var(--r-sm); padding:0 var(--s3); font-size:var(--fs-sm);
            font-weight:var(--fw-label); color:var(--text-2) !important;
            text-decoration:none !important; white-space:nowrap;
            transition:background var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease); }
          .chip-btn:hover{ border-color:var(--border-hover); background:var(--surface-hover);
            color:var(--text-1) !important; }
          .chip-btn svg{ color:var(--mark); }
          /* Optical padding: a trailing icon needs slightly less trailing space. */
          .chip-btn svg:last-child{ margin-right:-2px; }
          .btn-primary{ background:var(--blue); border-color:var(--blue);
            color:var(--n0) !important; }
          .btn-primary:hover{ background:var(--blue-hover); border-color:var(--blue-hover);
            color:var(--n0) !important; }
          .btn-primary svg{ color:var(--n0); }

          /* ---------- Stat cards ---------- */
          .cards{ display:grid; gap:var(--s4); margin-bottom:var(--s5); }
          .c3{ grid-template-columns:repeat(3,1fr); } .c4{ grid-template-columns:repeat(4,1fr); }
          .c6{ grid-template-columns:repeat(6,1fr); } .c2{ grid-template-columns:repeat(2,1fr); }
          @media (max-width:1100px){ .c3,.c4,.c6{ grid-template-columns:repeat(2,1fr);} }
          @media (max-width:640px){ .c2,.c3,.c4,.c6{ grid-template-columns:1fr;} }
          /* Border-led, not shadow-led. No lift on hover — these aren't buttons. */
          .stat{ position:relative; background:var(--surface); border:1px solid var(--border);
            border-radius:var(--r-md); padding:var(--s5); box-shadow:var(--e1); }
          .stat.brd{ border-left-width:3px; padding-left:calc(var(--s5) - 2px); }
          .stat .l{ font-size:var(--fs-caption); color:var(--text-4);
            font-weight:var(--fw-caption); display:flex; align-items:center; gap:var(--s2);
            letter-spacing:var(--tr-caption); text-transform:uppercase; }
          .stat .l .g{ margin-left:auto; color:var(--n400); display:inline-flex; }
          .stat .v{ font-size:var(--fs-display); font-weight:var(--fw-display);
            letter-spacing:var(--tr-display); margin-top:var(--s3); line-height:var(--lh-tight);
            color:var(--text-1); font-variant-numeric:tabular-nums; display:flex;
            align-items:baseline; gap:var(--s2); flex-wrap:wrap; }
          .stat .v .d{ font-size:var(--fs-label); font-weight:var(--fw-label);
            display:inline-flex; align-items:center; gap:2px; padding:2px var(--s2);
            border-radius:var(--r-pill); }
          .stat .v .d.up{ color:var(--st-ok); background:var(--st-ok-soft); }
          .stat .v .d.dn{ color:var(--st-bad); background:var(--st-bad-soft); }
          .stat .v .d.flat{ color:var(--text-3); background:var(--surface-inset); }
          .stat .foot{ font-size:var(--fs-label); color:var(--text-4); margin-top:var(--s2); }
          .stat .spark{ margin-top:var(--s4); height:48px; }

          /* ---------- Cards (raw HTML) ---------- */
          .card{ background:var(--surface); border:1px solid var(--border);
            border-radius:var(--r-md); box-shadow:var(--e1); overflow:hidden; }
          .card-h{ padding:var(--s4) var(--s5); display:flex; align-items:center; gap:var(--s3); }
          .card-h.bd{ border-bottom:1px solid var(--divider); }
          .card-h h4{ font-size:var(--fs-heading); font-weight:var(--fw-heading); margin:0;
            letter-spacing:var(--tr-heading); color:var(--text-1); }
          .card-h .cnt{ background:var(--surface-inset); color:var(--text-3);
            border-radius:var(--r-pill); font-size:var(--fs-label); font-weight:var(--fw-label);
            padding:2px var(--s2); font-variant-numeric:tabular-nums; }
          .card-h .sp{ margin-left:auto; display:flex; align-items:center; gap:var(--s2); }
          .card-b{ padding:var(--s5); }

          /* ---------- Status indicator ----------
             ONE definition, consumed everywhere: results table, summary card,
             chart legend, single-check verdict, export preview. A small filled
             dot plus a text label. The label carries the meaning; the dot only
             reinforces it, so this stays readable with no colour perception at
             all. Never a saturated pill, never an icon, never colour alone. */
          .stag{ display:inline-flex; align-items:baseline; gap:var(--s2);
            font-size:var(--fs-sm); font-weight:var(--fw-label); white-space:nowrap; }
          .stag .d{ width:7px; height:7px; border-radius:var(--r-pill); flex:0 0 7px;
            transform:translateY(-1px); }
          /* Sub-reason: secondary grey text, never its own hue. */
          .sreason{ font-size:var(--fs-label); color:var(--text-4); font-weight:400; }

          .sbar{ display:inline-flex; align-items:center; gap:var(--s3); }
          .sbar .track{ width:96px; height:5px; border-radius:var(--r-pill);
            background:var(--surface-inset); overflow:hidden; }
          .sbar .track i{ display:block; height:100%; border-radius:var(--r-pill); }
          .sbar .n{ font-size:var(--fs-sm); font-weight:var(--fw-label); color:var(--text-2);
            font-variant-numeric:tabular-nums; min-width:22px; text-align:right; }
          .tag{ display:inline-flex; align-items:center; gap:var(--s1); font-size:var(--fs-label);
            color:var(--text-3); font-weight:400; }

          /* ---------- HTML table (list views) ----------
             Fixed row height, sticky header, ellipsis truncation, numerals
             right-aligned. Hover changes surface only — never the border, which
             would jitter the whole row by 1px. */
          .vt{ width:100%; }
          .vt-scroll{ overflow-x:auto; overflow-y:visible; }
          .vt-head, .vt-row{ display:grid; align-items:center; gap:var(--s4);
            padding:0 var(--s3); }
          .vt-head{ height:34px; border-bottom:1px solid var(--border);
            position:sticky; top:0; z-index:2; background:var(--surface); }
          .vt-head span{ font-size:var(--fs-caption); font-weight:var(--fw-caption);
            letter-spacing:var(--tr-caption); text-transform:uppercase; color:var(--text-4);
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .vt-row{ height:52px; text-decoration:none !important; color:inherit !important;
            box-shadow:inset 0 -1px 0 var(--divider);
            transition:background var(--t-fast) var(--ease); }
          .vt-row:last-child{ box-shadow:none; }
          .vt-row:hover{ background:var(--surface-hover); }
          /* Every cell truncates rather than wrapping: a 60-character address
             must not change the row height. */
          .vt-head > span, .vt-row > div{ min-width:0; overflow:hidden;
            text-overflow:ellipsis; white-space:nowrap; }
          .vt .num-cell, .vt-head .num-cell{ text-align:right;
            font-variant-numeric:tabular-nums; }
          .vt .em{ font-size:var(--fs-sm); font-weight:var(--fw-label); color:var(--text-1); }
          .vt .muted{ font-size:var(--fs-sm); color:var(--text-4); }
          .avatar-sm{ width:28px; height:28px; flex:0 0 28px; border-radius:var(--r-pill);
            display:grid; place-items:center; color:var(--text-3); font-weight:600;
            font-size:var(--fs-caption); background:var(--surface-inset);
            border:1px solid var(--border); }
          .name-cell{ display:flex; align-items:center; gap:var(--s3); min-width:0; }
          .name-cell > div{ min-width:0; }
          .name-cell .nm, .name-cell .sub{ overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
          .name-cell .nm{ font-size:var(--fs-sm); font-weight:var(--fw-label);
            color:var(--text-1); }
          .name-cell .sub{ font-size:var(--fs-label); color:var(--text-4); }

          /* Skeleton rows at the real row height, so nothing shifts on load. */
          .vt-skel{ height:52px; display:flex; align-items:center; padding:0 var(--s3);
            box-shadow:inset 0 -1px 0 var(--divider); }
          .vt-skel i{ display:block; height:9px; border-radius:var(--r-pill);
            background:var(--surface-inset); }
          @keyframes shimmer{ 0%,100%{ opacity:1 } 50%{ opacity:.45 } }
          .vt-skel i{ animation:shimmer 1.4s var(--ease) infinite; }

          /* ---------- Stepper ---------- */
          .stepper{ display:flex; align-items:center; gap:0; flex-wrap:wrap; row-gap:var(--s2); }
          .step{ display:flex; align-items:center; gap:var(--s2); }
          .step .n{ width:22px; height:22px; border-radius:var(--r-pill); display:grid;
            place-items:center; flex:0 0 22px; font-size:var(--fs-caption); font-weight:600;
            font-variant-numeric:tabular-nums; }
          .step.done .n{ background:var(--st-ok); color:var(--n0); }
          .step.cur .n{ background:var(--blue); color:var(--n0); }
          .step.todo .n{ background:var(--surface-inset); color:var(--text-4);
            border:1px solid var(--border); }
          .step .t{ font-size:var(--fs-sm); font-weight:var(--fw-label); }
          .step.done .t{ color:var(--text-2); } .step.cur .t{ color:var(--blue); }
          .step.todo .t{ color:var(--text-4); }
          .step-line{ flex:1; height:1px; background:var(--border); margin:0 var(--s3);
            min-width:20px; }
          .step-line.done{ background:var(--st-ok); }

          .maptag{ display:inline-flex; align-items:center; gap:var(--s2);
            background:var(--surface-inset); border:1px solid var(--border);
            border-radius:var(--r-sm); padding:var(--s1) var(--s3); font-size:var(--fs-label);
            margin:0 var(--s2) 0 0; }
          .maptag b{ color:var(--blue); font-weight:var(--fw-label); }
          .maptag span{ color:var(--text-3); }

          .note{ display:flex; gap:var(--s3); align-items:flex-start; border-radius:var(--r-sm);
            padding:var(--s3) var(--s4); font-size:var(--fs-sm); line-height:var(--lh-body);
            border:1px solid transparent; }
          .note-info{ background:var(--blue-soft); color:var(--blue); border-color:var(--blue-soft); }
          .note-ok{ background:var(--st-ok-soft); color:var(--st-ok);
            border-color:var(--st-ok-soft); }
          .note svg{ flex:0 0 16px; margin-top:2px; }

          /* ---------- Single check verdict ---------- */
          .verdict{ display:flex; align-items:center; gap:var(--s5); padding:var(--s5); }
          .verdict .ring{ width:56px; height:56px; flex:0 0 56px; }
          .verdict .em{ font-size:var(--fs-heading); font-weight:var(--fw-heading);
            color:var(--text-1); letter-spacing:var(--tr-heading); overflow-wrap:anywhere; }
          .verdict .su{ color:var(--text-3); font-size:var(--fs-sm); margin-top:var(--s1);
            line-height:var(--lh-body); }
          .brk-row{ display:flex; align-items:center; gap:var(--s3); padding:var(--s3) var(--s5);
            border-top:1px solid var(--divider); }
          .brk-row .ic{ flex:0 0 18px; display:inline-flex; }
          .brk-row .ttl{ font-size:var(--fs-sm); font-weight:var(--fw-label); color:var(--text-2);
            min-width:142px; }
          .brk-row .ds{ font-size:var(--fs-sm); color:var(--text-4); }

          .fact{ border:1px solid var(--border); border-radius:var(--r-sm);
            padding:var(--s3) var(--s4); background:var(--surface); }
          .fact .l{ font-size:var(--fs-caption); text-transform:uppercase;
            letter-spacing:var(--tr-caption); color:var(--text-4); font-weight:var(--fw-caption); }
          .fact .v{ font-size:var(--fs-heading); font-weight:var(--fw-heading);
            margin-top:var(--s2); color:var(--text-1); }
          .chip{ display:inline-block; border:1px solid var(--border); border-radius:var(--r-pill);
            padding:2px var(--s3); font-size:var(--fs-label); font-weight:400; color:var(--text-3);
            margin:0 var(--s1) var(--s1) 0; background:var(--surface-inset); }

          /* ---------- Empty state ---------- */
          .empty{ text-align:center; padding:var(--s12) var(--s5); }
          .empty .eico{ width:40px; height:40px; border-radius:var(--r-md);
            background:var(--surface-inset); border:1px solid var(--border); color:var(--text-4);
            display:grid; place-items:center; margin:0 auto var(--s4); }
          .empty h4{ font-size:var(--fs-heading); font-weight:var(--fw-heading);
            color:var(--text-1); margin:0 0 var(--s2); letter-spacing:var(--tr-heading); }
          .empty p{ color:var(--text-3); font-size:var(--fs-sm); margin:0 auto; max-width:44ch;
            line-height:var(--lh-body); }

          .mbar{ display:flex; height:6px; border-radius:var(--r-pill); overflow:hidden;
            background:var(--surface-inset); }
          .mbar i{ display:block; height:100%; }
          .lgd{ display:flex; flex-direction:column; gap:var(--s3); margin-top:var(--s2); }
          .lgd .row{ display:flex; align-items:center; gap:var(--s2); font-size:var(--fs-sm);
            color:var(--text-3); }
          .lgd .row .nm{ font-weight:400; color:var(--text-2); }
          .lgd .row .v{ margin-left:auto; font-weight:var(--fw-label); color:var(--text-1);
            font-variant-numeric:tabular-nums; }
          .lgd .row .pc{ color:var(--text-4); font-weight:400; min-width:48px; text-align:right;
            font-variant-numeric:tabular-nums; }

          .dom{ display:flex; align-items:center; gap:var(--s3); padding:var(--s3) 0;
            border-bottom:1px solid var(--divider); }
          .dom:last-child{ border-bottom:0; }
          .dom .d{ flex:1; min-width:0; }
          .dom .nm{ font-size:var(--fs-sm); font-weight:var(--fw-label); color:var(--text-1);
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .dom .track{ height:5px; border-radius:var(--r-pill); background:var(--surface-inset);
            margin-top:var(--s2); overflow:hidden; }
          .dom .track i{ display:block; height:100%; border-radius:var(--r-pill);
            background:var(--blue); }
          .dom .rt{ text-align:right; }
          .dom .rt .v{ font-size:var(--fs-sm); font-weight:var(--fw-label); color:var(--text-1);
            font-variant-numeric:tabular-nums; }
          .dom .rt .pc{ font-size:var(--fs-caption); color:var(--text-4);
            font-variant-numeric:tabular-nums; }

          /* ---------- Verification layers (process) ---------- */
          .layer{ display:flex; gap:var(--s4); padding:var(--s5) 0;
            border-bottom:1px solid var(--divider); }
          .layer:last-child{ border-bottom:0; }
          .layer .n{ width:26px; height:26px; flex:0 0 26px; border-radius:var(--r-sm);
            display:grid; place-items:center; font-weight:var(--fw-label);
            font-size:var(--fs-label); background:var(--surface-inset); color:var(--text-3);
            border:1px solid var(--border); font-variant-numeric:tabular-nums; }
          .layer .ttl{ font-size:var(--fs-body); font-weight:var(--fw-heading); color:var(--text-1);
            display:flex; align-items:center; gap:var(--s2); flex-wrap:wrap;
            letter-spacing:var(--tr-heading); }
          .layer .ttl .tg{ font-size:var(--fs-caption); font-weight:var(--fw-caption);
            letter-spacing:var(--tr-caption); padding:2px var(--s2); border-radius:var(--r-pill);
            background:var(--surface-inset); color:var(--text-4); border:1px solid var(--border);
            text-transform:uppercase; }
          .layer .dsc{ color:var(--text-3); font-size:var(--fs-sm); line-height:var(--lh-body);
            margin-top:var(--s2); max-width:74ch; }

          /* ---------- Streamlit widget layer ----------
             Only what config.toml can't express. Selectors are data-testid or
             data-baseweb attributes and never generated class names, which are
             hashed and change on upgrade. */
          .stButton>button, .stDownloadButton>button, .stFormSubmitButton>button{
            min-height:34px; font-weight:var(--fw-label); font-size:var(--fs-sm);
            transition:background var(--t-fast) var(--ease),
              border-color var(--t-fast) var(--ease); }
          .stButton>button:hover, .stDownloadButton>button:hover,
          .stFormSubmitButton>button:hover{ border-color:var(--border-hover); }
          .stButton>button:disabled, .stDownloadButton>button:disabled,
          .stFormSubmitButton>button:disabled{ color:var(--text-disabled) !important;
            background:var(--surface-inset) !important; border-color:var(--border) !important; }

          [data-baseweb="input"]:focus-within, [data-baseweb="select"]>div:focus-within,
          [data-baseweb="textarea"]:focus-within{ box-shadow:var(--ring) !important;
            border-color:var(--blue) !important; }
          /* Reserve the help/error line so an error can't push content down. */
          [data-testid="stWidgetLabel"] p{ font-size:var(--fs-label) !important;
            font-weight:var(--fw-label) !important; color:var(--text-2) !important; }

          [data-baseweb="segmented-control"]{ background:var(--surface-inset) !important;
            padding:2px !important; border-radius:var(--r-sm) !important; gap:2px; }

          /* st.container(border=True) as the one card primitive. */
          div[data-testid="stVerticalBlockBorderWrapper"]{
            border:1px solid var(--border) !important; border-radius:var(--r-md) !important;
            background:var(--surface); box-shadow:var(--e1); }
          .ccard-h{ display:flex; align-items:center; gap:var(--s3); margin:0 0 var(--s4);
            padding-bottom:var(--s3); border-bottom:1px solid var(--divider); }
          .ccard-h h4{ font-size:var(--fs-heading); font-weight:var(--fw-heading); margin:0;
            color:var(--text-1); letter-spacing:var(--tr-heading); }
          .ccard-h .cnt{ background:var(--surface-inset); color:var(--text-3);
            border-radius:var(--r-pill); font-size:var(--fs-label); font-weight:var(--fw-label);
            padding:2px var(--s2); font-variant-numeric:tabular-nums; }
          .ccard-h .sp{ margin-left:auto; display:flex; align-items:center; gap:var(--s2); }

          [data-testid="stDataFrame"]{ border-radius:var(--r-sm); }
          .stTabs [data-baseweb="tab-list"]{ gap:var(--s1); }
          .stTabs [data-baseweb="tab"]{ font-weight:var(--fw-label); font-size:var(--fs-sm); }
          [data-testid="stMetricValue"]{ font-weight:var(--fw-display);
            letter-spacing:var(--tr-display); font-variant-numeric:tabular-nums; }
          div[data-testid="stExpander"]{ border:1px solid var(--border);
            border-radius:var(--r-sm); background:var(--surface); }
          .stProgress > div > div > div{ background:var(--blue) !important; }
          .stCaption, [data-testid="stCaptionContainer"]{ color:var(--text-4) !important;
            font-size:var(--fs-label) !important; }

          /* File dropzone: distinct idle / hover / drag-over states. */
          [data-testid="stFileUploaderDropzone"]{ background:var(--surface) !important;
            border:1px dashed var(--border-hover) !important; border-radius:var(--r-md) !important;
            transition:border-color var(--t-fast) var(--ease),
              background var(--t-fast) var(--ease); }
          [data-testid="stFileUploaderDropzone"]:hover{ border-color:var(--blue) !important;
            background:var(--surface-hover) !important; }
          [data-testid="stFileUploaderDropzone"]:focus-within{ border-color:var(--blue) !important;
            box-shadow:var(--ring) !important; }

          /* ---------- Responsive: usable from 320px ---------- */
          @media (max-width:1024px){
            .block-container{ padding:var(--s6) var(--s5) var(--s12); } }
          @media (max-width:640px){
            .block-container{ padding:var(--s5) var(--s4) var(--s10); }
            .phead{ flex-direction:column; gap:var(--s3); }
            .phead .sp{ margin-left:0; justify-content:flex-start; }
            :root{ --fs-display:26px; --fs-title:21px; }
            /* The table scrolls horizontally rather than being squeezed. */
            .vt-scroll{ overflow-x:auto; -webkit-overflow-scrolling:touch; }
            .vt-head, .vt-row, .vt-skel{ min-width:640px; } }

          @media (prefers-reduced-motion:reduce){ *{ animation-duration:.001ms !important;
            animation-iteration-count:1 !important; transition-duration:.001ms !important; } }

        </style>
        """,
        unsafe_allow_html=True,
    )


inject_css()


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
def api_get(api: str, path: str, **kw):
    return requests.get(f"{api}{path}", timeout=kw.pop("timeout", 15), **kw)


def api_post(api: str, path: str, **kw):
    return requests.post(f"{api}{path}", timeout=kw.pop("timeout", 30), **kw)


def style_results(df: pd.DataFrame):
    def color_status(val):
        return f"color:{STATUS_COLORS.get(str(val), '')};font-weight:700"

    if "email_status" in df.columns:
        return df.style.map(color_status, subset=["email_status"])
    return df


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def fmt_pct(part, whole, decimals: int = 1) -> str:
    """One decimal place, always. Never a raw float.

    The app used to mix `round(x, 1)` and `round(x)` for the same metric, so the
    deliverable rate rendered as "82.4%" on one screen and "82%" on another.
    """
    try:
        whole = float(whole)
        if whole <= 0:
            return "—"
        return f"{float(part) / whole * 100:.{decimals}f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


def pct_parts(values: dict, decimals: int = 1) -> dict:
    """Percentages that sum to exactly 100 after rounding.

    Largest-remainder method: round every share down, then hand the leftover
    units to the largest remainders. Without this the four verdict shares can
    display as 99.9% or 100.1% of a total the user can see for themselves.
    """
    total = sum(max(0, float(v or 0)) for v in values.values())
    if total <= 0:
        return {k: 0.0 for k in values}
    scale = 10 ** decimals
    exact = {k: max(0, float(v or 0)) / total * 100 * scale for k, v in values.items()}
    floors = {k: int(v) for k, v in exact.items()}
    leftover = round(100 * scale) - sum(floors.values())
    order = sorted(exact, key=lambda k: exact[k] - floors[k], reverse=True)
    for k in order[:max(0, leftover)]:
        floors[k] += 1
    return {k: v / scale for k, v in floors.items()}


# --------------------------------------------------------------------------- #
# Reusable HTML components
# --------------------------------------------------------------------------- #
def sparkline(values: list[float], color: str = BLUE, w: int = 320, h: int = 48) -> str:
    """A quiet line sparkline as inline SVG, scaled to the data.

    Returns "" for a series with fewer than two real points rather than
    inventing a shape — a placeholder curve reads as data the user doesn't have.
    """
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    step = w / (len(vals) - 1)
    pad = 5
    pts = [(i * step, h - pad - ((v - lo) / rng) * (h - 2 * pad)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"M0,{h} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in pts) + f" L{w},{h} Z"
    ex, ey = pts[-1]
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" '
        f'style="display:block" aria-hidden="true">'
        f'<path d="{area}" fill="{color}" fill-opacity="0.07"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="2.5" fill="{color}"/></svg>'
    )


def status_tag(status: str, sub: bool = True) -> str:
    """The one status indicator. Dot + label; label carries the meaning.

    `disposable` / `spam_trap` render as Undeliverable with their specificity as
    grey secondary text, so the four verdict colours stay 1:1 with four labels.
    """
    s = str(status)
    v = verdict(s)
    d = PRIMARY[v]
    reason = SUB_REASON.get(s) if sub else None
    tail = f'<span class="sreason">{_e(reason)}</span>' if reason else ""
    return (f'<span class="stag" style="color:{d["c"]}">'
            f'<span class="d" style="background:{d["c"]}"></span>{_e(d["label"])}</span>'
            f'{" " + tail if tail else ""}')


def score_bar(score, status: str | None = None) -> str:
    try:
        pct = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        pct = 0
    color = status_color(status) if status else BLUE
    return (f'<span class="sbar"><span class="track">'
            f'<i style="width:{pct}%;background:{color}"></i></span>'
            f'<span class="n num">{pct}</span></span>')


def page_header(title: str, sub: str, right: str = "") -> None:
    st.markdown(
        f'<div class="phead"><div><h1>{_e(title)}</h1>'
        f'<div class="sub">{_e(sub)}</div></div>'
        f'<div class="sp">{right}</div></div>',
        unsafe_allow_html=True,
    )


@contextmanager
def card(title: str | None = None, count: str = "", right_html: str = ""):
    """A native bordered container styled as a v3 card, so real Streamlit
    widgets/charts render *inside* it (raw-HTML <div> wrappers can't do that)."""
    box = st.container(border=True)
    with box:
        if title is not None:
            cnt = f'<span class="cnt">{_e(count)}</span>' if count else ""
            sp = f'<span class="sp">{right_html}</span>' if right_html else ""
            st.markdown(f'<div class="ccard-h"><h4>{_e(title)}</h4>{cnt}{sp}</div>',
                        unsafe_allow_html=True)
        yield box


def stat_card(label: str, value: str, foot: str = "", spark_vals=None, delta: str = "",
              delta_up: bool | None = True, accent: str | None = None,
              icon_name: str = "") -> str:
    """A KPI tile.

    `delta` is only ever a *change*, never a share of total — pass share-of-total
    through `foot`. `delta_up=None` renders the neutral style for a flat or
    non-directional value, so nothing shows a green up-arrow it hasn't earned.
    """
    cls = "stat brd" if accent else "stat"
    style = f' style="border-left-color:{accent}"' if accent else ""
    gl = f'<span class="g">{icon(icon_name, 15)}</span>' if icon_name else ""
    if delta:
        tone = "flat" if delta_up is None else ("up" if delta_up else "dn")
        arrow = "" if delta_up is None else icon("trendup" if delta_up else "trenddown", 12)
        delta_html = f'<span class="d {tone}">{arrow}{_e(delta)}</span>'
    else:
        delta_html = ""
    spark_svg = sparkline(spark_vals) if spark_vals else ""
    spark_html = f'<div class="spark">{spark_svg}</div>' if spark_svg else ""
    foot_html = f'<div class="foot">{foot}</div>' if foot else ""
    return (f'<div class="{cls}"{style}><div class="l">{_e(label)}{gl}</div>'
            f'<div class="v">{value}{delta_html}</div>{foot_html}{spark_html}</div>')


def mini_bar(counts: dict) -> str:
    """Stacked share bar. Segment colours are exactly the four status colours."""
    totals = verdict_totals(counts)
    total = sum(totals.values())
    if not total:
        return '<div class="mbar"></div>'
    segs = "".join(
        f'<i style="width:{totals[v] / total * 100:.1f}%;background:{PRIMARY[v]["c"]}" '
        f'title="{PRIMARY[v]["label"]}: {fmt_int(totals[v])}"></i>'
        for v in VERDICT_ORDER if totals[v]
    )
    return f'<div class="mbar">{segs}</div>'


def _avatar_color(seed: str) -> str:
    """Neutral ramp only.

    This used to return one of eight hues (purple, pink, cyan…) that existed
    nowhere in the theme, and rendered as dots directly beside status dots — a
    second dot system that carried no meaning.
    """
    return (N100, N150, N200)[sum(ord(c) for c in seed) % 3]


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def verdict_totals(status_totals: dict) -> dict:
    """Collapse the six engine statuses onto the four primary verdicts.

    Every chart and legend consumes this, so a segment can never exist without a
    matching legend row — the donut used to draw all six while the legend listed
    four, leaving two same-coloured slices unexplained and the percentages
    summing to 87.5%.
    """
    out = {v: 0 for v in VERDICT_ORDER}
    for status, n in status_totals.items():
        if status in VERDICT_OF:
            out[verdict(status)] += int(n or 0)
    return out


def donut_chart(status_totals: dict, height: int = 200):
    totals = verdict_totals(status_totals)
    df = pd.DataFrame({
        "verdict": VERDICT_ORDER,
        "status": [PRIMARY[v]["label"] for v in VERDICT_ORDER],
        "count": [totals[v] for v in VERDICT_ORDER],
    })
    df = df[df["count"] > 0]
    if df.empty:
        return None
    total = int(df["count"].sum())
    arc = (
        alt.Chart(df)
        # Separator matches the card surface exactly. A near-white stroke used to
        # leave a visible seam between segments.
        .mark_arc(innerRadius=58, cornerRadius=2, stroke=N0, strokeWidth=2)
        .encode(
            theta=alt.Theta("count:Q", stack=True),
            color=alt.Color(
                "status:N",
                scale=alt.Scale(
                    domain=[PRIMARY[v]["label"] for v in VERDICT_ORDER],
                    range=[PRIMARY[v]["c"] for v in VERDICT_ORDER],
                ),
                legend=None,
            ),
            order=alt.Order("count:Q", sort="descending"),
            tooltip=[alt.Tooltip("status:N", title="Status"),
                     alt.Tooltip("count:Q", title="Addresses", format=",")],
        )
    )
    big = alt.Chart(pd.DataFrame({"t": [fmt_int(total)]})).mark_text(
        fontSize=22, fontWeight=600, color=N900, dy=-7).encode(text="t:N")
    small = alt.Chart(pd.DataFrame({"t": ["addresses"]})).mark_text(
        fontSize=11, color=AX_LABEL, dy=13).encode(text="t:N")
    return (arc + big + small).properties(height=height)


def _axis_x(**kw):
    """Shared x-axis: one neutral ramp for every chart in the app."""
    base = dict(grid=False, labelColor=AX_LABEL, domainColor=AX_LINE, tickColor=AX_LINE,
                labelFontSize=11)
    base.update(kw)
    return alt.Axis(**base)


def _axis_y(**kw):
    base = dict(grid=True, gridColor=AX_GRID, labelColor=AX_LABEL, domainOpacity=0,
                tickOpacity=0, labelFontSize=11)
    base.update(kw)
    return alt.Axis(**base)


def volume_area(by_date: dict, height: int = 240):
    rows = [{"date": d, "verified": v["verified"]} for d, v in sorted(by_date.items())]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    base = alt.Chart(df).encode(
        x=alt.X("date:T", title=None, axis=_axis_x(format="%b %d")),
        y=alt.Y("verified:Q", title=None, axis=_axis_y(tickMinStep=1)),
        tooltip=[alt.Tooltip("date:T", title="Date"),
                 alt.Tooltip("verified:Q", title="Addresses", format=",")],
    )
    # Flat low-opacity fill instead of a three-stop gradient.
    area = base.mark_area(interpolate="monotone", line={"color": BLUE, "strokeWidth": 2},
                          color=BLUE, opacity=0.08)
    pts = base.mark_point(size=36, color=BLUE, filled=True, opacity=1, stroke=N0, strokeWidth=1.5)
    return (area + pts).properties(height=height)


def hbar_dist(counts: dict, height: int = 190):
    totals = verdict_totals(counts)
    labels = [PRIMARY[v]["label"] for v in VERDICT_ORDER]
    df = pd.DataFrame({
        "verdict": VERDICT_ORDER,
        "status": labels,
        "count": [totals[v] for v in VERDICT_ORDER],
    })
    return (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=2, height=14)
        .encode(
            x=alt.X("count:Q", title=None, axis=_axis_y(tickMinStep=1, grid=True)),
            y=alt.Y("status:N", sort=labels, title=None,
                    axis=alt.Axis(labelColor=N800, domainOpacity=0, tickOpacity=0,
                                  labelFontSize=12)),
            color=alt.Color(
                "verdict:N",
                scale=alt.Scale(domain=VERDICT_ORDER,
                                range=[PRIMARY[v]["c"] for v in VERDICT_ORDER]),
                legend=None,
            ),
            tooltip=[alt.Tooltip("status:N", title="Status"),
                     alt.Tooltip("count:Q", title="Addresses", format=",")],
        )
        .properties(height=height)
    )


def weekday_bar(by_date: dict, height: int = 210):
    rows = [{"date": d, "verified": v["verified"]} for d, v in by_date.items()]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["wd"] = df["date"].dt.dayofweek
    agg = df.groupby("wd")["verified"].sum().reindex(range(7), fill_value=0).reset_index()
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    agg["day"] = agg["wd"].map(dict(enumerate(names)))
    peak = agg["verified"].idxmax() if agg["verified"].sum() else -1
    agg["hl"] = agg.index == peak
    return (
        alt.Chart(agg)
        .mark_bar(cornerRadius=2, width=32)
        .encode(
            x=alt.X("day:N", sort=names, title=None,
                    axis=alt.Axis(labelColor=AX_LABEL, domainColor=AX_LINE, tickOpacity=0,
                                  labelAngle=0, labelFontSize=11)),
            y=alt.Y("verified:Q", title=None, axis=None),
            # Accent marks the peak; every other bar is neutral. Two tones, not
            # a second hue.
            color=alt.Color("hl:N", scale=alt.Scale(domain=[True, False], range=[BLUE, N300]),
                            legend=None),
            tooltip=[alt.Tooltip("day:N", title="Weekday"),
                     alt.Tooltip("verified:Q", title="Addresses", format=",")],
        )
        .properties(height=height)
    )


# --------------------------------------------------------------------------- #
# Contacts data layer (aggregates every run's cached cleaned.csv into one frame)
# --------------------------------------------------------------------------- #
CONTACT_COLS = [
    "email", "normalized_email", "status", "sub_status", "score",
    "is_disposable", "is_role", "is_free", "is_catch_all", "mx_found",
    "source", "run_id", "verified_at",
]
_BOOL_TRUE = {"true", "1", "yes", "y", "t"}


def _as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(_BOOL_TRUE)


def store_signature() -> str:
    """Cache key that changes whenever any run is added, completed or deleted."""
    return "|".join(
        f'{r["id"]}:{r.get("status")}:{r.get("completed_at") or ""}'
        for r in store.list_runs()
    )


@st.cache_data(show_spinner=False)
def load_contacts(signature: str) -> pd.DataFrame:
    """One de-duplicated row per verified mailbox across every completed run."""
    frames = []
    for r in store.list_runs():
        if r.get("status") != "completed":
            continue
        data = store.output_bytes(r["id"], "cleaned")
        if not data:
            continue
        try:
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
        except Exception:
            continue
        email_col = (r.get("mapping") or {}).get("email")
        if email_col in df.columns:
            emails = df[email_col]
        elif "normalized_email" in df.columns:
            emails = df["normalized_email"]
        else:
            continue
        norm = df["normalized_email"] if "normalized_email" in df.columns else emails
        when = (r.get("completed_at") or r.get("created_at") or "").replace("T", " ")[:16]
        frames.append(pd.DataFrame({
            "email": emails,
            "normalized_email": norm,
            "status": df.get("email_status", ""),
            "sub_status": df.get("sub_status", ""),
            "score": pd.to_numeric(df.get("score", 0), errors="coerce").fillna(0).astype(int),
            "is_disposable": _as_bool(df.get("is_disposable", "")),
            "is_role": _as_bool(df.get("is_role", "")),
            "is_free": _as_bool(df.get("is_free", "")),
            "is_catch_all": _as_bool(df.get("is_catch_all", "")),
            "mx_found": _as_bool(df.get("mx_found", "")),
            "source": r["filename"],
            "run_id": r["id"],
            "verified_at": when,
        }))
    if not frames:
        return pd.DataFrame(columns=CONTACT_COLS)
    allc = pd.concat(frames, ignore_index=True)
    allc["_key"] = allc["normalized_email"].where(allc["normalized_email"] != "", allc["email"])
    allc = (
        allc.sort_values("verified_at")
        .drop_duplicates("_key", keep="last")
        .sort_values("verified_at", ascending=False)
        .drop(columns="_key")
        .reset_index(drop=True)
    )
    return allc


def resample_activity(by_date: dict, grain: str) -> pd.DataFrame:
    """Roll the per-day verified/valid/invalid timeline up to day/week/month."""
    rows = [{"date": d, **v} for d, v in sorted(by_date.items())]
    if not rows:
        return pd.DataFrame(columns=["date", "verified", "valid", "invalid"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    rule = {"Daily": "D", "Weekly": "W-MON", "Monthly": "MS"}[grain]
    out = df.set_index("date").resample(rule).sum(numeric_only=True).reset_index()
    return out[out[["verified", "valid", "invalid"]].sum(axis=1) > 0]


# --------------------------------------------------------------------------- #
# Health + routing state
# --------------------------------------------------------------------------- #
api = st.session_state.get("api_base", DEFAULT_API)
api_ok, health = False, {}
try:
    health = api_get(api, "/health", timeout=3).json()
    api_ok = True
except Exception:
    api_ok = False

agg = store.aggregate()
contacts_df = load_contacts(store_signature())
agg["contacts_total"] = len(contacts_df)
ongoing_n = len(agg["ongoing"])
page = st.query_params.get("page", "dashboard")


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            f'<div class="brand"><span class="mark">{icon("checkcircle", 17, 2.2)}</span>'
            f'<span class="brand-word">valid<b>row</b></span></div>'
            f'<div class="searchbox">{icon("search", 16)}<span class="ph">Search</span>'
            f'<span class="kbd">⌘K</span></div>',
            unsafe_allow_html=True,
        )

        runs_badge = str(agg["runs_total"]) if agg["runs_total"] else ""
        contacts_badge = fmt_int(agg["contacts_total"]) if agg["contacts_total"] else ""
        val_badge = str(ongoing_n) if ongoing_n else ""
        nav_sections = [
            (None, [
                ("dashboard", "Home", "home", "", "home"),
                ("dashboard", "Dashboard", "grid", "", "dashboard"),
                ("validate", "Validate list", "bolt", val_badge, "validate"),
                ("single", "Single check", "target", "", "single"),
            ]),
            ("Workspace", [
                ("contacts", "Contacts", "users", contacts_badge, "contacts"),
                ("analytics", "Analytics", "bar", "", "analytics"),
                ("export", "Exports", "download", "", "export"),
                ("history", "History", "clock", runs_badge, "history"),
            ]),
        ]
        nav_html = ""
        for section, items in nav_sections:
            if section:
                nav_html += f'<div class="side-label">{section}</div>'
            for key, label, ic, badge_n, act_key in items:
                # "Home" is a design alias for Dashboard; Dashboard owns the active state.
                active = "active" if (page == key and (act_key != "home")) else ""
                if act_key == "home" and page not in {
                    "dashboard", "validate", "single", "contacts", "analytics", "export", "history",
                    "process", "settings",
                }:
                    active = "active"
                b = f'<span class="badge-n">{badge_n}</span>' if badge_n else ""
                nav_html += (
                    f'<a class="nav {active}" target="_self" href="?page={key}">'
                    f'{icon(ic, 20)}<span>{label}</span>{b}</a>'
                )
        st.markdown(nav_html, unsafe_allow_html=True)

        st.markdown('<div style="height:calc(100vh - 640px);min-height:8px"></div>', unsafe_allow_html=True)

        # Footer nav — Support routes to "How it works", Settings to connection.
        st.markdown(
            f'<a class="nav {"active" if page == "process" else ""}" target="_self" href="?page=process">'
            f'{icon("life", 20)}<span>Support</span></a>'
            f'<a class="nav {"active" if page == "settings" else ""}" target="_self" href="?page=settings">'
            f'{icon("settings", 20)}<span>Settings</span></a>',
            unsafe_allow_html=True,
        )

        live = "dot-live" if api_ok else ""
        color = PRIMARY["deliverable"]["c"] if api_ok else PRIMARY["undeliverable"]["c"]
        st.markdown(
            f'<div class="side-foot"><div class="avatar">PK</div>'
            f'<div style="flex:1;min-width:0"><div class="nm">Pranav Kumar</div>'
            f'<div class="em">pranav@validrow.io</div></div>'
            f'<span class="dot {live}" title="API {"online" if api_ok else "offline"}" '
            f'style="background:{color}"></span>'
            f'<span class="lo">{icon("logout", 18)}</span></div>',
            unsafe_allow_html=True,
        )


render_sidebar()


# --------------------------------------------------------------------------- #
# Small shared bits
# --------------------------------------------------------------------------- #
def link_btn(label: str, href: str, ic: str = "", primary: bool = False) -> str:
    cls = "chip-btn btn-primary" if primary else "chip-btn"
    return (f'<a class="{cls}" target="_self" href="{href}">{icon(ic, 16) if ic else ""}'
            f'<span>{_e(label)}</span></a>')


def empty_state(ic: str, title: str, body: str, cta_label: str = "", cta_href: str = "") -> None:
    cta = (f'<div style="margin-top:18px">{link_btn(cta_label, cta_href, "arrow", primary=True)}</div>'
           if cta_label else "")
    st.markdown(
        f'<div class="card"><div class="empty"><div class="eico">{icon(ic, 26)}</div>'
        f'<h4>{_e(title)}</h4><p>{_e(body)}</p>{cta}</div></div>',
        unsafe_allow_html=True,
    )


def render_results_table(rows: list[dict], cols_spec: list[tuple[str, str]]) -> None:
    """v3 email/results table. cols_spec = [(header, grid_fraction), ...]."""
    grid = " ".join(f for _, f in cols_spec)
    head = "".join(f"<span>{_e(h)}</span>" for h, _ in cols_spec)
    body = ""
    for r in rows:
        cells = "".join(f"<div>{r['cells'][i]}</div>" for i in range(len(cols_spec)))
        href = r.get("href")
        tag = "a" if href else "div"
        hattr = f' href="{href}" target="_self"' if href else ""
        body += f'<{tag} class="vt-row" style="grid-template-columns:{grid}"{hattr}>{cells}</{tag}>'
    st.markdown(
        f'<div class="vt"><div class="vt-head" style="grid-template-columns:{grid}">{head}</div>{body}</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Page: Dashboard
# --------------------------------------------------------------------------- #
def page_dashboard() -> None:
    page_header(
        "Dashboard",
        "Track deliverability and validation activity across all your lists.",
        right=link_btn("New validation", "?page=validate", "plus", primary=True),
    )

    st_ = agg["status_totals"]
    total_valid = st_["valid"]
    verified = agg["addresses_verified"]
    valid_rate = round(agg["avg_valid_rate"] * 100, 1)
    avg_score = round(contacts_df["score"].mean()) if len(contacts_df) else 0

    if agg["runs_total"] == 0:
        empty_state("mail", "No validations yet",
                    "Upload your first CSV to start cleaning lists. Your stats, volume trend and "
                    "recent results will show up here.",
                    "Go to Validate", "?page=validate")
        return

    # sparkline series from the daily volume timeline
    series = [v["verified"] for _, v in sorted(agg["by_date"].items())] or [0]
    valid_series = [v["valid"] for _, v in sorted(agg["by_date"].items())] or [0]

    st.markdown(
        '<div class="cards c3">'
        + stat_card("Total validated", fmt_int(verified), spark_vals=series,
                    foot=f'{fmt_int(total_valid)} deliverable')
        + stat_card("Deliverable rate", f'{valid_rate}%', spark_vals=valid_series or series)
        + stat_card("Avg. quality score", str(avg_score), spark_vals=series)
        + "</div>",
        unsafe_allow_html=True,
    )

    # Validation volume
    vc = volume_area(agg["by_date"])
    with card("Validation volume", right_html='<span class="tag">addresses verified / day</span>'):
        if vc is not None:
            st.altair_chart(vc, use_container_width=True)
        else:
            st.caption("Not enough data yet — validate a few lists to see the trend.")

    st.write("")

    # Recent results (recent verified contacts)
    with card("Recent results", count=f'{fmt_int(len(contacts_df))} verified',
              right_html=link_btn("View all", "?page=contacts", "arrow")):
        recent = contacts_df.head(7)
        if recent.empty:
            st.caption("No verified addresses yet.")
        else:
            rows = []
            for _, r in recent.iterrows():
                seg = r["source"].rsplit(".", 1)[0].replace("_", " ").title()
                rows.append({"cells": [
                    f'<span class="em">{_e(r["email"])}</span>',
                    status_tag(r["status"]),
                    score_bar(r["score"], r["status"]),
                    f'<span class="tag"><span class="dot" style="background:{_avatar_color(seg)}"></span>'
                    f'{_e(seg)}</span>',
                    f'<span class="muted">{_e(r["verified_at"][5:10] or "—")}</span>',
                ]})
            render_results_table(rows, [
                ("Email", "2.6fr"), ("Status", "1fr"), ("Quality score", "1.6fr"),
                ("Segment", "1.3fr"), ("Checked", "0.8fr"),
            ])

    if agg["ongoing"]:
        st.write("")
        st.markdown('<div class="side-label" style="margin-left:2px">Ongoing validations</div>',
                    unsafe_allow_html=True)
        for r in agg["ongoing"][:4]:
            c = r.get("counts", {})
            done = sum(c.get(k, 0) for k in STATUS_ORDER)
            total = c.get("unique_emails") or c.get("total_rows") or 0
            pctv = round(done / total * 100) if total else 0
            st.markdown(
                f'<a class="card" style="display:flex;align-items:center;gap:14px;padding:14px 18px;'
                f'text-decoration:none;color:inherit;margin-bottom:8px" target="_self" '
                f'href="?page=validate&resume={r["id"]}">'
                f'<span style="color:var(--blue)">{icon("clock", 20)}</span>'
                f'<div><div class="em">{_e(r["filename"])}</div>'
                f'<div class="muted">verifying… {done}/{total}</div></div>'
                f'<span class="sp" style="margin-left:auto"></span>'
                f'<span class="pill p-warn">{pctv}%</span></a>',
                unsafe_allow_html=True,
            )


# --------------------------------------------------------------------------- #
# Page: Validate
# --------------------------------------------------------------------------- #
def stepper(active: int) -> str:
    labels = ["Upload", "Map columns", "Validate", "Results"]
    parts = []
    for i, lab in enumerate(labels, start=1):
        cls = "done" if i < active else ("cur" if i == active else "todo")
        glyph = icon("check", 15) if i < active else str(i)
        parts.append(f'<div class="step {cls}"><span class="n">{glyph}</span>'
                     f'<span class="t">{lab}</span></div>')
        if i < len(labels):
            parts.append(f'<div class="step-line {"done" if i < active else ""}"></div>')
    return f'<div class="card"><div class="card-b"><div class="stepper">{"".join(parts)}</div></div></div>'


def page_validate() -> None:
    right = link_btn("New validation", "?page=validate", "plus", primary=True)
    page_header("Validate a list",
                "Upload a CSV, map the email column, and get a cleaned list back.", right=right)

    resume_id = st.query_params.get("resume")
    if resume_id:
        rec = store.get(resume_id)
        if rec and rec.get("job_id"):
            st.session_state.job_id = rec["job_id"]
            st.session_state.active_run = rec["id"]
        st.query_params.pop("resume", None)

    running = bool(st.session_state.get("job_id"))
    has_upload = st.session_state.get("uploaded_name") is not None
    active_step = 4 if running else (2 if has_upload else 1)
    st.markdown(stepper(active_step), unsafe_allow_html=True)
    st.write("")

    upload = st.file_uploader("Upload a CSV (emails + any other columns)", type=["csv"])

    if upload is not None:
        if st.session_state.get("uploaded_name") != upload.name:
            with st.spinner("Uploading & detecting columns…"):
                r = api_post(api, "/v1/files",
                             files={"file": (upload.name, upload.getvalue(), "text/csv")})
                r.raise_for_status()
                data = r.json()
            st.session_state.file_id = data["file_id"]
            st.session_state.detection = data["detection"]
            st.session_state.uploaded_name = upload.name
            st.session_state.pop("job_id", None)

        det = st.session_state.detection
        cols = det["columns"]
        size = upload.size or 0
        size_str = f"{size / 1_048_576:.1f} MB" if size > 1_048_576 else f"{size / 1024:.1f} KB"
        st.markdown(
            f'<div class="card"><div class="card-b" style="display:flex;align-items:center;gap:14px">'
            f'<span style="color:var(--blue)">{icon("file", 26)}</span>'
            f'<div><div class="em">{_e(upload.name)} '
            f'<span class="pill p-ok" style="margin-left:6px">READY</span></div>'
            f'<div class="muted">{len(cols)} columns · delimiter '
            f'“{_e(det.get("delimiter", ","))}” · {size_str}</div></div></div></div>',
            unsafe_allow_html=True,
        )

        st.markdown("##### Column preview")
        if det["sample_rows"]:
            st.dataframe(pd.DataFrame(det["sample_rows"]), width="stretch", hide_index=True)

        st.markdown("##### Map your columns")
        c1, c2, c3 = st.columns(3)
        email_idx = cols.index(det["guessed_email"]) if det.get("guessed_email") in cols else 0
        email_col = c1.selectbox("Email column *", cols, index=email_idx,
                                 help="Required — the address to verify.")
        name_opts = ["(none)"] + cols
        fi = name_opts.index(det["guessed_first_name"]) if det.get("guessed_first_name") in cols else 0
        li = name_opts.index(det["guessed_last_name"]) if det.get("guessed_last_name") in cols else 0
        first_col = c2.selectbox("First name", name_opts, index=fi,
                                 help="Optional — carried through to output.")
        last_col = c3.selectbox("Last name", name_opts, index=li,
                                help="Optional — carried through to output.")

        st.write("")
        if st.button("Validate list", type="primary", icon=":material/bolt:"):
            mapping = {"email": email_col}
            if first_col != "(none)":
                mapping["first_name"] = first_col
            if last_col != "(none)":
                mapping["last_name"] = last_col
            r = api_post(api, "/v1/jobs",
                         json={"file_id": st.session_state.file_id, "mapping": mapping})
            r.raise_for_status()
            jid = r.json()["id"]
            st.session_state.job_id = jid
            st.session_state.active_run = store.record_started(jid, upload.name, mapping)
            st.rerun()

    if st.session_state.get("job_id"):
        poll_and_render_job()


def poll_and_render_job() -> None:
    job_id = st.session_state.job_id
    resp = api_get(api, f"/v1/jobs/{job_id}")
    if resp.status_code != 200:
        run = st.session_state.get("active_run")
        if run:
            store.update(run, status="failed")
        st.info("That job is no longer available (the API may have restarted). Upload again.")
        st.session_state.pop("job_id", None)
        return
    job = resp.json()
    c = job["counts"]
    verified = sum(c[k] for k in STATUS_ORDER)
    run = st.session_state.get("active_run")

    if job["status"] in ("pending", "processing"):
        if run:
            store.update(run, counts=c, status="processing")
        total = c["unique_emails"] or c["total_rows"] or 1
        if verified == 0:
            phase = "Resolving domains (DNS)…" if c["unique_emails"] else "Reading & de-duplicating…"
            frac = 0.05
        else:
            phase = f"Verifying mailboxes… {verified} / {c['unique_emails']}"
            frac = min(0.99, verified / total)
        st.progress(frac, text=phase)
        st.caption(
            f"{c['total_rows']} rows · {c['unique_emails']} unique · {c['duplicates']} duplicates. "
            "This page refreshes automatically."
        )
        time.sleep(1.0)
        st.rerun()
    elif job["status"] == "failed":
        if run:
            store.update(run, status="failed")
        st.error(f"Job failed: {job.get('error')}")
    else:
        outputs = {}
        for seg in ("cleaned", "valid", "removed"):
            try:
                outputs[seg] = api_get(api, f"/v1/jobs/{job_id}/download",
                                       params={"segment": seg}).content
            except Exception:
                outputs[seg] = b""
        if run and store.get(run) and store.get(run).get("status") != "completed":
            store.complete(run, c, outputs)
        render_results(c, outputs)


REASON_TEXT = {
    "valid": "Mailbox exists", "risky": "Catch-all / role", "invalid": "Undeliverable",
    "unknown": "No answer", "disposable": "Disposable domain", "spam_trap": "Known spam trap",
}


def render_results(c: dict, outputs: dict) -> None:
    invalid_total = store.invalid_total(c)
    unique = c["unique_emails"] or 1

    if outputs.get("cleaned"):
        st.markdown(
            '<div class="card"><div class="card-b" style="display:flex;align-items:center;gap:12px">'
            f'{icon("checkcircle", 22, cls="")}'
            '<div style="color:var(--st-ok)"></div>'
            f'<div class="em" style="color:var(--text-1)">Validation complete · saved to history</div>'
            f'<span class="sp" style="margin-left:auto"></span></div></div>',
            unsafe_allow_html=True,
        )
        st.write("")

    def vcard(label, val, sub, status):
        d = STATUS[status]
        pct = round(val / unique * 100)
        return (f'<div class="stat brd" style="border-left-color:{d["dot"]}">'
                f'<div class="l"><span class="dot" style="background:{d["dot"]}"></span>{label}</div>'
                f'<div class="v">{fmt_int(val)}'
                f'<span class="d" style="color:var(--text-4);font-weight:600">{pct}%</span></div>'
                f'<div class="foot">{sub}</div></div>')

    st.markdown(
        '<div class="cards c4">'
        + vcard("Valid", c["valid"], "deliverable", "valid")
        + vcard("Risky", c["risky"], "catch-all / role", "risky")
        + vcard("Invalid", invalid_total, "undeliverable", "invalid")
        + vcard("Unknown", c["unknown"], "no answer", "unknown")
        + "</div>",
        unsafe_allow_html=True,
    )

    # Downloads
    dcols = st.columns([1, 1, 1, 2.4])
    if outputs.get("cleaned"):
        dcols[0].download_button("Cleaned CSV", outputs["cleaned"], "cleaned.csv", "text/csv",
                                 width="stretch", type="primary", icon=":material/download:")
    if outputs.get("valid"):
        dcols[1].download_button("Valid only", outputs["valid"], "valid.csv", "text/csv",
                                 width="stretch", icon=":material/download:")
    if outputs.get("removed"):
        dcols[2].download_button("Removed", outputs["removed"], "removed.csv", "text/csv",
                                 width="stretch", icon=":material/download:")

    st.write("")
    with card("Results", count=f'{fmt_int(c["total_rows"])} rows'):
        if outputs.get("cleaned"):
            df = pd.read_csv(io.BytesIO(outputs["cleaned"]))
            email_col = st.session_state.get("detection", {}).get("guessed_email")
            rows = _results_rows_from_df(df, email_col)
            if rows:
                render_results_table(rows, [
                    ("Email", "2.4fr"), ("Status", "1fr"), ("Reason", "1.6fr"), ("Score", "1.4fr"),
                ])
            st.caption(f"Showing {min(len(df), len(rows))} of {len(df)} rows.")


def _results_rows_from_df(df: pd.DataFrame, email_col: str | None, limit: int = 60) -> list[dict]:
    ecol = email_col if email_col in df.columns else (
        "normalized_email" if "normalized_email" in df.columns else df.columns[0])
    rows = []
    for _, r in df.head(limit).iterrows():
        status = str(r.get("email_status", "unknown"))
        sub = str(r.get("sub_status") or "") or REASON_TEXT.get(status, "—")
        corr = r.get("suggested_correction")
        if isinstance(corr, str) and corr:
            sub = f'Typo → {corr}'
        score = r.get("score", 0)
        rows.append({"cells": [
            f'<span class="em">{_e(r.get(ecol, ""))}</span>',
            status_tag(status),
            f'<span class="muted">{_e(sub)}</span>',
            score_bar(score, status),
        ]})
    return rows


# --------------------------------------------------------------------------- #
# Page: Single
# --------------------------------------------------------------------------- #
def page_single() -> None:
    page_header("Single check", "Verify one email address in real time, layer by layer.",
                right=link_btn("API docs", f"{api}/docs", "code"))

    left, right = st.columns([0.42, 0.58], gap="large")

    with left:
        with card("Check an address"):
            email = st.text_input("Email address", "john@gmial.com", label_visibility="collapsed",
                                  placeholder="name@company.com")
            if st.button("Verify address", type="primary", width="stretch", icon=":material/bolt:"):
                with st.spinner("Checking…"):
                    v = api_post(api, "/v1/verify", json={"email": email}, timeout=15).json()
                st.session_state.single_verdict = v
                st.session_state.setdefault("recent_checks", [])
                st.session_state.recent_checks = (
                    [{"email": v["email"], "status": v["status"]}]
                    + [c for c in st.session_state.recent_checks if c["email"] != v["email"]]
                )[:5]
            st.caption("Uses the full 7-layer pipeline incl. in-house SMTP probe. ~1 credit per check.")

        rc = st.session_state.get("recent_checks", [])
        if rc:
            body = "".join(
                f'<div class="brk-row" style="padding:11px 0;border-top:1px solid var(--divider)">'
                f'<span class="em" style="flex:1">{_e(c["email"])}</span>{status_tag(c["status"])}</div>'
                for c in rc
            )
            st.markdown(f'<div class="card" style="margin-top:16px"><div class="card-h">'
                        f'<h4>Recent checks</h4>'
                        f'</div><div class="card-b" style="padding-top:0">{body}</div></div>',
                        unsafe_allow_html=True)

    v = st.session_state.get("single_verdict")
    with right:
        if not v:
            st.markdown(
                f'<div class="card"><div class="empty"><div class="eico">{icon("mail", 26)}</div>'
                f'<h4>No address checked yet</h4>'
                '<p>Enter an email and hit Verify. Try a typo like '
                '<code>john@gmial.com</code> to see the correction in action.</p></div></div>',
                unsafe_allow_html=True,
            )
            return
        _render_single_verdict(v)


def _ring(score: int, color: str) -> str:
    pct = max(0, min(100, int(score)))
    r = 24
    circ = 2 * 3.14159 * r
    off = circ * (1 - pct / 100)
    return (
        f'<svg width="56" height="56" viewBox="0 0 56 56">'
        f'<circle cx="28" cy="28" r="{r}" fill="none" stroke="{N100}" stroke-width="4"/>'
        f'<circle cx="28" cy="28" r="{r}" fill="none" stroke="{color}" stroke-width="4" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{off:.1f}" '
        f'transform="rotate(-90 28 28)"/>'
        f'<text x="28" y="28" text-anchor="middle" dominant-baseline="central" '
        f'font-size="15" font-weight="640" fill="{N900}" '
        f'style="font-variant-numeric:tabular-nums">{pct}</text></svg>'
    )


def _render_single_verdict(v: dict) -> None:
    status = v["status"]
    d = STATUS.get(status, STATUS["unknown"])

    breakdown = [
        ("Syntax", v.get("mx_found") is not None,
         "Valid RFC 5322 format" if "@" in v.get("email", "") else "Malformed address"),
        ("DNS / MX records", bool(v.get("mx_found")),
         "Mail servers found" if v.get("mx_found") else "No MX records"),
        ("SMTP mailbox", status == "valid",
         "Mailbox exists and accepts mail" if status == "valid"
         else ("Could not confirm mailbox" if status in ("unknown", "risky") else "Mailbox rejected")),
        ("Catch-all domain", not v.get("is_catch_all"),
         "Domain accepts all addresses" if v.get("is_catch_all")
         else "Not a catch-all — verdict is confident"),
        ("Disposable", not v.get("is_disposable"),
         "Disposable / burner domain" if v.get("is_disposable") else "Not a disposable provider"),
        ("Role account", not v.get("is_role"),
         "Role address (info@, support@…)" if v.get("is_role") else "Personal mailbox, not role-based"),
        ("Free provider", not v.get("is_free"),
         f'Free consumer provider ({v.get("domain") or "—"})' if v.get("is_free")
         else f'Business domain ({v.get("domain") or "—"})'),
    ]

    subtitle = {
        "valid": "Deliverable · mailbox confirmed by SMTP probe · safe to send",
        "risky": "Deliverable but lower confidence · send with care",
        "unknown": "Domain accepts mail but the mailbox couldn't be confirmed",
        "invalid": "Undeliverable · sending will bounce · remove it",
        "disposable": "Burner / temporary domain · real today, gone tomorrow",
        "spam_trap": "Known spam-trap address · never send",
    }.get(status, "")

    rows = ""
    for title, ok, desc in breakdown:
        if status in ("invalid", "disposable", "spam_trap") and title in ("SMTP mailbox",):
            ic, col = icon("x", 20), STATUS["invalid"]["dot"]
        elif not ok and title in ("Catch-all domain", "Disposable", "Role account", "Free provider"):
            ic, col = icon("minus", 20), N500
        elif ok:
            ic, col = icon("checkcircle", 20), STATUS["valid"]["dot"]
        else:
            ic, col = icon("x", 20), STATUS["invalid"]["dot"]
        rows += (f'<div class="brk-row"><span class="ic" style="color:{col}">{ic}</span>'
                 f'<span class="ttl">{_e(title)}</span><span class="ds">{_e(desc)}</span></div>')

    st.markdown(
        f'<div class="card">'
        f'<div class="verdict"><div class="ring">{_ring(v.get("score", 0), d["dot"])}</div>'
        f'<div style="flex:1"><div class="em">{_e(v["email"])} &nbsp;{status_tag(status)}</div>'
        f'<div class="su">{_e(subtitle)}</div></div></div>'
        f'<div class="card-h" style="border-top:1px solid var(--divider);padding:12px 20px">'
        f'<h4 style="font-size:12px;letter-spacing:.04em;color:var(--text-4);text-transform:uppercase">'
        f'Verification breakdown</h4></div>{rows}</div>',
        unsafe_allow_html=True,
    )

    if v.get("suggested_correction"):
        st.markdown(
            f'<div class="note note-info" style="margin-top:14px">{icon("check", 16)}'
            f'<div>Did you mean <b>{_e(v["suggested_correction"])}</b>?</div></div>',
            unsafe_allow_html=True,
        )

    with st.expander("Raw API response"):
        st.json(v)


# --------------------------------------------------------------------------- #
# Page: Contacts
# --------------------------------------------------------------------------- #
def _status_options(df: pd.DataFrame) -> list[str]:
    present = [s for s in STATUS_ORDER if (df["status"] == s).any()]
    return present or STATUS_ORDER


def page_contacts() -> None:
    page_header("Contacts", "Every verified address across all your uploads, in one place.",
                right=link_btn("Export", "?page=export", "download")
                + link_btn("Add contact", "?page=single", "plus", primary=True))
    df = contacts_df
    if df.empty:
        empty_state("users", "No contacts yet",
                    "Validate a list and every address lands here as a searchable, filterable contact book.",
                    "Validate a list", "?page=validate")
        return

    total = len(df)
    valid = int((df["status"] == "valid").sum())
    risky = int((df["status"] == "risky").sum())
    bounced = int(df["status"].isin(["invalid", "disposable", "spam_trap"]).sum())
    st.markdown(
        '<div class="cards c4">'
        + stat_card("Total contacts", fmt_int(total))
        + stat_card("Deliverable", fmt_int(valid), delta=f'{round(valid / total * 100, 1)}%')
        + stat_card("Risky", fmt_int(risky), delta=f'{round(risky / total * 100, 1)}%')
        + stat_card("Bounced / invalid", fmt_int(bounced), delta=f'{round(bounced / total * 100, 1)}%',
                    delta_up=False)
        + "</div>",
        unsafe_allow_html=True,
    )

    with card():
        fc1, fc2, fc3 = st.columns([0.34, 0.42, 0.24])
        with fc1:
            status_filter = st.segmented_control(
                "Status", ["All", "Valid", "Risky", "Invalid"], default="All",
                label_visibility="collapsed")
        q = fc2.text_input("Search", placeholder="Search contacts…", label_visibility="collapsed")
        sort_by = fc3.selectbox("Sort", ["Recent", "Score ↓", "Score ↑", "Email A–Z"],
                                label_visibility="collapsed")

        view = df
        if q:
            view = view[view["email"].str.lower().str.contains(q.lower(), na=False)]
        if status_filter and status_filter != "All":
            key = {"Valid": ["valid"], "Risky": ["risky"], "Invalid": ["invalid", "disposable", "spam_trap"]}
            view = view[view["status"].isin(key[status_filter])]
        view = {
            "Recent": view.sort_values("verified_at", ascending=False),
            "Score ↓": view.sort_values("score", ascending=False),
            "Score ↑": view.sort_values("score", ascending=True),
            "Email A–Z": view.sort_values("email"),
        }[sort_by]

        st.markdown(f'<div class="tag" style="margin:6px 0 4px">{fmt_int(len(view))} of '
                    f'{fmt_int(total)} contacts</div>',
                    unsafe_allow_html=True)

        rows = []
        for _, r in view.head(200).iterrows():
            email = str(r["email"])
            name = email.split("@")[0].replace(".", " ").replace("_", " ").title()
            domain = email.split("@")[-1]
            seg = r["source"].rsplit(".", 1)[0].replace("_", " ").title()
            init = "".join(w[0] for w in name.split()[:2]).upper() or "?"
            rows.append({"cells": [
                f'<div class="name-cell"><span class="avatar-sm" style="background:{_avatar_color(email)}">'
                f'{_e(init)}</span><div><div class="nm">{_e(name)}</div>'
                f'<div class="sub">{_e(email)}</div></div></div>',
                f'<span class="tag">{_e(domain)}</span>',
                f'<span class="tag">{_e(seg)}</span>',
                status_tag(r["status"]),
                score_bar(r["score"], r["status"]),
                f'<span class="muted">{_e(r["verified_at"][5:10] or "—")}</span>',
            ]})
        render_results_table(rows, [
            ("Name", "2.4fr"), ("Domain", "1.2fr"), ("Segment", "1.2fr"),
            ("Status", "1fr"), ("Score", "1.4fr"), ("Last validated", "1fr"),
        ])
        if len(view) > 200:
            st.caption(f"Showing the first 200 — narrow to see the rest ({fmt_int(len(view))} match).")


# --------------------------------------------------------------------------- #
# Page: Analytics
# --------------------------------------------------------------------------- #
def page_analytics() -> None:
    right = ('<div class="chip-btn">' + icon("calendar", 16)
             + '<span>All time</span>' + icon("chevdown", 15) + '</div>')
    page_header("Analytics",
                "Deliverability, risk distribution and validation activity over time.", right=right)
    df = contacts_df
    st_ = agg["status_totals"]
    total = len(df)
    if total == 0:
        empty_state("bar", "No analytics yet",
                    "Once you validate a list, deliverability trends and risk breakdowns show up here.")
        return

    valid = int((df["status"] == "valid").sum())
    valid_rate = round(valid / total * 100, 1)

    left, right = st.columns([0.62, 0.38], gap="large")
    with left:
        with card():
            st.markdown(
                f'<div class="stat" style="border:0;box-shadow:none;padding:0 0 18px;background:none">'
                f'<div class="l">Deliverable rate over time</div>'
                f'<div class="v">{valid_rate}%<span class="d up">{icon("trendup", 13)}'
                f'{valid_rate} pts</span></div></div>',
                unsafe_allow_html=True,
            )
            act = resample_activity(agg["by_date"], "Daily")
            if act.empty or "valid" not in act:
                st.caption("Not enough activity yet.")
            else:
                act["rate"] = (act["valid"] / act["verified"].replace(0, 1) * 100).round(1)
                ch = alt.Chart(act).mark_area(
                    interpolate="monotone", line={"color": BLUE, "strokeWidth": 2},
                    color=BLUE, opacity=0.08,
                ).encode(
                    x=alt.X("date:T", title=None, axis=_axis_x(format="%b %d")),
                    y=alt.Y("rate:Q", title=None, scale=alt.Scale(domain=[0, 100]),
                            axis=_axis_y(format="d")),
                    tooltip=[alt.Tooltip("date:T", title="Day"),
                             alt.Tooltip("rate:Q", title="Deliverable %", format=".1f")],
                ).properties(height=210)
                st.altair_chart(ch, use_container_width=True)

    with right:
        with card("Status mix"):
            dc = donut_chart(st_)
            if dc is not None:
                st.altair_chart(dc, use_container_width=True)
            legend = ""
            for s in ["valid", "risky", "invalid", "unknown"]:
                n = st_.get(s, 0)
                pct = round(n / total * 100, 1) if total else 0
                legend += (f'<div class="row"><span class="d dot" style="background:{STATUS_DOT[s]}"></span>'
                           f'<span class="nm">{STATUS_LABEL[s]}</span><span class="v">{fmt_int(n)}</span>'
                           f'<span class="pc">{pct}%</span></div>')
            st.markdown(f'<div class="lgd">{legend}</div>', unsafe_allow_html=True)

    st.write("")
    left2, right2 = st.columns([0.62, 0.38], gap="large")
    with left2:
        with card("Volume by weekday"):
            wb = weekday_bar(agg["by_date"])
            if wb is not None:
                st.altair_chart(wb, use_container_width=True)
            else:
                st.caption("Not enough activity yet.")
    with right2:
        with card("Top domains"):
            dom = df.copy()
            dom["domain"] = dom["email"].str.split("@").str[-1]
            g = dom.groupby("domain").agg(
                total=("email", "size"), valid=("status", lambda s: (s == "valid").sum())).reset_index()
            g["pct"] = (g["valid"] / g["total"] * 100).round(0).astype(int)
            g = g.sort_values("total", ascending=False).head(5)
            mx = g["total"].max() if len(g) else 1
            doms = ""
            for _, r in g.iterrows():
                w = round(r["total"] / mx * 100)
                doms += (f'<div class="dom"><div class="d"><div class="nm">{_e(r["domain"])}</div>'
                         f'<div class="track"><i style="width:{w}%"></i></div></div>'
                         f'<div class="rt"><div class="v num">{fmt_int(r["total"])}</div>'
                         f'<div class="pc">{r["pct"]}% valid</div></div></div>')
            st.markdown(doms or '<div class="muted">No domains yet.</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Page: Exports
# --------------------------------------------------------------------------- #
EXPORT_PRESETS = {
    "All contacts": lambda d: d,
    "Valid only": lambda d: d[d["status"] == "valid"],
    "Invalid / undeliverable": lambda d: d[d["status"].isin(["invalid", "disposable", "spam_trap"])],
    "Risky (catch-all / role)": lambda d: d[d["status"] == "risky"],
    "Catch-all": lambda d: d[d["is_catch_all"]],
    "Disposable": lambda d: d[d["is_disposable"]],
    "Custom filter…": None,
}


def page_export() -> None:
    page_header("Exports", "Download filtered slices of your validated data — valid, risky, or custom.")
    df = contacts_df
    if df.empty:
        empty_state("download", "Nothing to export yet",
                    "Validate a list first — then export valid-only, risky, or custom slices from here.",
                    "Validate a list", "?page=validate")
        return

    with card("New export"):
        left, right = st.columns([0.42, 0.58], gap="large")
        with left:
            preset = st.radio("What to export", list(EXPORT_PRESETS), label_visibility="collapsed")
            if preset == "Custom filter…":
                picked = st.multiselect(
                    "Statuses", _status_options(df),
                    default=[s for s in ["valid"] if (df["status"] == s).any()],
                    format_func=lambda s: STATUS_LABEL.get(s, s))
                sel = df[df["status"].isin(picked)] if picked else df.iloc[0:0]
                only_mx = st.checkbox("Require MX record", value=False)
                excl_disp = st.checkbox("Exclude disposable", value=False)
                if only_mx:
                    sel = sel[sel["mx_found"]]
                if excl_disp:
                    sel = sel[~sel["is_disposable"]]
            else:
                sel = EXPORT_PRESETS[preset](df)
            src = st.selectbox("Columns", ["Full detail", "Email only"], label_visibility="collapsed",
                               help="Full detail includes status, score, flags and source list.")

        with right:
            st.markdown(
                f'<div class="stat"><div class="l">Contacts in this export</div>'
                f'<div class="v">{fmt_int(len(sel))}</div>'
                f'<div style="margin-top:12px">'
                f'{mini_bar(sel["status"].value_counts().to_dict())}</div></div>',
                unsafe_allow_html=True,
            )
            st.write("")
            if src == "Email only":
                out = sel[["email"]]
            else:
                out = sel[["email", "status", "score", "sub_status", "is_disposable", "is_role",
                           "is_catch_all", "is_free", "mx_found", "source", "verified_at"]]
            csv_bytes = out.to_csv(index=False).encode("utf-8")
            fname = "validrow_" + preset.split()[0].lower().strip("…") + ".csv"
            st.download_button(f"Export {fmt_int(len(sel))} contacts", csv_bytes, fname, "text/csv",
                               type="primary", width="stretch", icon=":material/download:",
                               disabled=len(sel) == 0)

    # Ready-to-download outputs from every completed run
    completed = [r for r in store.list_runs() if r.get("status") == "completed"]
    if completed:
        st.write("")
        with card("Run outputs", count=f'{len(completed)} ready'):
            rows = []
            for r in completed:
                c = r.get("counts", {})
                when = (r.get("completed_at") or r.get("created_at") or "").replace("T", " ")[:10]
                rows.append({"href": f'?page=history&id={r["id"]}', "cells": [
                    f'<span class="em">{icon("file", 15)} {_e(r["filename"])}</span>',
                    '<span class="tag">Cleaned</span>',
                    f'<span class="muted num">{fmt_int(c.get("unique_emails", 0))}</span>',
                    '<span class="tag">CSV</span>',
                    f'<span class="muted">{_e(when)}</span>',
                    '<span class="stag" style="color:var(--st-ok)">'
                    '<span class="d" style="background:var(--st-ok)"></span>Ready</span>',
                ]})
            render_results_table(rows, [
                ("Export", "2.4fr"), ("Segment", "1fr"), ("Rows", "0.8fr"),
                ("Format", "0.7fr"), ("Created", "1fr"), ("Status", "1fr"),
            ])


# --------------------------------------------------------------------------- #
# Page: History
# --------------------------------------------------------------------------- #
def page_history() -> None:
    detail_id = st.query_params.get("id")
    if detail_id:
        page_history_detail(detail_id)
        return

    runs = store.list_runs()
    right = ('<div class="chip-btn">' + icon("calendar", 16) + '<span>All time</span>'
             + icon("chevdown", 15) + '</div>')
    page_header("History", "Every past validation run, with per-run analytics and downloads.", right=right)

    if not runs:
        empty_state("clock", "No history yet",
                    "Validated lists will appear here with per-upload deliverability analytics and "
                    "downloadable outputs.")
        return

    with card("Validation runs", count=f'{len(runs)} runs'):
        rows = []
        for r in runs:
            c = r.get("counts", {})
            when = (r.get("completed_at") or r.get("created_at") or "").replace("T", " ")[:16]
            if r.get("status") == "completed":
                rate = round(store.valid_rate(c) * 100)
                rows.append({"href": f'?page=history&id={r["id"]}', "cells": [
                    f'<span class="em">{icon("file", 15)} {_e(r["filename"])}</span>',
                    f'<span class="muted num">{fmt_int(c.get("total_rows", 0))}</span>',
                    f'<span style="color:var(--st-ok);font-weight:650">{rate}%</span>',
                    mini_bar(c),
                    f'<span class="muted">{_e(when)}</span>',
                ]})
            else:
                rows.append({"href": f'?page=validate&resume={r["id"]}', "cells": [
                    f'<span class="em">{icon("file", 15)} {_e(r["filename"])}</span>',
                    f'<span class="muted num">{fmt_int(c.get("total_rows", 0))}</span>',
                    '<span class="pill p-warn">Processing</span>',
                    '',
                    f'<span class="muted">{_e(when)}</span>',
                ]})
        render_results_table(rows, [
            ("Run", "2.6fr"), ("Rows", "0.9fr"), ("Valid %", "0.9fr"),
            ("Breakdown", "1.6fr"), ("Completed", "1.4fr"),
        ])


def page_history_detail(run_id: str) -> None:
    r = store.get(run_id)
    if not r:
        page_header("Run not found", "That run no longer exists.",
                    right=link_btn("Back to history", "?page=history", "arrow"))
        return

    c = r.get("counts", {})
    back = link_btn("Back", "?page=history", "arrow")
    page_header(r["filename"],
                f'Validated {(r.get("completed_at") or r.get("created_at") or "").replace("T", " ")[:16]} · '
                f'status {r.get("status")}', right=back)

    if r.get("status") != "completed":
        st.info("This run is still processing or did not finish. Open it from the dashboard to resume.")
        return

    invalid_total = store.invalid_total(c)
    unique = c.get("unique_emails") or 1
    st.markdown(
        '<div class="cards c4">'
        + stat_card("Unique mailboxes", fmt_int(c.get("unique_emails", 0)),
                    foot=f'from {fmt_int(c.get("total_rows", 0))} rows')
        + stat_card("Valid", fmt_int(c.get("valid", 0)), accent=STATUS["valid"]["dot"],
                    foot=f'{round(c.get("valid", 0) / unique * 100)}% valid rate')
        + stat_card("Risky", fmt_int(c.get("risky", 0)), foot="catch-all / role",
                    accent=STATUS["risky"]["dot"])
        + stat_card("Removed", fmt_int(invalid_total), foot="undeliverable + disposable",
                    accent=STATUS["invalid"]["dot"])
        + "</div>",
        unsafe_allow_html=True,
    )

    dcols = st.columns([1, 1, 1, 1.5])
    for i, (seg, label) in enumerate([("cleaned", "Cleaned CSV"), ("valid", "Valid only"),
                                      ("removed", "Removed rows")]):
        data = store.output_bytes(run_id, seg)
        if data:
            dcols[i].download_button(label, data, f"{seg}.csv", "text/csv", width="stretch",
                                     type="primary" if seg == "cleaned" else "secondary",
                                     icon=":material/download:", key=f"dl_{seg}")
    if dcols[3].button("Delete this run", icon=":material/delete:", key="del_run", width="stretch"):
        store.delete(run_id)
        st.query_params.pop("id", None)
        st.rerun()

    st.write("")
    left, right = st.columns([0.6, 0.4], gap="large")
    with right:
        with card("Status distribution"):
            st.altair_chart(hbar_dist(c), use_container_width=True)
    with left:
        data = store.output_bytes(run_id, "cleaned")
        with card("Cleaned sheet", count=f'{fmt_int(c.get("total_rows", 0))} rows'):
            if data:
                df = pd.read_csv(io.BytesIO(data))
                rows = _results_rows_from_df(df, (r.get("mapping") or {}).get("email"))
                if rows:
                    render_results_table(rows, [
                        ("Email", "2.4fr"), ("Status", "1fr"), ("Reason", "1.6fr"), ("Score", "1.4fr")])
                st.caption(f"Showing {min(len(df), len(rows))} of {len(df)} rows.")
            else:
                st.caption("Cached output not available for this run.")


# --------------------------------------------------------------------------- #
# Page: Settings (connection + engine status)
# --------------------------------------------------------------------------- #
def page_settings() -> None:
    page_header("Settings", "Connect to the validation engine and review its status.")
    left, right = st.columns([0.55, 0.45], gap="large")
    with left:
        with card("Connection"):
            new_api = st.text_input("API base URL", api).rstrip("/")
            if new_api != api:
                st.session_state.api_base = new_api
                st.rerun()
            if not api_ok:
                st.markdown(
                    f'<div class="note note-info" style="margin-top:4px">{icon("shield", 16)}'
                    '<div>Start the service to validate addresses:</div></div>', unsafe_allow_html=True)
                st.code("uvicorn eve.api.main:app --port 8000", language="bash")
    with right:
        if api_ok:
            dns_on = health.get("dns_enabled")
            smtp_on = health.get("smtp_enabled")
            st.markdown(
                f'<div class="card"><div class="card-h bd"><h4>Engine status</h4>'
                f'<span class="sp"><span class="pill p-ok"><span class="dot dot-live" '
                f'style="background:var(--st-ok)"></span>Online</span></span></div><div class="card-b">'
                f'<div class="brk-row" style="padding:11px 0;border:0"><span class="ttl">Version</span>'
                f'<span class="ds" style="margin-left:auto">v{_e(health.get("version"))}</span></div>'
                f'<div class="brk-row" style="padding:11px 0;border-top:1px solid var(--divider)">'
                f'<span class="ttl">DNS / MX</span><span class="sp" style="margin-left:auto"></span>'
                f'<span class="pill {"p-ok" if dns_on else "p-off"}">{"ON" if dns_on else "OFF"}</span></div>'
                f'<div class="brk-row" style="padding:11px 0;border-top:1px solid var(--divider)">'
                f'<span class="ttl">SMTP probe</span><span class="sp" style="margin-left:auto"></span>'
                f'<span class="pill {"p-ok" if smtp_on else "p-off"}">'
                f'{"ON" if smtp_on else "OFF"}</span></div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="card"><div class="card-b"><div class="empty" style="padding:32px 20px">'
                f'<div class="eico" style="background:var(--st-bad-soft);'
                f'color:var(--st-bad);border-color:var(--st-bad-soft)">{icon("x", 20)}</div>'
                f'<h4>API offline</h4><p>Start the service and refresh this page.</p></div></div></div>',
                unsafe_allow_html=True,
            )


# --------------------------------------------------------------------------- #
# Page: How it works (verification process)
# --------------------------------------------------------------------------- #
VERIFICATION_LAYERS = [
    ("Syntax validation", "RFC 5322",
     "Every address is parsed against the RFC 5322 grammar — local part, @, domain and TLD. "
     "Malformed addresses (missing @, illegal characters, empty parts) are rejected before any "
     "network call, so we never waste a DNS or SMTP round-trip on garbage."),
    ("Normalize & de-duplicate", "canonical key",
     "Addresses are lower-cased and provider quirks collapsed — Gmail dots and +tags map to one "
     "canonical mailbox — so a list with john.doe+news@gmail.com and johndoe@gmail.com counts as a "
     "single unique contact. Duplicates are collapsed before the expensive checks run."),
    ("Typo repair", "Damerau-Levenshtein",
     "Common domain misspellings are caught and a correction suggested — gmial.com → gmail.com, "
     "hotnail.com → hotmail.com — using edit-distance against the top mail providers, so recoverable "
     "typos aren't silently thrown away."),
    ("Domain & MX records", "DNS",
     "We resolve the domain's DNS and confirm it publishes MX (mail-exchange) records. No MX means "
     "the domain can't receive mail at all — an instant, cheap invalid before we ever open a socket."),
    ("Classification", "disposable · role · free",
     "The mailbox is flagged against maintained lists: disposable/burner domains, role addresses "
     "(info@, support@, admin@) that rarely belong to a person, and free consumer providers. These "
     "flags feed the risk score without needing an SMTP probe."),
    ("SMTP mailbox probe", "in-house",
     "We open an SMTP conversation with the domain's mail server and ask whether the specific mailbox "
     "exists — without ever sending an email (we never issue DATA). This is the step that tools like "
     "Instantly and Smartlead resell; Validrow owns it in-house."),
    ("Catch-all detection", "per-domain, cached",
     "Some domains accept mail to every address whether the mailbox exists or not. We detect these by "
     "probing a random address, cache the result per-domain, and mark real addresses on those domains "
     "as risky rather than falsely valid."),
    ("Risk & final status", "0–100 score",
     "Every signal rolls into a deliverability score and a final status. Provider lies are handled "
     "honestly — Gmail/Outlook/Yahoo deny probes, so we never report a confident valid we can't back up."),
]


def page_process() -> None:
    page_header("How Validrow verifies email",
                "Eight layers, cheapest first — each one can stop the pipeline early, so we spend an "
                "SMTP probe only on addresses that earned it.")

    st.markdown(
        f'<div class="note note-info">{icon("shield", 16)}<div>Validrow runs every address through '
        'the same pipeline, in order. Cheap checks (syntax, DNS) run first and reject the obvious '
        'failures; the expensive mailbox probe runs only on what survives.</div></div>',
        unsafe_allow_html=True,
    )
    st.write("")

    rows = ""
    for i, (title, tag, desc) in enumerate(VERIFICATION_LAYERS, start=1):
        rows += (
            f'<div class="layer"><div class="n">{i}</div><div>'
            f'<div class="ttl">{_e(title)}<span class="tg">{_e(tag)}</span></div>'
            f'<div class="dsc">{_e(desc)}</div></div></div>'
        )
    st.markdown(f'<div class="card"><div class="card-b">{rows}</div></div>', unsafe_allow_html=True)

    st.write("")
    st.markdown('<h4 style="margin:6px 2px 10px;font-size:15px;font-weight:700">'
                'What each final status means</h4>', unsafe_allow_html=True)
    meanings = [
        ("valid", "Mailbox confirmed to exist and accept mail. Safe to send."),
        ("risky", "Deliverable but lower confidence — catch-all domain or role address. Send with care."),
        ("unknown", "Domain accepts mail but the mailbox couldn't be confirmed "
                    "(e.g. the provider blocks probes)."),
        ("invalid", "No such mailbox or no MX record. Sending will bounce — remove it."),
        ("disposable", "Burner / temporary domain. Real today, gone tomorrow."),
        ("spam_trap", "Known spam-trap address. Never send — it damages sender reputation."),
    ]
    cga, cgb = st.columns(2, gap="medium")
    half = (len(meanings) + 1) // 2
    left_cards, right_cards = "", ""
    for idx, (status, desc) in enumerate(meanings):
        block = (
            f'<div class="fact" style="margin-bottom:12px">{status_tag(status)}'
            f'<div style="margin-top:8px;color:var(--text-3);font-size:13.5px;line-height:1.55">'
            f'{_e(desc)}</div></div>'
        )
        (left_cards := left_cards + block) if idx < half else (right_cards := right_cards + block)
    cga.markdown(left_cards, unsafe_allow_html=True)
    cgb.markdown(right_cards, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
if not api_ok and page in ("validate", "single"):
    page_header("API offline", "Start the service to validate addresses.",
                right=link_btn("Settings", "?page=settings", "settings"))
    st.warning("The API is not reachable. Start it and refresh:  `uvicorn eve.api.main:app --port 8000`")
else:
    {
        "dashboard": page_dashboard,
        "validate": page_validate,
        "single": page_single,
        "contacts": page_contacts,
        "analytics": page_analytics,
        "export": page_export,
        "history": page_history,
        "settings": page_settings,
        "process": page_process,
    }.get(page, page_dashboard)()
