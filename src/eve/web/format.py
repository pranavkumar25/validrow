"""Presentation primitives: the palette, number formatting and SVG geometry.

Everything here is pure. The view-model (:mod:`eve.web.views`) turns store rows
into screen data; this module turns numbers into the exact strings and path
data the templates interpolate.

The geometry functions are ports of the prototype's chart maths and keep its
constants (the 502.4 donut circumference, the 232 score ring, the 6px chart
inset) so the rendered output is identical.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any, Optional

# --- Palette --------------------------------------------------------------- #
# One entry per primary verdict: the dot colour, the pill background ("wash")
# and the pill text colour ("ink").
VERDICT_STYLE = {
    "deliverable": {"label": "Deliverable", "dot": "#12B76A", "wash": "#ECFDF3", "ink": "#027A48"},
    "risky": {"label": "Risky", "dot": "#F79009", "wash": "#FFFAEB", "ink": "#B54708"},
    "unknown": {"label": "Unknown", "dot": "#A8A29E", "wash": "#F4F3EF", "ink": "#79756C"},
    "undeliverable": {"label": "Undeliverable", "dot": "#F04438", "wash": "#FEF3F2", "ink": "#B42318"},
}

#: The dashboard table and export builder use shorter labels for the same four
#: verdicts. Same colours, same meaning — only the wording is tighter.
SHORT_LABEL = {
    "deliverable": "Valid",
    "risky": "Risky",
    "unknown": "Unknown",
    "undeliverable": "Invalid",
}

ORDER = ["deliverable", "risky", "unknown", "undeliverable"]

# Brand + neutrals, lifted from the design.
BLUE = "#35ABFF"
BLUE_DARK = "#1C93EA"
BLUE_WASH = "#EAF6FF"
BLUE_LINE = "#D8ECFF"
INK = "#101014"
INK_2 = "#44413B"
INK_3 = "#57534B"
MUTED = "#79756C"
MUTED_2 = "#A8A29E"
LINE = "#E7E4DE"
LINE_2 = "#D8D4CC"
SURFACE = "#F4F3EF"
WHITE = "#FFFFFF"
GREEN = "#12B76A"
GREEN_INK = "#027A48"
AMBER = "#F79009"
RED = "#F04438"

DONUT_CIRCUMFERENCE = 502.4  # r=80
RING_CIRCUMFERENCE = 232.0  # r=37


# --- Numbers --------------------------------------------------------------- #
def jsround(x: float) -> int:
    """Round half away from zero, the way JavaScript's Math.round does.

    Python rounds halves to even, which would shift percentages by a point
    against the prototype on exact .5 boundaries.
    """
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


def fixed(x: float, places: int = 1) -> str:
    """Format like JS ``toFixed`` — half away from zero, always ``places`` digits."""
    factor = 10**places
    return f"{jsround(x * factor) / factor:.{places}f}"


def fmt(n: Optional[float]) -> str:
    """Thousands-separated integer, or an em dash when there is nothing to say."""
    if n is None:
        return "—"
    return f"{int(n):,}"


def share(part: float, whole: float, places: int = 0) -> str:
    """A percentage that never claims an absolute it has not reached.

    Rounding a real count down to ``0%``, or a partial share up to ``100%``
    while another category is non-zero, is the small kind of lie this product
    cannot afford: it makes the report say something the data does not. A
    non-zero part that rounds to zero reads ``<1%``; a part short of the whole
    that rounds to a hundred reads ``>99%``.
    """
    if not whole:
        return "—"
    raw = part / whole * 100
    smallest = 10.0**-places  # 1 at 0dp, 0.1 at 1dp
    if part > 0 and raw < smallest:
        return f"<{fixed(smallest, places)}%"
    if part < whole and raw > 100 - smallest:
        return f">{fixed(100 - smallest, places)}%"
    return fixed(raw, places) + "%"


def pct(numerator: float, denominator: float, places: int = 1) -> str:
    return share(numerator, denominator, places)


def mid_truncate(email: str, max_len: int) -> str:
    """Shorten a long address without hiding the domain — that is the part a
    reader scans for."""
    if len(email) <= max_len:
        return email
    at = email.rfind("@")
    if at < 0:
        return email[: max_len - 1] + "…"
    local, domain = email[:at], email[at:]
    keep = max(6, max_len - len(domain) - 1)
    return local[:keep] + "…" + domain


def duration(seconds: Optional[float]) -> str:
    """``6m 12s``. Sub-minute runs still show minutes so the column stays aligned."""
    if seconds is None:
        return "—"
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s"


# --- Verdict mixes --------------------------------------------------------- #
def mix(totals: dict[str, int], *, only_present: bool = True) -> list[dict[str, Any]]:
    """The stacked bar / legend rows for a verdict breakdown.

    A verdict with a non-zero count is floored at 0.6% width so it stays visible
    in the bar — a real result should never render as a hairline.
    """
    total = sum(totals.get(k, 0) for k in ORDER) or 1
    out = []
    for k in ORDER:
        n = totals.get(k, 0)
        raw = n / total * 100
        style = VERDICT_STYLE[k]
        out.append(
            {
                "k": k,
                "label": style["label"],
                "short": SHORT_LABEL[k],
                "dot": style["dot"],
                "wash": style["wash"],
                "ink": style["ink"],
                "count": fmt(n),
                "raw": n,
                "pct": fixed(max(0.6 if n > 0 else 0.0, raw)),
                "pct_label": share(n, total),
                "show": n > 0,
            }
        )
    return [s for s in out if s["show"]] if only_present else out


def donut(totals: dict[str, int], *, short_labels: bool = False) -> list[dict[str, Any]]:
    """Stacked arc segments for the verdict donut."""
    total = sum(totals.get(k, 0) for k in ORDER)
    offset = 0.0
    segs = []
    for k in ORDER:
        n = totals.get(k, 0)
        frac = (n / total) if total else 0.0
        length = frac * DONUT_CIRCUMFERENCE
        style = VERDICT_STYLE[k]
        segs.append(
            {
                "k": k,
                "label": SHORT_LABEL[k] if short_labels else style["label"],
                "dot": style["dot"],
                "dash": f"{length:.1f} {DONUT_CIRCUMFERENCE}",
                "offset": f"{-offset:.1f}",
                "count": fmt(n),
                "pct": share(n, total),
            }
        )
        offset += length
    return segs


def ring_dash(score: Optional[int]) -> str:
    """Dash array for the 0-100 score ring."""
    s = max(0, min(100, int(score or 0)))
    return f"{s / 100 * RING_CIRCUMFERENCE:.0f} {RING_CIRCUMFERENCE:.0f}"


# --- SVG paths ------------------------------------------------------------- #
def _pad(vals: Sequence[float]) -> list[float]:
    """A chart needs two points to draw a line; repeat a lone reading."""
    v = list(vals)
    if not v:
        return [0.0, 0.0]
    return v * 2 if len(v) == 1 else v


def smooth(vals: Sequence[float], w: float, h: float, area: bool = False) -> str:
    """Auto-scaled smoothed spline — the sparkline in the stat cards."""
    v = _pad(vals)
    hi, lo = max(v), min(v)
    top, bot = 6.0, h - 6.0
    span = (hi - lo) or 1
    pts = [(i / (len(v) - 1) * w, bot - (x - lo) / span * (bot - top)) for i, x in enumerate(v)]
    return _spline(pts, w, h, area)


def curve(
    vals: Sequence[float], w: float, h: float, lo: float, hi: float, area: bool = False
) -> str:
    """Smoothed spline on a shared scale — the two lines of the volume chart
    must use one scale or the comparison lies."""
    v = _pad(vals)
    span = (hi - lo) or 1
    pts = [(i / (len(v) - 1) * w, h - (x - lo) / span * h) for i, x in enumerate(v)]
    return _spline(pts, w, h, area)


def _spline(pts: list[tuple[float, float]], w: float, h: float, area: bool) -> str:
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        px, py = pts[i]
        nx, ny = pts[i + 1]
        mx = (px + nx) / 2
        d += f" C{mx:.1f} {py:.1f}, {mx:.1f} {ny:.1f}, {nx:.1f} {ny:.1f}"
    if area:
        d += f" L{w:g} {h:g} L0 {h:g} Z"
    return d


def polyline(vals: Sequence[float], w: float, h: float, top: float) -> str:
    """Straight-segment path against a fixed ceiling."""
    v = _pad(vals)
    ceiling = top or 1
    pts = [(i / (len(v) - 1) * w, h - x / ceiling * h) for i, x in enumerate(v)]
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def autoscale_polyline(vals: Sequence[float], w: float, h: float) -> str:
    """Straight-segment path with 12% headroom — the deliverable-rate chart."""
    v = _pad(vals)
    hi = (max(v) or 1) * 1.12
    pts = [(i / (len(v) - 1) * w, h - x / hi * h) for i, x in enumerate(v)]
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def bar_heights(values: Iterable[float], max_px: int) -> list[str]:
    vals = list(values)
    top = max(vals) if vals and max(vals) else 1
    return [f"{jsround(v / top * max_px)}px" for v in vals]
