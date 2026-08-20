"""A Family pizza sells every week; until 2026-08-20 it had no recipe anywhere.

88 Family products, $127,061 of lifetime revenue, Family Meatlovers sold THIS
week — and none in Produce, none in Back Office's export, so the whole family
booked at 100% GP and made up the largest block of Marilyna's uncosted revenue.
Meanwhile the weighed sheet had carried a `family` column since 2026-08-19,
annotated "here so the day a Family pizza is listed, the number is already
weighed". The pizzas were listed. Nothing connected them.

_add_family_pizzas connects them: a recipe per sold Family product from its
Large twin, with the sheet's family column taking over every line it governs.
These tests pin the connection and, more importantly, its EDGES — the wrong twin
or a flat-copied quantity would quietly cost one pizza as another, which is
worse than the 100% GP it replaces because nothing would ever flag it again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"


def _book():
    if not BOOK.exists():
        return None
    return json.loads(BOOK.read_text())["recipes"]


def _families(b):
    return {n: r for n, r in b.items() if r.get("family_from")}


def test_it_actually_found_the_pizzas():
    """Named, not counted — the two highest-revenue absences it was written for,
    plus the one that needed the normalised twin (its till name lost the comma)."""
    b = _book()
    if b is None:
        return
    fams = _families(b)
    if not fams:
        return          # no sales index in this checkout — nothing to say
    for name in ("Family Margherita", "Family Super House Special",
                 "Family  No Chorizo No Cry"):
        assert name in fams, f"{name} sells and should have a built recipe"
        assert float(b[name]["our_cost"]) > 0


def test_a_family_never_costs_less_than_its_large():
    """The bigger pizza cannot carry less. This is the exact shape the 0.716
    'lift' episode taught: Produce held Large figures below the weighed Regulars
    and a bigger pizza cannot carry less than a smaller one. A Family at or
    below its Large means a line was flat-copied or the twin is wrong."""
    b = _book()
    if b is None:
        return
    bad = []
    for name, r in _families(b).items():
        lg = b.get(r["family_from"])
        if not lg:
            bad.append(f"{name}: twin {r['family_from']!r} vanished")
            continue
        try:
            fc, lc = float(r["our_cost"]), float(lg["our_cost"])
        except (TypeError, ValueError):
            continue
        if lc > 0 and fc <= lc:
            bad.append(f"{name} ${fc:.2f} <= Large ${lc:.2f}")
    assert not bad, "a Family pizza costing no more than its Large:\n  " + "\n  ".join(bad)


def test_the_weighed_family_column_actually_landed():
    """The point of the whole exercise. The sheet says a Family base is 304 g;
    if the built recipe still carries the Large's 215 g, the pass created a
    plausible-looking pizza that ignores the one measurement it exists for."""
    b = _book()
    if b is None:
        return
    r = b.get("Family Margherita")
    if not r:
        return
    dough = [ln for ln in r["ingredients"] if "dough" in (ln.get("name") or "").lower()]
    assert dough, "Family Margherita has no dough line at all"
    assert float(dough[0]["qty"]) == 304, (
        f"Family base should be the sheet's 304 g, not {dough[0]['qty']}")
    assert dough[0].get("weighed"), "the dough line should carry the weighed stamp"


def test_an_inferred_quantity_says_so():
    """A line the sheet does not govern is scaled by the measured base ratio —
    an inference, and it must be marked as one. The no-rounding rule: a derived
    number that reads as a measurement is how the last bad lift propagated onto
    seven pizzas before anyone could see which numbers were real."""
    b = _book()
    if b is None:
        return
    for name, r in _families(b).items():
        for ln in r["ingredients"]:
            unit = str(ln.get("unit") or "").lower()
            if unit not in ("g", "ml"):
                continue
            assert ln.get("weighed") or ln.get("family_scaled"), (
                f"{name} / {ln.get('name')}: a mass line on a built Family "
                f"recipe must be either weighed (the sheet) or marked as "
                f"scaled — an unmarked quantity reads as a measurement")


def test_a_family_pizza_gets_one_15_inch_box():
    """Packaging is counted, never scaled — and it is the FAMILY box.

    A Family ships in the 15" (Zak, 2026-08-20), which Gulli invoices weekly as
    PBLTB15-U. For its first hours in the book the family wore the Large's 13"
    at $0.6426 instead of $0.7929 — 15c under on every family pizza, the
    flattering direction. Back Office holds no product for the 15", so the line
    carries the SUPPLIER identity, which recipe lines already do wherever
    Lightspeed has no product (b-e:, cub:, foodlink:).
    """
    b = _book()
    if b is None:
        return
    for name, r in _families(b).items():
        boxes = []
        for ln in r["ingredients"]:
            nm = (ln.get("name") or "").lower()
            # Actual packaging lines only. A bare word test caught
            # "Mushrooms [4Kg box]" — a topping whose PACK is a box — and
            # demanded one whole 4 kg box of mushrooms per pizza, which is the
            # exact misreading the packaging pass exists to prevent.
            if ("pizza box" in nm or "insert" in nm) and ln.get("unit") == "ea":
                assert float(ln.get("qty") or 0) == 1, (
                    f"{name}: packaging must be one whole unit, "
                    f"not {ln.get('qty')} of {ln.get('name')}")
                assert not ln.get("family_scaled"), (
                    f"{name}: a counted box carries a scaling marker — "
                    f"provenance describing a state that no longer exists")
                if "pizza box" in nm and "insert" not in nm:
                    boxes.append(ln)
        if boxes:
            assert [ln["ref"] for ln in boxes] == ["gulli:PBLTB15-U"], (
                f"{name}: a Family pizza ships in the 15\" "
                f"(gulli:PBLTB15-U), got {[ln['ref'] for ln in boxes]}")
