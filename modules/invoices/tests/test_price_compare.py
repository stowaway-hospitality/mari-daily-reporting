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
