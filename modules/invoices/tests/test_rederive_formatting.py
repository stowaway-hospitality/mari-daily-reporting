"""
A re-derive must fire on a changed PRICE, not on a changed number of zeros.

The DERIVED fields were compared as text, so "11.00" and "11.0000" read as a
change — and because the re-derive WRITES on any difference, cogs_list.csv
churned on every poll. The 2026-08-16 poller log shows the same JFC ramen line
going 11.00 -> 11.0000 and back to 11.00 inside ONE run, and Foodlink's
schnitzel doing 56.00 -> 56.0000 -> 56.00, because two rows carry two spellings
of one description and each was re-derived toward the other's formatting.

The real cost is camouflage, not churn: every run printed a handful of "the
parser now reads them differently" lines that meant nothing, which is precisely
the noise a genuine repricing has to be noticed in.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices.build_cogs_list import _refresh, _same_number  # noqa: E402


@pytest.mark.parametrize("a,b", [
    ("11.00", "11.0000"),      # the JFC ramen line, both directions
    ("11.0000", "11.00"),
    ("56.00", "56.0000"),      # Foodlink schnitzel
    ("0.0122", "0.01220"),
    ("2.3200", "2.32"),
    ("14", "14.000000"),
])
def test_the_same_number_written_differently_is_not_a_change(a, b):
    assert _same_number(a, b)


@pytest.mark.parametrize("a,b", [
    ("11.00", "11.50"),        # a real repricing must still show
    ("61.0000", "12.2000"),
    ("14.00", "13.99"),
    ("0.0122", "0.0124"),
])
def test_a_real_price_move_is_still_a_change(a, b):
    assert not _same_number(a, b)


@pytest.mark.parametrize("a,b", [
    ("", "11.00"),             # a fill is a change, not a formatting wobble
    ("11.00", ""),
    ("Gulli", "Gulli"),        # non-numeric falls through to the text compare
    ("ea", "g"),
    ("per_unit", "per_kg"),
])
def test_non_numeric_and_blank_fall_through(a, b):
    assert not _same_number(a, b)


def _row(**kw):
    base = {"supplier": "JFC", "cost_per_unit_incl_gst": "11.00",
            "pack_qty": "1", "pack_unit": "ea", "cost_per_base_unit": "11.00",
            "basis": "per_unit", "invoice_description": "SOMI Shoyu", "note": ""}
    base.update(kw)
    return base


def test_a_reformat_alone_produces_no_moves_and_writes_nothing():
    old = _row(cost_per_unit_incl_gst="11.00", cost_per_base_unit="11.00")
    new = _row(cost_per_unit_incl_gst="11.0000", cost_per_base_unit="11.0000")
    before = dict(old)
    moved, held = _refresh(old, new)
    assert held == ""
    assert moved == [], f"a pure reformat was reported as real work: {moved}"
    assert old == before, "the row was rewritten for no reason"


def test_a_real_increase_still_moves_the_row():
    old = _row(cost_per_unit_incl_gst="11.00")
    new = _row(cost_per_unit_incl_gst="12.5000")
    moved, held = _refresh(old, new)
    assert held == ""
    assert any("cost_per_unit_incl_gst" in m for m in moved)
    assert old["cost_per_unit_incl_gst"] == "12.5000"


def test_a_unit_change_still_moves_even_when_the_number_is_unchanged():
    # The San Pellegrino class of fix: same price, wrong unit.
    old = _row(pack_unit="can")
    new = _row(pack_unit="ml")
    moved, held = _refresh(old, new)
    assert any("pack_unit" in m for m in moved)


def test_a_cheaper_rederive_is_still_held_regardless_of_formatting():
    # The guard that matters most must not be weakened by this change: a
    # re-derive that LOWERS a cost flatters GP and still has to be held.
    old = _row(cost_per_unit_incl_gst="61.0000", cost_per_base_unit="61.0000")
    new = _row(cost_per_unit_incl_gst="12.2000", cost_per_base_unit="12.2000")
    moved, held = _refresh(old, new)
    assert held, "a large drop must still be held for review"
    assert old["cost_per_unit_incl_gst"] == "61.0000", "held rows stay untouched"
