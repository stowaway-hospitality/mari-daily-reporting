"""
Cross-supplier price comparison — identity, supplier canon, and the build.

The whole value of /pricing is trusting that two lines shown side by side really
are the same ingredient in the same unit. A wrong merge invents a saving that
isn't there; a wrong supplier-canon compares a supplier against itself. These
tests pin both the merges we WANT and the ones we must never make.

    python3 -m pytest modules/invoices/tests/test_price_compare.py
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest

from modules.invoices.price_compare import (
    canonical_key, canonical_supplier, tokens, display_name,
)
from modules.invoices import build_price_compare as bpc


# ── supplier canonicalisation ──────────────────────────────────────────────

@pytest.mark.parametrize("variants, expected", [
    (["Jun Pacific", "Jun Pacific Corporation Pty Ltd"], "Jun Pacific"),
    (["Sun Circle", "Sun Circle Food Manufacturing Pty Ltd"], "Sun Circle"),
    (["The Berry Man NSW Pty Ltd"], "Berry Man"),
    (["Mallia Industries Pty Ltd"], "Mallia"),
])
def test_supplier_variants_collapse(variants, expected):
    assert {canonical_supplier(v) for v in variants} == {expected}


def test_supplier_canon_keeps_acronyms_and_distinct_names():
    assert canonical_supplier("B&E") == "B&E"
    assert canonical_supplier("ILG") == "ILG"
    # two genuinely different suppliers must NOT collapse
    assert canonical_supplier("Combined Wines") != canonical_supplier("Select Fresh")
    assert canonical_supplier("Combined Wines") == "Combined Wines"


def test_supplier_canon_never_empties_a_name():
    # a name made entirely of "noise" words must fall back to the original, not ""
    assert canonical_supplier("Pty Ltd") == "Pty Ltd"
    assert canonical_supplier("   ") == ""


# ── ingredient identity: merges we WANT ────────────────────────────────────

def test_herb_prefix_is_dropped_so_suppliers_line_up():
    # Select Fresh writes "HERB PARSLEY …", Fresh Fruit Team writes "Parsley …"
    assert canonical_key("HERB PARSLEY CONTINENTAL BCH") == canonical_key("Parsley Continental")


def test_word_order_does_not_matter():
    assert canonical_key("Ruby Red Grapefruit") == canonical_key("Grapefruit Red Ruby")


def test_pack_size_and_packaging_are_stripped_from_identity():
    assert canonical_key("OLIVES 2KG TUB") == canonical_key("Olives (bulk)")


def test_manual_alias_merges_size_variants():
    # data/ingredient_aliases.json maps these onto the plain ingredient
    assert canonical_key("Carrot Large") == canonical_key("CARROT KG")
    assert canonical_key("BROCCOLINI BABY BCH") == canonical_key("Broccolini")


# ── ingredient identity: merges we must NEVER make ─────────────────────────

def test_juice_is_not_fresh_fruit():
    assert canonical_key("JUICE RUBY GRAPEFRUIT 2LTR") != canonical_key("Grapefruit Ruby Red")


def test_cut_florets_are_not_whole_head():
    assert canonical_key("CAULIFLOWER FLORETS SML 30MM") != canonical_key("Cauliflower")


def test_unrelated_items_do_not_merge():
    assert canonical_key("LEMON KG") != canonical_key(
        "OLIVES WHOLE LEMON & GARLIC MARINATED 10KG Sandhurst")


def test_dimensions_are_identity_not_pack_noise():
    # An 11" box is a different SKU from a 13" box — merging them invents a price
    # "rise" when you buy the bigger one. The inch dimension must survive.
    assert canonical_key('B Flute Lock Top 11" Pizza Boxes x 50') != \
           canonical_key('B Flute Lock Top 13" Pizza Boxes x 50')


def test_filler_stopwords_do_not_split_an_ingredient():
    assert canonical_key("OLIVES WHOLE IN BRINE 5KG") == canonical_key("Olives Whole Brine")


# ── the build: grouping, cheapest, unit-aware suspect gate ─────────────────

def _run_build(rows, tmp_path, monkeypatch):
    p = tmp_path / "cogs_list.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "supplier", "invoice_description", "cost_per_unit_incl_gst", "basis",
            "note", "invoice_date"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    monkeypatch.setattr(bpc, "COGS", p)
    return bpc.build()


def _row(sup, desc, price, basis="per_kg", date="2026-07-01"):
    return {"supplier": sup, "invoice_description": desc,
            "cost_per_unit_incl_gst": str(price), "basis": basis,
            "note": "", "invoice_date": date}


def test_build_groups_same_ingredient_and_flags_cheapest(tmp_path, monkeypatch):
    out = _run_build([
        _row("Select Fresh", "CARROT KG", "2.40"),
        _row("Fresh Fruit Team", "Carrot Large", "1.32"),
    ], tmp_path, monkeypatch)
    carrot = [i for i in out["ingredients"] if i["key"] == canonical_key("CARROT KG")]
    assert len(carrot) == 1, "the two carrot lines must group into one ingredient"
    g = carrot[0]
    assert g["multi"] and not g["suspect"]
    assert g["cheapest"] == "Fresh Fruit Team"          # 1.32 < 2.40
    assert g["suppliers"][0]["cost"] == 1.32            # cheapest first


def test_build_collapses_supplier_name_variants(tmp_path, monkeypatch):
    out = _run_build([
        _row("Sun Circle", "Pork Dumpling", "7.50"),
        _row("Sun Circle Food Manufacturing Pty Ltd", "Pork Dumpling", "7.50"),
    ], tmp_path, monkeypatch)
    g = [i for i in out["ingredients"] if "dumpling" in i["key"]][0]
    assert not g["multi"], "one supplier under two names must not look like two"
    assert g["suppliers"][0]["supplier"] == "Sun Circle"


def test_build_exact_unit_allows_large_real_gap(tmp_path, monkeypatch):
    # $/kg is exact — an 82% gap is a real saving, not a pack error.
    out = _run_build([
        _row("A Foods", "Spinach", "4.00"),
        _row("B Produce", "Spinach", "7.28"),
    ], tmp_path, monkeypatch)
    g = [i for i in out["ingredients"] if i["key"] == "spinach"][0]
    assert g["multi"] and not g["suspect"]


def test_build_inexact_unit_flags_moderate_gap(tmp_path, monkeypatch):
    # per-each is inexact — a 200% gap means "verify the pack", not "save".
    out = _run_build([
        _row("A Foods", "Lettuce Iceberg", "1.50", basis="per_unit"),
        _row("B Produce", "Lettuce Iceberg", "4.50", basis="per_unit"),
    ], tmp_path, monkeypatch)
    g = [i for i in out["ingredients"] if "lettuce" in i["key"]][0]
    assert g["multi"] and g["suspect"], "inexact unit + big gap must be suspect"


def test_build_surfaces_a_price_rise_as_a_mover(tmp_path, monkeypatch):
    # Same supplier, price went up between two invoices -> it must appear in movers.
    out = _run_build([
        _row("Foodlink", "Chicken Breast", "11.00", date="2026-06-01"),
        _row("Foodlink", "Chicken Breast", "13.20", date="2026-07-01"),
    ], tmp_path, monkeypatch)
    movers = [m for m in out["movers"] if "chicken" in m["name"].lower()]
    assert movers, "a 20% rise since last order must surface as a mover"
    m = movers[0]
    assert m["prev"] == 11.0 and m["cost"] == 13.2 and m["pct"] == 20.0


def test_build_ignores_tiny_moves_on_cheap_lines(tmp_path, monkeypatch):
    # A 1c wobble on a $0.40 line is noise, not cost creep — must NOT be a mover.
    out = _run_build([
        _row("Gulli", "Paper Straw", "0.40", basis="per_unit", date="2026-06-01"),
        _row("Gulli", "Paper Straw", "0.41", basis="per_unit", date="2026-07-01"),
    ], tmp_path, monkeypatch)
    assert not [m for m in out["movers"] if "straw" in m["name"].lower()]


# ── the build: lowest-ever, dollar saving, switch-these ────────────────────

def test_build_tracks_lowest_ever_price(tmp_path, monkeypatch):
    # The lowest $/unit ever seen (across suppliers + history) is the floor to
    # negotiate back to. Must be the min, with the date it was seen.
    monkeypatch.setattr(bpc, "_purchase_stats", lambda: {})
    out = _run_build([
        _row("Select Fresh", "CARROT KG", "2.40", date="2026-06-01"),
        _row("Fresh Fruit Team", "CARROT KG", "1.32", date="2026-06-15"),
        _row("Select Fresh", "CARROT KG", "2.90", date="2026-07-01"),
    ], tmp_path, monkeypatch)
    g = [i for i in out["ingredients"] if i["key"] == canonical_key("CARROT KG")][0]
    assert g["low"] == 1.32
    assert g["low_date"] == "2026-06-15"


def test_build_computes_dollar_saving_and_switch(tmp_path, monkeypatch):
    # You buy 100 kg of spinach from the dearer supplier ($7.28) when a cheaper one
    # ($4.00) carries it. Saving = 100 * (7.28-4.00) = $328, and because your MAIN
    # (most-spent) supplier isn't the cheapest, it must be flagged "switch".
    # (Supplier names chosen so canonical_supplier leaves them unchanged.)
    key = canonical_key("Spinach")
    monkeypatch.setattr(bpc, "_purchase_stats", lambda: {
        (key, "kg"): {
            "Bravo": {"spend": 728.0, "volume": 100.0},   # dearer, main
            "Alpha": {"spend": 40.0,  "volume": 10.0},    # cheaper
        }})
    out = _run_build([
        _row("Alpha", "Spinach", "4.00"),
        _row("Bravo", "Spinach", "7.28"),
    ], tmp_path, monkeypatch)
    g = [i for i in out["ingredients"] if i["key"] == key][0]
    assert g["switch"] is True
    assert g["main"] == "Bravo" and g["cheapest"] == "Alpha"
    assert g["est_saving"] == pytest.approx(328.0, abs=0.5)
    assert out["total_saving"] >= 328.0
    assert any(s["name"].lower().startswith("spinach") for s in out["switches"])


def test_build_no_switch_when_main_supplier_is_already_cheapest(tmp_path, monkeypatch):
    # You mostly buy from the cheapest already — nothing to switch, no false alert.
    key = canonical_key("Spinach")
    monkeypatch.setattr(bpc, "_purchase_stats", lambda: {
        (key, "kg"): {
            "Alpha": {"spend": 400.0, "volume": 100.0},   # cheaper, main
            "Bravo": {"spend": 7.28,  "volume": 1.0},     # dearer, tiny
        }})
    out = _run_build([
        _row("Alpha", "Spinach", "4.00"),
        _row("Bravo", "Spinach", "7.28"),
    ], tmp_path, monkeypatch)
    g = [i for i in out["ingredients"] if i["key"] == key][0]
    assert g["switch"] is False
    assert g["main"] == "Alpha" == g["cheapest"]
