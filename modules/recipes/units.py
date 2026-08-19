"""
Food is weighed. Drinks are poured.

    "it's always easier to measure in g if it's not a beverage"  -- Zak, 2026-08-16

That one line settles a class of problem that had been eating whole afternoons.
Nearly every batch in this book carries a yield whose UNIT nobody measured: the
number came from summing ingredients recorded in grams, millilitres and
sometimes bunches, and then somebody typed a unit on the end of it. Garlic Oil's
"1,500 ml" is 1,000 g of garlic plus 500 ml of oil. Mint Yoghurt's "1,102 ml" is
1,000 g + 100 ml + 2 BUNCHES.

Faced with that, the tempting move is to declare a density and convert. That is
two mistakes: it invents a number nobody measured, and it does it to reconcile a
label that was never meaningful in the first place.

The kitchen convention is better evidence than any density, because it describes
what the person actually does. A cook putting a sauce together puts the bowl on
a scale. A bartender pours. So:

    a batch that ends up in FOOD    declares its yield in g
    a batch that ends up in a DRINK declares its yield in ml

and the recipe lines drawing on it use the same unit. No density is applied
anywhere, because none is needed: the magnitude was a sum of mostly-masses to
begin with, and now it is labelled as one.

THE REFINEMENT, and it matters (Zak, 2026-08-16):

    "if something obviously liquid like milk or lime/lemon juice is used in a
     food, then stick with ml for those ingredients. but yes batch yields will
     always be in g for all food items"

So the rule is about BATCH YIELDS, not about every line. A cook weighs the bowl
the sauce ends up in; they still pour the milk into it from a jug. Two litres of
milk in a cauliflower cheese stays 2,000 ml even though the batch it makes is
declared in grams, and nothing in this module touches a raw ingredient line --
only the yields of batches, and the lines that DRAW on a batch, which follow the
unit that batch now declares.

That is also why the yield can be relabelled without converting: the mass and
the volume lines were being added together anyway. Labelling the total as grams
does not restate the milk.

WHERE THE CLASSIFICATION COMES FROM
-----------------------------------
The Sales Product API's reporting_group, which CLAUDE.md names as the authority
for product questions. A batch is a beverage when the products that draw on it
are drinks. That is a fact about the menu rather than a guess about the sauce.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SALES = ROOT / "dashboard" / "sales" / "products" / "index.json"

# Reporting groups that are drinks. Everything not named here is food, because
# the failure that matters is calling a sauce a beverage: it puts a yield in ml,
# and ml is the label nobody measured.
def apply_declared_yield_relabels(yields: dict) -> dict:
    """Apply data/batch_yield_units.yaml's `yield_unit_fixes` to a yield map.

    ONE PLACE, because there are three readers and they were disagreeing. A
    batch whose yield is labelled ml while every line drawing on it is written
    in g cannot be wired live: the units do not match, so the builder feed gives
    up and freezes the line as an "(imported)" snapshot. Sixteen lines were
    frozen that way — the Garlic Oil and Mint Yoghurt families — each with a
    worked proof sitting in the declarations file since an earlier session, and
    each only ever read by the STAGED book.

    Accepts either {name: {yield_qty, yield_unit}} or {name: (qty, unit)} and
    returns the same shape. RELABELS ONLY: the magnitude is left exactly as the
    kitchen recorded it, because a relabel that also moved the number would be a
    density assumption wearing a correction.
    """
    from pathlib import Path as _P
    f = _P(__file__).resolve().parents[2] / "data" / "batch_yield_units.yaml"
    if not f.exists():
        return yields
    import yaml as _yaml
    doc = _yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
    for fx in (doc.get("yield_unit_fixes") or []):
        cur = yields.get(fx["batch"])
        if cur is None:
            continue
        if isinstance(cur, dict):
            if (cur.get("yield_unit") or "") == fx["from_unit"]:
                yields[fx["batch"]] = dict(cur, yield_unit=fx["to_unit"])
        elif isinstance(cur, (tuple, list)) and len(cur) == 2:
            if cur[1] == fx["from_unit"]:
                yields[fx["batch"]] = (cur[0], fx["to_unit"])
    return yields


def measured_yields() -> dict:
    """batch -> the entry somebody put on a scale. Latest measurement wins.

    Two real weighings that disagree are information about variance, not a
    conflict, so the most recent one answers and both stay in the file.
    """
    from core.declarations import MEASURED_YIELDS
    out: dict = {}
    for e in (MEASURED_YIELDS.load().get("measured") or []):
        prev = out.get(e["batch"])
        if prev is None or str(e.get("measured_on")) >= str(prev.get("measured_on")):
            out[e["batch"]] = e
    return out


def apply_measured_yields(yields: dict) -> dict:
    """Overlay data/measured_yields.yaml. A MEASUREMENT OUTRANKS EVERYTHING.

    ONE PLACE, for the same reason apply_declared_yield_relabels is one place,
    and found the same way. The ladder resolve_yield walks — measured, then a
    written basis, then the bracket in the name — lived only in
    build_recipe_feeds, which builds the BUILDER's feeds. The LIVE converter,
    which is what the P&L costs off, walked a shorter ladder that started at
    prep_yields.yaml and had never heard of a measurement.

    The file was empty on 2026-08-19, so this moved no money on the day it
    landed. That is exactly why it was worth landing: the next line in
    measured_yields.yaml is Pizza Sauce, which carries $1.06M across 146 dishes
    and is item 7 on the open list. It would have shown up in the builder,
    stayed out of the P&L, and the two would have disagreed about the same batch
    with a fresh weighing sitting in the file looking authoritative.

    Accepts and returns the shapes already in use: {name: {yield_qty,
    yield_unit}} or {name: (qty, unit)}.
    """
    m = measured_yields()
    if not m:
        return yields
    for batch, e in m.items():
        cur = yields.get(batch)
        qty, unit = e["yield_qty"], e["yield_unit"]
        if isinstance(cur, (tuple, list)) and len(cur) == 2:
            yields[batch] = (float(qty), unit)
        elif isinstance(cur, dict):
            yields[batch] = dict(cur, yield_qty=qty, yield_unit=unit,
                                 basis=f"MEASURED {e.get('measured_on')} by "
                                       f"{e.get('measured_by')}: {e.get('note', '')}")
        else:
            # A batch weighed before anyone wrote a basis for it. The scale is
            # still the best answer in the book.
            yields[batch] = {"yield_qty": qty, "yield_unit": unit,
                             "basis": f"MEASURED {e.get('measured_on')}"}
    return yields


BEVERAGE_GROUPS = {
    "cocktails - classic", "cocktails - signature", "delivery cocktails",
    "delivery alcohol", "tap beer", "bottles / cans alcoholic",
    "red wine", "white wine", "sparkling wine", "orange / skins wine",
    "whisky", "gin", "rum", "tequila", "vodka", "liqueurs",
    "amaro / aperitif / fortified wine", "sake & soju",
    "non-alcoholic", "mocktails", "marilyna's soft drinks",
    "add-ons - bar", "bar / foh (no reporting group)",
}

# Names that are unmistakably a drink or a drink component even when nothing
# sold draws them yet, so a new syrup does not default to grams on day one.
_DRINK_WORDS = ("syrup", "juice", "soda", "tequila", "vodka", "gin ", "rum",
                "martini", "chu-hi", "vermouth", "cordial", "shrub", "tonic",
                "mulled", "sherbet", "washed", "infus", "bitters", "highball")


def _groups() -> dict:
    if not SALES.exists():
        return {}
    doc = json.loads(SALES.read_text(encoding="utf-8-sig"))
    return {(p.get("name") or "").strip().lower():
            (p.get("reporting_group") or "").strip().lower()
            for p in (doc.get("products") or [])}


def beverage_batches(book: dict) -> set:
    """Every batch whose output ends up in a glass.

    Walks from each SOLD product down through its sub-recipes, so a syrup used
    only inside a cocktail batch inside a cocktail is still a beverage. A batch
    reached by both a drink and a dish is FOOD -- weighing it is the option that
    works either way, and a scale is what the kitchen has.
    """
    groups = _groups()
    drinks, foods = set(), set()
    for product, r in book.items():
        if r.get("is_prep"):
            continue
        g = groups.get(product.strip().lower())
        if g is None:
            continue
        target = drinks if g in BEVERAGE_GROUPS else foods
        seen, frontier = set(), {product}
        while frontier:
            nxt = set()
            for n in frontier:
                for ln in (book.get(n, {}).get("ingredients") or []):
                    if ln.get("kind") == "subrecipe" and ln["ref"] not in seen:
                        seen.add(ln["ref"])
                        target.add(ln["ref"])
                        nxt.add(ln["ref"])
            frontier = nxt

    # A NAME THAT SAYS "BAR PREP" OUTRANKS A DISH THAT BORROWS IT.
    #
    # Super Lime Juice is super juice -- a cocktail technique, peels and acid and
    # water -- and it is poured. Guacamole happens to use 100 ml of it, and on
    # the "reached by a dish too, so call it food" rule that one borrowing turned
    # the whole bar prep into grams. The batch's own nature is the better signal.
    named = {n for n in book if any(w in n.lower() for w in _DRINK_WORDS)}
    out = (drinks - foods) | named

    # ...and the builder book is a source of truth the scrape never sees. Davy's
    # Old Fashioned and its batch both live in data/recipes/stowaway.yaml, so the
    # walk above cannot reach the batch and it defaulted to grams -- a cocktail
    # measured on a scale.
    out |= _authored_drink_batches(groups)
    return out


def _authored_drink_batches(groups: dict) -> set:
    """Sub-recipes drawn by a hand-authored recipe that sells as a drink."""
    import yaml
    out: set = set()
    d = ROOT / "data" / "recipes"
    if not d.exists():
        return out
    for f in sorted(d.glob("*.yaml")):
        blocks = yaml.safe_load(f.read_text(encoding="utf-8-sig")) or []
        by_product: dict = {}
        for b in blocks:
            if isinstance(b, dict) and b.get("product"):
                by_product[b["product"]] = b
        for name, b in by_product.items():
            g = groups.get(name.strip().lower())
            if g is None or g not in BEVERAGE_GROUPS:
                continue
            frontier, seen = {name}, set()
            while frontier:
                nxt = set()
                for n in frontier:
                    for ln in (by_product.get(n, {}).get("ingredients") or []):
                        sub = ln.get("subrecipe")
                        if sub and sub not in seen:
                            seen.add(sub)
                            out.add(sub)
                            nxt.add(sub)
                frontier = nxt
    return out


def house_unit(name: str, beverages: set) -> str:
    """g for food, ml for a drink. The whole rule."""
    return "ml" if name in beverages else "g"
