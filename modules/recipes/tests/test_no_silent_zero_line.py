"""
A recipe line worth $0 must never be publishable in silence.

THE GAP
-------
In convert_lightspeed_recipes.py the sub-recipe branch ends:

    else:
        eff = ls            # Lightspeed's own line cost
        full_ours = False

`ls` is 0 whenever the referenced product is not separately sold on the till,
carries no scraped cost, and matches none of the earlier branches. So the line
contributes nothing, the dish still totals, and `resolved_pct` still reads 100.
At the time of the audit 24 lines survived only on that gate, including the BBQ
Wings line on all 23 Wings Deal recipes. It is the same defect that let Choc
Brownie and the pizza menu-average contribute $0 to a $45 deal while the recipe
read as complete.

The 24 are gone from the data today. The PATH is not: it still returns zero and
still says nothing, so the next product that lands in it will be as quiet as the
last. This is the tripwire.

WHAT THIS GUARDS
----------------
- no sub-recipe line costs $0 (that path has no legitimate zero)
- a $0 direct line is allowed only for the handful of sub-cent garnishes that
  are deliberately left uncosted, and the list may not grow silently
- a recipe carrying any $0 line may not also claim to be fully on our book
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"

# Sub-cent garnishes and seasonings the costing deliberately leaves at $0 — see
# the "NOT DONE ON PURPOSE" note in convert_lightspeed_recipes.py about reading a
# "ml" line against a per-g price. Any NEW name here is a real gap, not a garnish.
KNOWN_ZERO = {
    "White Pepper", "Oregano Leaves Rubbed - Torino", "bicarb",
    "Dehydrated Lime Garnish", "Pickled Ginger", "Togarashi", "Cucumber",
    "White Miso", "Potato Starch", "Massenez Apple [700ml]",
    "Ginger Honey Syrup", "Yuzu Juice",
}


def _lines():
    if not BOOK.exists():
        return []
    book = json.loads(BOOK.read_text())["recipes"]
    return [(n, ln, r) for n, r in book.items() for ln in r.get("ingredients", [])]


def _is_zero(ln):
    try:
        return float(ln.get("eff_cost") or 0) == 0.0
    except (TypeError, ValueError):
        return True


def test_no_sub_recipe_line_costs_nothing():
    """There is no honest $0 sub-recipe. If a prep cannot be costed, the dish
    must be marked incomplete and the audit must shout — not quietly total."""
    bad = [f"{n}: {ln.get('name')} (qty {ln.get('qty')}, ls_cost {ln.get('ls_cost')})"
           for n, ln, _r in _lines()
           if ln.get("kind") == "subrecipe" and _is_zero(ln)]
    assert not bad, ("sub-recipe line costing $0 with the dish still totalling:\n  "
                     + "\n  ".join(bad[:12]))


def test_the_uncosted_garnish_list_does_not_grow_silently():
    names = {ln.get("name") for _n, ln, _r in _lines()
             if ln.get("kind") == "id" and _is_zero(ln)}
    new = names - KNOWN_ZERO
    assert not new, (
        "new ingredient(s) costing $0. If they are genuinely sub-cent garnishes, "
        f"add them to KNOWN_ZERO with a reason; otherwise they are a gap: {sorted(new)}")


def test_a_recipe_with_a_zero_line_does_not_claim_to_be_fully_costed():
    """`fully_our_book: true` is the flag the P&L and the pricing page trust.
    A dish with an uncosted line has not earned it."""
    bad = [f"{n}: {ln.get('name')}" for n, ln, r in _lines()
           if _is_zero(ln) and r.get("fully_our_book")]
    assert not bad, ("claims fully_our_book while carrying a $0 line:\n  "
                     + "\n  ".join(sorted(set(bad))[:12]))
