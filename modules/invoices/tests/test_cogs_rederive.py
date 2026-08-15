"""
cogs_list re-derive: a parser fix must reach old rows, and must not quietly
make anything cheaper on the way.

WHY THE RE-DERIVE EXISTS
------------------------
build_cogs_list used to ADD what was missing and skip every identity it already
had, which made every parser fix FORWARD-ONLY — it reached invoices that arrived
after it and nothing else. 344 ILG lines kept a case price in a per-bottle field
for months after `units_on_line` was corrected, because the correction could not
be applied to them.

WHY IT IS FENCED
----------------
The first four rows the re-derive unblocked outside ILG included two wrong ones,
both cheaper, both from parsers nobody had re-checked:

    Y&R  Villa Fresco Sangiovese 24 - OPO  $12.06 -> $6.03   ("24" is the vintage)
    Foodlink  FLOUR TORTILLAS 12X91GM      $33.60 -> $5.60   (CTN-6 split into 6)

A cost that comes out too high gets looked at; one that comes out too low
flatters GP and nothing asks. So the re-derive may raise a cost freely and holds
a lower one for a human.

THE REGRESSION THAT MATTERS: the ILG case->bottle fix drops the raw unit price
sixfold and holds cost_per_base_unit CONSTANT. It must NOT be caught by the
fence — if it is, the job this was written for cannot ship.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from modules.invoices.build_cogs_list import DERIVED, JUDGED, _cheaper, _refresh


def row(**kw):
    base = dict(cost_per_unit_incl_gst="10.00", pack_qty="1", pack_unit="L",
                cost_per_base_unit="10.0000", lightspeed_product="", basis="per_unit",
                pack_size="1", note="")
    base.update(kw)
    return base


def test_ilg_case_to_bottle_is_not_held():
    """THE ONE THAT MUST PASS. Veuve: the price falls 484.58 -> 80.76 because the
    pack falls 4.5 L -> 0.75 L with it. $/L is unchanged, so this is not a cost
    reduction and must go straight through."""
    old = row(cost_per_unit_incl_gst="484.58", pack_qty="4.5", cost_per_base_unit="107.6844")
    new = row(cost_per_unit_incl_gst="80.7633", pack_qty="0.750", cost_per_base_unit="107.6844")
    assert _cheaper(old, new) == ""
    moved, why = _refresh(old, new)
    assert why == ""
    assert old["cost_per_unit_incl_gst"] == "80.7633"
    assert old["pack_qty"] == "0.750"


def test_ilg_repack_line_rising_sixfold_is_not_held():
    """A repack line was 6x LOW (per-bottle price over a case pack). Correcting it
    RAISES the cost, which is the safe direction and always allowed."""
    old = row(cost_per_unit_incl_gst="50.85", pack_qty="4.2", cost_per_base_unit="12.1071")
    new = row(cost_per_unit_incl_gst="50.85", pack_qty="0.700", cost_per_base_unit="72.6429")
    assert _cheaper(old, new) == ""
    _, why = _refresh(old, new)
    assert why == ""


@pytest.mark.parametrize("was,now,unit", [
    ("12.0617", "6.0308", "ea"),      # Y&R Sangiovese — vintage read as a case count
    ("33.6000", "5.6000", "box"),     # Foodlink tortillas — CTN-6 split into 6
    ("100.0000", "50.0000", "kg"),
])
def test_a_materially_cheaper_rederive_is_held(was, now, unit):
    old, new = row(cost_per_base_unit=was, pack_unit=unit), row(cost_per_base_unit=now, pack_unit=unit)
    assert _cheaper(old, new) != ""
    moved, why = _refresh(old, new)
    assert moved == [] and why
    # HELD MEANS UNTOUCHED — not "applied with a warning".
    assert old["cost_per_base_unit"] == was


def test_held_row_is_left_completely_alone():
    old = row(cost_per_base_unit="33.6000", cost_per_unit_incl_gst="33.60", pack_unit="box")
    keep = dict(old)
    _refresh(old, row(cost_per_base_unit="5.6000", cost_per_unit_incl_gst="5.60", pack_unit="box"))
    assert old == keep


@pytest.mark.parametrize("was,now", [
    ("3.7687", "3.7686"),    # one unit in the last stored place
    ("9.4280", "9.4279"),
    ("73.8018", "73.8017"),
])
def test_last_digit_rounding_is_not_a_cost_reduction(was, now):
    """Re-deriving through a different division path moves the 4th decimal. If
    the fence catches these it will hold hundreds of legitimate ILG rows."""
    assert _cheaper(row(cost_per_base_unit=was), row(cost_per_base_unit=now)) == ""


def test_a_changed_pack_unit_is_not_comparable():
    """$3.69/ea and $2.95/L are different questions — a pack that became
    measurable is an improvement, not a discount."""
    assert _cheaper(row(cost_per_base_unit="3.6933", pack_unit="ea"),
                    row(cost_per_base_unit="2.9546", pack_unit="L")) == ""


def test_a_blank_base_unit_is_not_permission_to_go_cheaper():
    """B&E CHICKEN BREAST: the row moved $61.00 -> $12.20/kg while its pack stayed
    the whole 5 kg line, and the book divided by 5 again -> $2.44/kg, against
    $11.90-$12.20 on every other delivery of the same code. cost_per_base_unit
    was blank, so there was nothing to compare — absence of a second opinion is
    not permission, and the raw price is judged instead."""
    old = row(cost_per_unit_incl_gst="61.00", cost_per_base_unit="", pack_unit="kg")
    new = row(cost_per_unit_incl_gst="12.20", cost_per_base_unit="12.2000", pack_unit="kg")
    assert _cheaper(old, new) != ""
    moved, why = _refresh(old, new)
    assert moved == [] and why
    assert old["cost_per_unit_incl_gst"] == "61.00"


def test_filling_a_blank_is_not_a_move():
    assert _cheaper(row(cost_per_base_unit=""), row(cost_per_base_unit="5.0000")) == ""
    moved, why = _refresh(row(cost_per_base_unit=""), row(cost_per_base_unit="5.0000"))
    assert why == "" and any("cost_per_base_unit" in m for m in moved)


@pytest.mark.parametrize("field", JUDGED)
def test_a_humans_judgement_is_never_re_derived(field):
    """14 ILG rows carry a hand-set basis and a bridged product name. A re-derive
    that overwrote them would silently delete the work."""
    old = row(**{field: "HUMAN"})
    _refresh(old, row(**{field: "parser", "cost_per_base_unit": "20.0000"}))
    assert old[field] == "HUMAN"


@pytest.mark.parametrize("field", DERIVED)
def test_every_derived_field_actually_tracks_its_source(field):
    old, new = row(), row(**{field: "99.9999", "cost_per_base_unit": "20.0000"})
    _refresh(old, new)
    assert old[field] == new[field]


def test_an_unchanged_invoice_moves_nothing():
    """Idempotence: run it twice, the second run reports no move."""
    old = row()
    moved, why = _refresh(old, row())
    assert moved == [] and why == ""
