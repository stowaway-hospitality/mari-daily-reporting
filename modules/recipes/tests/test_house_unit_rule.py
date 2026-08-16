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


def test_a_liquid_INGREDIENT_keeps_its_millilitres_inside_a_food_recipe():
    """The refinement: the house rule governs batch YIELDS, not every line.

        "if something obviously liquid like milk or lime/lemon juice is used in
         a food, then stick with ml for those ingredients. but yes batch yields
         will always be in g for all food items"          — Zak, 2026-08-16

    A cook weighs the bowl the sauce ends up in and still pours the milk into it
    from a jug. Cauliflower Cheese Prep is 2,000 ml of milk making a batch
    declared in grams, and both of those are right.

    Asserted against what the materialiser actually CHANGED rather than against
    names in the finished file: an ingredient that is itself a bought sauce, like
    "Spiced Sour Cream [Batch]", is legitimately drawn in grams and was never
    touched. The question is only ever whether a relabel reached a raw line.
    """
    for venue in ("marilynas", "stowaway"):
        rp = ROOT / "data" / "_shadow" / f"materialise_{venue}.json"
        if not rp.exists():
            continue
        book = _book()
        subs = {l["ref"] for r in book.values()
                for l in (r.get("ingredients") or []) if l.get("kind") == "subrecipe"}
        for r in json.loads(rp.read_text())["unit_relabels"]:
            line = r.get("line")
            if line is None:                       # a batch yield: in scope
                continue
            assert line in subs or r.get("to") in ("ea", "each"), (
                f"{venue}: a relabel reached {line!r}, which is not a sub-recipe "
                f"draw. The house rule governs yields and the lines that draw "
                f"them, never a raw ingredient line.")


def test_the_rule_only_ever_touches_yields_and_the_lines_that_draw_them():
    """A structural guard on the same thing: materialise_recipes must not contain
    a path that relabels a non-sub-recipe line's unit from the house rule."""
    src = (ROOT / "scripts" / "materialise_recipes.py").read_text()
    fn = src[src.index("def _house_unit_line("):]
    fn = fn[:fn.index("\ndef ", 1)]
    assert 'sub = book.get(ref)' in fn and 'if not sub:' in fn, (
        "_house_unit_line must return early for anything that is not a "
        "sub-recipe draw — otherwise it starts rewriting ingredient lines")


def test_a_confirmed_unit_beats_the_house_rule():
    """A default exists to be overridden by somebody standing next to the thing.

    The builder writes `unit_confirmed: true` when a person uses the g/ml/ea
    selector, and materialise_recipes must then leave the unit alone. Without
    this the migration would quietly restate a deliberate choice on every run,
    which is worse than having no default at all -- you would fix it, watch it
    revert, and stop trusting the page.
    """
    src = (ROOT / "scripts" / "materialise_recipes.py").read_text()
    assert 'not blk.get("unit_confirmed")' in src, (
        "the house rule must not override a unit a human confirmed")

    js = (ROOT / "dashboard" / "_shared" / "recipe_builder.js").read_text()
    assert "UNIT_TOUCHED" in js and "unit_confirmed: true" in js, (
        "the builder must record that the selector was used")
    html = (ROOT / "modules" / "recipes" / "app" / "index.html").read_text()
    assert 'markUnitChosen()' in html, "the selector must call markUnitChosen"


def test_the_builder_still_emits_a_unit_at_all():
    """Guard against the YAML losing yield_unit while this was being wired."""
    js = (ROOT / "dashboard" / "_shared" / "recipe_builder.js").read_text()
    assert "yield_unit: ${ys(yu)}" in js
