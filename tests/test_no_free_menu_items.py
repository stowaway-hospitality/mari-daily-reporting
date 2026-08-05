"""A recipe that SELLS but costs $0 is the most dangerous number in the book.

It doesn't look broken — it looks like 100% GP, the best line on the menu. That is
exactly the failure CLAUDE.md warns about: "Errors that flatter you (too-high GP,
low cost) are the dangerous ones."

It happened for real. Lightspeed Produce holds "Unico Zelo Terra Cotta - Bottle"
and "De La Grosse Beaujolais - Bottle" as recipes with NO ingredient lines at all,
so a $73 and a $110 bottle both costed $0 and reported infinite margin. The fix
prices an empty "- Bottle" recipe as one full pack of the product it names.

This test locks that in. Anything NEW that sells for money and costs nothing fails
here rather than shipping a flattering GP to the dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"

# Known and accepted, each for a stated reason. Keep this list SHORT and justified —
# an entry here is a promise that the $0 is understood, not that it's fine.
KNOWN_ZERO = {
    # Sea Foam is discontinued — it is on the reorder skill's exclusion list, so no
    # invoice will ever price it again, yet the POS still sells it at $28 on
    # delivery. Left visible rather than back-filled: the fix is delisting it, not
    # inventing a cost for a wine we cannot buy.
    "Sea Foam Pet Nat D",
}


def _sellable_zero_cost():
    recipes = json.loads(COSTED.read_text())["recipes"]
    out = []
    for name, r in recipes.items():
        if r.get("is_prep"):
            continue                      # a prep's POS price is a placeholder
        if float(r.get("sell_incl") or 0) <= 0:
            continue                      # not sold, so no GP to overstate
        if float(r.get("our_cost") or 0) == 0:
            out.append((name, r.get("sell_incl")))
    return out


def test_nothing_sold_costs_zero():
    offenders = [(n, s) for n, s in _sellable_zero_cost() if n not in KNOWN_ZERO]
    assert not offenders, (
        "these SELL but cost $0, so they report 100% GP:\n"
        + "\n".join(f"  ${s} {n}" for n, s in sorted(offenders, key=lambda x: -x[1]))
        + "\nCost them, or add to KNOWN_ZERO with the reason."
    )


def test_known_zero_list_stays_honest():
    """Once one is fixed, drop it from the list — a stale allowlist hides the next bug."""
    still = {n for n, _ in _sellable_zero_cost()}
    assert not (KNOWN_ZERO - still), (
        f"these are costed now and should leave KNOWN_ZERO: {sorted(KNOWN_ZERO - still)}"
    )


@pytest.mark.parametrize("name,expected", [
    ("Unico Zelo Terra Cotta - Bottle", 21.89),
    ("De La Grosse Beaujolais - Bottle", 34.58),
])
def test_empty_bottle_recipe_costs_one_whole_bottle(name, expected):
    """The regression itself: an empty "- Bottle" recipe is one full pack, and the
    number is the one Zak confirmed off the supplier invoice, incl GST."""
    r = json.loads(COSTED.read_text())["recipes"][name]
    assert float(r["our_cost"]) == pytest.approx(expected, abs=0.02)
