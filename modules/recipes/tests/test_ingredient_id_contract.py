"""
THE contract that keeps the recipe builder costable: the id build_ingredients
puts in ingredients.json (what the app writes into a recipe) MUST equal the key
build_costs uses in the cost feed (what cost_on looks up). They diverged once —
slug() gave "foodlink-102689" while the cost engine keyed "foodlink:102689", so
0/120 ingredients resolved and every saved recipe failed to cost. This test runs
the real builder against a fixture and asserts the id is the cost-engine key.
"""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from core.domain import purchasable_id
from modules.recipes.pipeline import build_ingredients as bi


def _write_cogs(p: Path, rows: list[dict]) -> None:
    cols = ["supplier", "supplier_code", "invoice_description", "cost_per_unit_incl_gst",
            "basis", "note", "venue", "source_invoice", "invoice_date"]
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_builder_id_equals_cost_engine_key(tmp_path, monkeypatch):
    sup = sorted(bi.KITCHEN_SUPPLIERS)[0]           # a real kitchen supplier
    recent = (date.today() - timedelta(days=5)).isoformat()
    cogs = tmp_path / "cogs.csv"
    out = tmp_path / "ingredients.json"
    _write_cogs(cogs, [
        {"supplier": sup, "supplier_code": "ab12x", "invoice_description": "Test Item 2kg",
         "cost_per_unit_incl_gst": "10.00", "basis": "per_unit", "venue": "stowaway",
         "source_invoice": "INV1", "invoice_date": recent},
        {"supplier": sup, "supplier_code": "", "invoice_description": "No-code item",
         "cost_per_unit_incl_gst": "5.00", "basis": "per_unit", "venue": "stowaway",
         "source_invoice": "INV2", "invoice_date": recent},
    ])
    monkeypatch.setattr(bi, "ROOT", tmp_path)   # so OUT.relative_to(ROOT) works
    monkeypatch.setattr(bi, "COGS", cogs)
    monkeypatch.setattr(bi, "OUT", out)
    bi.main()

    items = json.loads(out.read_text())["ingredients"]
    ids = {i["id"] for i in items}
    # the coded item's id is EXACTLY the cost-engine key (colon + UPPERCASE code)
    assert purchasable_id(sup, "ab12x") in ids
    assert purchasable_id(sup, "ab12x").endswith(":AB12X")
    # a code-less line has no cost identity -> dropped, never given a fake slug id
    assert not any("no-code" in i["id"].lower() for i in items)


def test_purchasable_id_is_not_the_old_slug_form():
    # guard the exact regression: colon-and-uppercase, never hyphen-and-lowercase
    assert purchasable_id("Foodlink", "102689") == "foodlink:102689"
    assert purchasable_id("Foodlink", "102689") != "foodlink-102689"
