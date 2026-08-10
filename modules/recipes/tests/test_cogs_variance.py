"""
Estimated vs actual COGS — the arithmetic, and the traps that make it lie.

purchases (Xero, ex GST) - consumption (our recipe cost) = stock movement +
waste + theft + variance. COGS_ARCHITECTURE.md has named this number since the
file was written; both feeds were already on the dashboard, never differenced.

These tests pin the four things that will each silently produce a
plausible-looking wrong number.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_cogs_variance as V  # noqa: E402


# --- trap 4: week alignment ------------------------------------------------

@pytest.mark.parametrize("day,expected", [
    ("2026-08-03", "2026-08-09"),   # Monday   -> that Sunday
    ("2026-08-09", "2026-08-09"),   # Sunday   -> itself
    ("2026-08-08", "2026-08-09"),   # Saturday -> next day
    ("2026-08-02", "2026-08-02"),   # Sunday   -> itself
])
def test_weeks_are_monday_to_sunday_indexed_by_the_sunday(day, expected):
    """Xero's week_ending is a Sunday on all 37 weeks present. An off-by-one here
    smears every delivery into the neighbouring week and NOTHING in the output
    would look wrong."""
    assert V.week_ending(date.fromisoformat(day)) == expected


def test_the_xero_join_refuses_a_non_sunday(monkeypatch, tmp_path):
    """Asserted, not assumed: if Xero ever emits a Monday-ending week the build
    must stop rather than quietly misalign the subtraction."""
    bad = tmp_path / "x.csv"
    bad.write_text("week_ending,venue,actual_cogs_ex_gst,food_ex_gst,bev_ex_gst,other_ex_gst\n"
                   "2026-08-03,stow,100,50,50,0\n", encoding="utf-8")
    monkeypatch.setattr(V, "XERO", bad)
    with pytest.raises(ValueError, match="not a Sunday"):
        V.build()


# --- trap 1: GST ------------------------------------------------------------

def test_the_gst_rate_is_measured_not_assumed():
    """Xero is ex-GST and our cost book is inc-GST, but most FOOD is GST-free in
    Australia, so dividing by 1.1 invents a variance of ~5% of COGS at Stowaway —
    the same order as the real waste number.

    Measured off the invoices' own tax_treatment, beverages must land on 9.09%
    (alcohol is fully GST-bearing) and food far below it. If food ever comes out
    near 9% something has started taxing groceries."""
    rates = V.gst_rates()
    bev = [v for k, v in rates.items() if k[1] == "bev" and v > 0]
    food = [v for k, v in rates.items() if k[1] == "food"]
    if not bev or not food:
        pytest.skip("no invoices to measure")
    for b in bev:
        assert abs(b - 1 / 11) < 0.005, f"beverage GST measured {b:.4f}, expected ~0.0909"
    for f in food:
        assert f < 0.05, f"food GST measured {f:.4f} — that is not a GST-free basket"


# --- trap 3: coverage -------------------------------------------------------

def test_unknown_coverage_is_not_treated_as_zero():
    """cogs_blend returns source "lightspeed" whenever coverage <= 0, so a day
    file saying source "recipe_blend" BESIDE coverage 0.0 is impossible by that
    contract — it predates the guard. Averaging its false zero in drags a real
    80% week to nothing, so it must be excluded as unmeasured rather than
    counted."""
    cov, share = V.coverage_by_week()
    for k, c in cov.items():
        assert c > 0, f"{k} kept a zero coverage reading: {c}"
    for k, s in share.items():
        assert 0.0 <= s <= 100.0, f"{k} measured share out of range: {s}"


def test_a_low_coverage_week_is_never_marked_trustworthy():
    """A variance is only a waste number to the extent consumption is real.
    Harry Gatos has ~31% of revenue with no cost behind it, so its variance is
    inflated BY CONSTRUCTION and must not be presented as waste."""
    feed = V.build()
    for v, d in feed["venues"].items():
        for w in d["weeks"]:
            if w["trustworthy"]:
                assert w["recipe_coverage_pct"] >= feed["coverage_floor"], (v, w)
                assert w["coverage_measured_on_pct_of_revenue"] >= feed["coverage_floor"], (v, w)


# --- trap 2: lumpiness ------------------------------------------------------

def test_every_week_carries_a_rolling_figure():
    """You buy a case and pour it over three weeks, so one week's variance is
    mostly delivery timing. The rolling window is the number that means
    something, and it must exist on every row."""
    feed = V.build()
    for v, d in feed["venues"].items():
        for w in d["weeks"]:
            assert w["rolling_weeks"] >= 1
            assert w["rolling_weeks"] <= feed["roll_weeks"]
            assert "rolling_variance" in w


def test_the_variance_is_actually_purchases_minus_consumption():
    """The whole point, stated as arithmetic so a refactor cannot drift it."""
    feed = V.build()
    for v, d in feed["venues"].items():
        for w in d["weeks"]:
            assert abs(w["variance"] - (w["purchases_ex_gst"] - w["consumption_ex_gst"])) < 0.02
        c = d["cumulative"]
        assert abs(c["variance"] - (c["purchases_ex_gst"] - c["consumption_ex_gst"])) < 0.5


def test_consumption_ex_gst_is_never_above_inc_gst():
    """Direction check on the GST conversion. Getting it backwards would inflate
    consumption and hide a variance."""
    feed = V.build()
    for v, d in feed["venues"].items():
        for w in d["weeks"]:
            if w["consumption_inc_gst"]:
                assert w["consumption_ex_gst"] <= w["consumption_inc_gst"] + 0.01, w
