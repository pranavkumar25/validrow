"""Time series over real validation history.

The prototype filled its charts from a seeded random generator. These build the
same shapes from ``AddressStore.by_day()``, which means two things follow that
the prototype never had to handle:

* **A period can be genuinely empty.** Zero is a real reading, not missing data,
  so buckets with no activity are plotted as zero rather than skipped.
* **There may not be a previous period to compare against.** Deltas are only
  returned when a prior period actually has volume; otherwise the caller hides
  the delta chip rather than showing a meaningless "↑ 0%".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from eve.verdict import VERDICT_ORDER

BUCKETS = 12  # every chart in the design plots twelve points

GRAINS = ("Daily", "Weekly", "Monthly")


@dataclass
class Bucket:
    """One point on a chart."""

    start: date
    end: date  # inclusive
    label: str
    totals: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.totals.values())


def _empty() -> dict[str, int]:
    return {v.value: 0 for v in VERDICT_ORDER}


def _month_floor(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _bucket_bounds(grain: str, count: int, today: date) -> list[tuple[date, date, str]]:
    """The ``count`` most recent buckets, oldest first, as (start, end, label)."""
    out: list[tuple[date, date, str]] = []
    if grain == "Monthly":
        first = _add_months(_month_floor(today), -(count - 1))
        for i in range(count):
            start = _add_months(first, i)
            end = _add_months(start, 1) - timedelta(days=1)
            out.append((start, end, start.strftime("%b")))
    elif grain == "Weekly":
        # Weeks run Monday-Sunday and end with the week containing today.
        this_week = today - timedelta(days=today.weekday())
        for i in range(count):
            start = this_week - timedelta(weeks=count - 1 - i)
            out.append((start, start + timedelta(days=6), start.strftime("%b %-d")))
    else:  # Daily
        for i in range(count):
            day = today - timedelta(days=count - 1 - i)
            out.append((day, day, day.strftime("%b %-d")))
    return out


def build(
    by_day: dict[str, dict[str, int]],
    grain: str = "Monthly",
    *,
    count: int = BUCKETS,
    today: Optional[date] = None,
    offset_periods: int = 0,
) -> list[Bucket]:
    """Bucket daily counts into a chart series.

    ``offset_periods=1`` shifts the whole window one full period back, which is
    how the "Previous" comparison line is built.
    """
    today = today or datetime.now(timezone.utc).date()
    if offset_periods:
        if grain == "Monthly":
            today = _add_months(_month_floor(today), -count * offset_periods)
        elif grain == "Weekly":
            today = today - timedelta(weeks=count * offset_periods)
        else:
            today = today - timedelta(days=count * offset_periods)

    parsed: dict[date, dict[str, int]] = {}
    for day, totals in by_day.items():
        try:
            parsed[date.fromisoformat(day)] = totals
        except ValueError:
            continue

    buckets: list[Bucket] = []
    for start, end, label in _bucket_bounds(grain, count, today):
        acc = _empty()
        for d, totals in parsed.items():
            if start <= d <= end:
                for k, n in totals.items():
                    if k in acc:
                        acc[k] += n
        buckets.append(Bucket(start=start, end=end, label=label, totals=acc))
    return buckets


def deliverable_rate(buckets: list[Bucket]) -> list[float]:
    """Percentage deliverable per bucket. Empty buckets carry the last known
    rate forward so the line does not crater to zero on a quiet week."""
    out: list[float] = []
    last = 0.0
    for b in buckets:
        if b.total:
            last = b.totals.get("deliverable", 0) / b.total * 100
        out.append(round(last, 1))
    return out


def weekday_volume(by_day: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """Total addresses per weekday, Monday first."""
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    acc = [0] * 7
    for day, totals in by_day.items():
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        acc[d.weekday()] += sum(totals.values())
    return [{"d": names[i], "v": acc[i]} for i in range(7)]


def delta(current: float, previous: float) -> Optional[dict[str, Any]]:
    """A period-over-period change, or ``None`` when there is nothing to compare.

    Returning ``None`` is the point: with one week of history there is no honest
    percentage to show, and the design has a state for exactly that.
    """
    if not previous:
        return None
    change = (current - previous) / previous * 100
    return {
        "pct": f"{abs(change):.1f}%",
        "up": change >= 0,
        "good": change >= 0,
    }
