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

import sys
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # so the ONE cost-row rule can be imported
from core.domain import prefer_cost_row   # noqa: E402

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
    for spec in (yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []):
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
                # Sometimes the MAGNITUDE is wrong too, not just the label. Peking
                # only mislabelled ("750 L" was already 750 of the right thing), but
                # Salsa Rosa's "1.5 ml" of pizza sauce means 1.5 KG — relabelling
                # alone would leave 1.5 g. qty_factor rescales to the base unit.
                q = spec.get("qty_factor")
                if q:
                    try:
                        ing["qty"] = str(float(ing.get("qty") or 0) * float(q))
                    except (TypeError, ValueError):
                        pass
                n += 1
    return n


_UNIT_IN_NAME = re.compile(r"\s+(kg|kgs|l|lt|litre)\s*$", re.I)
_UNIT_IN_NAME_FACTOR = {"kg": 1000.0, "kgs": 1000.0, "l": 1000.0, "lt": 1000.0, "litre": 1000.0}
_UNIT_IN_NAME_BASE = {"kg": "g", "kgs": "g", "l": "ml", "lt": "ml", "litre": "ml"}


def apply_unit_in_name(rec: dict, rate_of) -> int:
    """The kitchen writes the unit into the NAME when Produce won't offer it.

    Produce's quantity dropdown only offers mL and g. So a line that is really
    15 KILOS of chicken thigh gets entered as qty 15, unit "ml", with the word
    "kg" typed on the end of the ingredient name — "Chicken Thigh Flt S/Off [Kg]
    kg". Twenty lines across the prep book are written this way. Read literally
    they are 15 millilitres of chicken, and that is what our book believed.

    This is NOT a judgement call, and it is not a typo file. Produce PRICED each
    of these lines off the magnitude the kitchen meant, so its own printed cost
    is an independent witness to which reading is right:

        15 x $16.30/kg = $244.50  <- exactly what Produce printed
        15 ml at the same rate    = $0.24

    So the rule proves itself before it fires: rescale ONLY when the printed line
    cost lands closer to the name's unit than to the recorded one. Where there is
    no live rate to check against (a sub-recipe reference, an ingredient we can't
    price yet), it does nothing and the line is left for the explicit, hand-proved
    entries in data/recipe_line_unit_fixes.yaml. Fail toward review.

    Measured 2026-08-15: 18 lines had a rate, 18 of 18 reconciled at the name's
    unit, 0 at the recorded one, most of them to the cent.
    """
    n = 0
    for rname, body in (rec or {}).items():
        for ing in ((body or {}).get("ingredients") or []):
            name = ing.get("name") or ""
            m = _UNIT_IN_NAME.search(name)
            if not m:
                continue
            suffix = m.group(1).lower()
            unit = str(ing.get("unit") or "").lower()
            if unit not in ("ml", "g"):
                continue          # only a base-unit label can be the wrong one
            # NB: no "already in base units" shortcut. For L the base unit IS ml,
            # so a genuine "2 L entered as 2 ml" line looks converted already. The
            # arithmetic below is the only honest test of whether it has been: if
            # the line really is 2000 ml, rate x 2000 already matches the printed
            # cost and scaling it again lands 1000x away, so it is refused there.
            try:
                qty = float(ing.get("qty") or 0)
                printed = float(ing.get("cost") or 0)
            except (TypeError, ValueError):
                continue
            if qty <= 0 or printed <= 0:
                continue
            rate = rate_of(ing)
            if not rate:
                continue          # can't prove it — leave it alone
            factor = _UNIT_IN_NAME_FACTOR[suffix]
            if abs(rate * qty * factor - printed) >= abs(rate * qty - printed):
                continue          # the cost does NOT back the name; refuse
            ing["qty"] = str(qty * factor)
            ing["unit"] = _UNIT_IN_NAME_BASE[suffix]
            n += 1
    return n


def apply_cook_yields(rec: dict) -> int:
    """Scale a PLATED quantity up to the RAW one somebody actually weighed.

    A roast recipe states what lands on the plate. The kitchen buys what goes in
    the oven, and the two differ by the cook loss — so a 220 g plated lamb line
    against a 2.3-from-2.7 kg weighing is really 258.3 g of raw leg. Until the
    weighing exists there is nothing to apply and the line stays as written;
    data/cost_book_flags.yaml carries the open question and sizes it with an
    assumption it never applies to a cost.

    THE QUANTITY MOVES, NOT PRODUCE'S COST. Our book charges qty x our own rate,
    so scaling `qty` from 220 g to 258.3 g is what makes the plate cost the raw
    joint behind it ($4.29 -> $5.04 at $0.0195/g). Produce's own stated line cost
    is left exactly as Produce states it: it is that system's figure for the
    plated portion, `_RAW_LINE_COST` deliberately wants it untouched for the
    whole-vs-fraction decision downstream, and rewriting another system's number
    to make ours agree would be the wrong way round.

    Deliberately narrow, exactly like apply_unit_fixes above: it touches only the
    (recipe, ingredient) pairs named in data/cook_yields.yaml, only where the name
    still matches, and only once per line. A yield outside 0 < y <= 1 is refused:
    cooking does not add weight, and a "yield" above 1 is somebody having entered
    the ratio upside down.
    """
    path = ROOT / "data" / "cook_yields.yaml"
    if not path.exists():
        return 0
    import yaml
    n = 0
    for spec in (yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []):
        body = (rec or {}).get(spec.get("recipe"))
        if not body:
            continue
        try:
            y = float(spec.get("yield"))
        except (TypeError, ValueError):
            continue
        if not 0 < y <= 1:
            continue
        want_ref = str(spec.get("ingredient_ref") or "").split(":")[-1]
        want_name = spec.get("ingredient")
        for ing in (body.get("ingredients") or []):
            if ing.get("_cook_yield_applied"):
                continue
            if want_name and ing.get("name") != want_name:
                continue
            q = ing.get("qty")
            if q in (None, ""):
                continue
            try:
                ing["qty"] = f"{float(q) / y:.4f}"
            except (TypeError, ValueError):
                continue
            ing["_cook_yield_applied"] = True
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
    for spec in (yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []):
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
    # Potato Salad seasons with "Togarashi" against a ProductID that has never
    # carried a cost, so 10 g of it was free. The book holds exactly ONE
    # togarashi — Ichimi Togarashi Chilli Pepper 300Gm at $0.0372/g — and there
    # is no shichimi anywhere in the corpus to confuse it with, so the generic
    # name can only mean that one. Same shape as the Oregano line above.
    "Togarashi": "Ichimi Togarashi Chilli Pepper 300Gm Best",
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
    # Same cooked brisket, reached two ways, priced 1.79x apart. Zak: "it's the
    # same beef brisket that's in beef burrito."
    #
    #   Beef Burrito ->  subrecipe "Cooked Beef Brisket [1Kg]"   $13.93/kg
    #   12 pizzas    ->  ProductID 22491831 "Pizza Beef Brisket" $25.00/kg
    #
    # Nobody puts raw brisket on a pizza, so both lines are the same braised
    # meat out of the same batch, and one product cannot cost two things. The
    # $25.00 was an ls-recipe-seed (Lightspeed's own recipe cost, median of 3);
    # the $13.93 came from reading Produce's Expected yield of 10,500 g as a
    # yield when it is the RAW weight of a 10,000 g batch — see
    # data/prep_yields.yaml. Both are now the prep, costed off one estimated
    # cook yield, so a single weighing fixes all 14 products at once instead of
    # correcting one path and leaving the other.
    "Pizza Beef Brisket [Kg]": "Cooked Beef Brisket [1Kg]",
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
    # Milagro Reposado was listed here as deliberately absent — no costed twin in
    # the group, nothing to point at. Zak, 2026-08-06: "milagro definitely has a
    # price somewhere too." It does, and not as a twin: ILG's own MAR 2026 price
    # book carries 360-126-7 Milagro Reposado Tequila 700ml at $66.66 a bottle.
    # That is a seed, not an alias — data/cogs_list.csv, source bo-seed-ilgpb.
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
    # It IS bridged to the ILG invoice, as of 2026-08-06. It was not, and the
    # reasoning that kept it out was wrong — recorded here because the shape of
    # the mistake is worth keeping.
    #
    # ILG bills "HAVANA CLUB 700ML 3YO." at $58.01 on a "6x700ML" uom. Reading
    # that as one bottle gives $82.87/L, exactly twice the seed, and the case
    # reading gives $9.67 a bottle, which is absurd. From those two the earlier
    # call was: the line must be TWO bottles at $29.01, because $29.09 is what
    # the seed says, and a figure that agrees with the seed to eight cents is not
    # a coincidence. So the invoice was judged wrong and the seed right.
    #
    # It was a coincidence. ILG's own MAR 2026 price book lists 355-055-2 Havana
    # Club 700ml 3yo. at $49.20 a bottle. $29.09 is 41% under the supplier's own
    # book price for a bottle they sell us — not a discount, an impossibility.
    # The seed was never an observation: "Lightspeed recipe cost (median of 4)",
    # i.e. Produce's own derived figure, which is exactly the input this project
    # exists because it cannot trust. The $58.01 line is one bottle, 18% over
    # book, which is what a single bottle costs when you don't buy the case.
    #
    # THE LESSON: two candidate readings were tested against ONE reference, and
    # the reference was the number under suspicion. A third, independent source
    # was sitting in data/invoice_corpus/ilg_pricebook.pdf the whole time. When
    # the tie-breaker is the thing being adjudicated, there is no tie-breaker.
    #
    # Bridging doubles the cost of every Havana pour. That is the point.
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
    """recipe name -> (qty, unit) from the name bracket or data/prep_yields.yaml.

    THE DECLARED YIELD RELABELS APPLY HERE TOO, and did not until 2026-08-19.
    data/batch_yield_units.yaml carries `yield_unit_fixes` — a batch whose yield
    is labelled ml while everything drawing on it is written in g — each with a
    worked proof:

        Garlic Oil [Batch]    1000 g garlic + 500 ml oil, yield "1500 ml";
                              two thirds of that sum is a mass and all 10
                              drawing lines are in g.
        Mint Yoghurt [Batch]  1000 g yoghurt + 100 ml lime + 2 bunches, yield
                              "1102 ml"; over 90% of it is the yoghurt.

    Both were written by an earlier session and both only ever reached the
    STAGED book. In the live one the batch went on yielding ml while every
    recipe drew grams, so the units disagreed, so the builder feed could not
    wire the line and froze it as an "(imported)" number instead. Sixteen lines
    across the Tandoori and garlic families, costing off a snapshot because of
    a label nobody had reconciled.

    That is the fourth declared fix today found running and reaching nothing.
    """
    out = {}
    y = ROOT / "data" / "prep_yields.yaml"
    if y.exists():
        import yaml
        for k, v in (yaml.safe_load(y.read_text(encoding="utf-8-sig")) or {}).items():
            out[k] = (float(v["yield_qty"]), v["yield_unit"])

    from modules.recipes.units import apply_declared_yield_relabels
    return apply_declared_yield_relabels(out)




def _dec_eq(a, b) -> bool:
    """Two recorded quantities are the same number, however each was spelled."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def load_line_unit_fixes():
    """(recipe, ingredient) -> the relabel declared in data/batch_yield_units.yaml.

    THIS FILE WAS ONLY EVER READ BY THE STAGED BOOK. Every declared fix in it
    carries worked arithmetic and a named ruling, and the live converter -- the
    one the P&L reads -- had never heard of any of them. A correction that only
    reaches the book nobody is using is not a correction.

    Concretely: `Tandoori Chicken [2Kg]` draws "400 ml" of a batch that yields
    GRAMS. Read literally that is 400 batches and Lightspeed prices it at $2,940;
    the cap added alongside this stops the catastrophe but bounds the line at one
    whole batch, which still over-costs a 400 g draw threefold. With the declared
    relabel the line is 400 g of a 1,116 g batch -- an ESTIMATED yield, but a
    stated one -- and costs $4.69, which is the number the staged book already
    produced. Zak, 2026-08-19: "just estimate the tandoori batch yield until
    verified."

    Only relabels, never conversions: the magnitude is left exactly as recorded.
    """
    out = {}
    y = ROOT / "data" / "batch_yield_units.yaml"
    if not y.exists():
        return out
    import yaml
    doc = yaml.safe_load(y.read_text(encoding="utf-8-sig")) or {}
    for f in (doc.get("line_qty_unit_fixes") or []):
        out[(f["recipe"], f["ingredient"])] = (f["from_qty"], f["from_unit"],
                                               f["to_qty"], f["to_unit"])
    for f in (doc.get("line_unit_fixes") or []):
        out.setdefault((f["recipe"], f["ingredient"]),
                       (None, f["from_unit"], None, f["to_unit"]))
    return out


_LINE_FIXES: dict = {}
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
    from core.domain import prefer_cost_row          # ONE definition of the rule

    latest = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k = r["ingredient"]
        cand = (r["cost_per_unit"], r["unit"], r["observed_on"])
        if prefer_cost_row(latest.get(k), cand):
            latest[k] = cand
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
        for r in (yaml.safe_load(f.read_text(encoding="utf-8-sig")) or []):
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


def load_our_book_lines(our_costs):
    """Recipes OUR book can fully cost, as scrape-shaped ingredient lines.

    Produce is a mirror, not the source. When Zak re-specs a prep in the builder
    the OLD version stays in Produce forever — nobody goes and edits it there —
    so the scrape keeps handing us a recipe the kitchen stopped making. Pizza
    Sauce was 10 kg of tinned tomato + oregano + tomato paste at $37.19 in the
    scrape while the kitchen had moved to 6 kg of Kagome + salt + parsley at
    $14.31. Every prep built on it (Salsa Rosa, and through it Black Beans,
    Pulled Mushroom, Burrito Rice Sauce and every burrito) was costed off the
    dead one.

    So where we have our own recipe, ours IS the recipe. Not just its cost —
    its ingredient list, so the book shows what the kitchen actually makes.

    GATED ON BEING ABLE TO COST IT. Some of our records carry no unit_cost_incl
    snapshot and lean entirely on live ids; if an id has no price yet, summing
    them yields a confident $0.00 (Davy's Old Fashioned) and replacing a $4.61
    scraped recipe with $0.00 would be a silent, flattering loss. So a recipe is
    only returned when EVERY line prices — otherwise the scrape stands and the
    difference stays visible. Fail toward review.

    Returns {product: [{name, qty, unit, cost}, ...]}, name = the line's `desc`
    so it round-trips through resolve() via OUR_LINE_IDS below.
    """
    import yaml

    out, ids, yields = {}, {}, {}
    for f in sorted((ROOT / "data" / "recipes").glob("*.yaml")):
        for r in (yaml.safe_load(f.read_text(encoding="utf-8-sig")) or []):
            if not (isinstance(r, dict) and r.get("product")):
                continue
            lines, ok = [], True
            for ln in (r.get("ingredients") or []):
                desc = (ln.get("desc") or "").strip()
                iid = (ln.get("id") or "").strip()
                try:
                    q = float(ln.get("qty") or 0)
                except (TypeError, ValueError):
                    ok = False
                    break
                live = our_costs.get(iid)
                unit = (ln.get("unit") or "").lower()
                if live and str(live[1] or "").lower() == unit:
                    per = float(live[0])
                elif ln.get("unit_cost_incl") is not None:
                    per = float(ln["unit_cost_incl"])
                else:
                    ok = False          # nothing prices this line — refuse
                    break
                if not desc or q <= 0:
                    ok = False
                    break
                if iid:
                    ids[desc] = iid
                lines.append({"name": desc, "qty": str(q), "unit": ln.get("unit"),
                              "cost": str(round(q * per, 6))})
            if ok and lines:
                out[r["product"]] = lines
                # THE YIELD TRAVELS WITH THE RECIPE. If we swap in our ingredient
                # list but leave prep_yields.yaml — which describes the SCRAPED
                # recipe — speaking for the batch, the cost of one recipe gets
                # divided by the yield of another. Both directions of that were
                # tried on 2026-08-15 and both invented a sauce that has never
                # existed: $6.17/kg (new yield, old $37.19) and $1.53/kg (new
                # $14.31, old 9338 g). The real answer is $2.37/kg.
                yq, yu = r.get("yield_qty"), (r.get("yield_unit") or "").lower()
                if yq and yu:
                    try:
                        q = float(yq)
                        if yu in _BASE:                 # kg -> g, L -> ml
                            yu, q = _BASE[yu][0], q * _BASE[yu][1]
                        if q > 0:
                            yields[r["product"]] = (q, yu)
                    except (TypeError, ValueError):
                        pass
    return out, ids, yields


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


# What each deal contains, from the Marilyna's menu Zak sent on 2026-08-06:
#   WINGS DEAL     1 large pizza, BBQ wings, garlic bread                  $30
#   FEAST DEAL     2 large pizzas, garlic bread, choc brownie              $45
#   BANQUET DEAL   3 large pizzas, garlic bread, 1.25L soft drink          $60
#   PIZZA PARTY    4 large pizzas, 2 garlic breads, 2 brownies, 2 x 1.25L  $90
#
# Keyed by the POS SKU. Marilyna's has sold these under two generations of name —
# the per-pizza SKUs ("Large Meatlovers Wings Deal") and the generic headers — and
# both are listed because both still ring.
_DEALS = {
    "WINGS DEAL":          [("Large Pizza [menu average]", 1), ("BBQ Wings", 1), ("Garlic Bread", 1)],
    "$45 FEAST":           [("Large Pizza [menu average]", 2), ("Garlic Bread", 1), ("Choc Brownie", 1)],
    "Feast Deal Pizzas":   [("Large Pizza [menu average]", 2), ("Garlic Bread", 1), ("Choc Brownie", 1)],
    "$60 BANQUET":         [("Large Pizza [menu average]", 3), ("Garlic Bread", 1), ("1.25L Coke", 1)],
    "Banquet Deal Pizzas": [("Large Pizza [menu average]", 3), ("Garlic Bread", 1), ("1.25L Coke", 1)],
    "$90 PIZZA PARTY":     [("Large Pizza [menu average]", 4), ("Garlic Bread", 2), ("Choc Brownie", 2),
                            ("1.25L Coke", 2)],
}


def apply_product_aliases(out: dict) -> int:
    """Publish a costed recipe under the name the TILL sells it as.

    The till and Produce are two naming systems nobody keeps in step. "Outback
    Prawn Toast" on the menu is "Devon's Prawn Toast" in the book — same dish,
    already costed — and no normaliser connects those words because there is
    nothing to connect. It is a rename.

    That costs real money, not just a bad audit line: cogs_blend._load_book_costs
    keys on the POS product name, so a renamed dish never reaches the P&L and the
    day falls through to Lightspeed's stale cost while a perfectly good recipe
    sits unused three feet away.

    The alias PUBLISHES the recipe under the POS name rather than renaming it, so
    the Produce name keeps working for anything that still references it, and
    both names cost identically.

    "NEVER OVERWRITES AN EXISTING ENTRY" WAS TOO STRONG
    ---------------------------------------------------
    `if pos_name in out: continue` treats "there is a key" as "there is a
    recipe", and those are different things. Produce carries name-only stubs —
    a product exists, nobody ever built it — and "Beef Burger D" was one of
    them. Zak confirmed on 2026-08-06 that it IS the American Standard Burger,
    the confirmation went into the yaml, and the alias then declined to apply
    because the empty stub was already sitting on the key. A $24.00 burger kept
    costing nothing while its nine-line, $5.84 build sat in the book, and the
    entry that blocked it contained no information at all.

    So the test is not "is the key taken" but "is there a BUILD under it":

      * an entry with at least one RESOLVED ingredient line is a genuine Produce
        recipe and is never overwritten, confirmation or no confirmation. A
        confirmed alias is one person's statement that two names mean the same
        dish; a built recipe is the kitchen's statement of what goes in it, and
        the second one wins. That is also the safe direction — replacing a real
        build with a copy of another dish is unrecoverable from here.
      * an entry with no resolved line prices nothing, so there is nothing to
        lose and a confirmed pairing is strictly better than a stub. It is
        replaced, and said out loud.

    Deliberately structural, not a cost comparison: it needs no costing pass, so
    it cannot memoise a cost that a later pass would have changed.
    """
    path = ROOT / "data" / "product_recipe_aliases.yaml"
    if not path.exists():
        return 0
    import copy
    import yaml
    n = 0
    for pos_name, book_name in (yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}).items():
        if book_name not in out:
            continue
        existing = out.get(pos_name)
        if existing is not None:
            if any(l.get("kind") for l in (existing.get("ingredients") or [])):
                print(f"  alias NOT applied: {pos_name!r} already has a built "
                      f"Produce recipe ({len(existing['ingredients'])} lines) — a "
                      f"build beats a rename. Confirm which one is right.")
                continue
            print(f"  alias replaces an unbuilt entry: {pos_name!r} "
                  f"-> {book_name!r} (it priced nothing)")
        out[pos_name] = copy.deepcopy(out[book_name])
        out[pos_name]["alias_of"] = book_name
        n += 1
    return n


def add_deal_recipes(out: dict, cost_of) -> int:
    """Cost a deal header from what the deal contains.

    THE DEFECT
    ----------
    Marilyna's deals used to ring as per-pizza SKUs — "Large Meatlovers Wings
    Deal", $99 of revenue against $20.88 of cost, correct. They now ring as
    generic headers: "$45 FEAST", "$60 BANQUET", "$90 PIZZA PARTY", "WINGS
    DEAL". The header carries the whole deal price at zero cost, and NO pizza
    rings anywhere to carry it — across every daily Insights export only $316 of
    component cost rings at zero revenue, and all of it is garlic bread,
    brownies and 1.25 L Cokes. Not one pizza.

    So $9,447 of revenue books at 100% GP. It is the single largest remaining
    under-cost in the book and it is entirely an artefact of a SKU rename.

    (I told Zak earlier that the deals were already costed and that giving the
    headers recipes would double-count. That was true of the Wings Deal, whose
    components genuinely do ring separately, and I generalised it to the rest
    without checking. This is the correction.)

    WHY AN AVERAGE, AND WHY IT IS NOT A GUESS
    -----------------------------------------
    The header does not record WHICH pizza was chosen — that information is not
    in the till, so no amount of care recovers it. What the till does record is
    every large pizza actually sold, with a costed recipe each. The
    sales-weighted mean of those is the best available statement of what a large
    pizza off this menu costs, and it is computed from real sales mix at build
    time rather than typed in, so it tracks the menu instead of going stale.

    It lands at ~$5.25 over ~1,800 pizzas across 32 SKUs. Under-costing is the
    flattering direction, so the mean is deliberately taken over pizzas SOLD
    (which weights toward the cheap high-volume Margherita and Hawaiian) rather
    than over the menu (which would weight toward the dear ones and flatter the
    deal's cost upward). If that is wrong it is wrong toward reporting a WORSE
    GP than reality, which is the safe side.

    A deal is skipped entirely if any component is missing from the book — a
    partial deal cost is worse than a visible zero, because it looks finished.
    """
    avg = _mean_large_pizza_cost(out, cost_of)
    if not avg:
        return 0
    # A real book entry, not a hidden constant: one line carrying the computed
    # mean, so the deal's cost is inspectable in the same place as every other
    # recipe and the number is visible rather than buried in code. ref is empty
    # because there is no ProductID for "the average large pizza" — kind stays
    # "id" so the auditor does not read it as a line that resolves to nothing.
    out.setdefault("Large Pizza [menu average]", {"ingredients": [{
        "name": "large pizza, sales-weighted mean of every large sold",
        "kind": "id", "ref": "", "qty": 1, "unit": "ea",
        "ls_cost": None, "our_cost": f"{avg:.4f}"}], "menu_average": True})
    added = 0
    for sku, parts in _DEALS.items():
        if sku in out:
            continue
        if any(nm != "Large Pizza [menu average]" and nm not in out for nm, _ in parts):
            continue
        # Resolved at BUILD time rather than left as sub-recipe references.
        # A sub-recipe line costed cleanly for BBQ Wings and Garlic Bread and
        # came back $0.00 for Choc Brownie and the menu average — so two of the
        # four components in a $45 deal silently contributed nothing while the
        # recipe still looked complete. Whatever the resolver is doing there, a
        # deal is not the place to find out: cost each component here, where a
        # zero is visible immediately and the deal is skipped rather than shipped
        # half-priced.
        lines, ok = [], True
        for nm, q in parts:
            try:
                c = avg if nm == "Large Pizza [menu average]" else float(cost_of(nm)[0] or 0)
            except (TypeError, ValueError, KeyError):
                c = 0.0
            if not c or c <= 0:
                ok = False
                break
            lines.append({"name": nm, "kind": "id", "ref": "", "qty": q, "unit": "ea",
                          "ls_cost": None, "our_cost": f"{c:.4f}"})
        if not ok:
            continue
        out[sku] = {"ingredients": lines, "deal_header": True}
        added += 1
    return added


def _mean_large_pizza_cost(out: dict, cost_of):
    """Sales-weighted mean cost of a large pizza, from the daily Insights exports.

    Weighted by units actually sold, not a flat average across the menu: a deal
    pizza is drawn from the same distribution customers order from, and the flat
    average would sit higher (more dear SKUs than cheap ones) and flatter the
    deal. Falls back to None — and the deals stay uncosted — if there is no sales
    data to weight with, because an unweighted number here would be a guess
    wearing a computed number's clothes.
    """
    import csv as _csv
    import glob as _glob
    sold = {}
    for f in sorted(_glob.glob(str(ROOT / "data" / "insights_*.csv"))):
        try:
            rows = list(_csv.DictReader(open(f, encoding="utf-8-sig")))
        except UnicodeDecodeError:
            rows = list(_csv.DictReader(open(f, encoding="latin-1")))
        except OSError:
            continue
        for r in rows:
            nm = (r.get("Product Name") or "").strip()
            if not nm.lower().startswith("large "):
                continue
            try:
                q = float(str(r.get("Product Quantity") or 0).replace(",", "") or 0)
            except ValueError:
                continue
            if q > 0:
                sold[nm] = sold.get(nm, 0.0) + q
    # cost_of, not rec["our_cost"]: this runs BEFORE the pass that writes
    # our_cost onto every entry, so reading the field would see nothing and the
    # mean would silently come out None with the deals left uncosted — which is
    # exactly what happened on the first attempt.
    tq = tc = 0.0
    for nm, q in sold.items():
        if nm not in out:
            continue
        try:
            c = float(cost_of(nm)[0] or 0)
        except (TypeError, ValueError, KeyError):
            c = 0.0
        if c > 0:
            tq += q
            tc += c * q
    return round(tc / tq, 4) if tq else None


def add_passthrough_products(out: dict) -> int:
    """Cost the things we sell exactly as we bought them.

    THE HOLE
    --------
    The costed book contains what Lightspeed PRODUCE has a recipe for, and nobody
    writes a recipe for a can of Corona. So every packaged drink at every venue —
    beer, cider, seltzer, soft drink in a bottle, a glass of Pepsi — was absent
    from the book entirely, fell through to Lightspeed's stale Average-Cost
    figure, and counted against recipe coverage. 47 products, $80,118 of lifetime
    revenue, at Stowaway as much as Harry Gatos: Heaps Normal $17,195, Corona
    $13,981, Monteith's $5,921.

    They were never going to arrive by the recipe route. A recipe answers "what
    goes into this"; for a can the answer is the can, and Produce has no way to
    say that.

    WHY THIS NEEDS NO JUDGEMENT
    ---------------------------
    The cost is already in the system, on the same product, typed by Zak: Back
    Office's CostPriceIncTax. This does not derive it, infer it, or reconcile it
    — it reads the number off the product being sold. Spot-checked against ILG's
    own price book, which is independent of both: Corona $2.57 vs $2.44 a bottle
    (1.05x), Asahi 3.5% $2.18 vs $2.12 (1.03x), Peroni 0% $1.85 vs $1.59 (1.16x).
    The resulting GPs land at 70-80%, which is what packaged beer is.

    SCOPE, deliberately tight:
      * unit-priced only. A per-ml or per-g product is a bottle you POUR from —
        that is an ingredient, and pricing it as a serve would cost a 30 ml nip
        at a whole bottle. This is the countable you hand over intact.
      * sold and costed. Both a Back Office price and a Back Office cost, both
        above zero. No price means it is not a menu item; no cost means there is
        nothing to read and it stays visibly at $0 for the audit to shout about.
      * never overrides Produce. If the book already has the name, Produce's
        recipe wins — this only fills absences.
      * never a prep or a stock pack. _PREP_NAME (Batch/Prep/[2Kg]) and
        _PACK_BRACKET ([Bottle]/[750ml]/[Can]) names are excluded: a
        batch's unit price is not its batch cost, which is the trap that made
        Dragon Soda book $37.20 against a $9.00 drink.

    Each entry carries one line — itself — so it costs through the ordinary
    machinery and reads honestly in the book: 1 x the thing, at what we paid.
    """
    import csv as _csv

    def _stock_key(nm):
        """A packaged drink's identity, with the venue tag and the container word
        removed. Harry Gatos files the same beer as "Grifter Big Sur IPA Can [HG]"
        that Stowaway files as "Grifter Big Sur IPA Tin", and "Monteith's Apple
        Cider [HG]" against "Monteith's Apple Cider Bottle". Can, tin and bottle
        are how it arrives, not what it is."""
        nm = re.sub(r"\[.*?\]", "", nm or "")
        nm = re.sub(r"\b(can|tin|bottle|btl|stubby|longneck|glass)\b", "", nm, flags=re.I)
        return re.sub(r"[^a-z0-9]", "", nm.lower())

    # Stowaway's costed packaged drinks, by stock identity. Used ONLY as a
    # fallback for a Harry Gatos product Back Office leaves at $0.00.
    #
    # The reason is purchasing, not naming — the same reason Whispering Angel and
    # Veuve are aliased across the two venues: of 449 non-seed supplier rows filed
    # to harry_gatos, every one is food except two lines of White Light Vodka. Harry
    # Gatos has no drinks supplier. The cans it sells were bought on a Stowaway
    # invoice, because there is no other invoice they could have come from.
    #
    # Requires exactly one Stowaway match. Two would mean the identity is ambiguous
    # and the honest answer is to leave the zero visible.
    _stow_cost, _stow_dupe = {}, set()
    _sp = ROOT / "data" / "bo_exports" / "stowaway_products.csv"
    if _sp.exists():
        for row in _csv.reader(_sp.open(encoding="utf-8-sig")):
            if len(row) < 12 or not row[0].isdigit() or row[6] != "unit":
                continue
            try:
                if float(row[10] or 0) <= 0:
                    continue
            except ValueError:
                continue
            k = _stock_key(row[2])
            if k in _stow_cost:
                _stow_dupe.add(k)
            _stow_cost[k] = (row[2].strip(), float(row[10]))
    for k in _stow_dupe:
        _stow_cost.pop(k, None)

    added = borrowed = 0
    for venue, fname in (("stowaway", "stowaway_products.csv"),
                         ("harry_gatos", "harry_gatos_products.csv")):
        path = ROOT / "data" / "bo_exports" / fname
        if not path.exists():
            continue
        for row in _csv.reader(path.open(encoding="utf-8-sig")):
            if len(row) < 12 or not row[0].isdigit():
                continue
            pid, name, unit, sell, cost = row[0], row[2].strip(), row[6], row[8], row[10]
            if unit != "unit" or not name or name in out:
                continue
            twin = ""
            try:
                if float(sell or 0) <= 0:
                    continue
                if float(cost or 0) <= 0:
                    if venue != "harry_gatos":
                        continue
                    got = _stow_cost.get(_stock_key(name))
                    if not got:
                        continue
                    twin, cost = got[0], f"{got[1]:.4f}"
                    borrowed += 1
            except ValueError:
                continue
            if _PREP_NAME.search(name) or _PACK_BRACKET.search(name):
                continue
            out[name] = {"ingredients": [{
                "name": name, "kind": "id", "ref": f"lightspeed:{pid}",
                "qty": "1", "unit": "ea", "ls_cost": None,
                "our_cost": f"{float(cost):.4f}", "passthrough": True,
                **({"stock_twin": twin} if twin else {})}],
                "passthrough": venue}
            added += 1
    if borrowed:
        print(f"  {borrowed} Harry Gatos product(s) costed from Stowaway's price "
              f"for the same stock")
    return added


def main() -> int:
    # stdout is output too — see build_costs.py. An em-dash in a progress line
    # under an ASCII locale kills a run whose files are already correct.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rec = json.loads(RECIPES.read_text(encoding="utf-8-sig"))
    global _LS_MISREAD_REFS, _RAW_LINE_COST
    _LS_MISREAD_REFS = load_ls_misread_refs()
    # Correct any unit typo BEFORE anything reads the line — see
    # data/recipe_line_unit_fixes.yaml, where each entry carries arithmetic proof.
    _fixed = apply_unit_fixes(rec)
    # ...then turn any PLATED quantity somebody has weighed into the RAW one the
    # kitchen actually buys. Before costing, so the cost follows the quantity.
    _cooked = apply_cook_yields(rec)
    if _cooked:
        print(f"  scaled {_cooked} plated quantit(ies) to raw from a measured "
              f"cook yield (data/cook_yields.yaml)")
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
    global _LINE_FIXES
    _LINE_FIXES = load_line_unit_fixes()
    our_book, our_line_ids, our_yields = load_our_book_lines(our_costs)
    # OUR BOOK REPLACES THE SCRAPE where we have the recipe and can cost it.
    # Produce is a mirror nobody updates: re-spec a prep in the builder and the
    # old version sits there forever, and every prep built on it keeps costing
    # off a recipe the kitchen stopped making. Done BEFORE resolve/costing so
    # the replacement is what gets costed, not a patch applied afterwards.
    # ...AND INSERTS ONE PRODUCE NEVER HAD. This only ever REPLACED, so a recipe
    # we had written for a product Produce does not carry was silently dropped —
    # and then add_passthrough_products invented "1 ea of itself at the Back
    # Office price" in its place. That is where "Pepsi Max Glass = 1 ea of Pepsi
    # Max Glass, $1.31" came from: not a bad recipe, a MANUFACTURED one, standing
    # in for the real recipe we already had.
    #
    # The rule is stated a few hundred lines down, about a different insertion:
    # "a real recipe always beats a Back Office unit price". It just was not true
    # for ours.
    _ourn = _ourins = 0
    # scrape names with any trailing "[...]" removed, so "Mint Yoghurt [Batch]"
    # answers to "Mint Yoghurt" when we ask whether Produce already has it.
    _rec_bare = {re.sub(r"\s*\[[^\]]*\]\s*$", "", _k).strip() for _k in rec}
    for _nm, _lines in our_book.items():
        if _nm in rec and (rec[_nm] or {}).get("ingredients"):
            rec[_nm] = dict(rec[_nm] or {}, ingredients=_lines, _from_our_book=True)
            _ourn += 1
        elif (_nm not in rec and _nm not in _rec_bare
                and (sell_of(_nm, *load_sell_prices()) or 0) > 0):
            # ONLY WHAT THE POS ACTUALLY SELLS, and only what Produce has no
            # record of under any name. That is precisely the population
            # add_passthrough_products would otherwise manufacture a "1 ea of
            # itself" recipe for, which is the defect being fixed — Pepsi Max
            # Glass, Pepsi Glass, Lemonade Glass.
            #
            # Inserting more widely looked tempting and was wrong twice over.
            # "Avocado Verde" is an authored BATCH nobody sells: inserted, it
            # was not used_as_sub, so it did not read as a prep, so the builder
            # guard flagged its three-bunches-of-coriander line as if it were a
            # plate. That it never reaches the book at all is a real finding,
            # and a separate one — it wants its own pass, not a side effect.
            #
            # NOT IF THE SCRAPE ALREADY HAS IT UNDER A BRACKETED NAME. Our book
            # calls it "Mint Yoghurt"; Produce calls the same batch "Mint Yoghurt
            # [Batch]". Inserting ours made a SECOND record that nothing draws
            # on — so it was not used_as_sub, so it did not read as a prep, so
            # the builder guard flagged its whole-bunch-of-mint line as if it
            # were a plate. One insert, three wrong answers.
            rec[_nm] = {"ingredients": _lines, "_from_our_book": True}
            _ourins += 1
    if _ourn:
        print(f"  replaced {_ourn} scraped recipe(s) with our own book's version")
    if _ourins:
        print(f"  inserted {_ourins} recipe(s) of ours that Produce does not carry")
    packs = load_packs()
    seed_base = load_seed_baseline()
    sell_by_exact, sell_by_norm, sell_by_tok = load_sell_prices()
    yields = load_yields()
    # Our own recipe's yield outranks prep_yields.yaml for anything we replaced —
    # the yield has to describe the ingredients it sits next to. See the note in
    # load_our_book_lines; getting this pairing wrong invents sauces.
    for _nm in our_book:
        if _nm in our_yields:
            yields[_nm] = our_yields[_nm]
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
        # A line we injected from OUR OWN recipe book already knows its id — the
        # chef picked the product by hand in the builder. That beats every name
        # heuristic below it, and it is the only way a non-Lightspeed supplier
        # code (b-e:14580, the Kagome sauce) can resolve at all.
        #
        # SCOPED TO THE REPLACED RECIPES, and it has to be. Unscoped, this map
        # answers for every scraped line that merely shares a description — it
        # silently repointed St. Germain across two cocktails onto the id our
        # book happens to name, stranding the price bridge on the other one. Our
        # id is authoritative for OUR lines, not for Produce's.
        if parent in our_book:
            _mine = our_line_ids.get((name or "").strip())
            if _mine:
                return ("id", _mine)
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

    # The kitchen writes "kg" on the end of an ingredient NAME when Produce's
    # dropdown will only offer mL/g. Rescale those lines now — it needs `resolve`
    # and `our_costs`, which only exist here, but it still runs BEFORE anything is
    # costed. Self-proving: it only fires where Produce's own printed line cost
    # backs the name's unit. See apply_unit_in_name.
    def _rate_for(ing):
        try:
            kind, ref = resolve(_UNIT_IN_NAME.sub("", ing.get("name") or "").strip())
        except Exception:
            return None
        if kind != "id" or ref not in our_costs:
            return None            # a sub-recipe has no per-unit rate here
        oc, ou = our_costs[ref]
        # only a BASE-unit rate can be compared against a base-unit quantity
        if str(ou or "").lower() not in ("g", "ml"):
            return None
        try:
            return float(oc) or None
        except (TypeError, ValueError):
            return None

    _named = apply_unit_in_name(rec, _rate_for)
    if _named:
        print(f"  rescaled {_named} line(s) whose unit was typed into the ingredient name")

    # ...then take the suffix OFF the name, now that everything which needed to
    # read it has. It was never part of the ingredient's name — it is a unit the
    # kitchen had nowhere else to put — and leaving it on breaks every lookup
    # keyed by name. "Pizza Sauce [Recipe] kg" did not match our own book's
    # "Pizza Sauce [Recipe]", so Salsa Rosa was costed off Lightspeed's
    # superseded 10 kg sauce ($6.17/kg) instead of the recipe Zak actually makes
    # ($2.37/kg) — and every burrito underneath it inherited that.
    _stripped = 0
    for _b in (rec or {}).values():
        for _i in ((_b or {}).get("ingredients") or []):
            _n = _i.get("name") or ""
            if _UNIT_IN_NAME.search(_n):
                _i["name"] = _UNIT_IN_NAME.sub("", _n).strip()
                _stripped += 1
    if _stripped:
        print(f"  stripped a trailing unit from {_stripped} ingredient name(s)")

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
        spec_path = ROOT / "data" / "pizza_portions.yaml"
        if not spec_path.exists():
            return 0, 0
        doc = yaml.safe_load(spec_path.read_text(encoding="utf-8-sig")) or {}
        spec = [x for x in (doc.get("portions") or []) if x.get("match")]
        for x in spec:
            x["_re"] = re.compile(x["match"], re.I)

        # Which weight column a product takes. Gluten-free is an 11" base --
        # "Pizza Base Gluten Free 11in" -- so it is a REGULAR whatever the crust
        # is made of. Family is carried in the sheet and no product uses it yet.
        def _size_of(name):
            if re.match(r"^Large\b", name, re.I):
                return "large"
            if re.match(r"^(Regular|Gluten-free)\b", name, re.I):
                return "regular"
            if re.match(r"^Family\b", name, re.I):
                return "family"
            return None

        changed = touched = 0
        for name, r in out.items():
            size = _size_of(name)
            if not size:
                continue
            low = name.lower()
            hit = False
            for ln in r["ingredients"]:
                nm = ln.get("name") or ""
                if _PACKAGING.search(nm):
                    continue                      # boxes are counted, not weighed
                # A line can be a whole SOLD pizza rather than an ingredient --
                # "Regular Pepperoni" is built from one "Pepperoni [Dine-in]",
                # where qty 1 means one pizza. Matching /pepperoni/ there and
                # writing "62g" turned a $15 pizza into a 62-gram nothing.
                if (ln.get("kind") == "subrecipe" and ln["ref"] in out
                        and not _PREP_NAME.search(ln["ref"])):
                    continue
                for x in spec:                    # first match wins; `when` rules
                    if not x["_re"].search(nm):   # are listed before the default
                        continue
                    if x.get("when") and x["when"].lower() not in low:
                        continue
                    grams = x.get(size)
                    if grams is None:
                        break
                    # STAMPED WHETHER OR NOT IT MOVES. This only recorded
                    # `weighed` when the sheet CHANGED a quantity, so a line the
                    # sheet governs and already agrees with looked identical to
                    # one the sheet says nothing about. Asking "how much of the
                    # pizza book is weighed" then answered 64% when the real
                    # figure is far higher — the provenance was measuring my
                    # edits rather than the sheet's reach.
                    ln["weighed"] = {"sheet": "Marilynas_Pizza_Portions_v2",
                                     "size": size, "rule": x.get("label")}
                    if float(ln.get("qty") or 0) != float(grams):
                        ln["qty_was"], ln["qty"] = ln.get("qty"), float(grams)
                        ln["unit"] = "g"
                        # RE-RESOLVE. our_cost was worked out against the line's
                        # ORIGINAL unit -- Produce writes some of these in
                        # "bunch" -- so after rewriting to grams it was left null
                        # and the line fell back to Lightspeed's cost for the OLD
                        # quantity. That is how 15g of shallots cost 8c on one
                        # pizza and 37c on another.
                        oc = our_costs.get(ln.get("ref") or "")
                        if oc and oc[1] == "g":
                            ln["our_cost"] = oc[0]
                            ln["ls_cost"] = float(oc[0]) * float(grams)
                        changed += 1
                        hit = True
                    break
            touched += 1 if hit else 0
        return changed, touched

    # THE 0.716 LIFT IS GONE, SUPERSEDED BY A MEASUREMENT.
    #
    # It existed for one morning. Produce's Large figures sat below the weighed
    # Regulars and a 13" pizza cannot carry less topping than an 11" one, so the
    # Large was lifted to regular/0.716 -- a reasonable inference from what was
    # known, and wrong. data/pizza_portions.yaml (Zak, 2026-08-19) weighs BOTH
    # sizes, and it puts Spanish onion at 10 g regular / 20 g large: Produce's
    # 20 g was right all along and the old sheet's 33 g regular was the bad
    # number the lift was propagating onto seven pizzas.
    #
    # An inference is what you reach for when nobody has measured. It stops being
    # the answer the moment somebody does.

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
        for spec in (yaml.safe_load(path.read_text(encoding="utf-8-sig")) or []):
            for rname in spec.get("recipes") or []:
                r = out.get(rname)
                if r is None and spec.get("create"):
                    # Produce has no recipe for this product AT ALL — not an
                    # incomplete one, an absent one. A house soda, a mixer glass,
                    # a drink one venue serves from another's build. Creating the
                    # entry here is the only way it can ever be costed, and it
                    # must happen BEFORE add_passthrough_products so a real recipe
                    # always beats a Back Office unit price.
                    r = out.setdefault(rname, {"ingredients": []})
                if not r:
                    continue
                if any((l.get("ref") == spec["ref"]) or
                       norm(l.get("name") or "") == norm(spec["name"])
                       for l in r["ingredients"]):
                    continue                      # already there — never duplicate
                # A SUB-RECIPE line carries its own quantity (a Wings Deal is one
                # portion of BBQ Wings), unlike an ingredient line whose grams come
                # from Zak's weighed sheet in the pass below.
                #
                # `ls_cost` IS OPTIONAL AND IT IS NOT A COST — it is the Lightspeed
                # REFERENCE for the line, the divisor cost_of() scales the batch by
                # (our_batch x ls_line / ls_batch). Without one, a restored
                # sub-recipe line can only be costed when the batch declares a yield
                # in the line's own unit, and two of the preps in this file do not:
                # "Yorkshire Pudding Prep [110 units]" has no yield anywhere (the
                # bracket says units, which load_yields does not read) and Gravy Prep
                # yields in ml while every roast draws it in g. Both then fell to the
                # final `eff = ls` branch and contributed $0.00 — a restored line
                # that silently prices nothing is worse than no line at all, because
                # the recipe then LOOKS complete.
                #
                # It may only ever be copied from a sibling recipe that already
                # carries the identical line, which is the same standard of evidence
                # the rest of this file is held to. It is never a number anyone made
                # up, and it is never used where the batch can be costed properly.
                if spec.get("subrecipe"):
                    r["ingredients"].append({
                        "name": spec["name"], "kind": "subrecipe", "ref": spec["ref"],
                        "qty": spec.get("qty", 1), "unit": spec.get("unit") or "ea",
                        "ls_cost": spec.get("ls_cost"), "our_cost": None,
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
            # APPLY THE DECLARED RELABEL BEFORE COSTING, not after.
            # See load_line_unit_fixes(): these are rulings with arithmetic
            # behind them, and until now only the staged book obeyed them.
            # Mutated IN PLACE, deliberately. Rebinding to a copy costs the line
            # correctly and then writes the uncorrected one into the artifact,
            # so the dashboard shows "400 ml" beside a price derived from 400 g
            # and eff_cost lands on the copy nobody keeps. The relabel has to be
            # visible in the record it changed.
            _fx = _LINE_FIXES.get((name, ln.get("name")))
            if _fx and (ln.get("unit") or "") == _fx[1] \
                    and (_fx[0] is None or _dec_eq(ln.get("qty"), _fx[0])):
                ln["unit_was"] = ln.get("unit")
                ln["unit"] = _fx[3]
                if _fx[2] is not None:
                    ln["qty_was"], ln["qty"] = ln.get("qty"), _fx[2]
                ln["relabelled_by"] = "data/batch_yield_units.yaml"

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
                _yf = yields.get(ln["ref"])     # RECORDED (prep_yields.yaml) only
                _y = _yf
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
                elif (so > 0 and _yf and _yf[0] > 0
                      and (ln.get("unit") or "").lower() == _yf[1]):
                    # A RECORDED yield beats Lightspeed's line ratio. The ratio path
                    # below assumes ls_line/ls_batch == qty/yield, so "the yield
                    # cancels" — and where that holds the two agree and this changes
                    # nothing. Where it does NOT hold, the ratio quietly gives one
                    # product two prices for the same stock:
                    #
                    #   Cooked Beef Brisket, one batch, one braise
                    #     Beef Burrito  225 g -> $13.95/kg
                    #     12 pizzas      70 g -> $25.89/kg
                    #
                    # Same meat, 1.86x apart, because Lightspeed's own per-line
                    # figures for the two are not on a common rate. qty/yield is,
                    # by construction.
                    #
                    # ONLY a yield from data/prep_yields.yaml (`_yf`), never one
                    # inferred from the name bracket. The bracket is a LABEL — that
                    # is the whole finding behind data/recipe_yields.yaml, and
                    # reading "[1Kg]" as the yield is exactly what once costed Beef
                    # Burrito off a $141/kg batch at $35.70. A recorded yield is a
                    # deliberate statement; a name is not.
                    eff = so * (_q2 / _yf[0])
                    full_ours = full_ours and sfo
                elif sl > 0 and so > 0 and ls > 0:
                    # Lightspeed gives a reference for this line, so scale the
                    # batch by it. Still preferred over a NAME-derived yield because
                    # it is self-correcting: it cannot be thrown by a wrong batch
                    # cost or a garbage quantity (costing Beef Burrito off the
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

                # YOU CANNOT TAKE PART OF A BATCH AND PAY MORE THAN ALL OF IT.
                #
                # The branch above is documented as unable to blow up, because
                # our_batch ~= ls_batch keeps the line near Lightspeed's own
                # per-use cost. That holds right up until LIGHTSPEED'S NUMBER is
                # the broken one, and then the ratio path faithfully reproduces
                # its madness:
                #
                #   Tandoori Chicken [2Kg]
                #     Chicken                1700 g   $20.74
                #     Tandoori Sauce [Batch]  400 ml  $2,940.00   <- LS's own figure
                #
                # The batch is 1 kg of yoghurt and 240 g of paste; it costs $7.35.
                # Lightspeed billed 400 BATCHES because the record draws ml from a
                # batch that yields grams, and every Tandoori pizza inherited it --
                # six products at -257% to -959% GP, $2,924 of quarterly revenue
                # priced as a catastrophe. Our own book refused the line (our_cost
                # is None, correctly), and then we handed the decision back to the
                # number we had just refused.
                #
                # So: a draw FROM a batch is capped at the batch. This needs no
                # unit, no yield and no density assumption -- it is true of any
                # portion of any thing. It caps rather than zeroing because an
                # uncosted line flatters GP, and it is deliberately generous: a
                # 400 ml draw on a ~1.2 kg batch really costs about a third of it,
                # so the cap still over-states. That is the safe direction and it
                # stays visible as a flag until a chef records the real yield.
                #
                # Sold sub-recipes are exempt: a Wings Deal legitimately contains
                # one whole $6.92 pizza, and two of them would legitimately cost
                # twice that. Only batches -- things you portion out of -- are
                # bounded by their own size.
                # HOW FAR OVER THE BATCH BEFORE IT IS IMPOSSIBLE RATHER THAN ODD.
                #
                # At 1x this rule is wrong more often than right. "Lime [ea]"
                # is a one-line pseudo-batch costing $0.50, and Super Lime
                # Juice draws THREE of them: capping that at one batch would
                # under-cost it by 3x, and under-costing is the direction that
                # flatters. Holy Guacamole's single lime at $0.60 against our
                # $0.50 is not a defect at all, just a price that has moved.
                #
                # Both of those are readings a kitchen could actually mean. 20x
                # is not. Nothing puts twenty whole batches of a prep into one
                # recipe and records it as a decimal quantity; at that multiple
                # the only available explanation is that the number is not a
                # portion at all. Tandoori is 224x. The two lime lines sit at
                # 1.2x and 3x and this rule never sees them.
                #
                # Deliberately in the same family as batch_overflow's 3x: a
                # threshold set where the ambiguity ends, not where suspicion
                # starts.
                _BATCH_CAP_X = 20
                _sell_sub = sell_of(ln["ref"], sell_by_exact, sell_by_norm,
                                    sell_by_tok) or 0
                if so > 0 and eff > so * _BATCH_CAP_X and _sell_sub < 3:
                    ln["capped_at_batch"] = {"was": round(eff, 2), "batch": round(so, 2)}
                    eff = so
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
                    if _raw is None:
                        # NO SCRAPE LINE OF ITS OWN -> ASK THE RECIPE IT IS A COPY
                        # OF. Without a raw figure this falls through to WHOLE, the
                        # expensive reading, and the copy costs more than the dish
                        # it copies. Bang Bang Cauli D took "0.01" of a $9.90 bunch
                        # of chives as a whole bunch and cost $12.57 on a $16 dish
                        # while the identical Bang Bang Cauli read it as 10c.
                        #
                        # An ALIAS is asked first, because it is not a heuristic: a
                        # confirmed pairing in product_recipe_aliases.yaml says these
                        # two names are the same dish, so the source recipe's own
                        # scrape line is the right authority. "Beef Burger D" is a
                        # deep copy of the American Standard Burger, whose lettuce
                        # line Produce prices at $0.23 — 0.083 of a $2.75 twin-pack
                        # is $0.228 and a whole pack is $2.75, so the fraction is
                        # the reading that matches. Without this it took the whole
                        # twin-pack and OVER-costed the burger by $2.52 (our_cost
                        # $8.362 against the $5.8403 of the dish it is a copy of),
                        # making lettuce dearer than the wagyu patty.
                        #
                        # The " D" suffix stays as the fallback for the delivery
                        # twins that have no confirmed alias.
                        _src = (out.get(name) or {}).get("alias_of")
                        if _src:
                            _raw = _RAW_LINE_COST.get((_src, norm(ln.get("name") or "")))
                        if _raw is None and name.endswith(" D"):
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
            # A LINE WORTH NOTHING IS NOT A LINE ON OUR BOOK.
            #
            # `fully_our_book` is the flag the P&L and the pricing page trust to
            # mean "every ingredient here is priced off a real invoice". A line
            # that resolved to $0 is the opposite of that: it is uncosted, and
            # the dish still totals without it. Three recipes claimed the flag
            # while carrying one (Frozen Marg's dehydrated lime, Regular Little
            # Italy's rubbed oregano) — small in dollars, but it is exactly the
            # shape that let Choc Brownie contribute $0 to a $45 deal while the
            # recipe read as complete. Cheap to state, and it makes the audit
            # able to see the class at all.
            if not eff:
                full_ours = False
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
                # A pass-through's "1 ea" is not a pack count Produce mistyped —
                # it is the whole statement: one of the thing, sold as bought.
                # Restating it would turn "1 ea of Dom Pérignon" into "643 ml".
                if ln.get("passthrough"):
                    continue
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
                # ...AND THE LINE IS NOW OURS TO PRICE. This is the half that was
                # missing. our_cost is decided up in cost_of(), against the unit the
                # line had BEFORE this function corrected it — so a line Produce
                # typed as "0.05 ml" of a thing we hold per EACH failed the unit
                # match, got no our_cost, and fell through to Lightspeed's figure.
                # It then arrived here, was restated to "1 ea", and nobody went back.
                #
                # 47 gluten-free pizza bases were the largest single Lightspeed
                # dependency in the book for exactly that reason: we hold the base
                # at $4.4375/ea, the recipes want one base, and the two never met.
                #
                # NO MONEY MOVES. The restatement is defined as implied = eff/rate,
                # so rate x implied == eff identically — this attributes the cost we
                # were already charging to the book it actually came from. The
                # aggregate pass below is re-run so fully_our_book keeps up.
                ln["our_cost"] = str(rate)
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

    _al = apply_product_aliases(out)
    if _al:
        print(f"  {_al} POS name(s) pointed at the recipe that is that product")

    _dl = add_deal_recipes(out, cost_of)
    if _dl:
        print(f"  {_dl} deal header(s) costed from their stated contents")

    _pt = add_passthrough_products(out)
    if _pt:
        print(f"  {_pt} sold-as-bought product(s) costed from their own Back Office price")

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
    if _restated:
        # cost_of() ran before the restatement, so the recipe-level totals still
        # describe the pre-restate attribution. Recompute them: the per-line
        # eff_cost is unchanged by construction, so this moves no money — it moves
        # lines out of the Lightspeed column and into ours.
        _re_ours = 0
        for _n in out:
            _o, _l, _fo = cost_of(_n)
            out[_n]["our_cost"] = _o
            out[_n]["ls_cost"] = _l
            if _fo and not out[_n].get("fully_our_book"):
                _re_ours += 1
            out[_n]["fully_our_book"] = _fo
        if _re_ours:
            print(f"  {_re_ours} recipe(s) became fully our book once the restated "
                  f"lines were attributed")


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
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tot = sum(ing_res.values())
    print(f"{len(out)} recipes -> {OUT.relative_to(ROOT)}")
    print(f"  ingredient refs: {dict(ing_res)}  ({100*(tot-ing_res['unmatched'])//tot}% resolved)")
    print(f"  recipes fully costable on our book: {fully_ours}/{len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
