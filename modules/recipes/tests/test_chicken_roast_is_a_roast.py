"""
Chicken Roast is the same plate as the other four roasts, and it must cost like one.

THE TWO DEFECTS
---------------
Lightspeed Produce's scrape (data/lightspeed_recipes.json) holds Chicken Roast as
four lines:

    Chicken Whole Bird [No.8]   qty 0.5   unit ml   $3.05
    Carrot Large [kg]           qty 45    g         $0.07
    Broccolini [Bunch]          qty 0.22  ml        $1.01
    Potato Desiree [10kg]       qty 125   g         $0.45

Pork, Lamb, Beef and Nut Roast carry the identical carrot / broccolini / potato
lines to the gram, at the identical $30.00 menu price, PLUS a trailing pair that
Chicken Roast has never had:

    Yorkshire Pudding Prep [110 units]   qty 1     ml   $0.21
    Gravy Prep                           qty 145   g    $0.98

1. THE BIRD'S UNIT IS MEANINGLESS. Half a millilitre of chicken is not a thing.
   The dollar figure was never wrong — $3.05 is exactly half of $6.10, which is
   what a No.8 bird costs — but a "ml" quantity can never be multiplied by a
   per-EACH price, so `our_cost` was left null on that line and the SCRAPED
   dollar figure carried it through the dimensionless seed-ratio fallback. The
   line therefore could not track its supplier: our cost book prices
   lightspeed:22992669 at $6.1000 per each, data/product_map.csv bridges that
   ProductID to B&E code 10677, and B&E have billed "CHICKEN - WHOLE BIRD NO.8"
   at $6.1000 per unit on twenty-plus invoices since April 2026. Had that price
   moved to $7.00, the roast would have gone on costing $3.05 forever.

2. THE GRAVY AND THE YORKSHIRE WERE MISSING. $1.0631 of a $30.00 plate, absent
   from the recipe, on 234 serves a year. Missing cost flatters GP, which is the
   dangerous direction.

WHAT MOVED, MEASURED
--------------------
    Chicken Roast   $4.3931 -> $5.4562   (+$1.0631)   GP 83.9% -> 80.0%

and nothing else in the 892-recipe book moves by a cent. It now sits between Nut
Roast ($5.0056) and Beef Roast ($5.4862), where a chicken plate belongs, instead
of being the cheapest and highest-GP roast on a menu where every roast is $30.

WHERE THE FIX LIVES, AND WHY NOT IN data/recipes/stowaway.yaml
--------------------------------------------------------------
data/lightspeed_recipes.json is an immutable capture and is not edited. The two
correction layers that patch it are:

    data/recipe_line_unit_fixes.yaml    a unit Produce typed wrong, with proof
    data/recipe_missing_lines.yaml      a line Produce left off, with evidence

Authoring a fresh serve recipe in data/recipes/stowaway.yaml would NOT have
worked, and the reason is structural rather than stylistic: a yaml recipe with no
`yield_qty` is invisible to convert_lightspeed_recipes.load_our_preps (it returns
None without a yield), so it never reaches data/lightspeed_recipes_costed.json —
which is what the /recipes/ book tab, scripts/audit_book.py, recipes_full.json
and the flags feed all read. Only cogs_blend._load_our_costs would have seen it.
The book would have gone on publishing $4.3931 and a 0.5 ml bird while the P&L
quietly used a different figure. That split already exists for four products
(Davy's Old Fashioned, Dragon Juice, Drunken Koi, Sensei Highball, where the yaml
build and the book build are different drinks) and this fix does not add a fifth.

WHAT THIS GUARDS
----------------
- the bird is expressed and priced per EACH, off our own book
- all five roasts carry the Yorkshire and the gravy, at the same cost to the cent
- Chicken Roast is no longer the flattering outlier of the five
- the restored sub-recipe lines carry the Lightspeed reference they need to cost
  at all (without it both fall to `eff = ls` = $0 — see test_no_silent_zero_line)
- the scrape on disk is still the scrape
"""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from convert_lightspeed_recipes import apply_unit_fixes            # noqa: E402

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
SCRAPE = ROOT / "data" / "lightspeed_recipes.json"
MISSING = ROOT / "data" / "recipe_missing_lines.yaml"

ROASTS = ["Chicken Roast", "Pork Roast", "Lamb Roast", "Beef Roast", "Nut Roast"]
BIRD = "lightspeed:22992669"


def _book():
    return json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]


def _line(recipe, name):
    for ln in recipe.get("ingredients") or []:
        if (ln.get("name") or "") == name:
            return ln
    return None


# --- 1. the unit -----------------------------------------------------------

def test_the_bird_is_declared_per_each_not_per_millilitre():
    """apply_unit_fixes, run against the REAL data/recipe_line_unit_fixes.yaml.

    Deliberately fed a hand-built copy of the scrape's own line so the assertion
    is about the yaml entry and not about whatever else the converter did later.
    """
    rec = {"Chicken Roast": {"ingredients": [
        {"name": "Chicken Whole Bird [No.8]", "qty": "0.5", "unit": "ml", "cost": "3.05"}]}}
    assert apply_unit_fixes(rec) >= 1
    ln = rec["Chicken Roast"]["ingredients"][0]
    assert ln["unit"] == "ea"


def test_the_birds_dollar_figure_is_not_rescaled():
    """No `cost_factor` on that entry, and there must not be one. Produce derived
    the $10,500 soy line from its wrong label and so it had to be divided down;
    it did NOT derive $3.05 from "0.5 ml". $3.05 is half of $6.10 and correct as
    it stands, so scaling it would introduce an error where there was none."""
    rec = {"Chicken Roast": {"ingredients": [
        {"name": "Chicken Whole Bird [No.8]", "qty": "0.5", "unit": "ml", "cost": "3.05"}]}}
    apply_unit_fixes(rec)
    assert float(rec["Chicken Roast"]["ingredients"][0]["cost"]) == 3.05


def test_the_bird_line_prices_off_our_book_per_each():
    """The point of the unit fix. `our_cost` is our per-each rate, so the line is
    rate x qty and a B&E price rise reaches the plate. Under "ml" it stayed null
    and the line was frozen at Produce's dollar figure."""
    ln = _line(_book()["Chicken Roast"], "Chicken Whole Bird [No.8]")
    assert ln is not None and ln["unit"] == "ea"
    assert ln["ref"] == BIRD
    assert float(ln["our_cost"]) == 6.10
    assert abs(float(ln["eff_cost"]) - 0.5 * 6.10) < 0.0001


# --- 2. the missing lines --------------------------------------------------

def test_every_roast_carries_the_yorkshire_and_the_gravy():
    """The finding itself. Four of five carried both; Chicken Roast carried
    neither."""
    book = _book()
    missing = [f"{r}: {n}" for r in ROASTS for n in
               ("Yorkshire Pudding Prep [110 units]", "Gravy Prep")
               if _line(book[r], n) is None]
    assert not missing, "roast missing a standard line:\n  " + "\n  ".join(missing)


def test_the_restored_lines_cost_the_same_as_the_siblings_to_the_cent():
    """Same tray, same ladle. A restored line that costs a different amount from
    the identical line on Pork Roast would mean the restoration invented a
    quantity rather than copying one."""
    book = _book()
    for n in ("Yorkshire Pudding Prep [110 units]", "Gravy Prep"):
        effs = {r: round(float(_line(book[r], n)["eff_cost"]), 6) for r in ROASTS}
        assert len(set(effs.values())) == 1, f"{n} costs differently per roast: {effs}"


def test_a_restored_sub_recipe_line_states_the_reference_it_needs():
    """Both preps are uncostable without it: "Yorkshire Pudding Prep [110 units]"
    declares no yield anywhere (load_yields does not read a "units" bracket) and
    Gravy Prep yields in ml while every roast draws it in g. With no `ls_cost`
    the ratio path cannot run, both lines fall to `eff = ls` = $0, and the recipe
    reads complete while pricing nothing — which test_no_silent_zero_line calls
    a defect in its own right. The value must be Produce's own per-serve figure."""
    specs = yaml.safe_load(MISSING.read_text(encoding="utf-8-sig")) or []
    chicken = [s for s in specs if s.get("recipes") == ["Chicken Roast"]]
    assert len(chicken) == 2, chicken
    scraped = json.loads(SCRAPE.read_text(encoding="utf-8-sig"))["Pork Roast"]["ingredients"]
    sibling = {i["name"]: float(i["cost"]) for i in scraped}
    for s in chicken:
        assert s.get("subrecipe") is True
        assert float(s["ls_cost"]) == sibling[s["name"]], s["name"]


# --- 3. the whole plate ----------------------------------------------------

def test_the_chicken_roast_costs_what_a_chicken_roast_costs():
    """The measured move: $4.3931 -> $5.4562, GP 83.9% -> 80.0%, on a $30.00
    plate. Bounded rather than pinned, so an invoice moving the bird or the gravy
    does not fail the suite — but a regression to the four-line recipe does."""
    r = _book()["Chicken Roast"]
    assert r["sell_incl"] == 30.0
    assert 5.30 <= float(r["our_cost"]) <= 5.65, r["our_cost"]
    assert 79.0 <= float(r["gp_pct"]) <= 81.0, r["gp_pct"]


def test_the_chicken_is_no_longer_the_cheapest_roast_on_a_one_price_menu():
    """All five sell for $30.00. Before the fix the chicken was the cheapest to
    make AND the highest GP of the five — the flattering direction, and the
    reason nobody looked at it. It now sits mid-pack, above Nut Roast."""
    book = _book()
    cost = {r: float(book[r]["our_cost"]) for r in ROASTS}
    assert cost["Chicken Roast"] > cost["Nut Roast"], cost
    assert cost["Chicken Roast"] < cost["Lamb Roast"], cost
    assert float(book["Chicken Roast"]["gp_pct"]) < float(book["Pork Roast"]["gp_pct"])


def test_the_scrape_on_disk_is_still_the_scrape():
    """data/lightspeed_recipes.json is an immutable capture of what Produce held.
    Both defects are corrected in the patch layers ABOVE it, so the capture must
    still show the four-line recipe and the 0.5 ml bird. If this ever passes
    because someone edited the json, the audit trail is gone."""
    raw = json.loads(SCRAPE.read_text(encoding="utf-8-sig"))["Chicken Roast"]["ingredients"]
    assert len(raw) == 4
    assert raw[0]["name"] == "Chicken Whole Bird [No.8]"
    assert raw[0]["unit"] == "ml" and raw[0]["qty"] == "0.5"
