"""The stock movement ledger, and the refusals that keep it honest.

The expensive lesson this repo already paid for: in COSTING a bad unit is one
wrong dish; in INVENTORY it is wrong on every movement for that item, forever,
and it compounds. CTN-6 read as one tin (6x), ILG cases read as bottles (6x),
Red Chilli (10x), Angostura (13x).

So the tests that matter most here are the ones asserting the ledger REFUSES:
an unprovable unit must raise, not guess, because a guess is invisible and
always errs toward stock lasting longer than it should.
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ledger import (BASE_UNITS, Movement, UnprovableUnit,      # noqa: E402
                    load_base_units, to_base)

LEDGER_DIR = ROOT / "data" / "ledger"
BASE_UNITS_FILE = ROOT / "data" / "item_base_units.csv"


def mv(**kw):
    base = dict(ts="2026-08-14", venue="stow", item_id="lightspeed:20445811",
                qty_base="700", base_unit="ml", direction="in",
                reason="receive", source_ref="invoice:ilg:1", actor="test")
    base.update(kw)
    return Movement(**base)


# ---- conversions -----------------------------------------------------------

@pytest.mark.parametrize("qty,unit,expect,base", [
    (Decimal(1), "kg", Decimal(1000), "g"),
    (Decimal("0.75"), "L", Decimal(750), "ml"),
    (Decimal(6), "ea", Decimal(6), "each"),
    (Decimal(500), "ml", Decimal(500), "ml"),
])
def test_declared_conversions(qty, unit, expect, base):
    got, got_base = to_base(qty, unit)
    assert (got, got_base) == (expect, base)


@pytest.mark.parametrize("unit", ["box", "tray", "bunch", "carton", "ctn", "", "case"])
def test_unprovable_units_raise_rather_than_guess(unit):
    """A box of what, how many? That number is not in the invoice."""
    with pytest.raises(UnprovableUnit):
        to_base(Decimal(1), unit)


# ---- row validation --------------------------------------------------------

def test_a_valid_row_passes():
    mv().validate()


def test_base_unit_must_be_g_ml_or_each():
    with pytest.raises(ValueError, match="g/ml/each"):
        mv(base_unit="box").validate()
    assert BASE_UNITS == {"g", "ml", "each"}


def test_negative_quantity_is_rejected():
    """Direction carries the sign. A negative qty plus direction 'out' is an
    addition nobody meant."""
    with pytest.raises(ValueError, match="negative"):
        mv(qty_base="-5").validate()


def test_item_id_must_be_namespaced_like_the_recipe_book():
    with pytest.raises(ValueError, match="namespaced"):
        mv(item_id="20445811").validate()


def test_unknown_reason_is_rejected():
    with pytest.raises(ValueError, match="unknown reason"):
        mv(reason="shrinkage").validate()


def test_every_row_must_be_traceable():
    with pytest.raises(ValueError, match="source_ref"):
        mv(source_ref="").validate()


# ---- base units ------------------------------------------------------------

def test_base_units_file_exists_and_refuses_conflicts():
    assert BASE_UNITS_FILE.exists(), "run scripts/build_item_base_units.py"
    with BASE_UNITS_FILE.open() as f:
        rows = list(csv.DictReader(f))
    assert rows

    usable = load_base_units()
    for r in rows:
        if r["conflict"] == "true":
            assert r["item_id"] not in usable, (
                f"{r['item_id']} ({r['item_name']}) has conflicting units and must not "
                f"be loadable — {r['note']}")
        else:
            assert r["base_unit"] in BASE_UNITS

    # Measured 2026-08-15: 21 packaged drinks are consumed as BOTH 'each' and
    # 'ml', and 7 items only in 'bunch'/'tray'. They are refused, not guessed.
    conflicted = [r for r in rows if r["conflict"] == "true"]
    assert conflicted, ("no conflicts at all is suspicious — the beer tins were "
                        "consumed in both 'each' and 'ml' when this was written")


# ---- the ledger itself -----------------------------------------------------

def test_booked_rows_are_all_in_base_units():
    files = sorted(LEDGER_DIR.glob("movements_*.csv"))
    if not files:
        pytest.skip("no ledger yet — run scripts/build_receive_movements.py --write")
    n = 0
    for path in files:
        with path.open() as f:
            for r in csv.DictReader(f):
                assert r["base_unit"] in BASE_UNITS, f"{path.name}: {r}"
                assert Decimal(r["qty_base"]) >= 0
                assert ":" in r["item_id"]
                n += 1
    assert n, "ledger files exist but hold no rows"


def test_on_hand_is_measured_forward_from_the_last_count(tmp_path, monkeypatch):
    """A count is TRUTH, not an adjustment: everything before it is superseded.
    If a count did not supersede, every stocktake would be added to the stock it
    was measuring."""
    import ledger as L
    monkeypatch.setattr(L, "LEDGER_DIR", tmp_path)

    L.append([
        mv(ts="2026-01-01", qty_base="1000", reason="receive", direction="in"),
        mv(ts="2026-01-02", qty_base="200", reason="sale", direction="out"),
        # a count on the 3rd says there are really 500 ml
        mv(ts="2026-01-03", qty_base="500", reason="count", direction="in",
           source_ref="stocktake:1", actor="zak"),
        mv(ts="2026-01-04", qty_base="100", reason="sale", direction="out"),
    ])
    bal = L.on_hand()
    got = bal[("stow", "lightspeed:20445811")]
    assert got == Decimal(400), (
        f"expected 500 counted - 100 sold = 400, got {got}. If this is 1200 the "
        f"count was added to the stock it was measuring instead of replacing it.")
