"""
ILG: the pack must describe ONE of whatever qty counts.

THE DEFECT THIS GUARDS
----------------------
`units_on_line` fixed the COUNT — a bare "1" against a Pack of "6x700ML" is one
CARTON, so the line bought 6 bottles and the unit price is the carton total / 6.
It said nothing about the PACK, and `cost_per_base_unit` is price / pack
(build_cogs_list), so the two are the numerator and denominator of ONE division
and have to describe the same thing.

Veuve Clicquot, ILG 235-1323, "6x750ML", Qty "1", $484.58 inc:

    before  price $484.58 (the CASE)   pack 4.5 L (the CASE)   -> $107.68/L  RIGHT
    broken  price  $80.76 (a BOTTLE)   pack 4.5 L (the CASE)   -> $17.95/L   6x LOW
    after   price  $80.76 (a BOTTLE)   pack 0.75 L (a BOTTLE)  -> $107.68/L  RIGHT

The middle row is what re-parsing WITHOUT this fix produces, and it is the
dangerous direction: a cost that under-states flatters GP. It is also invisible
to every existing guard, because $17.95/L is a perfectly self-consistent number
— which is exactly why the two errors cancelling in the "before" row let this
sit in the book for months.

So these tests assert the RELATIONSHIP, not any total: whatever the pack cell,
`price / pack` must equal `line_total / (units x one_unit_size)`. Re-break the
pack to the case and every one of them reds by the carton factor.
"""

from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from modules.invoices.parsers.ilg import one_unit_pack, units_on_line

# (pack cell, inner size in base units, base unit) — the shapes ILG actually
# prints, taken from the 54-invoice corpus. Not a count: a family.
MEASURED_PACKS = [
    ("6x700ML", D("0.7"), "L"), ("24x330ML", D("0.33"), "L"),
    ("24x375ML", D("0.375"), "L"), ("6x750ML", D("0.75"), "L"),
    ("12x1.25LT", D("1.25"), "L"), ("6x1LT", D("1"), "L"),
    ("24x355ML", D("0.355"), "L"), ("1x15LT", D("15"), "L"),
    ("8x500ML", D("0.5"), "L"), ("12x150ML", D("0.15"), "L"),
    ("30x375ML", D("0.375"), "L"), ("3x700ML", D("0.7"), "L"),
]
# Packs whose inner size is NOT a measure. These must return None so the line
# keeps the generic pack reader rather than being given a made-up volume.
UNMEASURED_PACKS = ["1xKEG49.", "1xKEG50", "", None, "6xPACK", "CTN"]


@pytest.mark.parametrize("cell,size,unit", MEASURED_PACKS)
def test_pack_cell_yields_one_inner_unit(cell, size, unit):
    """"6x700ML" is six 700 mL bottles — one unit is 0.7 L, never the 4.2 L case."""
    got = one_unit_pack(cell)
    assert got is not None, f"{cell} should read as a measured pack"
    qty, base = got
    assert base == unit
    assert qty == size


@pytest.mark.parametrize("cell", UNMEASURED_PACKS)
def test_unmeasured_pack_refuses(cell):
    """A keg states no volume in the cell. Refuse rather than invent one."""
    assert one_unit_pack(cell) is None


@pytest.mark.parametrize("cell,size,_u", MEASURED_PACKS)
def test_one_unit_is_never_the_whole_case(cell, size, _u):
    """The regression itself: for a multi-pack the answer must be strictly
    SMALLER than the carton. If this ever equals the case volume, the 6x-low
    cost is back."""
    per = int(cell.split("x")[0])
    qty, _ = one_unit_pack(cell)
    case = qty * per
    if per > 1:
        assert qty < case, f"{cell}: pack {qty} is the whole case, not one unit"


# Real lines, verbatim from ILG invoice 03729959 (see test_ilg_03729959.py) plus
# Veuve from 03694253. (pack, qty cell, cost, total_ex, tot_incl, expected units)
REAL_LINES = [
    ("6x700ML",   "1",   D("156.94"), D("156.94"), D("174.49"), 6),   # APEROL
    ("6x700ML",   "0/1", D("282.81"), D("48.32"),  D("53.61"),  1),   # BUFFALO TRACE
    ("6x700ML",   "3",   D("280.10"), D("840.30"), D("929.90"), 18),  # ROOSTER ROJO
    ("6x700ML",   "0/2", D("235.54"), D("80.47"),  D("89.44"),  2),   # SAILOR JERRY
    ("24x355ML",  "1",   D("54.41"),  D("54.41"),  D("61.71"),  24),  # CORONA
    ("12x1.25LT", "1",   D("38.60"),  D("38.60"),  D("44.32"),  12),  # COCA COLA
    ("24x500ML",  "1",   D("49.71"),  D("49.71"),  D("56.54"),  24),  # S.PELLEGRINO
    ("6x750ML",   "1",   D("435.14"), D("435.14"), D("484.58"), 6),   # VEUVE CLICQUOT
]


@pytest.mark.parametrize("pack,qcell,cost,total,tot_incl,units", REAL_LINES)
def test_price_and_pack_divide_the_same_thing(pack, qcell, cost, total, tot_incl, units):
    """THE INVARIANT. price/pack must equal line_total/(units x one unit).

    Both sides are derived independently — the left from what the parser puts on
    the line, the right straight from the supplier's own inc-GST total — so they
    can only agree if the count and the pack describe the same unit."""
    n = units_on_line(pack, qcell, cost, total)
    assert n == D(units), f"{pack} {qcell}: expected {units} units, got {n}"
    size, _unit = one_unit_pack(pack)
    unit_price = tot_incl / n
    cents = D("0.000001")
    assert (unit_price / size).quantize(cents) == (tot_incl / (n * size)).quantize(cents)


@pytest.mark.parametrize("pack,qcell,cost,total,tot_incl,units", REAL_LINES)
def test_case_pack_would_undercost_by_the_carton_factor(pack, qcell, cost, total, tot_incl, units):
    """RE-BREAK IT AND THIS REDS. Dividing the per-unit price by the CASE — what
    the generic pack reader does with "6x700ML" — is low by exactly the carton
    count, and low is the direction that flatters GP."""
    per = int(pack.split("x")[0])
    size, _ = one_unit_pack(pack)
    n = units_on_line(pack, qcell, cost, total)
    right = (tot_incl / n) / size
    wrong = (tot_incl / n) / (size * per)          # the case reading
    if per > 1:
        assert wrong < right
        assert (right / wrong).quantize(D("0.000001")) == D(per)


def test_veuve_lands_on_its_known_per_bottle_price():
    """The figure the 2026-08-09 handoff named: $484.58 for 6x750ML is $80.76 a
    bottle at $107.68/L — NOT $484.58 a bottle, and NOT $17.95/L."""
    n = units_on_line("6x750ML", "1", D("435.14"), D("435.14"))
    assert n == 6
    per_bottle = (D("484.58") / n).quantize(D("0.01"))
    assert per_bottle == D("80.76")
    size, unit = one_unit_pack("6x750ML")
    assert (size, unit) == (D("0.75"), "L")
    assert ((D("484.58") / n) / size).quantize(D("0.01")) == D("107.68")


def test_parser_leaves_the_pack_alone_when_the_count_is_unprovable():
    """An unprovable line is priced as ONE unit at the WHOLE line total, so one
    unit is the whole line and the carton reading is the right-or-high one. The
    parser must not assert a bottle-sized pack against a carton-sized price."""
    # cost x cases != total, and the repack ratio does not land -> not provable
    assert units_on_line("6x700ML", "1", D("100.00"), D("999.99")) is None
