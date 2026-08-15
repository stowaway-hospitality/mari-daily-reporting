"""A line the restater corrects must be priced from OUR book, not Lightspeed's.

THE ORDERING BUG. `cost_of()` decides `our_cost` by matching the ingredient's base
unit against the unit on the recipe line. `_restate_pack_quantities()` CORRECTS
that unit — it is the function that turns Produce's "0.05 ml" of a thing we hold
per EACH into "1 ea" — and it runs afterwards. So the match was tried against the
wrong unit, failed, the line fell through to Lightspeed's per-line figure, and
nobody went back once the unit was right.

It was the largest single Lightspeed dependency in the book: 47 gluten-free pizza
base lines. We hold the base at $4.4375/ea from B&E, the recipes want one base,
and the two never met.

NO MONEY MOVES, and that is the point. The restatement is defined as
`implied = eff / rate`, so `rate x implied == eff` identically. Attributing the
line changes which column the same dollar sits in, nothing else — 88 lines moved,
$390 of cost, 0 recipes changed total.

Guard both halves: the attribution happened, and the money did not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
pytestmark = pytest.mark.skipif(not BOOK.exists(), reason="costed book not built")


def _book():
    d = json.loads(BOOK.read_text(encoding="utf-8-sig"))
    return d.get("recipes") or d


def test_the_gluten_free_base_is_priced_from_our_own_book():
    """The canonical case. Re-break the attribution and this reds."""
    lines = [ln for r in _book().values() for ln in (r.get("ingredients") or [])
             if ln.get("ref") == "lightspeed:22996511"]
    assert lines, "the gluten-free base is not in the book at all"
    unpriced = [ln for ln in lines if ln.get("our_cost") in (None, "", 0, "0")]
    assert not unpriced, (
        f"{len(unpriced)} of {len(lines)} gluten-free base lines still fall through "
        f"to Lightspeed — check _restate_pack_quantities still sets our_cost")
    for ln in lines:
        assert ln["unit"] == "ea", ln
        assert float(ln["our_cost"]) == pytest.approx(4.4375, rel=1e-6), ln


def test_every_restated_line_carries_our_price():
    """The general rule, not just the one example: if a line's unit was corrected
    to the unit our book holds the ingredient in, our book must be pricing it.

    A restated line is recognisable after the fact — its unit is a base unit and
    it has a ref we hold — so this asserts the population, not a count.
    """
    ings = ROOT / "data" / "ingredients.json"
    if not ings.exists():
        pytest.skip("ingredients feed not built")
    have = {i["id"]: (i.get("pack_unit") or "").lower()
            for i in json.loads(ings.read_text(encoding="utf-8-sig"))["ingredients"]
            if i.get("id") and i.get("cost_per_base_unit")}
    stranded = []
    for name, r in _book().items():
        for ln in (r.get("ingredients") or []):
            ref = ln.get("ref")
            if ln.get("kind") != "id" or ref not in have:
                continue
            if (ln.get("unit") or "").lower() != have[ref]:
                continue                      # units genuinely differ — not this rule
            if ln.get("our_cost") in (None, "", 0, "0"):
                stranded.append(f"{name} -> {ln.get('name')} ({ln.get('qty')}{ln.get('unit')})")
    assert not stranded, (
        "lines whose unit matches our own book but which are still priced by "
        "Lightspeed:\n  " + "\n  ".join(stranded[:12]))


def test_the_restated_lines_cost_exactly_quantity_times_our_rate():
    """The restater's contract, asserted where it applies.

    NOT BOOK-WIDE, and the first cut of this test was wrong to try. `our_cost`
    being set does not mean it was USED: `_trust_direct()` refuses our rate when it
    disagrees with Lightspeed's line by too much, and the whole-pack path prices a
    line at a full container regardless. Heinz BBQ Sauce carries our $0.003475/ml
    and costs $30.58 for "2" — a whole 4 L jug, correctly. Asserting
    eff == qty x rate everywhere flags twelve such lines that are all fine.

    So this is scoped to the restated population, whose defining property is that
    the restatement was DERIVED from the cost: implied = eff / rate, hence
    rate x implied == eff exactly. The gluten-free base is the whole of that
    population that matters — 47 lines, the largest single dependency there was.
    """
    lines = [ln for r in _book().values() for ln in (r.get("ingredients") or [])
             if ln.get("ref") == "lightspeed:22996511"]
    assert lines
    for ln in lines:
        q, rate, eff = float(ln["qty"]), float(ln["our_cost"]), float(ln["eff_cost"])
        assert eff == pytest.approx(q * rate, rel=1e-6), ln
        # ...and it is still the same dollar Lightspeed was charging, to the cent:
        # attribution, not repricing.
        assert eff == pytest.approx(float(ln["ls_cost"]), abs=0.01), ln
