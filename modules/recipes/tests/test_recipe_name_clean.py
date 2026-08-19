#!/usr/bin/env python3
"""Pin the recipe-name cleaner and the costed-feed sanity guards, so the scrape
pollution (avatar badges, me&u quick-codes) and the mis-unit cost blow-ups can
never silently come back."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import clean_recipe_names as C


@pytest.fixture(scope="module")
def clean():
    return C.build_cleaner(C.load_authoritative())


@pytest.mark.parametrize("mangled,expected", [
    ("NeNegroni", "Negroni"),            # doubled avatar initials
    ("GrGravy", "Gravy"),
    ("FiFireball", "Fireball"),
    ("CM400 Conejos Margarita", "400 Conejos Margarita"),   # 400 = brand, kept
    ("TB818 Tequila Blanco", "818 Tequila Blanco"),         # 818 = brand, kept
    ("PK4 Pines Kolsch Bottle", "4 Pines Kolsch Bottle"),   # 4 Pines = brand
    ("HR$5 House Red", "$5 House Red"),  # $5 is in the real product name
    ("ZC.Coke Zero Can", "Coke Zero Can"),
    ("B.Garlic Bread", "Garlic Bread"),
    ("JAJ.J. Aioli [Batch]", "J.J. Aioli [Batch]"),
])
def test_known_manglings(clean, mangled, expected):
    assert clean(mangled) == expected


def test_genuine_names_survive(clean):
    for good in ("Frozen Marg", "Margy Jar", "Mulled Wine Jar", "Negroni", "Gravy"):
        assert clean(good) == good


def _back_office_names() -> set:
    """Every product name exactly as Lightspeed Back Office spells it."""
    import csv as _csv
    out = set()
    for f in ("stowaway_products.csv", "harry_gatos_products.csv"):
        path = ROOT / "data" / "bo_exports" / f
        if path.exists():
            for r in _csv.DictReader(path.open(encoding="utf-8-sig")):
                out.add((r.get("ProductName") or "").strip())
    return out


def test_no_dirty_residue_in_data():
    """The shipped feed must contain zero avatar-doubled or dot/space-dirty keys."""
    R = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    # A NAME THAT IS IN THE POS IS NOT RESIDUE, however ugly. "Tengumai Junmai -
    # 180ml  [SERVE WARM]" really does carry a double space in Back Office, and
    # "cleaning" it here would only stop our record matching the product it is
    # for. This rule is for parser ARTEFACTS — a doubled avatar prefix, a
    # trailing dot, a space the reader invented — and telling those apart from an
    # ugly real name means asking the source.
    #
    # It surfaced on 2026-08-19 because that recipe had been in our book all
    # along and was being silently dropped, so its name never reached the feed
    # to be checked.
    real = _back_office_names()
    dirty = [n for n in R
             if n.strip() not in real
             and ((re.match(r"^([A-Z][a-z])([A-Z][a-z])", n) and n[:2].lower() == n[2:4].lower())
                  or n.endswith(".") or "  " in n)]
    assert dirty == [], f"dirty names leaked: {dirty[:10]}"


def test_no_absurd_costs_or_garbage_gp():
    """No non-prep recipe should cost like a mis-unit blow-up, and no menu GP
    should be garbage (the $419 pizza / -1085% batch bugs)."""
    R = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    # Pass-throughs excluded for the same reason audit_book excludes them: this
    # rule catches a unit/pack blow-up, and a product costed as one unit of
    # itself straight from Back Office cannot have one. Dom Pérignon really does
    # cost $332 a bottle and really is sold by the bottle.
    absurd = [(n, R[n]["our_cost"]) for n in R
              if R[n]["our_cost"] > 120 and not R[n]["is_prep"] and not R[n].get("passthrough")]
    assert absurd == [], f"absurd non-prep costs: {absurd}"
    garbage = [(n, R[n]["gp_pct"]) for n in R
               if R[n]["gp_pct"] is not None and (R[n]["gp_pct"] < -60 or R[n]["gp_pct"] > 99.5)]
    assert garbage == [], f"garbage GP: {garbage}"


def test_no_undercosting_regressions():
    """Guards two bugs a mis-built sanity guard caused: roasts under-costed to 52c
    (LS line cost wrongly divided by a garbage qty), and gluten-free / variant
    pizzas costing $0 (their base line zeroed). These must stay properly costed."""
    R = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    for n, lo in [("Chicken Roast", 3.0), ("Nut Roast", 3.0), ("Gluten-free Pepperoni", 3.0),
                  ("Large Pepperoni", 3.0), ("Large Meatlovers", 3.0)]:
        if n in R:
            assert R[n]["our_cost"] > lo, f"{n} under-costed at ${R[n]['our_cost']}"


def test_menu_gp_distribution_sane():
    """A healthy hospitality book: median food GP comfortably in the 60-85% band.
    A crash here means costs went systemically wrong in one direction."""
    import statistics
    R = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    gps = [R[n]["gp_pct"] for n in R if R[n]["gp_pct"] is not None]
    assert 60 <= statistics.median(gps) <= 85, f"median GP off: {statistics.median(gps)}"


def test_trust_direct_rejects_a_coincidental_unit_match():
    """Costing our_price x recipe_qty assumes the scraped (qty, unit) pair is real.
    Truffle Oil Prep says 4 "ml" but means 4 BOTTLES — once a $/ml price existed it
    costed 18c instead of $45.60, a 250x UNDER-cost (the flattering direction). Our
    price must agree with Lightspeed's line when that line is itself credible."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from convert_lightspeed_recipes import _trust_direct
    truffle = {"qty": "4", "our_cost": "0.0456"}          # 4 x 0.0456 = $0.18
    assert not _trust_direct(truffle, 45.60, is_prep=True)   # prep -> LS line credible
    vesper = {"qty": "45", "our_cost": "0.070633"}         # 45ml gin = $3.18, correct
    assert _trust_direct(vesper, 95.34, is_prep=False)      # >$40 on a serve: LS is the
    agree = {"qty": "200", "our_cost": "0.0114"}           # garbage one, must not veto
    assert _trust_direct(agree, 2.28, is_prep=False)        # agreement -> trusted


def test_no_recipe_costs_off_a_stale_cheap_seed():
    """The Berry Man passionfruit invoice ($9.50/kg) must reach the product, not be
    dropped in favour of the 12x-too-cheap scrape seed ($0.79/kg). A pack override
    in the wrong unit silently broke the bridge once; this pins the outcome."""
    import csv
    ing = {i["id"]: i for i in
           json.loads((ROOT / "data" / "ingredients.json").read_text())["ingredients"]}
    pf = ing.get("lightspeed:22843691")
    assert pf is not None, "passionfruit puree missing from the ingredient book"
    assert float(pf["cost_per_base_unit"]) > 0.005, (
        f"passionfruit at ${pf['cost_per_base_unit']}/{pf['pack_unit']} — the invoice "
        f"is not reaching the product (expect ~$0.0095/g, not the $0.00079/g seed)")


def test_manual_lines_cost_self_contained():
    """A Lightspeed-imported recipe loads as editable 'manual' lines that carry
    their own cost — so cost_on prices them with no cost-book lookup."""
    from decimal import Decimal
    from datetime import date
    from modules.recipes.cost import Recipe, RecipeLine, cost_on
    from core.domain import CostSeries
    r = Recipe(product="X", venue="stowaway", sell_incl_gst=None,
               lines=(RecipeLine(ingredient="", qty=Decimal("30"), unit="ml",
                                 fixed_unit_cost=Decimal("0.05")),
                      RecipeLine(ingredient="", qty=Decimal("2"), unit="ea",
                                 fixed_unit_cost=Decimal("1.10")),))
    assert cost_on(r, CostSeries([]), date.today()) == Decimal("3.70")
