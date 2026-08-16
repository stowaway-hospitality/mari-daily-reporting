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
