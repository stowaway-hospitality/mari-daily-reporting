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


def test_no_dirty_residue_in_data():
    """The shipped feed must contain zero avatar-doubled or dot/space-dirty keys."""
    R = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    dirty = [n for n in R
             if (re.match(r"^([A-Z][a-z])([A-Z][a-z])", n) and n[:2].lower() == n[2:4].lower())
             or n.endswith(".") or "  " in n]
    assert dirty == [], f"dirty names leaked: {dirty[:10]}"


def test_no_absurd_costs_or_garbage_gp():
    """No non-prep recipe should cost like a mis-unit blow-up, and no menu GP
    should be garbage (the $419 pizza / -1085% batch bugs)."""
    R = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    absurd = [(n, R[n]["our_cost"]) for n in R if R[n]["our_cost"] > 120 and not R[n]["is_prep"]]
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
