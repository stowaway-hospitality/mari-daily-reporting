"""
A container's price is not its per-unit rate.

    Frank's Hot Sauce 1Galln 3.8L    seeded $35.07 per LITRE   (it is the jug)
    Honey Pure 3kg Wild Flower       seeded $20.98 per KILO    (it is the tub)

An `ls-recipe-seed` carries Lightspeed's own recipe cost for a ProductID, and on
these two that figure is the price of the whole container filed against pack_qty
1 with a per-ml / per-g unit. Same defect as the ILG case/bottle, one level up: a
price that counts a CONTAINER against a unit that counts its CONTENTS.

It survived because it OVER-states cost. Every guard in this repo is pointed at
the flattering direction, so 3-4x too dear on four recipes went unremarked.

THE SIGNATURE needs no judgement. Where the same goods are also bought on a real
invoice the book holds both, and the seeded rate divided by the invoice rate
lands on the PACK SIZE printed in the product's own name — 3.0 for a 3 kg tub,
3.785 for a gallon. No price difference does that.

AND IT MUST NOT FIRE ON REAL PRICE MOVEMENT. Brown sugar, demerara, spanish onion
and prosciutto all sit 1.5-2.0x from their twins, and none of those ratios match
a pack size in their names. They are price differences, and a detector that
reports them too is a detector somebody turns off.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_pack_as_rate import _stated_pack, findings  # noqa: E402

COGS = ROOT / "data" / "cogs_list.csv"
COSTS = ROOT / "data" / "costs.csv"
pytestmark = pytest.mark.skipif(not COGS.exists(), reason="cogs_list.csv not built")


def test_nothing_outstanding():
    found = findings(list(csv.DictReader(COGS.open(encoding="utf-8-sig"))))
    assert not found, "\n".join(
        f"{f['description']} ({f['code']}): seeded ${f['seeded']} vs invoice "
        f"${f['invoice']} = {f['ratio']}x against a stated {f['pack']} pack"
        for f in found)


@pytest.mark.parametrize("ingredient,expect,source", [
    # the rate that must come out, and the invoice that says so
    ("lightspeed:22888642", 0.006993, "Foodlink SI4274123: $21.00 / 3 kg = $7.00/kg"),
    ("lightspeed:22888760", 0.009266, "Foodlink SI4312724: $32.50 / 3.8 L = $8.55/L"),
])
def test_the_two_fixed_rates_match_their_invoices(ingredient, expect, source):
    """Not a pinned magic number — each is the invoice figure arrived at from a
    completely separate source, which is the check that these were units errors
    and not prices. Re-break the pack override and they go back to 3-4x."""
    if not COSTS.exists():
        pytest.skip("costs.csv not built")
    rows = [r for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if r["ingredient"] == ingredient]
    assert rows, f"{ingredient} is not in the book"
    for r in rows:
        assert abs(float(r["cost_per_unit"]) - expect) < 1e-6, (
            f"{ingredient} costs {r['cost_per_unit']}, expected {expect} — {source}")


@pytest.mark.parametrize("desc,pack", [
    ("Honey Pure 3kg Wild Flower", 3.0),
    ("Frank's Hot Sauce 1Galln 3.8L Franks", 3.78541),
    ("Sugar Demerera 375Gm CSR", 0.375),
    ("Salt Cooking 10kg Olssons", 10.0),
    ("No size here at all", None),
])
def test_the_pack_in_the_name_is_read_correctly(desc, pack):
    got = _stated_pack(desc)
    if pack is None:
        assert got is None
    else:
        assert got is not None and abs(got - pack) < 0.01


def test_a_plain_price_difference_is_not_reported():
    """A twin 2x dearer with no matching pack size is a price, not a units error."""
    rows = [
        {"supplier": "Foodlink", "supplier_code": "1", "invoice_description": "Sugar Brown 1kg CSR",
         "cost_per_base_unit": "4.20", "pack_unit": "kg", "pack_qty": "1", "source_invoice": "SI1"},
        {"supplier": "Lightspeed", "supplier_code": "9", "invoice_description": "Sugar Brown 1kg CSR",
         "cost_per_base_unit": "0.0084", "pack_unit": "g", "pack_qty": "1",
         "source_invoice": "ls-recipe-seed"},
    ]
    assert not findings(rows)


def test_the_detector_can_actually_fire():
    """Plant the defect: a 3 kg tub whose seeded rate IS the tub price."""
    rows = [
        {"supplier": "Foodlink", "supplier_code": "1", "invoice_description": "Thing 3kg Brand",
         "cost_per_base_unit": "7.00", "pack_unit": "kg", "pack_qty": "3", "source_invoice": "SI1"},
        {"supplier": "Lightspeed", "supplier_code": "9", "invoice_description": "Thing 3kg Brand",
         "cost_per_base_unit": "0.021", "pack_unit": "g", "pack_qty": "1",
         "source_invoice": "ls-recipe-seed"},
    ]
    got = findings(rows)
    assert len(got) == 1 and abs(got[0]["ratio"] - 3.0) < 0.01
