"""Par model v2 guards.

Covers the three guarantees that must never regress:
  1. the coverage gate fails when a live Classic/Signature cocktail has no recipe;
  2. hard overrides are honoured — a min is never lowered, a zero is forced to 0,
     a max is never exceeded, a hold pins exactly;
  3. sanity — Rooster resolves >= 40 and a margarita's qty reaches Rooster.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.par import model  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ── 1. coverage gate ────────────────────────────────────────────────────────
def test_coverage_gate_fails_when_cocktail_lacks_recipe():
    week = "2026-08-09"
    rows = [
        {"venue": "stow", "reporting_group": "Cocktails - Classic",
         "product_name": "Nonexistent Test Cocktail", "week_ending": week, "qty": 12.0},
        {"venue": "stow", "reporting_group": "Cocktails - Classic",
         "product_name": "$21 Custom Cocktail", "week_ending": week, "qty": 9.0},
    ]
    matcher = lambda name: None  # nothing resolves
    gaps = model.coverage_gap(rows, "stow", matcher, {}, [week])
    names = [n for n, _ in gaps]
    assert "Nonexistent Test Cocktail" in names          # real miss is caught
    assert "$21 Custom Cocktail" not in names            # open-price custom excused


def test_coverage_gate_passes_when_recipe_present():
    week = "2026-08-09"
    rows = [{"venue": "stow", "reporting_group": "Cocktails - Signature",
             "product_name": "Testini", "week_ending": week, "qty": 5.0}]
    matcher = lambda name: "testini"
    gaps = model.coverage_gap(rows, "stow", matcher, {"testini": {"x": 1.0}}, [week])
    assert gaps == []


def test_live_coverage_gate_currently_passes():
    """The real committed data must satisfy the gate (build would exit nonzero)."""
    for venue in ("stow", "hg"):
        _, meta = model.compute_venue(venue, DATA)
        assert meta["coverage_gaps"] == [], f"{venue}: {meta['coverage_gaps']}"


# ── 2. hard overrides ───────────────────────────────────────────────────────
def test_hard_min_never_lowered():
    assert model.apply_override(5.0, {"type": "min", "value": 40.0}) == 40.0   # raised
    assert model.apply_override(55.0, {"type": "min", "value": 40.0}) == 55.0  # not lowered


def test_hard_zero_forces_zero():
    assert model.apply_override(99.0, {"type": "zero", "value": None}) == 0.0


def test_hard_max_never_exceeded():
    assert model.apply_override(99.0, {"type": "max", "value": 10.0}) == 10.0  # capped
    assert model.apply_override(3.0, {"type": "max", "value": 10.0}) == 3.0    # left alone


def test_hard_hold_pins_exactly():
    assert model.apply_override(7.3, {"type": "hold", "value": 1.0}) == 1.0


def test_rooster_override_enforced_in_full_build():
    recs, _ = model.compute_venue("stow", DATA)
    rooster = recs["Rooster Rojo Blanco Tequila [Bottle]"]
    # override is min 40; model must never resolve below it
    assert rooster["rec_par"] >= 40.0


# ── 3. sanity: margarita consumption reaches Rooster ─────────────────────────
def test_margarita_qty_reaches_rooster_consumption():
    id2name, bo_meta = model.load_bo(DATA, "stow")
    scrape = model.load_scrape(DATA, "stow")
    overrides = model.load_overrides(DATA, "stow")
    leaves_by_norm, recipe_norms = model.load_recipes(DATA, "stow")
    idx = model.ParIndex(scrape, overrides, bo_meta)
    matcher = model.build_recipe_matcher(recipe_norms)
    weeks = ["2026-08-09"]
    rows = [{"venue": "stow", "reporting_group": "Cocktails - Classic",
             "product_name": "Classic Margarita", "week_ending": "2026-08-09", "qty": 100.0}]
    pour, recipe = model._build_consumption(rows, "stow", idx, leaves_by_norm, matcher, id2name, weeks)
    rooster = "Rooster Rojo Blanco Tequila [Bottle]"
    assert rooster in recipe
    # 100 margaritas * 40ml / 700ml = ~5.71 bottles
    assert recipe[rooster][0] > 0
    assert recipe[rooster][0] == pytest.approx(100 * 40 / 700, rel=0.05)


def test_rooster_recipe_driver_positive_on_real_data():
    recs, _ = model.compute_venue("stow", DATA)
    assert recs["Rooster Rojo Blanco Tequila [Bottle]"]["drivers"]["recipe_wk"] > 0
