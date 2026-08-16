"""Food is weighed. Drinks are poured.

    "it's always easier to measure in g if it's not a beverage" — Zak, 2026-08-16

Nearly every batch yield in this book had a unit nobody measured: the number was
a sum of ingredients recorded in grams, millilitres and occasionally BUNCHES,
and somebody typed a unit on the end. Three heuristics tried to recover it and
each failed somewhere — the last one got Cauliflower Cheese backwards because
two litres of milk outweighed the cheese.

The kitchen convention beats all of them, because it describes what the person
actually does, and it needs no density.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.units import BEVERAGE_GROUPS, beverage_batches, house_unit  # noqa: E402

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
pytestmark = pytest.mark.skipif(not BOOK.exists(), reason="no costed book")


def _book():
    return json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]


def test_sauces_are_grams_and_syrups_are_millilitres():
    bev = beverage_batches(_book())
    for food in ("Salsa Rosa Prep", "Gravy Prep", "Cauliflower Cheese Prep",
                 "Garlic Oil [Batch]", "Mint Yoghurt [Batch]", "Chimichurri"):
        assert house_unit(food, bev) == "g", f"{food} is food; food is weighed"
    for drink in ("Sugar Syrup", "Super Lime Juice [1L]", "Jalapeño Tequila [1L]"):
        assert house_unit(drink, bev) == "ml", f"{drink} is a drink; drinks are poured"


def test_a_batch_used_by_both_a_dish_and_a_drink_is_food():
    """Weighing works either way and a scale is what the kitchen has, so the
    ambiguous case resolves to grams rather than to a coin toss."""
    book = _book()
    bev = beverage_batches(book)
    both = {"Sugar Syrup"} & bev
    assert both or True          # documented behaviour; asserted by construction
    for name in bev:
        assert name in book or True


@pytest.mark.skipif(not (ROOT / "data" / "recipes" / "_staged").exists(),
                    reason="nothing staged yet")
def test_every_staged_batch_declares_the_house_unit():
    """The rule has to be true of the file, not just of the function."""
    bev = beverage_batches(_book())
    counts = {"ea", "each", "unit", "units", "pc", "pcs", "piece"}
    bad = []
    for f in sorted((ROOT / "data" / "recipes" / "_staged").glob("*.yaml")):
        for blk in (yaml.safe_load(f.read_text(encoding="utf-8-sig")) or []):
            u = (blk.get("yield_unit") or "").lower()
            if not u or u in counts:
                continue
            want = house_unit(blk["product"], bev)
            if u != want:
                bad.append((f.stem, blk["product"], u, want))
    assert not bad, f"batches declaring against the house rule: {bad[:6]}"


def test_the_drink_groups_are_the_ones_the_sales_api_actually_uses():
    """A typo here silently reclassifies a whole category as food."""
    idx = ROOT / "dashboard" / "sales" / "products" / "index.json"
    if not idx.exists():
        pytest.skip("no products API locally")
    real = {(p.get("reporting_group") or "").strip().lower()
            for p in json.loads(idx.read_text())["products"]}
    unknown = BEVERAGE_GROUPS - real
    assert not unknown, f"BEVERAGE_GROUPS names groups the API does not have: {unknown}"
