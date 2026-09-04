"""Presentation maths: percentages, donut geometry and chart paths.

The prototype's rendering is the reference. These pin the conversions that make
the Python output identical to it, plus the empty/degenerate cases the
prototype's invented data never produced.
"""
from __future__ import annotations

from datetime import date

import pytest

from eve.web import format as F
from eve.web import series as S


# --- Numbers --------------------------------------------------------------- #
def test_jsround_matches_javascript_not_bankers_rounding():
    # Python's round() gives 2 for both of these; JavaScript gives 3 and 3.
    assert F.jsround(2.5) == 3
    assert F.jsround(3.5) == 4
    assert F.jsround(-2.5) == -3


def test_fmt_uses_thousands_separators_and_an_em_dash_for_nothing():
    assert F.fmt(12480) == "12,480"
    assert F.fmt(0) == "0"
    assert F.fmt(None) == "—"


def test_pct_refuses_to_divide_by_zero():
    assert F.pct(5, 10) == "50.0%"
    assert F.pct(0, 0) == "—"


def test_share_never_rounds_a_real_count_down_to_zero():
    """1 of 10,000 is not 0% — a category with a count behind it must stay
    visible as a share."""
    assert F.share(1, 10_000) == "<1%"
    assert F.share(1, 10_000, places=1) == "<0.1%"
    assert F.share(0, 10_000) == "0%"  # a genuine zero may say zero


def test_share_never_rounds_a_partial_up_to_a_hundred():
    """9,999 of 10,000 is not 100% while one address sits in another verdict."""
    assert F.share(9_999, 10_000) == ">99%"
    assert F.share(9_999, 10_000, places=1) == ">99.9%"
    assert F.share(10_000, 10_000) == "100%"  # a genuine whole may say 100


def test_mix_and_donut_labels_obey_the_same_rule():
    lopsided = {"deliverable": 10_000, "risky": 3, "unknown": 0, "undeliverable": 0}
    labels = {m["k"]: m["pct_label"] for m in F.mix(lopsided)}
    assert labels["deliverable"] == ">99%"
    assert labels["risky"] == "<1%"

    donut = {d["k"]: d["pct"] for d in F.donut(lopsided)}
    assert donut["deliverable"] == ">99%"
    assert donut["risky"] == "<1%"
    # A verdict with no count is still allowed to read as zero.
    assert donut["unknown"] == "0%"


def test_mid_truncate_keeps_the_domain_visible():
    out = F.mid_truncate("averyveryverylonglocalpart@example-domain.com", 30)
    assert out.endswith("@example-domain.com")
    assert "…" in out
    assert F.mid_truncate("short@a.io", 30) == "short@a.io"


def test_duration_pads_seconds():
    assert F.duration(372) == "6m 12s"
    assert F.duration(5) == "0m 05s"
    assert F.duration(None) == "—"


# --- Verdict mixes --------------------------------------------------------- #
def test_mix_floors_nonzero_slices_so_they_stay_visible():
    totals = {"deliverable": 999, "risky": 1, "unknown": 0, "undeliverable": 0}
    mix = {m["k"]: m for m in F.mix(totals)}
    assert "unknown" not in mix  # zero counts drop out of the legend
    assert float(mix["risky"]["pct"]) >= 0.6
    assert mix["risky"]["pct_label"] == "<1%"


def test_mix_with_no_data_does_not_divide_by_zero():
    empty = {k: 0 for k in F.ORDER}
    assert F.mix(empty) == []
    assert len(F.mix(empty, only_present=False)) == 4


def test_donut_segments_tile_the_circle_without_gaps():
    totals = {"deliverable": 50, "risky": 25, "unknown": 15, "undeliverable": 10}
    segs = F.donut(totals)
    lengths = [float(s["dash"].split()[0]) for s in segs]
    assert sum(lengths) == pytest.approx(F.DONUT_CIRCUMFERENCE, abs=0.05)
    # Each segment starts where the previous one ended.
    running = 0.0
    for seg, length in zip(segs, lengths):
        assert float(seg["offset"]) == -round(running, 1)
        running += length


def test_ring_dash_clamps_to_the_score_range():
    assert F.ring_dash(100) == "232 232"
    assert F.ring_dash(0) == "0 232"
    assert F.ring_dash(None) == "0 232"
    assert F.ring_dash(150) == "232 232"


# --- SVG paths ------------------------------------------------------------- #
def test_paths_survive_empty_and_single_point_series():
    """Real workspaces start with one day of data, or none."""
    for vals in ([], [7]):
        assert F.smooth(vals, 100, 50).startswith("M")
        assert F.curve(vals, 100, 50, 0, 10).startswith("M")
        assert F.autoscale_polyline(vals, 100, 50).startswith("M")


def test_smooth_area_path_closes_the_shape():
    d = F.smooth([1, 2, 3], 100, 50, area=True)
    assert d.endswith("Z")
    assert "L100 50 L0 50 Z" in d


def test_flat_series_does_not_blow_up_the_scale():
    d = F.smooth([5, 5, 5], 100, 50)
    assert "nan" not in d.lower() and "inf" not in d.lower()


def test_bar_heights_scale_to_the_tallest_bar():
    assert F.bar_heights([10, 5, 0], 100) == ["100px", "50px", "0px"]
    assert F.bar_heights([0, 0], 100) == ["0px", "0px"]


# --- Series ---------------------------------------------------------------- #
def test_build_returns_twelve_buckets_with_zeros_for_quiet_periods():
    by_day = {"2026-07-24": {"deliverable": 4, "risky": 1, "unknown": 0, "undeliverable": 0}}
    buckets = S.build(by_day, "Monthly", today=date(2026, 7, 28))
    assert len(buckets) == 12
    assert buckets[-1].total == 5  # July holds the data
    assert all(b.total == 0 for b in buckets[:-1])
    assert buckets[-1].label == "Jul"


def test_previous_period_shifts_a_full_window_back():
    by_day = {"2026-07-24": {"deliverable": 4, "risky": 0, "unknown": 0, "undeliverable": 0}}
    prev = S.build(by_day, "Monthly", today=date(2026, 7, 28), offset_periods=1)
    assert sum(b.total for b in prev) == 0
    assert prev[-1].end < date(2026, 7, 1)


def test_delta_is_none_without_a_previous_period_to_compare():
    """A fresh workspace has nothing to compare against, and the UI hides the
    chip rather than showing a fabricated percentage."""
    assert S.delta(100, 0) is None
    up = S.delta(110, 100)
    assert up["pct"] == "10.0%" and up["up"] is True
    down = S.delta(90, 100)
    assert down["pct"] == "10.0%" and down["up"] is False


def test_deliverable_rate_carries_forward_through_quiet_buckets():
    by_day = {"2026-07-01": {"deliverable": 8, "risky": 2, "unknown": 0, "undeliverable": 0}}
    buckets = S.build(by_day, "Daily", count=3, today=date(2026, 7, 3))
    # 1 Jul is 80%; the two quiet days hold that rate instead of dropping to 0.
    assert S.deliverable_rate(buckets) == [80.0, 80.0, 80.0]


def test_weekday_volume_covers_all_seven_days():
    by_day = {"2026-07-27": {"deliverable": 3, "risky": 0, "unknown": 0, "undeliverable": 0}}
    wk = S.weekday_volume(by_day)
    assert [b["d"] for b in wk] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert wk[0]["v"] == 3  # 27 Jul 2026 is a Monday
    assert sum(b["v"] for b in wk) == 3
