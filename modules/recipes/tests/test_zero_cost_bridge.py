"""A product with NO cost can now be given one — and only in the one safe case.

THE LOOP THAT NEVER TERMINATED. build_costs' bridge turns a supplier invoice into
a ProductID's cost by converting the price into that ProductID's own unit, and it
took that unit from `seed_conv`, which is built from the product's EXISTING seed.
So a product with no cost had no seed, therefore no conversion, therefore the
bridge emitted nothing, therefore it still had no cost — no matter how many
invoices arrived for it. Every step was individually correct.

What that cost, measured on 2026-08-14:

    22962978 White Pepper   4 recipes seasoning for free (10 g, 10 g, 10 g, 7.5 g)
    22962975 bicarb         2 marinations at 50 g each — the velveting agent
    22874517 Yuzu Juice     Shiba Highball, 10 ml, which was HALF the drink's cost

Back Office states `Unit` for all three — g, g, ml. Only `CostPriceIncTax` was
missing. The basis was never unknown; it was simply not being read from the one
file that had it.

THE RULE IS DELIBERATELY THE NARROWEST ONE THAT WORKS. The magnitude guard that
protects every other bridged row compares the new rate against the product's seed
price — and a product with no seed has no seed price, so that guard cannot fire
here. The fallback therefore refuses anything requiring arithmetic: it applies
only when the invoice is ALREADY in the unit Back Office declares, so there is no
divisor, no scale factor and nothing inferred. g->g and ml->ml, or nothing.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_costs import bo_declared_units  # noqa: E402

COSTS = ROOT / "data" / "costs.csv"
pytestmark = pytest.mark.skipif(not COSTS.exists(), reason="costs.csv not built")


def _latest(ingredient):
    rows = [r for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if r["ingredient"] == ingredient]
    return max(rows, key=lambda r: r["observed_on"]) if rows else None


def test_only_base_units_are_declared():
    """"unit", "each" and Lightspeed's "UNIT" default say nothing about how a
    recipe portions a thing. Letting those through is how a keg price lands on a
    per-ml beer, so the map holds g and ml and nothing else."""
    units = bo_declared_units()
    assert units, "no Back Office units were read at all"
    assert set(units.values()) <= {"g", "ml"}, sorted(set(units.values()))
    assert all(k.startswith("lightspeed:") for k in units)


@pytest.mark.parametrize("ingredient,unit,rate,who", [
    ("lightspeed:22962978", "g", 0.032400, "White Pepper <- b-e:31254 PEPPER WHITE GROUND SS"),
    ("lightspeed:22962975", "g", 0.004050, "bicarb <- b-e:12429 BI-CARB SODA 2KG"),
    ("lightspeed:22874517", "ml", 0.071290, "Yuzu Juice <- foodlink:103169 JUICE LIME YUZU 1LT"),
])
def test_the_three_zero_cost_products_now_carry_the_invoice(ingredient, unit, rate, who):
    """Each had NO cost row at all. Re-break bo_declared_units and these red."""
    r = _latest(ingredient)
    assert r is not None, f"{who}: no cost observation at all — the bridge is not reaching it"
    assert r["unit"] == unit, (who, r["unit"])
    assert float(r["cost_per_unit"]) == pytest.approx(rate, rel=1e-6), (who, r["cost_per_unit"])
    # ...and it came from an INVOICE, not a seed. A seeded figure here would mean
    # the zero was papered over rather than sourced.
    assert not r["source_invoice"].startswith(("bo-seed", "ls-recipe-seed",
                                               "bo-ingredient-seed", "recipe-bridge-seed")), r


def test_no_recipe_line_of_these_three_still_costs_nothing():
    """The point of the exercise, stated as the outcome rather than the mechanism."""
    import json
    book = ROOT / "data" / "lightspeed_recipes_costed.json"
    if not book.exists():
        pytest.skip("costed book not built")
    d = json.loads(book.read_text(encoding="utf-8-sig"))
    d = d.get("recipes") or d
    watched = {"lightspeed:22962978", "lightspeed:22962975", "lightspeed:22874517"}
    free = []
    for name, rec in d.items():
        for ln in (rec.get("ingredients") or []):
            if ln.get("ref") in watched:
                try:
                    q, eff = float(ln.get("qty") or 0), float(ln.get("eff_cost") or 0)
                except (TypeError, ValueError):
                    continue
                if q > 0 and eff == 0:
                    free.append(f"{name} -> {ln.get('name')} ({q}{ln.get('unit')})")
    assert not free, "still costing nothing on a real quantity:\n  " + "\n  ".join(free)


def test_a_unit_that_needs_converting_is_still_refused():
    """The guard rail, not the feature. The fallback exists because the magnitude
    check cannot run without a seed — so it must never accept a rate that needs
    arithmetic to reach the product's unit. Nothing in the book may show a bridged
    row whose pack note says the conversion came from a Back Office unit while the
    units differ; if that ever appears, the narrow rule has been widened."""
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        if not r["ingredient"].startswith("lightspeed:"):
            continue
        if "(via " not in (r.get("pack") or ""):
            continue
        # a bridged row's unit must be a base unit or a whole selling unit —
        # never a raw supplier pack word that was silently reinterpreted
        assert r["unit"] in ("g", "ml", "ea", "each", "bottle", "keg", "can",
                             "bunch", "tray", "box", "punnet", "bag", "unit"), r
