"""
One stock item costs one thing, however a recipe reaches it.

THE DEFECT
----------
Cooked beef brisket is braised once, in one batch, and reached two ways:

    Beef Burrito   225 g  via the subrecipe  ->  $13.95/kg
    12 pizzas       70 g  via ProductID 22491831 "Pizza Beef Brisket [Kg]" -> $25.89/kg

Zak: "it's the same beef brisket that's in beef burrito." Nobody puts raw brisket
on a pizza, so both are the same cooked meat and one product cannot cost two
things. 1.86x apart, and the cheaper reading is the one on the higher-volume
product, which is the direction that flatters GP.

TWO CAUSES, both fixed:

1. The two lines pointed at different identities. INGREDIENT_ALIAS now maps
   "Pizza Beef Brisket [Kg]" -> "Cooked Beef Brisket [1Kg]", exactly as it
   already did for the same onion filed under two ProductIDs.

2. Aliasing alone did NOT equalise them, which is the more interesting half. The
   subrecipe costing scales a prep's batch cost by Lightspeed's own line/batch
   ratio, on the stated assumption that `ls_line/ls_batch == qty/yield` so "the
   yield cancels". Where that identity fails — and for the brisket it failed by
   1.86x — the same batch yields two rates. A RECORDED yield (data/prep_yields.yaml)
   now beats the ratio, because qty/yield is on a common rate by construction.
   A yield merely inferred from the NAME bracket still does not: "[1Kg]" is a
   label, and reading it as a yield is what once costed Beef Burrito at $35.70.

WHAT THIS TEST PINS is the invariant, not the number: every recipe drawing on one
prep must pay the same rate per gram. The rate itself moves when the yield
estimate is replaced by a real weighing, and that is fine — it must move for ALL
of them together.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
pytestmark = pytest.mark.skipif(not BOOK.exists(), reason="costed book not built")


def _book():
    return json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]


def _rates(prep):
    """per-gram rate every recipe pays for `prep`, keyed by recipe name."""
    out = {}
    for nm, r in _book().items():
        for i in r.get("ingredients") or []:
            if i.get("ref") != prep:
                continue
            try:
                q = float(i.get("qty") or 0)
                c = float(i.get("eff_cost") or 0)
            except (TypeError, ValueError):
                continue
            if q > 0 and c > 0 and (i.get("unit") or "").lower() == "g":
                out[nm] = c / q
    return out


@pytest.mark.parametrize("prep", ["Cooked Beef Brisket [1Kg]", "Achiote Chicken [15Kg]"])
def test_every_recipe_pays_the_same_rate_for_one_prep(prep):
    rates = _rates(prep)
    if len(rates) < 2:
        pytest.skip(f"{prep}: fewer than two consumers in the book")
    lo, hi = min(rates.values()), max(rates.values())
    assert hi <= lo * 1.001, (
        f"{prep} costs different amounts per gram depending on which recipe asks:\n" +
        "\n".join(f"    {n:<40} ${v * 1000:,.2f}/kg" for n, v in sorted(rates.items(), key=lambda x: -x[1])))


def test_the_pizza_and_the_burrito_agree_on_the_brisket():
    """The finding itself, named. Re-break either half — drop the alias, or let
    the Lightspeed line ratio win over a recorded yield — and this reds."""
    rates = _rates("Cooked Beef Brisket [1Kg]")
    pizza = [v for n, v in rates.items() if "meatlovers" in n.lower() or "sanchez" in n.lower()]
    burrito = [v for n, v in rates.items() if "beef burrito" in n.lower()]
    assert pizza and burrito, f"expected both paths in the book, got {sorted(rates)}"
    assert abs(max(pizza) - max(burrito)) < 1e-6, (
        f"pizza pays ${max(pizza) * 1000:,.2f}/kg, burrito pays ${max(burrito) * 1000:,.2f}/kg")


def test_the_brisket_is_not_cheaper_than_the_raw_meat():
    """A sanity floor that needs no yield estimate to be true. Cooked brisket
    cannot cost less per kg than the raw brisket it is made from — B&E invoice
    it at $14.00/kg — because cooking removes water and adds nothing back.
    The old $13.93/kg failed this, and nothing was checking."""
    rates = _rates("Cooked Beef Brisket [1Kg]")
    if not rates:
        pytest.skip("brisket not in the book")
    assert min(rates.values()) * 1000 > 14.00, (
        f"cooked brisket at ${min(rates.values()) * 1000:,.2f}/kg is below the "
        f"$14.00/kg raw price — the yield is being read as more than went in")
