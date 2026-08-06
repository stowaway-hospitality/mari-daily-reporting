#!/usr/bin/env python3
"""
Convert the scraped Lightspeed Produce recipe book (data/lightspeed_recipes.json)
into our format, RESOLVING every ingredient to a cost identity in our own book:

  * a SUB-RECIPE  -> another recipe in the book (Pizza Dough [Recipe], Sugar Syrup)
  * a BOTTLE/STOCK item -> lightspeed:<ProductID> (the identity the seed + the
    supplier_code->ProductID invoice bridge already keep current)

    python3 scripts/convert_lightspeed_recipes.py

WHY
---
The recipe book lived only in Lightspeed. This lands it in our repo, keyed to the
cost identities we maintain, so every drink and dish costs off OUR book and updates
as invoices land (via build_costs' bridge), instead of a hand-typed Lightspeed cost
that no one refreshes. The scraped per-ingredient cost is kept as `ls_cost` — a
provenance baseline to sanity-check our number against, never the source of truth.

Output: data/lightspeed_recipes_costed.json
  { generated, recipes: { name: {yield?, ingredients:[{name, ref, qty, unit,
    our_cost, ls_cost}], our_cost, ls_cost, resolved_pct} }, coverage:{...} }
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "data" / "lightspeed_recipes.json"
COSTS = ROOT / "data" / "costs.csv"
COGS = ROOT / "data" / "cogs_list.csv"
EXPORTS = [("stowaway", ROOT / "data" / "bo_exports" / "stowaway_products.csv"),
           ("harry_gatos", ROOT / "data" / "bo_exports" / "harry_gatos_products.csv")]
OUT = ROOT / "data" / "lightspeed_recipes_costed.json"

_SIZE = re.compile(r"\[[^\]]*\]")
_TRAIL_UNIT = re.compile(r"\b(kgs?|k|gm?|mls?|lt?|litres?|ea|each|box|bunch|punnet|tin|bottle|btl)\s*$", re.I)

# plausible ceiling for a single base unit. A per-ml cost above $0.60 (premium
# spirit) or a per-g above $0.20 (saffron) is a mis-unit seed, not a real price.
_UNIT_CEIL = {"ml": 0.60, "g": 0.20}
# How far our price x recipe-qty may stray from Lightspeed's own line cost before we
# stop believing the recipe's (qty, unit) pair. A real price move is a few tens of
# percent; 5x/0.2x means the quantity is garbage, not that the price moved.
_AGREE_LO, _AGREE_HI = 0.2, 5.0
# An LS line above this in a NON-prep serve is itself garbage (a whole bottle logged
# against one cocktail), so it may not be used to judge anything.
_LS_LINE_CAP = 40.0


# ProductIDs whose Lightspeed cost is PROVEN wrong: the BO export states one cost
# and Lightspeed's own recipe-derived seed contradicts it by >3x (see
# build_costs.ls_seed_is_misread). For these, and ONLY these, an LS line cost may
# not veto our price — it is computed from the same misread number.
_LS_MISREAD_REFS: set[str] = set()

# (recipe name, normalised ingredient name) -> the cost Produce itself states for
# that line, straight from the scrape and untouched by any later scaling. The
# whole-vs-fraction decision below needs the ORIGINAL figure; ln["ls_cost"] has by
# then been rescaled for deal lines and would answer the wrong question.
_RAW_LINE_COST: dict = {}



def apply_unit_fixes(rec: dict) -> int:
    """Rewrite units Produce typed wrong. Refuses silently if the file is absent.

    Deliberately narrow: it only touches the exact (recipe, ingredient) pairs
    named in data/recipe_line_unit_fixes.yaml, and only when the line still shows
    the `from_unit` stated there — so a fix that Produce later corrects at source
    becomes a no-op instead of double-applying.
    """
    path = ROOT / "data" / "recipe_line_unit_fixes.yaml"
    if not path.exists():
        return 0
    import yaml
    n = 0
    for spec in (yaml.safe_load(path.read_text()) or []):
        body = (rec or {}).get(spec.get("recipe"))
        if not body:
            continue
        for ing in (body.get("ingredients") or []):
            if (ing.get("name") == spec.get("ingredient")
                    and str(ing.get("unit") or "").lower() == str(spec.get("from_unit")).lower()):
                ing["unit"] = spec["to_unit"]
                # Produce derived this line's COST from the same wrong unit, so the
                # cost carries the identical error and must be scaled with it.
                f = spec.get("cost_factor")
                if f:
                    try:
                        ing["cost"] = str(float(ing.get("cost") or 0) * float(f))
                    except (TypeError, ValueError):
                        pass
                n += 1
    return n


def apply_ingredient_swaps(rec: dict) -> int:
    """Repoint a line at the bottle the venue actually pours.

    Distinct from INGREDIENT_ALIAS, which asserts two NAMES are one stock. This
    asserts the opposite: Produce records one product and the bar pours another.
    Scoped to the exact (recipe, ingredient) pair named in
    data/recipe_ingredient_swaps.yaml — see that file for why it is not an alias.

    Produce's stated line cost goes with the old name. It was computed for a
    product this recipe no longer contains, so carrying it forward would let a
    price for the wrong bottle survive the swap; the line is re-costed from the
    book like any other. A no-op if the line already names the replacement, so a
    correction made at source later does not double-apply.
    """
    path = ROOT / "data" / "recipe_ingredient_swaps.yaml"
    if not path.exists():
        return 0
    import yaml
    n = 0
    for spec in (yaml.safe_load(path.read_text()) or []):
        body = (rec or {}).get(spec.get("recipe"))
        if not body:
            continue
        for ing in (body.get("ingredients") or []):
            if ing.get("name") == spec.get("from"):
                ing["name"] = spec["to"]
                ing["cost"] = ""
                n += 1
    return n


# How a recipe line's unit LABEL maps onto the base unit our cost book prices in,
# and by what factor its quantity must be multiplied to get there.
_LINE_UNIT = {
    "ml": ("ml", 1.0), "millilitre": ("ml", 1.0), "milliliter": ("ml", 1.0),
    "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "ltr": ("ml", 1000.0),
    "litre": ("ml", 1000.0), "liter": ("ml", 1000.0),
    "g": ("g", 1.0), "gm": ("g", 1.0), "gr": ("g", 1.0), "gram": ("g", 1.0),
    "kg": ("g", 1000.0), "kilo": ("g", 1000.0), "kilogram": ("g", 1000.0),
    "ea": ("ea", 1.0), "each": ("ea", 1.0), "unit": ("ea", 1.0),
    "units": ("ea", 1.0), "pc": ("ea", 1.0), "pcs": ("ea", 1.0), "piece": ("ea", 1.0),
}

# The largest single ingredient line this kitchen can physically hold, in litres
# or kilograms. Not a guess about any one recipe — a statement about the business:
# the biggest legitimate line anywhere in the 829-recipe book is Achiote Chicken's
# 15 kg, and it says so in its own name. See _bulk_label_is_typo below.
_MAX_REAL_BULK_LINE = 25.0


def _bulk_label_is_typo(qty: float, unit_l: str) -> bool:
    """Is a line labelled "L"/"kg" really a base-unit quantity that kept the
    product's bulk label?

    THE DEFECT
    ----------
    Harry Gatos' Produce entries carry lines like "Soy Sauce Tamari Spiral [10L]
    — 1600 L" inside Shiitake Tare, and "Mirin [1.8L] — 4000 L" inside Gochujang
    Honey Soy. 2,800 litres of tare and 11,000 litres of gochujang. The cook typed
    the millilitre figure and left the bulk label sitting next to it.

    This is the defect data/recipe_line_unit_fixes.yaml was opened for — Peking
    Sauce's "750 L" of soy, proved by the quantities summing to the 6.75 L the
    recipe name declares. That proof needed a declared yield. None of these 13
    Harry Gatos batches has one, so the yield proof cannot be run on them, and
    naming each line in the yaml would need 25 hand-written proofs of a fact that
    is one fact.

    THE PROOF THAT DOES RUN
    -----------------------
    The book contains the same recipe typed BOTH ways. "HG's Soy Chilli Sauce"
    reads 2.5 L soy + 0.25 kg chilli. "HG Soy Chilli Sauce" — same sauce, same
    kitchen — reads 2500 L soy + 250 kg chilli. Identical recipe, identical
    ratios, exactly 1000x apart. The correctly-typed twin states what the
    magnitudes mean, and it means the big one is a label, not a quantity.

    WHICH WAY THIS MOVES THE MONEY
    ------------------------------
    Both ways, which is why it is not the flattering kind of fix. Wattleseed
    Honey Soy falls $7,093 -> $9 and Corpse Reviver No. 2 falls $1,438 -> $2.75;
    but the same normalisation lifts Garlic Oil off $0 and gives 162 Harry Gatos
    lines an invoice-fed cost they never had. What it removes is not cost, it is
    nonsense, and nonsense in a batch is what hides the real numbers behind it.

    DELIBERATELY CONSERVATIVE
    -------------------------
    Only "L" and "kg" labels, never "ml"/"g" — reading a base-unit label as bulk
    is the mistake that took Rosemary Salted Fries from $1.86 to $0.0019, and
    this function cannot make it. And only above 25 L/kg, which leaves every real
    line in the book (5 kg pork belly, 4.3 L vodka, 15 kg achiote chicken)
    untouched with a wide margin. The nearest thing it does catch is 90 L.
    """
    return unit_l in ("l", "lt", "ltr", "litre", "liter", "kg", "kilo", "kilogram") \
        and qty > _MAX_REAL_BULK_LINE


def normalise_line_units(rec: dict) -> int:
    """Express every recipe line's (qty, unit) in BASE units — the same units
    `_to_base` puts the cost book in.

    WHY THIS IS NOT COSMETIC
    ------------------------
    Our book's price is only used when its unit matches how the recipe uses the
    line (`if ou == ing["unit"]`), and that comparison is a raw string compare.
    The Stowaway scrape writes "ml"; the Harry Gatos scrape writes "mL", "L",
    "kg" and "Units". So 299 lines — every one of them at Harry Gatos — could
    never match, no matter how good our invoice data was. 162 of those lines have
    a real invoice-fed cost sitting in data/costs.csv that was never consulted.

    They did not fail loudly. They fell through to Lightspeed's scraped figure,
    and where Lightspeed's figure is 0.00 the line cost $0 and the drink read as
    100% GP. That is the flattering direction, which is the dangerous one.

    NO JUDGEMENT IS APPLIED HERE. This is arithmetic on a label: 0.27 L is 270 ml
    and 0.25 kg is 250 g, always. The line's COST is untouched — it is a dollar
    figure and carries no unit. A label this function does not recognise is left
    exactly as it is, so an unknown unit stays visible rather than being guessed
    into a base unit it may not belong to.

    Runs AFTER apply_unit_fixes(), so a line whose label is a typo is corrected to
    the unit it should have had before it is scaled — otherwise Peking Sauce's
    "750 L" of soy would be normalised to 750,000 ml on the way past.
    """
    n = 0
    typos: list[str] = []
    for rname, body in (rec or {}).items():
        for ing in (body.get("ingredients") or []):
            raw = str(ing.get("unit") or "").strip()
            m = _LINE_UNIT.get(raw.lower())
            if not m:
                continue
            base, mult = m
            try:
                q = float(ing.get("qty") or 0)
            except (TypeError, ValueError):
                continue              # non-numeric qty: leave the label alone too
            if mult != 1.0 and _bulk_label_is_typo(q, raw.lower()):
                # The quantity is already in base units; only the label is bulk.
                #
                # Produce sometimes derived this line's COST from the same wrong
                # label and sometimes did not — inside ONE recipe, Gochujang Honey
                # Soy, it priced "4000 L" of mirin at $26,666.67 (bulk) and "4000
                # L" of sake at $7.39 (base). Which way it went depends on the
                # product's own stock unit, so the cost has to be judged per line
                # rather than scaled blindly.
                #
                # The test is the one _UNIT_CEIL already states: no real product
                # costs more than 60c a millilitre or 20c a gram. An implied rate
                # above that is a bulk rate wearing a base-unit label, and only
                # then is the cost divided down with the quantity.
                #
                # It is checkable: "HG Soy Chilli Sauce" resolves to $35.00 of soy
                # and $4.38 of chilli — the exact figures its correctly-typed twin
                # "HG's Soy Chilli Sauce" states at 2.5 L and 0.25 kg.
                note = ""
                try:
                    c = float(ing.get("cost") or 0)
                except (TypeError, ValueError):
                    c = 0.0
                ceil = _UNIT_CEIL.get(base)
                if c > 0 and q > 0 and ceil is not None and (c / q) > ceil:
                    ing["cost"] = str(c / mult)
                    note = f", cost ${c:,.2f} -> ${c / mult:,.2f}"
                typos.append(f"{rname} / {ing.get('name')}: "
                             f"{q:g} {raw} -> {q:g} {base}{note}")
                mult = 1.0
            if base == raw and mult == 1.0:
                continue
            if mult != 1.0:
                ing["qty"] = q * mult
            ing["unit"] = base
            n += 1
    if typos:
        print(f"  {len(typos)} bulk-label typo(s) read as base units "
              f"(a line cannot be >{_MAX_REAL_BULK_LINE:g} L/kg):")
        for t in typos:
            print(f"      {t}")
    return n


def load_raw_line_costs(rec: dict) -> dict:
    out = {}
    for rname, body in (rec or {}).items():
        for ing in (body.get("ingredients") or []):
            try:
                out[(rname, norm(ing.get("name") or ""))] = float(ing.get("cost") or 0)
            except (TypeError, ValueError):
                continue
    return out


def load_ls_misread_refs(cogs_path=None) -> set[str]:
    """Which ProductIDs does Lightspeed itself price wrong?

    Elderflower is the case: BO export $253/5L ($0.0506/ml, matching every Massenez
    sibling) vs Lightspeed $24.17/5L. The bad number reached BOTH the seed and the
    per-line cost, so Hugo Spritz's 60ml line reads $0.29 instead of $3.04 and the
    drink reports 92.9% GP. Agreement-with-LS is normally good evidence that a
    recipe quantity is sane -- but not when LS's own price for that product is the
    thing we can prove wrong.
    """
    import csv as _csv
    from decimal import Decimal as _D
    path = cogs_path or COGS
    bo, ls = {}, {}
    try:
        rows = list(_csv.DictReader(open(path, encoding="utf-8-sig")))
    except OSError:
        return set()
    for r in rows:
        pid = (r.get("supplier_code") or "").strip()
        src = r.get("source_invoice") or ""
        try:
            if src.startswith("bo-seed"):
                q = _D(r["pack_qty"])
                if (r.get("pack_unit") or "").strip().lower() in ("ml", "g") and q > 0:
                    bo[pid] = _D(r["cost_per_unit_incl_gst"]) / q
            elif src.startswith("ls-recipe-seed"):
                v = _D(r.get("cost_per_base_unit") or 0)
                if v > 0:
                    ls[pid] = v
        except Exception:
            continue
    out = set()
    for pid in set(bo) & set(ls):
        if bo[pid] > 0:
            ratio = float(ls[pid] / bo[pid])
            if ratio > 3.0 or ratio < 1 / 3.0:
                out.add(f"lightspeed:{pid}")
    return out


def _trust_direct(ln, ls, is_prep):
    """May we cost this line as our_price x recipe_qty?

    Only if the recipe's (qty, unit) pair is believable, and the check for that is
    agreement with Lightspeed's own line cost — but ONLY when that line is itself
    credible. Two real cases pull in opposite directions:

      * Truffle Oil Prep: qty "4 ml", LS line $45.60. It is really 4 BOTTLES, so
        4 x $0.0456 = 18c is a 250x UNDER-cost. It is a PREP, so a $45.60 line is
        legitimate -> LS is credible -> the disagreement condemns the quantity.
      * Vesper Martini: qty "45 ml" of gin, LS line $95.34. Here 45 x $0.0706 =
        $3.18 is right and the LS line is the garbage one (a whole bottle against
        one serve). It is a NON-prep above the cap -> LS is not credible -> it may
        not veto our price.

    Under-costing is the flattering direction, so when the evidence is good enough
    to doubt the quantity we drop to the dimensionless ratio path, which never
    multiplies by a garbage number."""
    try:
        qty = float(ln.get("qty") or 0)
    except (TypeError, ValueError):
        return True
    if ls <= 0 or qty <= 0:
        return True
    if ls > _LS_LINE_CAP and not is_prep:      # LS line is the untrustworthy one
        return True
    # Lightspeed's price for THIS product is provably a misread (its own two seeds
    # contradict each other >3x), so its line cost carries the same error and may
    # not veto our stated, invoice/BO-backed rate. Narrow by construction: only
    # products with that contradiction qualify, so Truffle Oil's "4 ml" quantity
    # is still condemned by a credible LS line exactly as before.
    if ln.get("ref") in _LS_MISREAD_REFS:
        return True
    direct = float(ln["our_cost"]) * qty
    return _AGREE_LO * ls <= direct <= _AGREE_HI * ls


def norm(s: str) -> str:
    s = (s or "").lower()
    s = _SIZE.sub(" ", s)                 # drop [size]
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while s != prev:                      # strip trailing bare units, repeatedly
        prev, s = s, _TRAIL_UNIT.sub("", s).strip()
    return s


def load_bo_ids():
    """normalised ProductName -> ProductID (inventory-tracked stock items)."""
    by_name = {}
    prefixes = []                         # (normname, id) for truncated-name prefix match
    for _v, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            n = norm(r["ProductName"])
            if n:
                by_name.setdefault(n, r["ProductID"])
                prefixes.append((n, r["ProductID"]))
    return by_name, prefixes


def load_bo_groups():
    """normalised ProductName -> ReportingGroup, for products that have one.

    A blank ReportingGroup means Back Office does not sell the thing: every one
    of the 64 real preps in the book is blank. So a non-blank group is Lightspeed
    stating that a customer can order this, which is the fact that separates a
    house blend we make from a wine called "Red Blend".

    Keyed on the EXACT ProductName, deliberately. norm() strips the bracket, so
    "Stow Vermouth Blend [Bottle]" and "Stow Vermouth Blend [30ml]" collapse to
    one key — and they are precisely the pair that must NOT: the 30 ml pour is
    sold at $12 and the bottle is a $44.65 batch. Letting the pour's category
    reach the bottle turned the bottle into a menu item reporting -309% GP. An
    exact match can only ever fail closed, back to the name rule."""
    out = {}
    for _v, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            g = (r.get("ReportingGroup") or "").strip()
            n = (r.get("ProductName") or "").strip()
            if n and g:
                out.setdefault(n, g)
    return out


def _tok(s):
    """word-order-independent key: the alnum tokens, sorted. 'Coke 1.25L' and
    '1.25L Coke' collapse to the same key so a reversed menu name still matches."""
    return " ".join(sorted(re.findall(r"[a-z0-9]+", (s or "").lower())))


# Discontinued products still living under an old name in Produce. The recipe
# keeps the retired name but is poured/priced as the CURRENT product, so its sell
# price must follow the replacement, not the stale POS entry. Keyed recipe-name
# -> current product name (looked up in the priced product sheet). Add a line here
# whenever a product is renamed/replaced but the Produce recipe name lags behind.
# Same physical thing entered under several names in Produce. Confirmed by Zak.
# The scrape treats each as its own uncosted product, so a dish built on the
# duplicate costs nothing while the real recipe sits right there. Alias them and
# the line resolves to the costed original.
INGREDIENT_ALIAS = {
    "Tomato Base": "Pizza Sauce [Recipe]",          # Zak: "tomato base IS pizza sauce"
    "Pizza Tomato Sauce": "Pizza Sauce [Recipe]",   # ditto
    "Pizza Tomato Sauce ": "Pizza Sauce [Recipe]",  # trailing space in the export
    "S.S.C [Small Bottle]": "Spiced Sour Cream [Batch]",   # S.S.C = spiced sour cream
    "S.S.C": "Spiced Sour Cream [Batch]",
    "Oregano": "Oregano Leaves Rubbed - Torino",   # same herb, one costed
    # lowercase generics entered beside the real, costed product
    "shaved parmesan": "Dairy Farmers Shaved Parmesan [1kg]",
    "parmesan": "Dairy Farmers Shaved Parmesan [1kg]",
    "Passionfruit Juice": "Passionfruit Syrup",     # Zak: Puerto Sunset uses the syrup
    # Two names for one prep — 4 pizzas use the bare name, 42 the bracketed one.
    "Pizza Dough": "Pizza Dough [Recipe]",
    # Same onion, entered twice with the words swapped and on two ProductIDs.
    # 49 recipes use one, 6 the other. Consolidated onto the one with four
    # recent Fresh Fruit Team invoices behind it.
    "Onion Spanish [10kg]": "Spanish Onion [10Kg]",
    # Harry Gatos' name for Stowaway's house blend. data/recipe_venue_mirrors.yaml
    # already records the decision — "'Stow Vermouth Blend' vs 'Vermouth Blend'.
    # Same liquid, same pour. One spec, maintained once." — and mirrors Americano,
    # Boulevardier, Manhattan and Sbagliato Negroni to Stowaway's recipes. But the
    # mirror is at RECIPE level, and these four drinks reach the blend through an
    # INGREDIENT line, so the decision never got applied: four cocktails costed
    # 20-45 ml of vermouth at $0.00. Harry Gatos' Produce carries both names with
    # no cost on either; Stowaway's carries the price ($41.34 / 990 ml) and the
    # blend recipe (Carpano + Antica + Regal Rogue, 330 ml each).
    "Vermouth Blend [Bottle]": "Stow Vermouth Blend [Bottle]",
    # Harry Gatos' own SKUs for two wines it does not buy.
    #
    # Both carry a 750 ml size and CostPriceIncTax 0.0000 in Harry Gatos' Back
    # Office, so a $26 glass of rosé and a $32 glass of Veuve reported 100% GP.
    # Stowaway's records for the same two wines are invoice-fed — Whispering
    # Angel at $0.0457/ml off four ILG deliveries, Veuve at $0.1077/ml — and
    # there is exactly one product of each name in the group.
    #
    # The reason to read them as the same stock is not the name, it is the
    # purchasing: of 449 non-seed supplier rows filed to harry_gatos, every one
    # is food except two lines of White Light Vodka. Harry Gatos has no wine
    # supplier. The bottles it pours were bought on a Stowaway invoice, because
    # there is no other invoice they could have come from.
    #
    # Milagro Reposado is deliberately NOT here: it has no costed twin anywhere in
    # the group, so there is nothing to point at and it stays visible in the audit
    # until an invoice arrives.
    #
    # Velho Berreiro Cachaça was listed alongside it until 2026-08-06, on the same
    # reasoning. That reasoning was answered rather than met: Zak says the drink is
    # made with Germana, so the right fix is not to declare the two bottles one
    # stock — they are two brands — but to record that Harry Gatos pours a
    # different bottle than Produce says. That lives in
    # data/recipe_ingredient_swaps.yaml, scoped to the one recipe.
    "Whispering Angel - Bottle [HG]": "Whispering Angel Rosé - Bottle",
    "Veuve Clicquot - Bottle [HG]": "Veuve Clicquot Yellow Label - Bottle",
    # Zak: "havana club definitely has a price somewhere". It does — under a
    # name one word shorter. Pika Pika draws "Havana Club 3yr [700ml]"; the
    # priced Stowaway product is "Havana 3yr [700ml]" at $0.041556/ml. Both Back
    # Office entries read $0.00, and there is one Havana in the group.
    #
    # NOT bridged to the ILG invoice, deliberately. ILG bills "HAVANA CLUB 700ML
    # 3YO." at $58.01, and resolve_pack read the 700ML out of the description and
    # divided by one bottle — $82.87/L, exactly twice the seed. Neither the
    # single-bottle nor the 6-pack reading lands anywhere sensible ($58.01 or
    # $9.67 a bottle); $58.01 over TWO bottles is $29.01, and the seed says
    # $29.09. The line is two bottles, so the invoice-derived rate in costs.csv
    # is the one that is wrong, and the seed is right. Bridging it would double
    # the cost of every Havana pour. (That row is inert today — nothing
    # references ilg:355-0552 — but it is why this is an alias and not a bridge.)
    "Havana Club 3yr [700ml]": "Havana 3yr [700ml]",
    # Produce holds dried shiitake twice: "Shiitake Mushrooms Dried" (DefaultSize
    # 1 g, $25.00 — i.e. $25 a GRAM) and "Mushroom Shiitake Dried [1kg]" at
    # $31.25/kg. Jun Pacific invoice NB10486744 settles it: "Dried Shiitake
    # Mushroom 1kg", $31.25. Shiitake Tare draws 50 g of the broken one.
    "Shiitake Mushrooms Dried": "Mushroom Shiitake Dried [1kg]",
}

# Redundant lines Zak has asked to drop. NOT deleted from any source file — the
# scrape stays intact; the converter just stops carrying them into the book.
IGNORE_INGREDIENTS = {"bolognese"}

# Products no longer sold. Dropped from the book so they stop padding counts and
# skewing the GP spread — a Solo Combo that hasn't sold in months still reported a
# GP. The scrape on disk keeps them, so restoring one is deleting a line here.
RETIRED_RECIPES = re.compile(r"\bSolo Combo\b", re.I)

# Countable packaging, and which box each pizza size actually goes in. Both boxes
# are invoiced by Gulli every week (11" $24.10/50, 13" $32.13/50), so the cost
# follows the invoices; only the CHOICE of box is encoded here.
_SIZE_WORD = re.compile(r"^(Large|Regular|Gluten-free|Kids|Family)\b", re.I)
_PACKAGING = re.compile(r"pizza box|box insert", re.I)
# A bare unit word stuck on the end of an ingredient NAME (not a bracketed pack).
_TAIL_UNIT_WORD = re.compile(r"\s+(kgs?|g|gm|ml|lt?|ea|each)$", re.I)
# What makes a recipe a PIZZA rather than just a menu item with a size word.
_BASE_LINE = re.compile(r"pizza dough|pizza base|pizza sauce", re.I)
# A PREP/batch name (module-level twin of PREP_RE, which is built later in main()).
# A bracket that names a PACK — weight, volume or container. Marks a stock item.
_PACK_BRACKET = re.compile(r"\[\s*(?:[\d.]+\s*(?:kgs?|g|gm|l|lt|ml)\b"
                           r"|bottle|btl|ea|each|tin|can|box|bunch|punnet|jar|tub|keg|pack)",
                           re.I)
_PREP_NAME = re.compile(r"\[(batch|prep|recipe|\d+\s*(kg|g|l|ml))\]"
                        r"|\b(prep|mix|marination|batch|blend)\b", re.I)
_INSERT_REF = "lightspeed:22873876"   # Pizza Box Inserts, $0.11055 ea (Gulli)
_BOX_BY_SIZE = {
    "large": ('Large Pizza Box 13"', "lightspeed:22873851"),
    "family": ('Large Pizza Box 13"', "lightspeed:22873851"),
    "regular": ('Regular Pizza Box 11"', "lightspeed:22873831"),
    "gluten-free": ('Regular Pizza Box 11"', "lightspeed:22873831"),   # 11in base
    "kids": ('Regular Pizza Box 11"', "lightspeed:22873831"),
}

RENAMED_TO = {
    "Btl Disco Volante D": "Trutta Streamside Shiraz [Chilled] - Bottle",  # $68 bottle
}

# A recipe whose Produce NAME is not the name Back Office sells it under. The
# book is renamed on the way out, after every transform has run against the name
# the scrape uses, so nothing upstream has to know.
#
# Get this wrong and a recipe stops matching its product, so entries need the
# same standard as any other: the Back Office export must carry the new name and
# NOT the old one.
RECIPE_RENAMED_TO = {
    # Every other dine-in pizza is "Regular X [Dine-in]" — twenty of them. This
    # one lost its size prefix in Produce, and _mirror_dine_in already had to
    # special-case it ("a legacy name whose takeaway twin is Regular Pepperoni").
    #
    # The name cost real money twice over. Back Office sells "Regular Pepperoni
    # [Dine-in]" at $21 and has no product called "Pepperoni [Dine-in]" at all,
    # so the P&L could not match the recipe to the SKU — $253 a quarter, 114
    # serves since launch, costed off Lightspeed. And the sell-price lookup
    # normalises the bracket away, landing on the $2.00 "Pepperoni" add-on, which
    # is where the SEVERE "real recipe priced below cost, sells $2.00 costs
    # $2.11" came from. That was never a POS pricing error. It was this.
    "Pepperoni [Dine-in]": "Regular Pepperoni [Dine-in]",
}


def load_sell_prices():
    """normalised ProductName -> sell price incl GST (what the menu charges).

    Returns (by_exact, by_norm, by_tok), tried in that order.

    by_exact IS THE ONE THAT MATTERS AND IT WAS MISSING. norm() strips the
    bracket, so "Regular Margherita [Dine-in]" and "Regular Margherita" collapse
    to one key and whichever the export listed first won. It was always the
    takeaway, so every dine-in pizza carried its takeaway price — $14 against a
    real $21, six to eight dollars low, on all 77 of them. The cost was right and
    the GP was not, in the direction that makes a dish look worse than it is.
    Matching the product's own name first cannot be ambiguous, so it goes first.

    by_norm is the bracket-insensitive match. by_tok is a word-order-tolerant
    fallback that ONLY carries a token key when every priced product sharing that
    key agrees on ONE price — so 'Coke 1.25L' ($6) rescues the recipe named
    '1.25L Coke', but an ambiguous key like the three-size 'Trutta Streamside
    Shiraz' ($18/$27/$68) is dropped rather than guessed."""
    exact_prices = {}
    by_norm = {}
    tok_prices = {}
    for _v, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            try:
                p = float(r.get("SellPriceIncTax") or 0)
            except ValueError:
                p = 0
            nm = r["ProductName"]
            n = norm(nm)
            if n and p > 0:
                exact_prices.setdefault(nm.strip(), set()).add(round(p, 2))
                by_norm.setdefault(n, p)
                tok_prices.setdefault(_tok(nm), set()).add(round(p, 2))
    # Same discipline as by_tok: a name that two venues price differently is
    # dropped rather than resolved to whichever export was read first.
    by_exact = {k: next(iter(ps)) for k, ps in exact_prices.items() if len(ps) == 1}
    by_tok = {t: next(iter(ps)) for t, ps in tok_prices.items() if len(ps) == 1}
    return by_exact, by_norm, by_tok


def sell_of(name, by_exact, by_norm, by_tok):
    """current-product override (renamed items) first, then the product's OWN
    name, then the bracket-insensitive match, then the word-order fallback."""
    lookup = RENAMED_TO.get(name, name)
    return (by_exact.get(lookup.strip())
            or by_norm.get(norm(lookup))
            or by_tok.get(_tok(lookup)))


_YB = re.compile(r"\[(\d+(?:\.\d+)?)\s*(kg|g|l|ml|lt|litre)\]", re.I)


def load_yields():
    """recipe name -> (qty, unit) from the name bracket or data/prep_yields.yaml."""
    out = {}
    y = ROOT / "data" / "prep_yields.yaml"
    if y.exists():
        import yaml
        for k, v in (yaml.safe_load(y.read_text()) or {}).items():
            out[k] = (float(v["yield_qty"]), v["yield_unit"])
    return out


_BASE = {"kg": ("g", 1000.0), "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "litre": ("ml", 1000.0),
         # A COUNTABLE IS A COUNTABLE. resolve_pack answers "one whole pack" as
         # "can" (its basis word) while every recipe line says "ea", so a cost we
         # hold per-unit could never be multiplied by a per-unit quantity: 105
         # cost rows in "can" against 274 recipe lines in "ea", and each mismatch
         # silently fell through to Lightspeed's number or to $0.
         #
         # They are the same dimension and the same magnitude — one of the thing
         # you bought — so this is a rename, not a conversion, and the factor is
         # 1.0. Nothing here means "a 375 ml can": that is a pack SIZE, which
         # lives in the pack columns, not the unit.
         "can": ("ea", 1.0)}


def _to_base(cost, unit):
    """Express a price in BASE units: $/kg -> $/g, $/L -> $/ml.

    The seed and the invoice for one product often disagree only in scale — the
    passionfruit seed resolves per GRAM while the Berry Man invoice bridges per
    KILO. Same price, same dimension, but the ratio path compares unit strings,
    so it could never run and the syrup stayed uncosted off our book. Normalising
    both sides at load makes them comparable without any unit maths downstream.
    Only mass and volume convert; ea/box/bunch have no base and pass through."""
    u = (unit or "").lower()
    if u in _BASE:
        nu, div = _BASE[u]
        try:
            return (str(float(cost) / div), nu)
        except (TypeError, ValueError):
            return (cost, unit)
    return (cost, unit)


def load_our_costs():
    """ingredient id -> latest (cost_per_unit, unit) from our cost book."""
    latest = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k = r["ingredient"]
        d = r["observed_on"]
        if k not in latest or d >= latest[k][2]:
            latest[k] = (r["cost_per_unit"], r["unit"], d)
    return {k: _to_base(v[0], v[1]) for k, v in latest.items()}


def load_our_preps(our_costs):
    """OUR OWN recipe book (data/recipes/*.yaml), priced per BASE unit.

    The sauces Zak keyed in — Spiced Sour Cream, Chimichurri, Minced Garlic Prep —
    are costed in our book, but the Lightspeed converter never read them. Produce
    also carries each one as an empty PRODUCT stub, so every pizza drawing 40g of
    spiced sour cream resolved to the stub and added $0. Five Sanchez variants were
    understating cost that way. Our own recipe is the better source: it is built
    from invoice-fed ingredients, so it reprices when they do.
    """
    import yaml

    specs = {}
    for f in sorted((ROOT / "data" / "recipes").glob("*.yaml")):
        for r in (yaml.safe_load(f.read_text()) or []):
            if isinstance(r, dict) and r.get("product"):
                specs[r["product"]] = r

    memo: dict[str, tuple[float, str] | None] = {}

    def rate_of(nm, stack=()):
        if nm in memo:
            return memo[nm]
        r = specs.get(nm)
        if not r or nm in stack:
            return None
        total = 0.0
        for ln in (r.get("ingredients") or []):
            q = float(ln.get("qty") or 0)
            unit = (ln.get("unit") or "").lower()
            if ln.get("subrecipe"):
                sub = rate_of(ln["subrecipe"], stack + (nm,))
                if not sub or sub[1] != unit:
                    return None
                total += q * sub[0]
                continue
            live = our_costs.get(ln.get("id") or "")
            if live and live[1] == unit:
                total += q * float(live[0])           # invoice-fed, preferred
            elif ln.get("unit_cost_incl") is not None:
                total += q * float(ln["unit_cost_incl"])   # the keyed-in snapshot
            else:
                return None
        y, yu = r.get("yield_qty"), (r.get("yield_unit") or "").lower()
        if not y or float(y) <= 0:
            return None
        if yu in _BASE:                                # kg -> g, L -> ml
            yu, y = _BASE[yu][0], float(y) * _BASE[yu][1]
        memo[nm] = (total / float(y), yu)
        return memo[nm]

    out = {}
    for nm in specs:
        got = rate_of(nm)
        if got:
            out[norm(nm)] = got
    return out


def load_packs():
    """ingredient id -> (pack_qty, unit) in BASE units, from the cost book's own
    pack contract. Used to price a whole pack — a "- Bottle" menu line is one
    750ml bottle, not one millilitre."""
    out = {}
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        q, u = (r.get("pack_qty") or "").strip(), (r.get("pack_unit") or "").strip().lower()
        k, d = r.get("lightspeed_id") or "", (r.get("invoice_date") or "")
        k = f"lightspeed:{r['supplier_code']}" if (r.get("supplier") or "") == "Lightspeed" else ""
        if not (k and q and u):
            continue
        try:
            qf = float(q)
        except ValueError:
            continue
        if u in _BASE:                     # kg -> g, L -> ml, matching _to_base()
            u, qf = _BASE[u][0], qf * _BASE[u][1]
        if k not in out or d >= out[k][2]:
            out[k] = (qf, u, d)
    return {k: (v[0], v[1]) for k, v in out.items()}


def load_seed_baseline():
    """ingredient id -> the SEED per-unit (the scrape-time baseline). Used to update
    a line by the ratio latest/baseline without needing the recipe's (often garbage)
    unit to match — a dimensionless price-movement factor, so it can't blow up."""
    base, earliest = {}, {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k, d = r["ingredient"], r["observed_on"]
        if str(r.get("source_invoice") or "").startswith(
                ("ls-recipe-seed", "bo-seed", "recipe-bridge-seed", "bo-ingredient-seed",
                 "house-recipe-seed", "invoice-derived-seed")):
            base[k] = _to_base(r["cost_per_unit"], r["unit"])
        if k not in earliest or d < earliest[k][2]:
            earliest[k] = (r["cost_per_unit"], r["unit"], d)
    # A product with real invoices but no seed row still needs a scrape-time
    # baseline, or the ratio path can't run and the line falls back to Lightspeed
    # for want of a divisor. Its OLDEST observation is the best evidence we have of
    # what it cost when the recipe was scraped, so use that. (A seed row, when one
    # exists, still wins: it is dated at the scrape itself.)
    for k, v in earliest.items():
        base.setdefault(k, _to_base(v[0], v[1]))
    return base


def main() -> int:
    rec = json.loads(RECIPES.read_text())
    global _LS_MISREAD_REFS, _RAW_LINE_COST
    _LS_MISREAD_REFS = load_ls_misread_refs()
    # Correct any unit typo BEFORE anything reads the line — see
    # data/recipe_line_unit_fixes.yaml, where each entry carries arithmetic proof.
    _fixed = apply_unit_fixes(rec)
    if _fixed:
        print(f"  corrected {_fixed} mislabelled unit(s) (recipe_line_unit_fixes.yaml)")
    # ...repoint any line Produce files against a bottle the venue no longer pours,
    # BEFORE the raw line costs are captured — the old bottle's cost must not
    # survive the swap. See data/recipe_ingredient_swaps.yaml.
    _swapped = apply_ingredient_swaps(rec)
    if _swapped:
        print(f"  swapped {_swapped} ingredient(s) (recipe_ingredient_swaps.yaml)")
    # ...then put every line in the base units our cost book is priced in, so the
    # Harry Gatos scrape's "mL"/"L"/"kg"/"Units" can reach it at all.
    _normalised = normalise_line_units(rec)
    if _normalised:
        print(f"  normalised {_normalised} line unit(s) to base units")
    _RAW_LINE_COST = load_raw_line_costs(rec)
    if _LS_MISREAD_REFS:
        print(f"  {len(_LS_MISREAD_REFS)} ProductIDs Lightspeed prices wrong "
              f"(BO export contradicts its own seed >3x) — our rate wins on those")
    bo_by_name, bo_prefixes = load_bo_ids()
    bo_groups = load_bo_groups()

    def bo_group(nm):
        """The menu category Back Office files this product under, or ""."""
        return bo_groups.get((nm or "").strip()) or ""
    our_costs = load_our_costs()
    our_preps = load_our_preps(our_costs)
    packs = load_packs()
    seed_base = load_seed_baseline()
    sell_by_exact, sell_by_norm, sell_by_tok = load_sell_prices()
    yields = load_yields()
    # 47 recipe names collide once normalised (a base recipe and its "[Dine-in]"
    # twin). Keep the SHORTEST — the base recipe — so a normalised lookup lands on
    # the plain version rather than whichever happened to be inserted last.
    recnames: dict[str, str] = {}
    for _k in rec:
        _n = norm(_k)
        if _n not in recnames or len(_k) < len(recnames[_n]):
            recnames[_n] = _k

    def resolve(name, parent=None):
        name = INGREDIENT_ALIAS.get(name, INGREDIENT_ALIAS.get((name or '').strip(), name))
        # EXACT recipe name first. norm() strips a bracket suffix, so "Regular
        # Margherita" and "Regular Margherita [Dine-in]" collapse to one key; when
        # the [Dine-in] variant won that key, resolving its own "Regular Margherita"
        # line looked like a self-reference, the guard blocked it, and the line fell
        # through to an UNCOSTED product of the same name. Matching the literal name
        # first keeps the two apart, so the costed base recipe is used.
        # An EMPTY recipe is not a recipe. Produce holds "De La Grosse Beaujolais -
        # Bottle" with no ingredients at all, so every size referencing it inherited
        # nothing and the whole wine stayed off our book. When the recipe is empty,
        # fall through to the PRODUCT of the same name, which is costed ($34.58 a
        # 750ml bottle) — that is what a "bottle" line actually means.
        if name in rec and name != parent and (rec[name] or {}).get("ingredients"):
            return ("subrecipe", name)
        n = norm(name)
        # A PACK-SIZED name is a stock item, not a menu item. norm() strips brackets,
        # so "Pepperoni [3kg]" — a bag of pepperoni — normalised to "pepperoni" and
        # matched the RECIPE "Pepperoni [Dine-in]", costing a topping as a whole
        # pizza. A bracket carrying a weight, volume or container is the giveaway
        # that the line means stock, so the product wins for those.
        if _PACK_BRACKET.search(name) and n in bo_by_name:
            return ("id", f"lightspeed:{bo_by_name[n]}")
        # a recipe used as an ingredient is a SUB-RECIPE and folds in its build cost
        # off our book (via the safe ls-ratio scaling in cost_of). Block only a TRUE
        # self-reference (a recipe listing itself). Many preps — Pizza Dough [Recipe],
        # Sugar Syrup, Gravy Prep — are ALSO products in the export, so we must prefer
        # the recipe here or those lines resolve to an uncosted product ("part LS").
        if n in recnames and recnames[n] != parent and (rec.get(recnames[n]) or {}).get("ingredients"):
            return ("subrecipe", recnames[n])
        if n in bo_by_name:
            return ("id", f"lightspeed:{bo_by_name[n]}")
        # truncated scrape name: unique BO product that starts with it
        if len(n) >= 8:
            hits = {pid for pn, pid in bo_prefixes if pn.startswith(n)}
            if len(hits) == 1:
                return ("id", f"lightspeed:{hits.pop()}")
        return (None, None)

    def _canonicalise(ings):
        """Rewrite duplicate ingredient names to the ONE real thing, drop the
        redundant ones, and merge any line that now collides.

        Produce holds the same prep under several names ("Tomato Base", "Pizza
        Tomato Sauce" = Pizza Sauce). Aliasing only the lookup left the old name
        on display and could leave a recipe carrying the same sauce twice, so the
        line itself is renamed and same-name/same-unit lines are summed. The
        scrape on disk is untouched — this is a read-time normalisation."""
        out, seen = [], {}
        for ing in ings:
            nm = (ing.get("name") or "").strip()
            if nm in IGNORE_INGREDIENTS:
                continue
            # A bare unit word glued on the end — "Mushrooms [4Kg box] kg",
            # "Pizza Sauce [Recipe] kg". Same ProductID as the clean name every
            # time, so it is a display duplicate: the picker and the recipe book
            # list one product twice. Strip it when the clean name is a real one.
            stripped = _TAIL_UNIT_WORD.sub("", nm).strip()
            if stripped and stripped != nm:
                nm = stripped
                ing = dict(ing, name=nm)
            canon = INGREDIENT_ALIAS.get(ing.get("name")) or INGREDIENT_ALIAS.get(nm)
            if canon:
                ing = dict(ing, name=canon)
            key = (ing.get("name"), (ing.get("unit") or "").lower())
            if key in seen:                      # same prep twice -> one line
                prev = seen[key]
                try:
                    prev["qty"] = float(prev.get("qty") or 0) + float(ing.get("qty") or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    prev["cost"] = float(prev.get("cost") or 0) + float(ing.get("cost") or 0)
                except (TypeError, ValueError):
                    pass
                continue
            ing = dict(ing)
            seen[key] = ing
            out.append(ing)
        return out

    def _dedupe_truncated(ings):
        """The Produce scrape truncates long ingredient names at a fixed width, and
        for some rows emits BOTH the cut name ('...Shiraz [Chilled] - Bot') and the
        full one ('...- Bottle') — same qty, unit and cost — so the recipe counts the
        pour twice ($39.68 for a $19.84 bottle). Drop a line whose name is a strict
        prefix of another line's name when qty, unit AND scraped cost all match: a
        real second ingredient never collides on all three."""
        def sig(i):
            return (str(i.get("qty")), i.get("unit"), str(i.get("cost")))
        drop = set()
        for a in range(len(ings)):
            for b in range(len(ings)):
                if a == b or a in drop or b in drop:
                    continue
                na, nb = ings[a].get("name") or "", ings[b].get("name") or ""
                if na != nb and nb.startswith(na) and sig(ings[a]) == sig(ings[b]):
                    drop.add(a)          # a is the truncated prefix -> drop it
        return [i for k, i in enumerate(ings) if k not in drop]

    out = {}
    ing_res = Counter()
    for name, body in rec.items():
        if RETIRED_RECIPES.search(name):
            continue
        lines = []
        for ing in _canonicalise(_dedupe_truncated(body.get("ingredients", []))):
            kind, ref = resolve(ing["name"], name)
            ing_res[kind or "unmatched"] += 1
            our = None
            if kind == "id" and ref in our_costs:
                oc, ou = our_costs[ref]
                # only use our number when its unit matches how the recipe uses it.
                # a keg priced per-keg used as 570 "ml", or a bottle per-bottle used
                # as ml, must NOT be multiplied by the ml qty — fall back to the
                # scraped per-pour cost, which is already in the right unit.
                if ou == (ing.get("unit") or ""):
                    # magnitude sanity: a per-ml/per-g cost above a plausible ceiling
                    # is a mis-unit seed (e.g. a $26.50 pizza box or a per-litre sauce
                    # tagged "ml"). Multiplying it by the recipe qty produces absurd
                    # costs ($419 pizzas), so fall back to the sane scraped per-line
                    # cost. Fail toward review.
                    ceil = _UNIT_CEIL.get(ou)
                    if ceil is None or float(oc) <= ceil:
                        our = oc
            if our is None:
                # Produce's empty PRODUCT stub for a sauce we actually have a
                # recipe for. Our book knows what it costs to make — use that.
                p = our_preps.get(norm(ing["name"]))
                lu = (ing.get("unit") or "").lower()
                # g <-> ml is allowed HERE ONLY, at density 1.0. Our sauces yield in
                # ml (Spiced Sour Cream 1190ml) while the pizza recipes draw them in
                # grams — a kitchen weighs a sauce and pours a batch. These are all
                # water/dairy-based, so 1ml = 1g within a few percent, and the
                # alternative was costing 40g of sauce at $0. Cross-DIMENSION
                # conversion (ml -> ea) stays banned; that one caused $11,400 serves.
                if p and (p[1] == lu or {p[1], lu} == {"g", "ml"}):
                    our = p[0]
            lines.append({"name": ing["name"], "kind": kind, "ref": ref,
                          "qty": ing.get("qty"), "unit": ing.get("unit"),
                          "ls_cost": ing.get("cost"), "our_cost": our})
        out[name] = {"ingredients": lines}

    # ---- EXPAND size variants into real ingredient lists -------------------
    # Produce builds a Regular pizza as "0.716 x the Large" and a Wings Deal as
    # "1 x the Large" — one sub-recipe line, no ingredients of its own. That is
    # unreadable on the floor and impossible to cost line-by-line, so the
    # reference is replaced by the parent's OWN lines with every quantity scaled
    # by the fraction. Each size becomes a self-contained recipe whose cost is
    # computed from its ingredients, not inherited.
    #
    # Only a SOLD serve (>= $3) at a serve-sized multiple is expanded: a batch
    # tray is also "qty 1" but that means one portion of it, not the whole thing.
    def _expand_serves():
        for _ in range(5):                       # nested variants; converges fast
            changed = False
            for nm, body in out.items():
                fresh, hit = [], False
                for ln in body["ingredients"]:
                    ref = ln.get("kind") == "subrecipe" and ln.get("ref")
                    if ref and ref in out and ref != nm:
                        try:
                            q = float(ln.get("qty") or 0)
                        except (TypeError, ValueError):
                            q = 0.0
                        if 0 < q <= 2 and (sell_of(ref, sell_by_exact, sell_by_norm, sell_by_tok) or 0) >= 3:
                            for sub in out[ref]["ingredients"]:
                                c = dict(sub)
                                for k in ("qty", "ls_cost"):   # the LS reference must scale too
                                    # (scaling "cost" was a no-op: the built
                                    #  line names the field ls_cost, so every
                                    #  ratio-costed line kept its FULL-size
                                    #  figure and a Regular came out 0.819x)
                                    try:
                                        c[k] = float(sub.get(k) or 0) * q
                                    except (TypeError, ValueError):
                                        pass
                                c["scaled_from"] = f"{ref} x{q:g}"
                                fresh.append(c)
                            hit = changed = True
                            continue
                    fresh.append(ln)
                if hit:
                    body["ingredients"] = fresh
            if not changed:
                break
    _expand_serves()

    def _fix_packaging():
        """A pizza goes in ONE box with ONE insert, whatever size it is.

        Produce builds a Regular as "0.716 x the Large", and that ratio was landing
        on the PACKAGING too — a regular pizza was charged 0.716 of a 13" box. You
        cannot use 71.6% of a box, and it isn't even the right box: Gulli invoice
        both 11" ($0.482 ea) and 13" ($0.643 ea) cartons weekly, and a regular
        pizza goes in the 11". Flour scales with size; cardboard does not.

        So packaging is forced to exactly 1 whole unit, and the box is chosen by
        the size the recipe name declares.
        """
        def priced(ref):
            oc = our_costs.get(ref or "")
            return float(oc[0]) if oc and oc[1] == "ea" else None

        fixed = added = removed = 0
        for name, r in out.items():
            m = _SIZE_WORD.match(name)
            if not m:
                continue
            box = _BOX_BY_SIZE.get(m.group(1).lower())
            # A DINE-IN pizza comes out on a plate. Produce charged 34 of them for a
            # box anyway. A TAKEAWAY pizza physically cannot leave without one, yet
            # 19 had none. The rule is the physical fact, not the data entry.
            dine_in = "[dine-in]" in name.lower()
            keep = []
            for ln in r["ingredients"]:
                nm = ln.get("name") or ""
                if not _PACKAGING.search(nm):
                    keep.append(ln)
                    continue
                if dine_in:
                    removed += 1
                    continue
                if box and "insert" not in nm.lower():
                    ln["name"], ln["ref"], ln["kind"] = box[0], box[1], "id"
                ln["qty"], ln["unit"] = 1, "ea"
                # The scraped per-line cost was a fraction of the WRONG box, so it
                # can't stand as a fallback. Price the line off our book directly —
                # both boxes are invoiced weekly and carry a live per-box rate.
                ln["our_cost"] = priced(ln["ref"])
                ln["ls_cost"] = ln["our_cost"]
                keep.append(ln)
                fixed += 1
            # ...but only for something that is actually a PIZZA. "Kids" matches
            # Kids Spag Bol too, and handing a bowl of pasta a pizza box made its
            # only cost line a cardboard box. A pizza has a base or a dough.
            def _has_base(lines, depth=0):
                # Some takeaway pizzas are entered as "1 x the [Dine-in] one", so
                # the dough is a level down. Without following the reference,
                # Regular Pepperoni looked like it had no base and went out unboxed.
                for l in lines:
                    if _BASE_LINE.search(l.get("name") or ""):
                        return True
                    ref = l.get("ref")
                    if depth < 2 and l.get("kind") == "subrecipe" and ref in out:
                        if _has_base(out[ref]["ingredients"], depth + 1):
                            return True
                return False

            is_pizza = _has_base(keep)
            if not dine_in and box and is_pizza:
                # Zak: a takeaway pizza has the box AND the insert. Produce had 80
                # carrying an insert and 19 not, which was data entry rather than a
                # decision, so both are now guaranteed — exactly one of each.
                for pname, pref in ((box[0], box[1]),
                                    ("Pizza Box Inserts", _INSERT_REF)):
                    if any((l.get("ref") == pref) for l in keep):
                        continue
                    keep.append({"name": pname, "kind": "id", "ref": pref, "qty": 1,
                                 "unit": "ea", "ls_cost": priced(pref),
                                 "our_cost": priced(pref)})
                    added += 1
            r["ingredients"] = keep
        # Inserts are NOT synthesised — 80 takeaway pizzas carry one and 19 don't,
        # and unlike the box I have no physical rule that says which is right. Left
        # as found, and flagged, rather than guessed at 11c a go.
        return fixed, added, removed

    def _apply_regular_grams():
        """Put Zak's WEIGHED regular-pizza quantities in place of the derived ones.

        Produce had no real regular recipe — it built one as "0.716 x the Large", so
        every quantity was a ratio off another size. data/pizza_regular_grams.yaml
        holds the measured grams. Per Zak: "if the ingredient is on the pizza, this
        is how much of it is used" — so this CORRECTS quantities on lines that are
        already there and never adds an ingredient a recipe doesn't list.
        """
        import yaml
        spec_path = ROOT / "data" / "pizza_regular_grams.yaml"
        if not spec_path.exists():
            return 0, 0
        spec = [s for s in (yaml.safe_load(spec_path.read_text()) or []) if s.get("match")]
        for s in spec:
            s["_re"] = re.compile(s["match"], re.I)

        changed = touched = 0
        for name, r in out.items():
            if not re.match(r"^Regular\b", name, re.I):
                continue
            low = name.lower()
            hit = False
            for ln in r["ingredients"]:
                nm = ln.get("name") or ""
                if _PACKAGING.search(nm):
                    continue                      # boxes are counted, not weighed
                # A line can be a whole SOLD pizza rather than an ingredient —
                # "Regular Pepperoni" is built from one "Pepperoni [Dine-in]", where
                # qty 1 means one pizza. Matching /pepperoni/ there and writing "62g"
                # turned a $15 pizza into a 62-gram nothing. Only weigh INGREDIENTS.
                # (Checked against the recipe book, not the price sheet — the sell
                # lookup misses on names like "Pepperoni [Dine-in]" and a miss here
                # silently lets a whole pizza be re-weighed as 62g of topping.)
                if (ln.get("kind") == "subrecipe" and ln["ref"] in out
                        and not _PREP_NAME.search(ln["ref"])):
                    continue
                for s in spec:                    # first match wins; `when` rules
                    if not s["_re"].search(nm):   # are listed before the default
                        continue
                    if s.get("when") and s["when"].lower() not in low:
                        continue
                    if float(ln.get("qty") or 0) != float(s["grams"]):
                        ln["qty"], ln["unit"] = float(s["grams"]), "g"
                        # RE-RESOLVE. our_cost was worked out earlier against the
                        # line's ORIGINAL unit — Produce writes some of these in
                        # "bunch" — so after rewriting to grams it was left null and
                        # the line fell back to Lightspeed's cost for the OLD
                        # quantity. That is how 15g of shallots cost 8c on one pizza
                        # and 37c on another. Re-price against our book in the unit
                        # the line now uses, and bring the referee with it.
                        oc = our_costs.get(ln.get("ref") or "")
                        if oc and oc[1] == "g":
                            ln["our_cost"] = oc[0]
                            ln["ls_cost"] = float(oc[0]) * float(s["grams"])
                        changed += 1
                        hit = True
                    break
            touched += 1 if hit else 0
        return changed, touched

    def _add_missing_lines():
        """Put back a topping Produce omitted from a recipe entirely.

        A missing ingredient under-costs the dish and flatters its GP — the
        dangerous direction — but inventing toppings is worse, so every entry in
        data/recipe_missing_lines.yaml needs the same pizza in another size to
        carry it. Added BEFORE the weighed-grams pass so the quantity comes from
        Zak's sheet, not from here.
        """
        import yaml
        path = ROOT / "data" / "recipe_missing_lines.yaml"
        if not path.exists():
            return 0
        added = 0
        for spec in (yaml.safe_load(path.read_text()) or []):
            for rname in spec.get("recipes") or []:
                r = out.get(rname)
                if not r:
                    continue
                if any((l.get("ref") == spec["ref"]) or
                       norm(l.get("name") or "") == norm(spec["name"])
                       for l in r["ingredients"]):
                    continue                      # already there — never duplicate
                # A SUB-RECIPE line carries its own quantity (a Wings Deal is one
                # portion of BBQ Wings), unlike an ingredient line whose grams come
                # from Zak's weighed sheet in the pass below.
                if spec.get("subrecipe"):
                    r["ingredients"].append({
                        "name": spec["name"], "kind": "subrecipe", "ref": spec["ref"],
                        "qty": spec.get("qty", 1), "unit": spec.get("unit") or "ea",
                        "ls_cost": None, "our_cost": None,
                    })
                    added += 1
                    continue
                oc = our_costs.get(spec["ref"])
                # qty 0 = "the grams come from Zak's weighed sheet in the pass
                # below" (pizza toppings). A cocktail line states its own ml here,
                # because a branded Margarita is the Classic's spec with the base
                # spirit swapped — there is no weighed sheet for a pour.
                r["ingredients"].append({
                    "name": spec["name"], "kind": "id", "ref": spec["ref"],
                    "qty": spec.get("qty", 0), "unit": spec.get("unit") or "g",
                    "ls_cost": None,
                    "our_cost": float(oc[0]) if oc and oc[1] == (spec.get("unit") or "g") else None,
                })
                added += 1
        return added

    def _mirror_dine_in():
        """A dine-in pizza IS the takeaway one, minus the packaging (Zak).

        Produce maintained the two separately, so they drifted: different toppings,
        different weights, and the dine-in twins never got the weighed-grams pass or
        the restored Hawaiian ham. Copying the takeaway recipe makes one of them the
        single source of truth and the other a plate.

        NOT done when the takeaway is itself built as "1 x the [Dine-in] one" —
        Regular Pepperoni is exactly that, and copying it back would replace the
        dine-in's real toppings with a reference to itself.
        """
        import copy
        mirrored = skipped = 0
        for name in list(out):
            if not name.endswith(" [Dine-in]"):
                continue
            stem = name[:-len(" [Dine-in]")]
            # "Pepperoni [Dine-in]" has no size prefix at all — a legacy name whose
            # takeaway twin is "Regular Pepperoni". Without this fallback it kept
            # Produce's derived weights forever, because no rule matched its name.
            src = out.get(stem) or out.get(f"Regular {stem}")
            if not src:
                continue
            if any(l.get("ref") == name for l in src["ingredients"]):
                skipped += 1                  # takeaway points AT this recipe
                continue
            out[name]["ingredients"] = [
                copy.deepcopy(l) for l in src["ingredients"]
                if not _PACKAGING.search(l.get("name") or "")
            ]
            mirrored += 1
        return mirrored, skipped

    def _flatten_pointer_pizzas():
        """Give a pizza its own ingredients when it is only a pointer at another.

        Regular Pepperoni had ONE line: "1 x Pepperoni [Dine-in]" — a stray legacy
        recipe with no size prefix and a $2 placeholder price. Because its toppings
        lived a level down, the weighed-grams pass skipped it entirely and it kept
        Produce's derived weights. Copying the lines up means every regular pizza is
        a real ingredient list, which is what Zak asked for.
        """
        n = 0
        for name, r in out.items():
            if not _SIZE_WORD.match(name):
                continue
            body = [l for l in r["ingredients"] if not _PACKAGING.search(l.get("name") or "")]
            if len(body) != 1:
                continue
            ln = body[0]
            src = out.get(ln.get("ref") or "")
            if (ln.get("kind") != "subrecipe" or not src
                    or not any(_BASE_LINE.search(x.get("name") or "")
                               for x in src["ingredients"])):
                continue                       # not a pointer at another pizza
            import copy
            r["ingredients"] = ([copy.deepcopy(x) for x in src["ingredients"]
                                 if not _PACKAGING.search(x.get("name") or "")]
                                + [l for l in r["ingredients"]
                                   if _PACKAGING.search(l.get("name") or "")])
            n += 1
        return n

    def _strip_wheat_from_gf():
        """A gluten-free pizza does not contain wheat dough.

        Every one of the 47 GF recipes carried BOTH the gluten-free base AND
        "Pizza Dough [Recipe]" — Produce built the GF variants by copying a normal
        pizza and adding the GF base, without taking the dough out. It double-counts
        the base, but the real problem is that the recipe a kitchen reads for a
        coeliac order lists wheat flour.

        Only ever removes the wheat line, and only from a recipe that already has a
        gluten-free base to replace it.
        """
        n = 0
        for name, r in out.items():
            if not any("gluten free" in (l.get("name") or "").lower()
                       for l in r["ingredients"]):
                continue
            keep = [l for l in r["ingredients"]
                    if not re.search(r"pizza dough", l.get("name") or "", re.I)]
            n += len(r["ingredients"]) - len(keep)
            r["ingredients"] = keep
        return n

    _wheat = _strip_wheat_from_gf()
    print(f"  removed {_wheat} wheat-dough line(s) from gluten-free recipes")

    _flat = _flatten_pointer_pizzas()
    if _flat:
        print(f"  flattened {_flat} pizza(s) that were only a pointer at another")

    _missing = _add_missing_lines()
    if _missing:
        print(f"  restored {_missing} ingredient line(s) Produce had omitted")

    _rg_changed, _rg_recipes = _apply_regular_grams()
    print(f"  regular pizzas: {_rg_changed} quantities set from Zak's weighed sheet "
          f"across {_rg_recipes} recipes")

    # NO ROUNDING PASS HERE, DELIBERATELY. I added one to turn leftovers like
    # "2.14799g of basil" into weighable numbers, and Zak pushed back. He is right:
    # those quantities are still Produce's 0.716-scaled DERIVATIONS, not
    # measurements, and rounding 42.96 -> 43 makes a guess look like a weighed fact.
    # An ugly number is honest signal that nobody has weighed that ingredient yet.
    # Quantities from data/pizza_regular_grams.yaml are exact because Zak weighed
    # them; everything else should stay visibly derived until he does.


    _mir, _mir_skip = _mirror_dine_in()
    print(f"  dine-in: {_mir} recipes mirrored from their takeaway twin "
          f"({_mir_skip} left alone — the takeaway is built FROM the dine-in)")

    _pf, _pa, _pr = _fix_packaging()
    print(f"  packaging: {_pf} lines set to one whole box, {_pa} takeaway pizzas "
          f"given the box they were missing, {_pr} dine-in box lines removed")

    # a recipe used as an ingredient by another recipe is a PREP/BATCH (its POS
    # "sell price" is a placeholder, and it may legitimately carry a big bulk line
    # like $244 of chicken). Bracket sizes ([Batch]/[2Kg]/[1L]) mark bulk preps too.
    # (cap lives at module level so _trust_direct can read it too)
    used_as_sub = {ln["ref"] for r in out.values() for ln in r["ingredients"]
                   if ln["kind"] == "subrecipe" and ln["ref"]}
    PREP_RE = re.compile(r"\[(batch|prep|\d+\s*(kg|g|l|ml))\]|\b(prep|mix|marination|batch|blend)\b", re.I)

    def prep_ish(nm):
        return nm in used_as_sub or bool(PREP_RE.search(nm))

    # recursive cost: prefer our_cost, else ls_cost; sub-recipes fold in their total
    memo = {}

    def cost_of(name, stack=()):
        if name in memo:
            return memo[name]
        if name in stack:                 # cycle guard
            return (0.0, 0.0, True)
        r = out.get(name)
        if not r:
            return (0.0, 0.0, False)
        # AN EMPTY RECIPE THAT SELLS IS THE DANGEROUS CASE. Produce holds
        # "Unico Zelo Terra Cotta - Bottle" with no ingredients at all, so it costed
        # $0 against a $73 sell price — a 100% GP that reads like the best line on
        # the menu. resolve() already sends the SIZES to the product; the bottle
        # itself still needs a cost, and a "- Bottle" line means exactly one full
        # pack of that product ($21.89 for the 750ml). Cost it that way.
        if not r["ingredients"]:
            pid = bo_by_name.get(norm(name))
            pack = packs.get(f"lightspeed:{pid}") if pid else None
            cur = our_costs.get(f"lightspeed:{pid}") if pid else None
            if pack and cur and pack[1] == cur[1] and float(pack[0]) > 0:
                whole = float(cur[0]) * float(pack[0])
                memo[name] = (round(whole, 4), round(whole, 4), True)
                return memo[name]
        our_tot = ls_tot = 0.0
        full_ours = True
        for ln in r["ingredients"]:
            # the scraped per-line `cost` is Lightspeed's own dollar amount for that
            # line — reliable even when the qty/unit shown are garbage (a whole
            # chicken logged as "0.5 ml"). So it is NOT divided by qty; doing so
            # zeroed legitimate lines and under-costed roasts to 52c.
            ls = float(ln["ls_cost"] or 0)
            eff = 0.0                          # this line's ACTUAL $ contribution
            if ln["kind"] == "subrecipe":
                # cost a sub-recipe off OUR book without needing its batch yield:
                # scale the prep's our-book batch cost by the LS ratio of this line to
                # the prep's LS batch total. (our_use = our_batch x ls_line/ls_batch =
                # our_batch x qty_used/yield — the yield cancels.) So the prep's real
                # invoice-fed ingredient costs flow through, and it can't blow up: when
                # our_batch ~= ls_batch the line stays ~= the reliable LS per-use cost.
                so, sl, sfo = cost_of(ln["ref"], stack + (name,))
                _yb = _YB.search(ln["ref"] or "")
                _y = yields.get(ln["ref"])
                if not _y and _yb:
                    _q, _u = float(_yb.group(1)), _yb.group(2).lower()
                    _y = (_q * 1000, "g") if _u == "kg" else (_q * 1000, "ml") if _u in ("l", "lt", "litre") else (_q, _u)
                try:
                    _q2 = float(ln.get("qty") or 0)
                except (TypeError, ValueError):
                    _q2 = 0.0
                if (so > 0 and not _y and 0 < _q2 <= 2
                        and (sell_of(ln["ref"], sell_by_exact, sell_by_norm, sell_by_tok) or 0) >= 3):
                    # A whole SERVE used inside another product: a Regular pizza is
                    # "0.716 of the Large", a Wings Deal is "1 x the Large", so the
                    # quantity IS the multiplier. Scaling by Lightspeed's price ratio
                    # instead drifts badly — it billed a Wings Deal $8.26 for one
                    # $6.92 pizza (+19%), and every Regular the same way.
                    #
                    # The sub must itself be SOLD (sell price >= $3) for this to mean
                    # a serve. A batch tray is also "qty 1" but that means one
                    # PORTION, not the whole tray — reading it as a multiplier costed
                    # a $10 brownie at $46.72.
                    eff = so * _q2
                    full_ours = full_ours and sfo
                elif (so > 0 and 0 < _q2 < 1
                      and (not _y or (ln.get("unit") or "").lower() != _y[1])):
                    # A sub-1 quantity against a yield-less BATCH is a batch
                    # FRACTION, the same notation Produce uses for a pack ("0.05" of
                    # a 20-base carton). Cauliflower Cheese draws "0.077" of its prep
                    # — one thirteenth of the tray, $0.88 — but the price-ratio path
                    # below read Lightspeed's own line cost and returned 7c, a 12x
                    # under-cost on a $6.50 side. The fraction is the honest reading:
                    # it uses OUR batch cost and needs no yield we haven't measured.
                    eff = so * _q2
                    full_ours = full_ours and sfo
                elif sl > 0 and so > 0 and ls > 0:
                    # Lightspeed gives a reference for this line, so scale the
                    # batch by it. Preferred over yield maths because it is
                    # self-correcting: it cannot be thrown by a wrong batch cost
                    # or a garbage quantity (costing Beef Burrito off the
                    # $141/kg "Cooked Beef Brisket" batch made it $35.70).
                    eff = so * (ls / sl)
                    full_ours = full_ours and sfo
                elif so > 0 and _y and _y[0] > 0 and (ln.get("unit") or "").lower() == _y[1]:
                    # No reference at all (an aliased duplicate carried none),
                    # but the batch HAS a yield -> qty / yield x batch cost.
                    eff = so * (_q2 / _y[0])
                    full_ours = full_ours and sfo
                else:
                    eff = ls
                    full_ours = False
                our_tot += eff
                ls_tot += ls
            elif ln["our_cost"] is not None and _trust_direct(ln, ls, prep_ish(name)):
                # our invoice-fed book prices this line directly (unit matched, sane
                # magnitude — it agrees with LS at ratio ~1.0). Trust it fully.
                eff = float(ln["our_cost"]) * float(ln["qty"] or 0)
                our_tot += eff
                ls_tot += ls
            else:
                # this line falls back to the LS per-line cost. Cap a rare bad datum
                # (a $274 "garnish" line, wrong unit) but only in a non-prep serve —
                # this protects both paths below without touching the precise
                # our_cost path above (so a $80 champagne bottle line stays intact).
                if ls > _LS_LINE_CAP and not prep_ish(name):
                    ls = 0.0
                # our book prices this ProductID but in a different unit than the
                # recipe uses (a wine pour vs a per-bottle cost). Rather than reject
                # it, update the reliable LS line by the dimensionless ratio of the
                # product's CURRENT cost to its scrape-time baseline — so a real
                # invoice still flows through, with no unit maths and no blow-up risk
                # (ratio is 1.0 until an invoice moves the price).
                ref = ln["ref"]
                base = seed_base.get(ref)
                cur = our_costs.get(ref)
                # A COUNTABLE drawn as a pack fraction. Produce writes "0.025" for
                # one garlic bread out of a 40-carton and "0.05" for one base out of
                # 20 — but our book has already divided the carton down to a single
                # unit ($1.49 a bread), so multiplying by the fraction divides twice
                # and bills a $5 side 4c. When our price is per-EACH and the recipe
                # asks for less than one, it means exactly one of them.
                try:
                    _qf = float(ln.get("qty") or 0)
                except (TypeError, ValueError):
                    _qf = 0.0
                # NOT DONE ON PURPOSE: reading a "ml" line against our per-g price
                # at density 1.0. It would rescue three sub-cent garnish lines
                # (dehydrated lime, rubbed oregano) that currently contribute $0.
                # I tried it and it was badly wrong: Produce also writes "0.35 ml"
                # for 0.35 KG of Farm Frites, so the rule read 350g of chips as
                # 0.35g and took Rosemary Salted Fries from $1.86 to $0.0019 — a
                # 100% GP on a $12.90 dish. The unit in Produce is not reliable
                # enough to reinterpret, and under-costing is the direction that
                # flatters. Three garnishes at 2c stay visible in the audit instead.
                # ...but "less than one" means two different things, and Produce's
                # own stated line cost tells them apart. Form BOTH readings and keep
                # whichever matches it:
                #
                #   Garlic Bread [Deal]  0.025 of "Garlic Bread [9" x40]"; Produce
                #     says $1.32. Whole = $1.50 (matches), fraction = $0.04 (does
                #     not) -> the 0.025 means ONE bread out of the 40-carton.
                #   American Standard Burger  0.083 of "Lettuce Cos Baby Twin Pack";
                #     Produce says $0.23. Fraction = $0.228 (matches), whole = $2.75
                #     (12x out) -> Fresh Fruit Team's "each" IS the pack, so 0.083 is
                #     a real twelfth of it, and promoting it made lettuce the dearest
                #     thing in the burger — above the wagyu patty.
                #
                # Compare against the RAW scrape figure, never ln["ls_cost"]: that has
                # already been scaled for deal lines ($0.0355 on the garlic bread), so
                # comparing against it picks the wrong reading every time.
                if cur and cur[1] == "ea" and 0 < _qf < 1:
                    _whole = float(cur[0])
                    _raw = _RAW_LINE_COST.get((name, norm(ln.get("name") or "")))
                    if _raw is None and name.endswith(" D"):
                        # A delivery twin is a copy of the dine-in recipe and has
                        # no scrape line of its own, so there is no raw cost to
                        # judge whole-vs-fraction with — and the default is WHOLE,
                        # the expensive reading. Bang Bang Cauli D took "0.01" of
                        # a $9.90 bunch of chives as a whole bunch and cost $12.57
                        # on a $16 dish, while the identical Bang Bang Cauli read
                        # the same line as 10c. Ask the twin.
                        _raw = _RAW_LINE_COST.get((name[:-2], norm(ln.get("name") or "")))
                    if _raw and _raw > 0 and abs(_whole * _qf - _raw) < abs(_whole - _raw):
                        eff = _whole * _qf        # a real fraction of a real pack
                    else:
                        eff = _whole              # "one of them" out of the carton
                    our_tot += eff
                    ls_tot += ls
                    ln["eff_cost"] = round(eff, 6)
                    continue
                if base and cur and float(base[0]) > 0 and base[1] == cur[1]:
                    eff = ls * (float(cur[0]) / float(base[0]))
                    our_tot += eff
                    ls_tot += ls
                else:
                    eff = ls
                    our_tot += ls
                    ls_tot += ls
                    full_ours = False
            ln["eff_cost"] = round(eff, 6)      # the number the builder shows for this line
        res = (round(our_tot, 4), round(ls_tot, 4), full_ours)
        memo[name] = res
        return res

    # is_prep (prep/batch, not a directly-sold menu line) reuses prep_ish above: its
    # POS "sell price" is often a $1-$2 placeholder, so we must not compute a GP off
    # it (that's where the -1085% garbage came from).
    def _restate_pack_quantities():
        """Say what the line actually uses, in the unit our book prices it in.

        Produce writes a PACK COUNT and calls it millilitres: "1 ml" of Yoghurt
        Greek Style [1Kg] means one 1kg tub, "3 ml" of black beans means three tins,
        "4 ml" of pizza flour means four bags. The COST has always been right — it
        comes from the pack — which is why the totals looked sane while the recipe
        read as gibberish and the builder quoted "$7350.00/L".

        So this changes ONLY the stated quantity and unit, never the cost: divide
        what the line costs by our per-gram rate and you get what it really uses.
        Applied only when the two disagree by 5x or more, and only when the result
        lands within 2% of a whole number of packs — a partial pack means the
        quantity was a real measurement after all, so it is left alone.
        """
        n = 0
        for rname, r in out.items():
            for ln in r["ingredients"]:
                cur = our_costs.get(ln.get("ref") or "")
                if not cur:
                    continue
                try:
                    q, eff, rate = (float(ln.get("qty") or 0),
                                    float(ln.get("eff_cost") or 0), float(cur[0]))
                except (TypeError, ValueError):
                    continue
                if q <= 0 or eff <= 0 or rate <= 0 or cur[1] == (ln.get("unit") or "").lower():
                    continue
                implied = eff / rate
                if implied < q * 5:
                    continue
                packs = implied / q                 # e.g. 1000 g per "1"
                if abs(packs - round(packs)) > 0.02 * packs:
                    continue                        # not a clean pack multiple
                ln["qty"] = round(implied, 2)
                ln["unit"] = cur[1]
                n += 1
        return n

    # RENAME BEFORE COSTING, not after. Every transform above works on the name
    # the scrape uses; everything below — the sell price, the GP, the key the P&L
    # looks up — must use the name Back Office sells. Renaming at the end left the
    # recipe carrying the $2.00 price of the "Pepperoni" add-on it had matched on
    # the way past. Verified first: nothing references the old name as a
    # sub-recipe (_flatten_pointer_pizzas has already lifted Regular Pepperoni's
    # lines up), so the rename cannot orphan a reference.
    _renamed = 0
    for _old, _new in RECIPE_RENAMED_TO.items():
        if _old in out and _new not in out:
            _still_used = any(l.get("ref") == _old
                              for r in out.values() for l in r["ingredients"])
            if _still_used:
                print(f"  NOT renaming {_old!r}: still referenced as a sub-recipe")
                continue
            out[_new] = out.pop(_old)
            _renamed += 1
    if _renamed:
        print(f"  renamed {_renamed} recipe(s) to the name Back Office sells them under")

    fully_ours = 0
    for name in out:
        o, l, fo = cost_of(name)
        out[name]["our_cost"] = o
        out[name]["ls_cost"] = l
        out[name]["fully_our_book"] = fo
        nl = len(out[name]["ingredients"]) or 1
        res = sum(1 for x in out[name]["ingredients"] if x["kind"])
        out[name]["resolved_pct"] = round(100 * res / nl)
        # PREP classification for GP purposes:
        #  * a NAME-flagged prep (Blend/Batch/Prep/Mix/[2Kg]...) is always a prep —
        #    its POS price is a per-unit that doesn't match the batch cost (a house
        #    "Vermouth Blend [Bottle]" sells $12 but the batch costs $32).
        #  * an item merely USED as a base keeps its GP if it has a real menu price
        #    ("Large Meatlovers" is sold AND used by the gluten-free version).
        #  * ...but the NAME is only a hint, and Back Office knows better. A
        #    product it files under a menu category at a menu price is something
        #    a customer orders, whatever word is in its name. "Sigurd GSM Red
        #    Blend" is a wine, not a blend we make: it matched on "Blend", was
        #    filed as a batch, and was therefore excluded from the P&L's cost
        #    book — $2,417 of wine revenue costed off Lightspeed instead of our
        #    own $4.62/glass. "Yuzushu [60ml]" and "Kunizakari Umeshu [60ml]"
        #    matched the same way on their SERVE size.
        #    The real batches are unmistakable on the same test: all 64 of them
        #    carry a blank ReportingGroup and a $0 Back Office price, and the two
        #    that do have a group — Mint Yoghurt [Batch] $1, Tandoori Chicken
        #    [2Kg] $2 — are placeholder prices on things used as sub-recipes,
        #    which the used_as_sub clause below keeps as preps regardless.
        sell = sell_of(name, sell_by_exact, sell_by_norm, sell_by_tok)
        menu_priced = bool(sell and sell >= 3)
        name_prep = bool(PREP_RE.search(name)) and not (menu_priced and bo_group(name))
        is_prep = name_prep or (name in used_as_sub and not menu_priced)
        out[name]["is_prep"] = is_prep
        out[name]["sell_incl"] = sell
        if menu_priced and o and not is_prep:
            ex = sell / 1.1                    # ex-GST revenue
            out[name]["gp_pct"] = round(100 * (ex - o) / ex, 1) if ex else None
        else:
            out[name]["gp_pct"] = None
        if fo:
            fully_ours += 1

    _restated = _restate_pack_quantities()
    print(f"  restated {_restated} pack-count quantities into real units "
          f"(costs unchanged)")


    payload = {
        "generated": date.today().isoformat(),
        "source": "Lightspeed Produce (scraped)",
        "recipe_count": len(out),
        "coverage": {
            "ingredient_refs": dict(ing_res),
            "recipes_fully_on_our_book": fully_ours,
        },
        "recipes": out,
    }
    OUT.write_text(json.dumps(payload, indent=1))
    tot = sum(ing_res.values())
    print(f"{len(out)} recipes -> {OUT.relative_to(ROOT)}")
    print(f"  ingredient refs: {dict(ing_res)}  ({100*(tot-ing_res['unmatched'])//tot}% resolved)")
    print(f"  recipes fully costable on our book: {fully_ours}/{len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
