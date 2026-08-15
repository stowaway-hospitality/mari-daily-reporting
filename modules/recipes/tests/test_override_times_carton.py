"""
A confirmed pack is the size of ONE piece; a carton holds N of them.

THE DEFECT
----------
`resolve_pack`'s description path already multiplies a single piece by its CTN-N
note — that is what rescues a $45.60 box of 12 camembert wheels from being read
as one 125 g wheel. The chef-confirmed OVERRIDE path did not, so a line that
bought a CARTON was divided by ONE piece:

    Foodlink 100175  BEANS BLACK WHOLE TIN A10
        $8.70  "EA"      -> $0.0029/g
        $52.20 "CTN-6"   -> $0.0174/g     SIX TIMES OVER, live in the book

and 6 x $8.70 is exactly $52.20, so the carton reading is arithmetic, not a
judgement call.

THE DISCRIMINATOR is already on the invoice. `pack_size` is N when the parser has
ALREADY divided the carton into pieces, and 1 when the price is the whole line.
Foodlink bills the same camembert code both ways in the same month:

    SI4480678  $3.80   pack_size 12  CTN-12   price is per piece   -> do NOT multiply
    SI4467596  $45.60  pack_size 1   CTN-12   price is the carton  -> multiply by 12

THE PROOF THIS IS RIGHT AND NOT MERELY CONSISTENT: those two rows are priced
differently, come from different invoices, and are handled by different branches
— and they have to land on the SAME rate. They both land on $0.0304/g. The
$45.60 row had previously been dropped from the book entirely.
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

COSTS = ROOT / "data" / "costs.csv"
pytestmark = pytest.mark.skipif(not COSTS.exists(), reason="costs.csv not built")


def _rows(ingredient):
    return [r for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if r["ingredient"] == ingredient]


@pytest.mark.parametrize("ingredient", ["foodlink:100175", "foodlink:100487"])
def test_a_carton_line_and_a_piece_line_agree_on_the_rate(ingredient):
    """Every delivery of one code must cost the same per gram however the
    supplier chose to bill it. Re-break the CTN multiplication and the carton
    rows fly off by their carton count and this reds."""
    rows = _rows(ingredient)
    assert len(rows) >= 3, f"{ingredient}: expected several deliveries, got {len(rows)}"
    rates = {r["cost_per_unit"] for r in rows}
    assert len(rates) == 1, (
        f"{ingredient} costs different amounts per {rows[0]['unit']} on different "
        f"deliveries: " + ", ".join(
            f"{r['observed_on']} {r['cost_per_unit']} ({r['pack']})" for r in rows))


def test_the_carton_rows_are_actually_being_multiplied():
    """A test that would still pass if the CTN branch never fired proves nothing.
    At least one row must say so in its own `pack` provenance."""
    rows = _rows("foodlink:100487") + _rows("foodlink:100175")
    assert any("CTN" in (r["pack"] or "") for r in rows), \
        "no row records a CTN multiplication — the branch is dead"


def test_the_previously_dropped_carton_row_is_in_the_book():
    """SI4467596's $45.60 camembert carton had no readable pack and was skipped
    entirely. It should now be present, at the same rate as the pieces."""
    rows = {r["observed_on"]: r for r in _rows("foodlink:100487")}
    assert "2026-07-16" in rows, "the $45.60 carton row is still missing"
    assert D(rows["2026-07-16"]["cost_per_unit"]) == D("0.030400")
