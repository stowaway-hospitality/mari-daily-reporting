"""Every batch in the staged book must still cost what the old book says.

WHY THIS IS THE GUARD THAT MATTERS
----------------------------------
A batch is a cost multiplier. `House BBQ Sauce [11L]` is $44.33 and feeds every
wings deal at Marilyna's; if it silently became $0.01, three products would
under-cost and nobody would look, because a cost that falls does not alarm.

And it very nearly could. The scrape records pack COUNTS with a junk unit:

    Heinz BBQ Sauce [4L]        2 ml     <- two 4-litre packs, not two millilitres
    Sunshine Smokey BBQ [3L]    1 ml     <- one 3-litre pack
    Avocado [Tray]              1 ml     <- one tray
    White Truffle Oil [250ml]   4 ml     <- four bottles

The converter resolves those against pack sizes. `modules.recipes.cost` cannot
and must not -- multiplying 2 ml by a per-ml rate is the arithmetically perfect,
physically absurd answer the unit guard exists to refuse. So materialisation
freezes each such line as `manual` with the cost it actually carries. Two lines
at $15.29 and $13.75 per pack reproduce $44.33 to the cent.

This asserts that the freezing worked, on every batch, rather than trusting that
it did. It is the difference between a migration that carried its costs and one
that quietly dropped them.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

STAGED = ROOT / "data" / "recipes" / "_staged" / "marilynas.yaml"
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"

pytestmark = pytest.mark.skipif(not (STAGED.exists() and BOOK.exists()),
                                reason="Mari is not staged yet (pre-Phase-2a)")

# Batches that deliberately do NOT reproduce, each for a reason recorded in the
# repo. Anything else failing is a real regression.
_EXPECTED_DIVERGENCE = {
    # The hand-authored record outranks the scrape (provenance is a rank, not an
    # order), and Zak superseded this recipe on 2026-08-15.
    "Chimichurri",
    # Follows Chimichurri, plus the g -> ml line fix in batch_yield_units.yaml.
    "J.J. Aioli [Batch]",
    # Open question: "1 ml" of Tandoori Sauce is 1 kg or the whole 1,116 g
    # batch, and the two readings differ by 12% on six products. Refuses on
    # purpose until somebody rules. See HANDOFF_20260816_phase2a.md.
    "Tandoori Chicken [2Kg]",
}


def test_every_staged_batch_reproduces_the_old_books_cost():
    from core.domain import CostSeries, load_cost_observations
    from modules.recipes.cost import cost_on, load_recipes, recipe_as_of

    blocks = {b["product"]: b
              for b in (yaml.safe_load(STAGED.read_text(encoding="utf-8-sig")) or [])}
    book = json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    recipes = load_recipes("marilynas", path=STAGED)
    costs = CostSeries(load_cost_observations())
    on = date.today()

    batches = [n for n, b in blocks.items() if b.get("yield_qty")]
    assert batches, "fixture sanity: the staged book should contain batches"

    failures = []
    for name in batches:
        if name in _EXPECTED_DIVERGENCE:
            continue
        r = recipe_as_of(recipes, name, on)
        if r is None:
            failures.append((name, "no version in force"))
            continue
        try:
            new = float(cost_on(r, costs, on, recipes=recipes))
        except Exception as e:  # noqa: BLE001
            failures.append((name, f"refused: {e}"))
            continue
        try:
            old = float(book[name]["our_cost"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(new - old) > max(0.005, abs(old) * 0.001):
            failures.append((name, f"{old:.4f} -> {new:.4f}"))

    assert not failures, f"batch cost(s) did not survive materialisation: {failures}"


def test_the_pack_count_lines_are_frozen_not_multiplied():
    """The specific trap, named. `House BBQ Sauce [11L]` is two pack counts
    wearing millilitre labels; if either line is ever unfrozen, the batch falls
    from $44.33 to about a cent and takes every wings deal down with it."""
    blocks = {b["product"]: b
              for b in (yaml.safe_load(STAGED.read_text(encoding="utf-8-sig")) or [])}
    sauce = blocks.get("House BBQ Sauce [11L]")
    if sauce is None:
        pytest.skip("House BBQ Sauce is not reachable from this venue")
    for line in sauce["ingredients"]:
        assert line.get("manual") and line.get("unit_cost_incl"), (
            f"{line.get('desc')!r} is a pack COUNT ({line.get('qty')} "
            f"{line.get('unit')}) and must carry its own cost, not be multiplied "
            f"by a per-{line.get('unit')} rate")
    total = sum(float(l["unit_cost_incl"]) * float(l["qty"])
                for l in sauce["ingredients"])
    assert abs(total - 44.33) < 0.01, f"batch reconstructs to ${total:.2f}, not $44.33"
