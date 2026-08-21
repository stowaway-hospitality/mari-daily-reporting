"""
Units the feed cannot mean — the defect class Zak keeps catching by eye.

WHY THIS EXISTS
---------------
`book_reconcile.py` asks "does this line agree with the other 891 recipes?".
This asks a narrower question that nothing asked at all: **is the UNIT on this
record a unit this product can possibly be measured in?**

Four live examples, all found by a human reading a screen and by no check:

  1. "Lemon" published at $0.375/**ml** — $375 a litre of lemon. The same cost
     book holds Lemon at $0.0033/g ($3.30/kg) from two other suppliers. A lemon
     is not a liquid, so one of those two records is not a lemon price.
  2. "Cauliflower [ea]" carrying pack unit **can**. The name states the pack in
     its own bracket and the pack unit contradicts it.
  3. "Turkish Bread [ea]" — same shape, same `can`.
  4. "Avocado" priced $26.40 per **tray** while the same avocado is $3.10 per
     **ea** elsewhere in the book. Anything that draws "one avocado" off the
     tray record charges the dish a whole tray.

And the line-level twin of it, which is what makes the American Standard Burger
argument go round in circles: the burger's lettuce line is `0.083` **ml** of an
ingredient bought by the **ea**. The QUANTITY is right (a twelfth of a twin
pack, $0.23 — exactly what the book charges) and the UNIT is meaningless, so
every time somebody reads the line they see "ml" against a countable pack and
re-raises it. It is the same typo family as the whole chicken logged as "0.5 ml"
(`Chicken Roast`) and the $10,530 Peking Sauce batch that
`data/recipe_line_unit_fixes.yaml` exists to correct.

PURE. Reads dicts, returns dicts. No I/O, no wording, no money formatting —
scripts/build_cost_book_flags.py does that, the same way it does for
book_reconcile. `modules/recipes/tests/test_feed_defects.py` runs it against the
real feeds.

IT NEVER CORRECTS A UNIT. Reading "0.35 ml" as 0.35 KG took Rosemary Salted
Fries from $1.86 to $0.0019, which is why every finding here is a QUESTION with
its evidence attached, and why the only layer allowed to rewrite a unit
(data/recipe_line_unit_fixes.yaml) demands an arithmetic proof.

CALIBRATION — measured against the real feeds, 1,091 ingredient records
(data/ingredients.json) and 2,518 id lines / 913 recipes
(data/lightspeed_recipes_costed.json), 2026-08-08. flagged / true / false:

    pack_unit_contradicts_name    10 / 10 / 0    over 1,091 ingredients
    product_priced_in_two_worlds   4 /  4 / 0    over 968 product stems
    line_unit_contradicts_pack    17 / 17 / 0    over 2,518 id lines

The three thresholds that keep those columns clean, and why:

  * a bracket that names a CONTAINER ("[Bottle]", "[Keg]", "[Can]") says nothing
    about how the contents are measured — 426 saved lines draw ml out of a
    "[Bottle]" and every one is correct — so only a bare measurement unit
    ("[85g]", "[360mL]", "[kg]") or a piece word ("[ea]", "[Each]") is read.
    Without that, 199 of 1,091 ingredients flag and 195 of them are spirits.
  * `product_priced_in_two_worlds` needs a 3x rate gap on top of the unit
    disagreement, because limes really are bought both by the kilo and by the
    tray and nothing is wrong with that.
  * `line_unit_contradicts_pack` is restricted to ingredients bought BY THE
    PIECE (ea / bunch / punnet / tray / head / loaf / box). Including `can`
    adds 45 delivery lines — "Corona D, 1 ml" — where 1 means one can, the cost
    is right, and the fix is in Lightspeed's export, not in this book.
"""

from __future__ import annotations

import re
from collections import defaultdict

# The same three dimensions dashboard/_shared/recipe_line_guard.js uses in the
# builder, deliberately: a unit that reads as a defect on the page a chef types
# into must read as a defect in the queue that chases it, or the two disagree.
MASS = {"g", "gm", "gms", "gr", "gram", "grams", "kg", "kgs", "kilo",
        "kilogram", "kilograms"}
VOLUME = {"ml", "mls", "millilitre", "l", "lt", "ltr", "litre", "liter",
          "litres", "cl"}
COUNT = {"ea", "each", "unit", "units", "pc", "pcs", "piece", "pieces",
         "portion", "slice", "slices", "bunch", "punnet", "can", "cans",
         "bottle", "bottles", "tray", "box", "pack", "packet", "sheet", "leaf",
         "clove", "egg", "tin", "jar", "block", "roll", "bag", "case",
         "carton", "dozen", "keg", "tub", "pkt", "serve", "serves", "head",
         "loaf", "fillet"}

# A word for the THING it is delivered in, not for the thing itself. A
# cauliflower does not come in a can; a Coke does.
CONTAINER = {"can", "cans", "tin", "jar", "tray", "box", "carton", "case",
             "bag", "packet", "pack", "tub", "pkt"}
# A word for one of the thing. These are the units a kitchen counts in.
PIECE = {"ea", "each", "unit", "units", "pc", "pcs", "piece", "pieces", "loaf",
         "head", "bunch", "punnet", "clove", "egg", "sheet", "leaf", "slice",
         "slices", "fillet", "dozen"}
# Bought by the piece AND consumed by the piece — the ingredients where a line
# denominated in g or mL cannot be read at all. `can` is excluded on purpose;
# see the calibration note above.
PIECEWISE_PACK = {"ea", "each", "unit", "units", "bunch", "punnet", "tray",
                  "head", "loaf", "box", "dozen"}

_BARE = re.compile(r"^\s*(kgs|kg|gms|gm|g|mls|ml|litres|litre|ltr|lt|l)\s*$", re.I)
_THING = re.compile(r"^\s*(each|ea|bunch|punnet|head|dozen|twin\s*pack|loaf"
                    r"|slice|piece|pc|pcs)\s*$", re.I)
_SIZED = re.compile(r"([0-9]*\.?[0-9]+)\s*(kgs|kg|gms|gm|gr|g|mls|ml|litres"
                    r"|litre|ltr|lt|l)\b", re.I)
_TRAILING_BRACKET = re.compile(r"\s*\[[^\]]*\]\s*$")

# A 3x gap is what separates "two ways of buying the same thing" from "one of
# these is not this product's price". Below it sit the ordinary pairs: a tray of
# limes and a kilo of limes, a box of baby gem and a bag of it.
TWO_WORLDS_X = 3.0


def unit_dimension(unit):
    """mass / volume / count — or None for a unit we have never seen and will
    not guess at. Guessing is how "0.35 ml" became 0.35 kg."""
    u = str(unit or "").strip().lower().rstrip(".")
    if u in MASS:
        return "mass"
    if u in VOLUME:
        return "volume"
    if u in COUNT:
        return "count"
    return None


def name_declared_unit(name):
    """-> (the unit the trailing bracket names, its dimension).

    The bracket is load-bearing and ONLY the trailing one is read. The cost
    book's naming convention puts the pack there ("[85g]", "[360mL]", "[ea]"),
    and reading a size anywhere in the name flags "Coke 1.25L" — bought and
    priced by the can, entirely correct.
    """
    m = re.search(r"\[([^\]]*)\]\s*$", str(name or ""))
    if not m:
        return None, None
    inner = m.group(1).strip()
    if _BARE.match(inner):
        return inner.lower(), unit_dimension(inner)
    if _THING.match(inner):
        return inner.lower(), "count"
    dims = {unit_dimension(u) for _q, u in _SIZED.findall(inner)}
    dims = {d for d in dims if d}
    if len(dims) == 1:
        return inner.lower(), next(iter(dims))
    return None, None          # "[10x2 CTN]" style mixes say nothing


def product_stem(description) -> str:
    """The product name with its trailing bracket removed, lowercased. What
    makes 'Avocado' and 'Avocado [Tray]' one product and 'Lemon' and 'Preserved
    Lemon 350g Chefs Choice' two."""
    return _TRAILING_BRACKET.sub("", str(description or "")).strip().lower()


def _rate(i) -> float:
    try:
        return float(i.get("cost_per_base_unit") or 0)
    except (TypeError, ValueError):
        return 0.0


def pack_unit_contradicts_name(ingredients) -> list:
    """An ingredient whose PACK UNIT its own name contradicts.

    Two readings of "contradicts", and both are needed:
      * a different DIMENSION — "Onion Brown [kg]" priced per `can`. The name
        says this is weighed and the rate says it is counted.
      * a piece word against a CONTAINER word — "Cauliflower [ea]" priced per
        `can`. Same dimension, so no arithmetic sees it, and a cauliflower has
        never come in a can. This is Lightspeed's default pack unit leaking
        into a produce line.

    NOT A CONTRADICTION: A PIECE NAME WITH A DECLARED WEIGHT. "Turkish Bread
    [ea]" at 120 g is not two answers fighting, it is ONE PIECE WEIGHING 120 g —
    which is the most useful thing anybody can know about it, and precisely what
    a declared pack is for. Urbun Bakery print it on the invoice ("Turkish/
    Focaccia Bread (120g)") and the recipes draw 20 g of it, a sixth of a loaf.
    Firing here would demand somebody "fix" a pack size that had just been read
    off a supplier document, and the only way to satisfy it would be to throw the
    weight away and go back to pricing a whole loaf per prawn toast.

    So a mass/volume pack against a piece name is admitted when the id carries a
    declared override. Without one it still fires: an undeclared dimension clash
    is the Onion Brown case and stays a defect.
    """
    try:
        from core.pack_overrides import load_pack_overrides
        declared = set(load_pack_overrides())
    except Exception:                                    # noqa: BLE001
        declared = set()
    out = []
    for i in (ingredients or []):
        desc = i.get("description") or ""
        nu, nd = name_declared_unit(desc)
        pu = str(i.get("pack_unit") or "").strip().lower()
        pd = unit_dimension(pu)
        if not nu or not nd or not pd:
            continue
        if (nd == "count" and pd in ("mass", "volume")
                and i.get("id") in declared):
            continue                    # a declared piece WEIGHT, not a clash
        if nd != pd:
            why = "dimension"
        elif nd == "count" and nu in PIECE and pu in CONTAINER:
            why = "container"
        else:
            continue
        out.append({
            "id": i.get("id"),
            "description": desc,
            "name_unit": nu,
            "pack_unit": pu,
            "name_dimension": nd,
            "pack_dimension": pd,
            "rate": _rate(i),
            "kind": why,
            "supplier": i.get("supplier") or "",
        })
    out.sort(key=lambda f: (-f["rate"], f["description"]))
    return out


def product_priced_in_two_worlds(ingredients) -> list:
    """One product, two records, in units that cannot both describe it.

    Two shapes, and neither is an arithmetic error — both records are
    internally consistent, which is exactly why nothing else sees them:
      * mass AND volume for the same product. A lemon cannot be both.
      * a CONTAINER record against a PIECE record more than 3x apart. "Avocado"
        at $26.40 a tray beside "Avocado" at $3.10 each: whichever recipes use
        the tray record charge the dish a whole tray for one avocado.
    """
    groups = defaultdict(list)
    for i in (ingredients or []):
        stem = product_stem(i.get("description"))
        if stem:
            groups[stem].append(i)
    out = []
    for stem, items in groups.items():
        if len(items) < 2:
            continue
        by_unit = {}
        for i in items:
            u = str(i.get("pack_unit") or "").strip().lower()
            if u and _rate(i) > 0:
                by_unit.setdefault(u, i)
        if len(by_unit) < 2:
            continue
        dims = {unit_dimension(u) for u in by_unit}
        kind = None
        if "mass" in dims and "volume" in dims:
            kind = "two_dimensions"
        else:
            containers = [u for u in by_unit if u in CONTAINER]
            pieces = [u for u in by_unit if u in PIECE]
            if containers and pieces:
                hi = max(_rate(by_unit[u]) for u in containers)
                lo = min(_rate(by_unit[u]) for u in pieces)
                if lo > 0 and hi / lo >= TWO_WORLDS_X:
                    kind = "container_vs_piece"
        if not kind:
            continue
        members = sorted(
            ({"id": i.get("id"), "description": i.get("description"),
              "pack_unit": u, "rate": _rate(i), "supplier": i.get("supplier") or ""}
             for u, i in by_unit.items()), key=lambda m: -m["rate"])
        out.append({
            "stem": stem,
            "kind": kind,
            "members": members,
            "ratio": (members[0]["rate"] / members[-1]["rate"]
                      if members[-1]["rate"] > 0 else 0.0),
        })
    out.sort(key=lambda f: (-f["ratio"], f["stem"]))
    return out


def line_unit_contradicts_pack(recipes, ingredients) -> list:
    """A recipe line measured in grams or millilitres against an ingredient the
    kitchen buys BY THE PIECE.

    The American Standard Burger's lettuce is the canonical one: `0.083 ml` of
    "Lettuce Cos Baby Twin Pack [Each]". The quantity is right — a twelfth of
    the pack, $0.23, which is what the book charges and what the other burger
    charges — and the unit is not a unit that pack has. So the line reads as a
    mistake every time somebody looks at it, gets re-raised, and is closed again
    by re-deriving the same $0.23.

    One entry per INGREDIENT, listing every line, because the fix is one edit in
    Produce per ingredient and not one per dish.
    """
    ing = {i.get("id"): i for i in (ingredients or []) if i.get("id")}
    hits = defaultdict(list)
    for name, r in (recipes or {}).items():
        for ln in (r.get("ingredients") or []):
            if ln.get("kind") != "id":
                continue
            i = ing.get(ln.get("ref"))
            if not i:
                continue
            pu = str(i.get("pack_unit") or "").strip().lower()
            if pu not in PIECEWISE_PACK:
                continue
            qd = unit_dimension(ln.get("unit"))
            pd = unit_dimension(pu)
            if not qd or not pd or qd == pd:
                continue
            try:
                eff = float(ln.get("eff_cost") or 0)
            except (TypeError, ValueError):
                eff = 0.0
            hits[ln["ref"]].append({
                "recipe": name, "qty": ln.get("qty"), "unit": ln.get("unit"),
                "eff_cost": eff, "is_prep": bool(r.get("is_prep")),
            })
    out = []
    for ref, lines in hits.items():
        i = ing[ref]
        lines.sort(key=lambda l: (-l["eff_cost"], l["recipe"]))
        out.append({
            "id": ref,
            "description": i.get("description") or ref,
            "pack_unit": str(i.get("pack_unit") or "").strip().lower(),
            "rate": _rate(i),
            "supplier": i.get("supplier") or "",
            "lines": lines,
            "line_count": len(lines),
        })
    out.sort(key=lambda f: (-sum(l["eff_cost"] for l in f["lines"]),
                            f["description"]))
    return out
