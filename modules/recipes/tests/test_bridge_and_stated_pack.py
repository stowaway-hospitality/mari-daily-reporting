"""
Two ways a real invoice failed to reach the book, both silent.

ONE CODE, TWO PRODUCTS. load_bridge() mapped supplier:code -> ONE ProductID, so a
second row for the same code overwrote the first without a word. Ten codes in
product_map.csv do that, and for several the duplication is deliberate and
documented in the file's own notes — Appleton is bridged to both "[Bottle]" and
"[House]" because the invoices name the bottle and the recipes resolve the House
identity ("Same bottle, two Back Office identities, both bridged"). Only one was
ever reaching the book, which is the bug that note was written to fix.

A STATED UNIT vs ONE GUESSED FROM A NAME. A recipe-bridge-seed row states its own
unit and its price is already per that unit, but resolve_pack only ever read the
DESCRIPTION — which on these rows carries the container. So $3.475/L of Heinz BBQ
Sauce named "[4L]" was divided by 4000 ml and booked at $0.869/L, and $11.53/kg
of milk bun named "[85g]" was divided by 85 and booked at $135.64/kg.

The limit of that fix is the important half: only a size the NAME guessed at is
overridden, never one invented where the name said nothing. On the liquor rows
the description is bare ("Jack Daniels") and the pack_unit column says "L" while
the price plainly is not per litre — $6.55 for Jack Daniels, $12.73 for Buffalo
Trace [House] against a $76/L invoice. Trusting the column there would publish
those as real rates.
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


def _build(tmp_path, monkeypatch, seed_rows, map_rows):
    cogs, pmap, out = tmp_path / "cogs.csv", tmp_path / "map.csv", tmp_path / "costs.csv"
    ov = tmp_path / "po.yaml"
    ov.write_text("[]\n", encoding="utf-8")
    _write(cogs, SEED_FIELDS, seed_rows)
    _write(pmap, MAP_FIELDS, map_rows)
    for attr, val in (("COGS", cogs), ("PRODUCT_MAP", pmap), ("OUT", out),
                      ("PACK_OVERRIDES", ov), ("ROOT", tmp_path)):
        monkeypatch.setattr(bc, attr, val)
    bc.main()
    with out.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _seed(pid, desc, cost, **kw):
    row = {"supplier": "Lightspeed", "supplier_code": pid, "invoice_description": desc,
           "cost_per_unit_incl_gst": cost, "basis": "", "pack_qty": "", "pack_unit": "",
           "venue": "stowaway", "source_invoice": "bo-seed-stowaway", "invoice_date": "2026-01-01"}
    row.update(kw)
    return row


def test_one_supplier_code_reaches_every_product_it_bridges_to(tmp_path, monkeypatch):
    """The Appleton case: same bottle, two Back Office identities, both bridged.
    A dict kept whichever row came last and dropped the other silently."""
    rows = _build(
        tmp_path, monkeypatch,
        [_seed("7001", "House Rum 700ML", "70.00"),
         _seed("7002", "Bottle Rum 700ML", "70.00"),
         {"supplier": "ILG", "supplier_code": "AAA-1", "invoice_description": "HOUSE RUM",
          "cost_per_unit_incl_gst": "77.00", "basis": "per_bottle", "pack_qty": "", "pack_unit": "",
          "venue": "stowaway", "source_invoice": "INV1", "invoice_date": "2026-07-01", "note": ""}],
        [{"supplier": "ILG", "supplier_code": "AAA-1", "product_id": "7001",
          "product_name": "House Rum", "venue": "stowaway"},
         {"supplier": "ILG", "supplier_code": "AAA-1", "product_id": "7002",
          "product_name": "Bottle Rum", "venue": "stowaway"}])
    landed = {r["ingredient"] for r in rows
              if r["source_invoice"] == "INV1" and r["ingredient"].startswith("lightspeed:")}
    assert landed == {"lightspeed:7001", "lightspeed:7002"}, (
        "both bridged identities must receive the observation, not just the last row")


def test_a_stated_per_litre_price_is_not_divided_by_the_size_in_its_name(tmp_path, monkeypatch):
    # Heinz BBQ Sauce: $3.475 a LITRE, on a row that names the 4L container.
    rows = _build(
        tmp_path, monkeypatch,
        [{"supplier": "Lightspeed", "supplier_code": "8001",
          "invoice_description": "Heinz BBQ Sauce [4L]", "cost_per_unit_incl_gst": "3.475",
          "basis": "per_unit", "pack_qty": "1", "pack_unit": "L", "venue": "stowaway",
          "source_invoice": "recipe-bridge-seed", "invoice_date": "2026-01-03", "note": ""}],
        [])
    r = next(x for x in rows if x["ingredient"] == "lightspeed:8001")
    assert r["unit"] == "ml"
    # 3.475 / 1000 = 0.003475.  The bug divided by 4000 and wrote 0.000869.
    assert Decimal(r["cost_per_unit"]) == Decimal("0.003475")


def test_a_stated_per_kilo_price_is_not_divided_by_the_size_in_its_name(tmp_path, monkeypatch):
    # T2 Milk Bun: $11.5294 a KILO, on a row that names the 85g bun. This one
    # failed in the OVER direction — $135.64/kg, 11.8x — which is the direction
    # that gets noticed, and it still sat there.
    rows = _build(
        tmp_path, monkeypatch,
        [{"supplier": "Lightspeed", "supplier_code": "8002",
          "invoice_description": "T2 Milk Bun Sliced White Sesame [85g]",
          "cost_per_unit_incl_gst": "11.5294", "basis": "per_unit", "pack_qty": "1",
          "pack_unit": "kg", "venue": "stowaway", "source_invoice": "recipe-bridge-seed",
          "invoice_date": "2026-01-03", "note": ""}],
        [])
    r = next(x for x in rows if x["ingredient"] == "lightspeed:8002")
    assert r["unit"] == "g"
    assert Decimal(r["cost_per_unit"]) == Decimal("0.011529")


def test_a_bare_description_is_left_alone_even_though_the_row_says_L(tmp_path, monkeypatch):
    """THE LIMIT OF THE FIX. "Jack Daniels" names no container, so there is nothing
    resolve_pack guessed wrong and nothing to correct — and this family's pack_unit
    is not to be trusted on its own: it says "L" for a $6.55 whisky. Publishing
    $6.55/L would be inventing a rate, not repairing one."""
    rows = _build(
        tmp_path, monkeypatch,
        [{"supplier": "Lightspeed", "supplier_code": "8003", "invoice_description": "Jack Daniels",
          "cost_per_unit_incl_gst": "6.55", "basis": "per_unit", "pack_qty": "1", "pack_unit": "L",
          "venue": "stowaway", "source_invoice": "recipe-bridge-seed",
          "invoice_date": "2026-01-03", "note": ""}],
        [])
    assert not [r for r in rows if r["ingredient"] == "lightspeed:8003"], (
        "a bare liquor description must stay skipped, not become a $6.55/L rate")


def test_a_stated_pack_resolves_into_base_units_so_an_invoice_can_match_it(tmp_path, monkeypatch):
    """seed_conv took pack_unit raw and wrote (1, "L") — a unit no invoice line can
    ever equal, so the bridge comparison could never succeed and the product stayed
    frozen on its January seed. 117 observations were lost this way."""
    rows = _build(
        tmp_path, monkeypatch,
        [{"supplier": "Lightspeed", "supplier_code": "8004", "invoice_description": "House Gin",
          "cost_per_unit_incl_gst": "70.00", "basis": "per_unit", "pack_qty": "1", "pack_unit": "L",
          "venue": "stowaway", "source_invoice": "recipe-bridge-seed",
          "invoice_date": "2026-01-03", "note": ""},
         {"supplier": "ILG", "supplier_code": "BBB-2", "invoice_description": "HOUSE GIN",
          "cost_per_unit_incl_gst": "0.075", "basis": "per_L", "pack_qty": "", "pack_unit": "",
          "venue": "stowaway", "source_invoice": "INV9", "invoice_date": "2026-07-01", "note": ""}],
        [{"supplier": "ILG", "supplier_code": "BBB-2", "product_id": "8004",
          "product_name": "House Gin", "venue": "stowaway"}])
    bridged = [r for r in rows if r["ingredient"] == "lightspeed:8004"
               and r["source_invoice"] == "INV9"]
    assert bridged, "the invoice must reconcile with the seed's unit and reach the ProductID"
    assert bridged[0]["unit"] == "ml"


def test_a_per_kilo_invoice_reconciles_with_a_per_gram_seed(tmp_path, monkeypatch):
    """Once the seed's unit is resolved into a base unit, a $/kg invoice no longer
    equals it. kg->g is the one conversion cost.py permits, and refusing it here
    silently dropped the Berry Man passionfruit puree on its way to the book."""
    rows = _build(
        tmp_path, monkeypatch,
        [{"supplier": "Lightspeed", "supplier_code": "8005",
          "invoice_description": "Passionfruit Puree Seedless [1kg]",
          "cost_per_unit_incl_gst": "9.65", "basis": "per_unit", "pack_qty": "1", "pack_unit": "kg",
          "venue": "stowaway", "source_invoice": "recipe-bridge-seed",
          "invoice_date": "2026-01-03", "note": ""},
         {"supplier": "The Berry Man NSW Pty Ltd", "supplier_code": "PJ1",
          "invoice_description": "Passionfruit Puree 12x1kg", "cost_per_unit_incl_gst": "9.50",
          "basis": "per_kg", "pack_qty": "", "pack_unit": "", "venue": "stowaway",
          "source_invoice": "IN9", "invoice_date": "2026-07-24", "note": ""}],
        [{"supplier": "The Berry Man NSW Pty Ltd", "supplier_code": "PJ1", "product_id": "8005",
          "product_name": "Passionfruit Puree", "venue": "stowaway"}])
    bridged = [r for r in rows if r["ingredient"] == "lightspeed:8005"
               and r["source_invoice"] == "IN9"]
    assert bridged, "a $/kg invoice must still reach a $/g seed"
    assert bridged[0]["unit"] == "g"
    assert Decimal(bridged[0]["cost_per_unit"]) == Decimal("0.009500")
