"""An ingredient swap must repoint the line AND drop Produce's price for it.

A swap says "Produce records bottle A, the bar pours bottle B". The subtle way
to get it wrong is to rewrite the name and keep the number: Produce's line cost
was computed for the bottle we no longer pour, and every downstream consumer
(the whole-vs-fraction decision, the ratio path, the audit's "Produce says X, we
say Y" comparison) treats that number as a statement about THIS line. Carrying
it over would let a price for the wrong product go on speaking for the right
one — and in the case that opened this file, the stale number was $0.00, which
is exactly the shape that flatters GP.

Also pins the scoping: (recipe, ingredient), never name-globally. A global
rewrite would swallow the next recipe that legitimately uses the old bottle.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from convert_lightspeed_recipes import apply_ingredient_swaps  # noqa: E402

SWAPS = ROOT / "data" / "recipe_ingredient_swaps.yaml"


def _specs():
    if not SWAPS.exists():
        return []
    return yaml.safe_load(SWAPS.read_text()) or []


def test_a_swap_repoints_the_line_and_drops_produces_price():
    spec = next(iter(_specs()), None)
    if spec is None:
        return
    rec = {spec["recipe"]: {"ingredients": [
        {"name": spec["from"], "qty": "60", "unit": "mL", "cost": "0.00"},
        {"name": "Lime [Each]", "qty": "1", "unit": "ea", "cost": "0.80"},
    ]}}
    assert apply_ingredient_swaps(rec) == 1
    lines = rec[spec["recipe"]]["ingredients"]
    assert lines[0]["name"] == spec["to"]
    assert lines[0]["cost"] == "", "Produce's price belonged to the old bottle"
    assert lines[0]["qty"] == "60", "a swap changes the product, never the amount"
    assert lines[1] == {"name": "Lime [Each]", "qty": "1", "unit": "ea",
                        "cost": "0.80"}, "untouched lines stay untouched"


def test_a_swap_is_scoped_to_its_own_recipe():
    """The same ingredient in a DIFFERENT recipe must not be rewritten."""
    spec = next(iter(_specs()), None)
    if spec is None:
        return
    rec = {"Some Other Drink": {"ingredients": [
        {"name": spec["from"], "qty": "30", "unit": "mL", "cost": "2.00"},
    ]}}
    assert apply_ingredient_swaps(rec) == 0
    assert rec["Some Other Drink"]["ingredients"][0]["name"] == spec["from"]


def test_a_swap_is_idempotent():
    """Re-running, or Produce fixing it at source, must not double-apply."""
    spec = next(iter(_specs()), None)
    if spec is None:
        return
    rec = {spec["recipe"]: {"ingredients": [
        {"name": spec["to"], "qty": "60", "unit": "mL", "cost": "4.45"},
    ]}}
    assert apply_ingredient_swaps(rec) == 0
    assert rec[spec["recipe"]]["ingredients"][0]["cost"] == "4.45"


def test_every_swap_names_a_replacement_that_the_cost_book_can_price():
    """A swap that points at an unpriced product trades a known $0 for a new one.

    The whole reason to swap is that the recorded bottle has no price. Pointing
    at a second bottle that also has none would move the defect rather than fix
    it, and would do it silently — the recipe would still cost $0 but the audit
    line would now name a product Zak confirmed, which reads as resolved.
    """
    import csv
    specs = _specs()
    if not specs:
        return
    costed = set()
    path = ROOT / "data" / "cogs_list.csv"
    if not path.exists():
        return
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        for key in ("product_name", "invoice_description", "description"):
            v = (r.get(key) or "").strip()
            if v:
                costed.add(v)
    for spec in specs:
        assert spec["to"] in costed, (
            f"{spec['recipe']}: swapped to {spec['to']!r}, which the cost book "
            f"has no row for — that is the same $0 under a different name")


def test_every_swap_records_who_said_so():
    """No arithmetic can tell you what is in the bottle. Only Zak can."""
    for spec in _specs():
        assert spec.get("why"), f"{spec.get('recipe')}: a swap needs a stated reason"
