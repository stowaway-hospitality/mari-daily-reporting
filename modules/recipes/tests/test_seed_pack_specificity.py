"""
A seed's pack size must be decided by SPECIFICITY, not by where it sits in the file.

data/cogs_list.csv holds two seed families for the same bottle. The bo-seed states
the container ("Geppetto Pinot Noir 750ML", dated 2026-01-01). The ls-recipe-seed
states a BASIS instead (per_L, dated 2026-01-02) and resolve_pack answers that with
a nominal 1000, which describes a litre of the stuff, not the bottle it comes in.

The file is date-sorted, so the nominal landed second and won on file order alone.
Every bridged invoice for that bottle was then divided by 1000 instead of 750 — a
flat 25% under-cost on every glass poured, and a ratio of exactly 0.75, which sits
comfortably inside the 0.1-10 magnitude guard, so nothing refused it. Eight wines
and a vermouth were live on that number; six kegs were the same defect pointing the
other way (a 50 L keg divided by 1000 was 50x out, so the guard refused the bridge
and the keg stayed frozen on its January seed forever).

The second test is the one that keeps the fix honest: plenty of products really are
sold in 1 L, and for those the stated size IS 1000. The rule must be "a nominal
never overwrites a stated pack", not "prefer the smaller number".
"""

from __future__ import annotations

import csv
from decimal import Decimal

import pytest

from modules.recipes.pipeline import build_costs as bc

SEED_FIELDS = ["supplier", "supplier_code", "invoice_description", "cost_per_unit_incl_gst",
               "basis", "pack_qty", "pack_unit", "venue", "source_invoice", "invoice_date", "note"]
MAP_FIELDS = ["supplier", "supplier_code", "product_id", "product_name", "venue"]


def _write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _run(tmp_path, monkeypatch, stated_desc, stated_cost, litre_rate, invoice_cost):
    """Seed one ProductID from both families, bridge one per_bottle invoice onto it."""
    pid = "9001"
    cogs = tmp_path / "cogs.csv"
    _write(cogs, SEED_FIELDS, [
        # states the container — dated FIRST, so it loses any file-order race
        {"supplier": "Lightspeed", "supplier_code": pid, "invoice_description": stated_desc,
         "cost_per_unit_incl_gst": stated_cost, "basis": "", "pack_qty": "", "pack_unit": "",
         "venue": "stowaway", "source_invoice": "bo-seed-stowaway", "invoice_date": "2026-01-01"},
        # states a BASIS — resolve_pack answers a nominal 1000. Dated SECOND.
        {"supplier": "Lightspeed", "supplier_code": pid, "invoice_description": "Test Bottle - Bottle",
         "cost_per_unit_incl_gst": litre_rate, "basis": "per_L", "pack_qty": "1", "pack_unit": "ml",
         "venue": "stowaway", "source_invoice": "ls-recipe-seed", "invoice_date": "2026-01-02"},
        # a real invoice, priced per whole bottle, on a bridged supplier code
        {"supplier": "Combined Wines", "supplier_code": "TESTCODE", "invoice_description": stated_desc,
         "cost_per_unit_incl_gst": invoice_cost, "basis": "per_bottle", "pack_qty": "", "pack_unit": "",
         "venue": "stowaway", "source_invoice": "SINV1", "invoice_date": "2026-07-13"},
    ])
    pmap = tmp_path / "product_map.csv"
    _write(pmap, MAP_FIELDS, [{"supplier": "Combined Wines", "supplier_code": "TESTCODE",
                               "product_id": pid, "product_name": stated_desc, "venue": "stowaway"}])
    out = tmp_path / "costs.csv"
    ov = tmp_path / "po.yaml"
    ov.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(bc, "COGS", cogs)
    monkeypatch.setattr(bc, "PRODUCT_MAP", pmap)
    monkeypatch.setattr(bc, "OUT", out)
    monkeypatch.setattr(bc, "PACK_OVERRIDES", ov)
    monkeypatch.setattr(bc, "ROOT", tmp_path)
    bc.main()
    with out.open(encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f)
                if r["ingredient"] == f"lightspeed:{pid}" and r["source_invoice"] == "SINV1"]


def test_stated_bottle_size_beats_a_per_litre_nominal(tmp_path, monkeypatch):
    # $17.5608 a bottle, 750 ml stated, ~$23.40 a litre quoted by the recipe seed.
    rows = _run(tmp_path, monkeypatch, "Test Bottle 750ML", "17.5608", "23.4000", "17.5608")
    assert rows, "the bridged invoice must reach the ProductID identity"
    got = Decimal(rows[0]["cost_per_unit"])
    assert rows[0]["unit"] == "ml"
    # 17.5608 / 750 = 0.023414.  The bug divided by 1000 and wrote 0.017561.
    assert got == Decimal("0.023414"), f"expected the 750 ml divisor, got {got}"
    assert got != Decimal("0.017561")


def test_a_genuine_one_litre_bottle_still_divides_by_1000(tmp_path, monkeypatch):
    # Antica, Campari, Noilly Prat and friends really are 1 L. The stated size is
    # 1000 and the nominal agrees, so the answer must not move.
    rows = _run(tmp_path, monkeypatch, "Test Bottle 1000ML", "64.2700", "64.2700", "64.2700")
    assert rows, "the bridged invoice must reach the ProductID identity"
    assert Decimal(rows[0]["cost_per_unit"]) == Decimal("0.064270")


def test_a_keg_bridge_is_no_longer_refused_by_the_magnitude_guard(tmp_path, monkeypatch):
    # A 50 L keg divided by the nominal 1000 is 50x out. The guard (0.1-10x)
    # refused it, so the keg silently kept its January seed and no delivery ever
    # updated it. With the stated 50000 the arithmetic lands and the bridge holds.
    rows = _run(tmp_path, monkeypatch, "Test Keg 50000ML", "212.4400", "4.3029", "212.4400")
    assert rows, "a keg bridge must land, not be silently refused"
    assert Decimal(rows[0]["cost_per_unit"]) == Decimal("0.004249")
