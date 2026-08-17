#!/usr/bin/env python3
"""
Phase 2a — materialise the costed scrape book into data/recipes/<venue>.yaml.

WHAT THIS IS FOR
----------------
Today a venue's costs come from data/lightspeed_recipes_costed.json: a scrape of
Lightspeed Produce put through 17 correction stages. The corrections live in nine
YAML files and must be re-applied, in order, every time the scrape regenerates.
That layer can never converge (COST_BOOK_ARCHITECTURE_PLAN.md Part I).

This script walks through the one-way door: it writes the corrected result out as
the BOOK — one record per product, per-line quantities, and, crucially, a `source`
on every line saying who says so.

    weighed   somebody put it on scales
    invoice   the quantity comes off a supplier document
    derived   computed from another line (scaled variant, cook yield)
    mirrored  deliberately the same spec as another venue's record
    rule      a written rule in data/*.yaml corrected it, with proof
    authored  a human typed it into the recipe builder
    scrape    nobody has checked it; this is what Produce happened to hold

`scrape` is the honest default and the whole point: it makes "~2,300 lines nobody
has ever checked" a number on a dashboard that can only ever shrink.

EQUIVALENCE, NOT CORRECTION
---------------------------
This deliberately reproduces today's costs INCLUDING the wrong ones. Migration
and correction are separate steps (plan, T2). Where a line's cost today comes
from Lightspeed's own figure rather than our book, it is emitted as a `manual`
line carrying that frozen number, tagged `source: scrape` and flagged in the
report -- so it is visibly a borrowed number, not silently one of ours.

STAGING, NOT CUTOVER
--------------------
Default output is data/recipes/_staged/<venue>.yaml. Writing straight to
data/recipes/<venue>.yaml IS the cutover: cogs_blend._load_our_costs prefers the
builder book over the costed book, so the P&L would switch engines the moment the
file lands. The shadow diff has to be boringly zero first (plan, T2). The staged
directory is a subdirectory on purpose -- data/recipes/*.yaml is globbed
non-recursively by the converter and by test_saved_recipe_log_is_unambiguous, so
a staged file cannot be picked up by accident.

    python3 scripts/materialise_recipes.py --venue marilynas
    python3 scripts/materialise_recipes.py --venue marilynas --promote   # cutover
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.domain import CostSeries, load_cost_observations  # noqa: E402
from modules.recipes.cost import cost_on  # noqa: E402
from modules.recipes.units import beverage_batches, house_unit  # noqa: E402
from modules.recipes.pipeline.build_recipe_feeds import (  # noqa: E402
    _prep_yield_estimates, resolve_yield, venue_of,
)

DATA = ROOT / "data"
COSTED = DATA / "lightspeed_recipes_costed.json"
RECIPES = DATA / "recipes"
STAGED = RECIPES / "_staged"

# Sub-recipes the scrape uses as a whole unit rather than by weight -- "BBQ Wings
# x 1 ea" is one serve, so the yield is one serve.
#
# This used to be a dict here. It is empty now because those two moved into
# data/prep_yields.yaml where every other yield lives: a special case hidden in
# a script is invisible to scripts/yield_worklist.py, so the two batches with no
# yield anywhere were reported as unresolved while quietly working. Yields are
# DATA. Kept as a hook for a case that genuinely cannot be written down.
def _serve_yields() -> dict:
    """product -> (qty, unit) for a SOLD item also used as a sub-recipe.

    Deliberately not in prep_yields.yaml: the converter and audit_book both read
    "has a yield" as "is a batch", and a batch is excluded from serve costs. Put
    these three there and three sold products cost $0.00 at 100% GP.
    See data/batch_yield_units.yaml for the full reasoning.
    """
    doc = _load_yaml("batch_yield_units.yaml") or {}
    return {e["product"]: (Decimal(str(e["yield_qty"])), e["yield_unit"])
            for e in (doc.get("serve_yields") or [])}


def _dec(x):
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001
        return None


def _num(x):
    """A YAML-friendly number: int where exact, else a plain float string."""
    d = _dec(x)
    if d is None:
        return None
    if d == d.to_integral_value():
        return int(d)
    return float(d)



# ---------------------------------------------------------------------------
# provenance — read off the correction files, not inferred
# ---------------------------------------------------------------------------
_PACKAGING = __import__("re").compile(r"\bbox|insert|bag|carton|container|lid\b", __import__("re").I)


def _load_yaml(name):
    p = DATA / name
    if not p.exists():
        return []
    return yaml.safe_load(p.read_text(encoding="utf-8-sig")) or []


def line_provenance_index() -> dict:
    """(recipe_name, ingredient_name) -> (source, evidence).

    Read straight off the correction files rather than by instrumenting the
    converter. Every stage in convert_lightspeed_recipes.py documents itself as
    "deliberately narrow: it only touches the exact (recipe, ingredient) pairs
    named in <file>" -- so the file IS the index of what that stage touched.
    Nothing is inferred, the converter stays untouched, and if a stage ever
    widens its scope this index is wrong in the safe direction: a line falls back
    to `scrape`, the label that claims least.

    Ordering matters where two files name the same line. Later writes win, and
    the order below is the provenance rank the plan sets out (weighed > derived >
    mirrored > scrape) applied to the stages that can overlap.
    """
    idx: dict = {}

    # A line Produce omitted entirely, put back from the same pizza in another
    # size. The quantity is that sibling's -- deliberately the same spec.
    for spec in _load_yaml("recipe_missing_lines.yaml"):
        nm = spec.get("name")
        for r in (spec.get("recipes") or []):
            idx[(r, nm)] = ("mirrored",
                            f"recipe_missing_lines.yaml: from sibling size")

    # A unit Produce typed wrong, corrected with arithmetic proof in the file.
    for spec in _load_yaml("recipe_line_unit_fixes.yaml"):
        idx[(spec.get("recipe"), spec.get("ingredient"))] = (
            "rule", f"recipe_line_unit_fixes.yaml: "
                    f"{spec.get('from_unit')} -> {spec.get('to_unit')}")

    # Produce records one product, the venue pours another.
    for spec in _load_yaml("recipe_ingredient_swaps.yaml"):
        idx[(spec.get("recipe"), spec.get("to"))] = (
            "rule", f"recipe_ingredient_swaps.yaml: was {spec.get('from')!r}")

    # A measured cook loss, applied to a plated quantity to get the raw one.
    for spec in _load_yaml("cook_yields.yaml"):
        idx[(spec.get("recipe"), spec.get("ingredient"))] = (
            "derived", f"cook_yields.yaml: measured yield {spec.get('yield')}")

    return idx


def regular_grams_matcher():
    """(recipe_name, line) -> evidence when Zak's weighed grams set the quantity.

    Mirrors convert_lightspeed_recipes._apply_regular_grams exactly: only
    products matching ^Regular\\b, never a packaging line, never a line that is
    itself a whole sold pizza, first spec match wins and `when` narrows before
    the default. Reproducing the selection rule rather than approximating it is
    the difference between `weighed` meaning something and being decoration.
    """
    import re
    spec = [s for s in _load_yaml("pizza_regular_grams.yaml") if s.get("match")]
    for s in spec:
        s["_re"] = re.compile(s["match"], re.I)

    def match(product: str, line: dict, sold_products: set):
        if not re.match(r"^Regular\b", product, re.I):
            return None
        nm = line.get("name") or ""
        if _PACKAGING.search(nm):
            return None
        if line.get("kind") == "subrecipe" and line.get("ref") in sold_products:
            return None
        low = product.lower()
        for s in spec:
            if not s["_re"].search(nm):
                continue
            if s.get("when") and s["when"].lower() not in low:
                continue
            if float(line.get("qty") or 0) == float(s["grams"]):
                return f"pizza_regular_grams.yaml: {s['label']} {s['grams']}g (weighed)"
            return None
        return None

    return match


# ---------------------------------------------------------------------------
# the builder book — hand-authored records that outrank the scrape
# ---------------------------------------------------------------------------
def builder_records() -> dict:
    """product -> (venue_file, raw yaml block), last block wins.

    data/recipes/*.yaml are append-only logs: the save endpoint only ever
    appends, so POSITION IN FILE IS CHRONOLOGICAL and the last block for a
    product is the live one (build_recipe_feeds says the same, at length, and
    names the two cases that got it wrong when ranked on date instead).
    """
    out = {}
    for f in sorted(RECIPES.glob("*.yaml")):
        for blk in (yaml.safe_load(f.read_text(encoding="utf-8-sig")) or []):
            if isinstance(blk, dict) and blk.get("product"):
                out[blk["product"]] = (f.stem, blk)
    return out


def _line_out(ln, source, evidence, frozen=None):
    """One emitted recipe line.

    `frozen` is a per-unit cost carried on the line itself (manual: true). It is
    used ONLY where today's cost does not come from our book at all -- the line
    resolves to no priced ingredient and cogs_blend is currently publishing
    Lightspeed's own figure for it. Emitting it as a manual line reproduces
    today's number exactly while making its origin visible, which is the whole
    bargain of this migration: equivalence first, correction second.
    """
    d = {}
    if ln.get("kind") == "subrecipe":
        d["subrecipe"] = ln["ref"]
    else:
        d["id"] = ln.get("ref") or ""
    d["desc"] = ln.get("name") or ""
    d["qty"] = _num(ln.get("qty"))
    d["unit"] = ln.get("unit") or ""
    if frozen is not None:
        d["manual"] = True
        d["unit_cost_incl"] = float(frozen)
    d["source"] = source
    if evidence:
        d["evidence"] = evidence
    return d





_SUM_BASIS = __import__("re").compile(r"sum of ingredients", __import__("re").I)


def _dominant_unit(batch: dict):
    """The unit of the single largest component of a batch, by magnitude.

    Only meaningful for a yield whose stated basis is "sum of ingredients xN":
    such a number is arrived at by ADDING quantities in different units -- Garlic
    Oil's 1500 is 1000 g + 500 ml, Mint Yoghurt's 1102 is 1000 g + 100 ml + 2
    BUNCHES. The sum has no unit of its own, so the label on it was chosen, not
    measured. The biggest contributor is the least arbitrary answer available.
    """
    best_q, best_u = None, None
    for ln in (batch.get("ingredients") or []):
        u = (ln.get("unit") or "").lower()
        if u not in ("g", "ml"):
            continue
        try:
            q = float(ln.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if best_q is None or q > best_q:
            best_q, best_u = q, u
    return best_u


def derive_yield_unit_fixes(book: dict, report: dict) -> dict:
    """batch name -> the unit it should declare, from the house rule.

    Food is weighed, drinks are poured (modules/recipes/units.py). This replaced
    a two-way-agreement heuristic on 2026-08-16 -- "relabel when the drawing
    lines AND the batch's dominant component both disagree with the label" --
    which was sound but timid: it could not fire when the drawing recipes
    disagreed with each other (Salsa Rosa, drawn in g by two and ml by four), or
    when the batch was a dead-even 1,000 g of sugar against 1,000 ml of cream
    (Salted Caramel), and it got Cauliflower Cheese backwards because 2 L of milk
    outweighed the cheese.

    The kitchen convention answers all three, and answers them without inventing
    a density: those are all FOOD, so they are all grams.

    The magnitude is untouched. It was a sum of mostly-masses to begin with --
    that is exactly why its unit was arbitrary -- so relabelling it as a mass
    asserts nothing new.
    """
    beverages = beverage_batches(book)
    est = _prep_yield_estimates()
    out = {}
    for name, r in book.items():
        if not r.get("is_prep") and name not in {
                l["ref"] for rr in book.values()
                for l in (rr.get("ingredients") or []) if l.get("kind") == "subrecipe"}:
            continue
        q, u = resolve_yield(name)
        if q is None or not u:
            continue
        if u.lower() in ("ea", "each", "unit", "units", "pc", "pcs", "piece"):
            continue                       # a count is a count in either world
        want = house_unit(name, beverages)
        if u.lower() == want:
            continue
        out[name] = want
        report["unit_relabels"].append(
            {"batch": name, "from": u, "to": want,
             "why": ("house rule: a drink is poured, so ml" if want == "ml"
                     else "house rule: food is weighed, so g"),
             "basis": ((est.get(name) or {}).get("basis")
                       or "read off the [bracket] in the name")[:120]})
    return out


def _batch_unit_fixes():
    """(yield relabels, line relabels) from data/batch_yield_units.yaml.

    See that file for the arithmetic. In short: two of the three blocked batches
    have a yield number that is a SUM OF MIXED UNITS, so its "ml" is a label
    nobody measured; the third has a correctly-labelled yield and a mislabelled
    drawing line. Neither correction assumes a density.
    """
    doc = _load_yaml("batch_yield_units.yaml") or {}
    ylds = {e["batch"]: (e["from_unit"], e["to_unit"])
            for e in (doc.get("yield_unit_fixes") or [])}
    lns = {(f["recipe"], f["ingredient"]): (f["from_unit"], f["to_unit"])
           for f in (doc.get("line_unit_fixes") or [])}
    # A line whose QUANTITY is wrong as well as its unit -- "1 ml" meaning one
    # whole 1,116 g batch. Kept separate from the unit-only fixes because it
    # asserts more: a unit relabel leaves the magnitude alone, this replaces it.
    qty_lns = {(f["recipe"], f["ingredient"]):
               (f["from_qty"], f["from_unit"], f["to_qty"], f["to_unit"])
               for f in (doc.get("line_qty_unit_fixes") or [])}
    return ylds, lns, qty_lns


def _yield_for(name: str, report: dict):
    """The batch yield to divide by — prep_yields.yaml FIRST, name bracket second.

    This is the opposite precedence to build_recipe_feeds.resolve_yield, which
    prefers a bracket in the name on the grounds that "a MEASURED yield in the
    name always beats an estimate". For a prep whose bracket really is a measured
    yield that is right. For these it is not: `Cooked Beef Brisket [1Kg]` is a
    PACK LABEL inherited from Lightspeed, and prep_yields.yaml says 6,000 g with
    a worked basis (10,000 g raw x 60% cook loss, independently corroborated by
    Lightspeed's own $25.00/kg seed implying 58.5%).

    Read the bracket as the yield and cooked brisket costs $0.1463/g instead of
    $0.0244/g -- a 6x over-cost on every Meatlovers and Sanchez, which is the same
    shape as the black-bean 6x the plan says a second derivation keeps producing.

    The disagreement is reported, not swallowed: an estimate silently outranking a
    measurement is how the next one of these hides.
    """
    est = _prep_yield_estimates().get(name)
    bq, bu = resolve_yield(name, estimates={})       # bracket only
    if est:
        q, u = Decimal(str(est["yield_qty"])), est["yield_unit"]
        if bq is not None and (Decimal(str(bq)) != q or bu != u):
            report["yield_conflicts"].append(
                {"product": name, "used": f"{q} {u}", "name_bracket": f"{bq} {bu}",
                 "ratio": float(q / Decimal(str(bq))) if bq else None,
                 "basis": (est.get("basis") or "")[:160]})
        return q, u
    if bq is not None:
        return Decimal(str(bq)), bu
    return _serve_yields().get(name, (None, None))


def _fraction_of_batch(ln, book, report, product, yield_fixes):
    """A sub-recipe line stating a FRACTION OF THE BATCH rather than a quantity.

    "Cauliflower Cheese Prep 0.077 ml" is not 77 microlitres of sauce; it is
    0.077 of the batch. The proof is arithmetic and exact:

        0.077 x 4,376 ml yield = 336.95, and the line's own cost implies 336.952
        0.03  x 1,000 ml yield =  30.00, and the line's own cost implies  30.000

    Both land on their implied quantity to five figures, which a coincidence does
    not do. Only fires when it reproduces to within 1% -- Nut Roast and Tandoori
    also carry junk units but do NOT satisfy this, so they are left alone rather
    than swept into a rule they do not fit. Refusal over guessing, per line.
    """
    sub = book.get(ln.get("ref") or "")
    if not sub:
        return None
    yq, yu = resolve_yield(ln["ref"])
    yu = yield_fixes.get(ln["ref"], yu)     # the unit the RECORD will carry
    q, eff = _dec(ln.get("qty")), _dec(ln.get("eff_cost"))
    try:
        bc = _dec(sub.get("our_cost"))
    except Exception:  # noqa: BLE001
        return None
    if not (yq and bc and q and eff) or q <= 0 or bc <= 0:
        return None
    rate = Decimal(str(bc)) / Decimal(str(yq))
    if rate <= 0:
        return None
    implied = eff / rate
    as_fraction = q * Decimal(str(yq))
    if as_fraction <= 0:
        return None
    if abs(implied - as_fraction) / as_fraction > Decimal("0.01"):
        return None
    report["unit_relabels"].append(
        {"product": product, "line": ln.get("ref"),
         "from": f"{q} {ln.get('unit')}", "to": f"{as_fraction} {yu}",
         "why": f"a fraction of the batch: {q} x {yq} {yu} reproduces the "
                f"line's own cost to within 1%"})
    return as_fraction, yu



_COUNTS = {"ea", "each", "unit", "units", "pc", "pcs", "piece"}


def _count_line(ln, book, report, product, yield_fixes):
    """A line asking for "1 ml" of a batch that yields 24 brownies.

    Produce writes a unit on every line whether or not one applies, so a batch
    measured in things -- 24 pieces, 110 puddings -- is drawn with a millilitre
    label on a number that is plainly a count. The line's own cost settles it:
    one twenty-fourth of the brownie batch is what "1" costs, so the 1 is one
    piece. Fires only when the cost agrees to within 1%.
    """
    sub = book.get(ln.get("ref") or "")
    if not sub:
        return None
    yq, yu = resolve_yield(ln["ref"])
    yu = yield_fixes.get(ln["ref"], yu)
    if not yq or (yu or "").lower() not in _COUNTS:
        return None
    if (ln.get("unit") or "").lower() in _COUNTS:
        return None
    q, eff = _dec(ln.get("qty")), _dec(ln.get("eff_cost"))
    bc = _dec(sub.get("our_cost"))
    if not (q and eff and bc) or q <= 0 or bc <= 0:
        return None
    rate = bc / Decimal(str(yq))
    if rate <= 0 or abs(eff / rate - q) / q > Decimal("0.01"):
        return None
    report["unit_relabels"].append(
        {"product": product, "line": ln.get("ref"),
         "from": f"{q} {ln.get('unit')}", "to": f"{q} {yu}",
         "why": f"the batch yields {yq} {yu}; the line's own cost is {q} of them"})
    return q, yu



def _house_unit_line(ln, book, report, product, yield_fixes):
    """A line drawing a batch in the other unit. Follow the batch.

    Under the house rule a food batch is grams, so a recipe asking for "50 ml"
    of a roasted-pepper sauce is asking for 50 g of it. The magnitude is what
    the cook wrote down and is left alone; only the label moves, and it moves to
    the one the batch itself now carries.
    """
    ref = ln.get("ref") or ""
    sub = book.get(ref)
    if not sub:
        return None
    yq, yu = resolve_yield(ref)
    yu = yield_fixes.get(ref, yu)
    lu = (ln.get("unit") or "").lower()
    if not yq or not yu or not lu:
        return None
    if lu == yu.lower():
        return None
    if {lu, yu.lower()} - {"g", "ml"}:
        return None                        # counts and packs are not this rule
    report["unit_relabels"].append(
        {"product": product, "line": ref, "from": lu, "to": yu,
         "why": "house rule: the line follows the batch's unit"})
    return yu



def _engine_line_cost_sub(ln, book, costs, on, venue, yield_fixes, recipes_so_far):
    """What cost.py would charge for one SUB-RECIPE line, or None if it can't say.

    Costs the batch straight from the book rather than from the emitted records,
    because the records are still being built when this is asked.
    """
    sub = book.get(ln.get("ref") or "")
    if not sub:
        return None
    yq, yu = resolve_yield(ln["ref"])
    yu = yield_fixes.get(ln["ref"], yu)
    q, bc = _dec(ln.get("qty")), _dec(sub.get("our_cost"))
    if not (yq and bc and q) or bc <= 0 or Decimal(str(yq)) <= 0:
        return None
    return (bc / Decimal(str(yq))) * q


def _engine_line_cost(ln, costs, on, venue):
    """What modules.recipes.cost would charge for this one line, or None if it
    refuses. A single-line throwaway recipe is the honest way to ask: it goes
    through the same lookup, the same unit rules and the same refusals as the
    real thing."""
    from modules.recipes.cost import Recipe, RecipeLine
    q = _dec(ln.get("qty"))
    if q is None:
        return None
    probe = Recipe(product="_probe", venue=venue, lines=(
        RecipeLine(ingredient=ln.get("ref") or "", qty=q, unit=ln.get("unit") or "",
                   desc=ln.get("name") or ""),))
    try:
        return cost_on(probe, costs, on, recipes=[])
    except Exception:  # noqa: BLE001  — a refusal is an answer
        return None


def materialise(venue: str) -> tuple[list, dict]:
    costs = CostSeries(load_cost_observations())
    today = date.today()
    book = json.loads(COSTED.read_text(encoding="utf-8-sig"))["recipes"]
    mine = {n: r for n, r in book.items() if venue_of(n) == venue}
    sold = {n for n, r in book.items() if not r.get("is_prep")}

    prov = line_provenance_index()
    declared_yield_fixes, line_fixes, qty_fixes = _batch_unit_fixes()
    weighed = regular_grams_matcher()
    authored = builder_records()

    # Every sub-recipe my products reach for, transitively.
    drawn_as_sub = {ln["ref"] for rr in book.values()
                    for ln in (rr.get("ingredients") or [])
                    if ln.get("kind") == "subrecipe"}
    need, seen = set(), set()
    frontier = set(mine)
    while frontier:
        nxt = set()
        for n in frontier:
            if n in seen:
                continue
            seen.add(n)
            for ln in (book.get(n, {}).get("ingredients") or []):
                if ln.get("kind") == "subrecipe":
                    ref = ln["ref"]
                    if ref not in mine:
                        need.add(ref)
                    nxt.add(ref)
        frontier = nxt - seen

    report = {"venue": venue, "generated": date.today().isoformat(),
              "products": len(mine), "subrecipes_pulled_in": sorted(need),
              "lines": 0, "by_source": Counter(), "manual_lines": [],
              "yield_conflicts": [], "unit_relabels": [],
              "authored_overlap": [], "no_yield": [], "unresolved": []}

    yield_fixes = derive_yield_unit_fixes(book, report)
    # A hand-written declaration in data/batch_yield_units.yaml overrides the
    # derivation, so a case the rule gets wrong can always be settled in writing.
    yield_fixes.update({k: v[1] for k, v in declared_yield_fixes.items()})

    records = []
    for name in sorted(set(mine) | need):
        # A HAND-AUTHORED RECORD OUTRANKS THE SCRAPE, AND ALREADY DOES.
        #
        # Provenance is a rank, not an order (plan, section 5): authored beats
        # scrape. It is also what production already does -- cogs_blend prefers
        # the builder book over the costed book -- so carrying the authored block
        # through is what keeps this migration equivalent rather than a change of
        # numbers wearing a migration's clothes.
        if name in authored:
            venue_file, blk = authored[name]
            rec = {"product": name, "venue_source": venue,
                   "authored_in": f"data/recipes/{venue_file}.yaml"}
            for k in ("sell_incl_gst", "effective_from", "entered_by",
                      "prep_minutes", "yield_qty", "yield_unit"):
                if blk.get(k) is not None:
                    rec[k] = blk[k]
            # THE HOUSE RULE APPLIES TO A HAND-AUTHORED YIELD TOO.
            #
            # Chimichurri was entered as "650 ml" from Zak's recipe doc. It is a
            # sauce, so under the house rule it is 650 g, and J.J. Aioli draws
            # 60 g of it. Leaving authored records out of the relabel left the
            # batch in ml while every line reaching it had moved to g, and six
            # Jimmy Jury products refused over it.
            #
            # This is the one place the rule overrides a human, and it is worth
            # being plain about: for a mostly-oil sauce, 650 g and 650 ml differ
            # by about 8%. The magnitude is not being converted -- it is being
            # relabelled -- so that 8% is a real open question, and Chimichurri
            # is on the weighing list because of it.
            if (rec.get("yield_unit") and (fx := yield_fixes.get(name))
                    and not blk.get("unit_confirmed")):
                # unit_confirmed means a person used the g/ml selector in the
                # builder for this batch. A default exists to be overridden by
                # somebody standing next to the thing; the house rule does not
                # get to argue with them.
                if rec["yield_unit"] != fx:
                    report["unit_relabels"].append(
                        {"batch": name, "from": rec["yield_unit"], "to": fx,
                         "why": "house rule, applied over a hand-authored unit"})
                    rec["yield_unit"] = fx
            out_lines = []
            for l in (blk.get("ingredients") or []):
                d = dict(l)
                d["source"] = "authored"
                d.setdefault("evidence", f"recipe builder, data/recipes/{venue_file}.yaml")
                out_lines.append(d)
                report["by_source"]["authored"] += 1
                report["lines"] += 1
            rec["ingredients"] = out_lines
            records.append(rec)
            report["authored_overlap"].append({"product": name, "in": venue_file})
            continue

        r = book[name]
        is_sub = name in need
        lines = []
        for ln in (r.get("ingredients") or []):
            key = (name, ln.get("name"))
            src, ev = "scrape", None

            # `recipe: "*"` applies wherever the ingredient appears -- used when
            # the mislabel belongs to the INGREDIENT, not to one recipe.
            lf = line_fixes.get(key) or line_fixes.get(("*", ln.get("name")))
            if (qf := qty_fixes.get(key)) and (ln.get("unit") or "") == qf[1] \
                    and _dec(ln.get("qty")) == _dec(qf[0]):
                ln = dict(ln, qty=qf[2], unit=qf[3])
                src = "rule"
                ev = (f"batch_yield_units.yaml: {qf[0]} {qf[1]} -> {qf[2]} {qf[3]} "
                      f"(the whole batch; see the arithmetic in that file)")
                report["unit_relabels"].append(
                    {"product": name, "line": key[1],
                     "from": f"{qf[0]} {qf[1]}", "to": f"{qf[2]} {qf[3]}"})
            elif lf and (ln.get("unit") or "") == lf[0]:
                ln = dict(ln, unit=lf[1])
                src = "rule"
                ev = (f"batch_yield_units.yaml: {lf[0]} -> {lf[1]}, per the "
                      f"hand-authored record for the same sauce")
                report["unit_relabels"].append(
                    {"product": name, "line": key[1], "from": lf[0], "to": lf[1]})

            if (w := weighed(name, ln, sold)):
                src, ev = "weighed", w
            elif key in prov:
                src, ev = prov[key]
            elif ln.get("scaled_from"):
                src, ev = "derived", f"scaled from {ln['scaled_from']}"

            if ln.get("kind") == "subrecipe":
                if (fb := _fraction_of_batch(ln, book, report, name, yield_fixes)):
                    ln = dict(ln, qty=fb[0], unit=fb[1])
                    src = "rule"
                    ev = ("stated as a fraction of the batch; recovered from the "
                          "line's own cost")
                elif (hu := _house_unit_line(ln, book, report, name, yield_fixes)):
                    ln = dict(ln, unit=hu)
                    src = "rule"
                    ev = ("house rule: the batch is " + hu + ", so the line is too")
                elif (cl := _count_line(ln, book, report, name, yield_fixes)):
                    ln = dict(ln, qty=cl[0], unit=cl[1])
                    src = "rule"
                    ev = ("the batch is measured in things, not millilitres; the "
                          "line's own cost confirms the count")

            # Can the TARGET ENGINE price this line, and does it land on today's
            # number? Asked of cost.py itself rather than reasoned about: an
            # arithmetic check ("rate x qty == eff_cost") passes for Coke 1.25L,
            # whose rate is per 'can' against a recipe line in 'ea' -- cost.py
            # then refuses the whole dish on a unit mismatch it is right to
            # refuse. The engine's own answer is the only one that matters here.
            frozen = None
            if ln.get("kind") != "subrecipe":
                q, eff = _dec(ln.get("qty")), _dec(ln.get("eff_cost"))
                engine = _engine_line_cost(ln, costs, today, venue)
                # A PRICE REFRESH IS NOT A DEFECT.
                #
                # This froze on ANY disagreement, which meant a 10% price move
                # got baked into the book permanently: Spanish Onion is
                # $0.002420 in the old engine (Stowaway's 20 July invoice) and
                # $0.002200 in ours (the 1 August one), and freezing it wrote
                # July's price into 57 recipe lines for good. The whole point of
                # the migration is that the book follows invoices.
                #
                # What a freeze is FOR is a line our book cannot price in the
                # same terms at all -- a pack count wearing a millilitre label,
                # a rate per 'can' against a recipe in 'ea'. Those are wrong by
                # a pack factor, not by a few percent. So: agree within 25%,
                # take our number; disagree by more, preserve today's and flag
                # it. 25% is comfortably above real price movement (the biggest
                # single move in the stale-price audit was 40%, and that WAS a
                # defect) and far below any pack factor.
                agrees = (engine is not None and eff is not None
                          and (abs(engine - eff) <= max(Decimal("0.0005"), abs(eff) / 1000)
                               or (eff > 0 and engine > 0
                                   and Decimal("0.8") <= engine / eff <= Decimal("1.25"))))
                if not agrees and eff is not None and q not in (None, 0):
                    frozen = eff / q
                    report["manual_lines"].append(
                        {"product": name, "line": ln.get("name"), "ref": ln.get("ref"),
                         "qty": float(q), "unit": ln.get("unit"),
                         "frozen_unit_cost": float(frozen), "line_cost": float(eff),
                         "engine": (float(engine) if engine is not None else None),
                         "why": ("our book prices this line differently"
                                 if engine is not None else
                                 "our book cannot price this line at all; today's "
                                 "cost is Lightspeed's own figure")})
                elif not agrees:
                    report["unresolved"].append({"product": name, "line": ln.get("name"),
                                                 "why": "no cost either way"})

            # A SUB-RECIPE LINE WHOSE QUANTITY IS JUNK IN EITHER UNIT.
            #
            # Nut Roast draws "1 ml" of a 7,304 g batch. The house rule relabels
            # that to 1 g -- and one gram of nut roast is not a portion, it is
            # nothing, so the dish quietly lost $2.39. The "1" was never a
            # quantity in millilitres OR grams; it means one portion, and the
            # batch's own cost says that portion is about 304 g.
            #
            # A relabel is only ever a change of label, so it must not move the
            # line's cost. When the engine and the old book disagree by more
            # than 3x after every rule has had its go, the quantity is junk and
            # equivalence is preserved by freezing it -- visibly, as a debt --
            # rather than by publishing a number that is 300x too small in the
            # flattering direction. 3x is deliberately loose: garlic oil
            # disagrees by 2.7x for a good reason (our book prices it and the
            # old engine did not) and must stay unfrozen.
            if ln.get("kind") == "subrecipe" and frozen is None:
                eng = _engine_line_cost_sub(ln, book, costs, today, venue,
                                            yield_fixes, recipes_so_far=records)
                eff = _dec(ln.get("eff_cost"))
                q = _dec(ln.get("qty"))
                if (eng is not None and eff is not None and q not in (None, 0)
                        and eng > 0 and (eng / eff > 3 or eff / eng > 3)):
                    frozen = eff / q
                    report["manual_lines"].append(
                        {"product": name, "line": ln.get("ref"), "ref": ln.get("ref"),
                         "qty": float(q), "unit": ln.get("unit"),
                         "frozen_unit_cost": float(frozen), "line_cost": float(eff),
                         "engine": float(eng),
                         "why": "the quantity is junk in either unit — a relabel "
                                "moved this line's cost by more than 3x, so the "
                                "number is not a quantity at all"})

            lines.append(_line_out(ln, src, ev, frozen))
            report["by_source"][src] += 1
            report["lines"] += 1

        # A PRODUCT WITH NO LINES IS NOT A FREE PRODUCT.
        #
        # Two Stowaway wines are sold as the bottle they were bought as, so the
        # scrape decomposes them into nothing at all -- and the old book still
        # carries their cost, $34.58 and $21.89. Emitting an empty record makes
        # cost_on return $0.00, which publishes a $34 bottle at 100% GP: a
        # silent, flattering, entirely wrong number of exactly the shape this
        # project exists to stop. Carry the cost as a manual line instead.
        if not lines and (oc := _dec(r.get("our_cost"))) and oc > 0:
            lines.append({"id": "", "desc": f"{name} (sold as bought)",
                          "qty": 1, "unit": "ea", "manual": True,
                          "unit_cost_incl": float(oc), "source": "scrape",
                          "evidence": "no recipe: the product IS the purchase, "
                                      "so its cost is carried whole"})
            report["by_source"]["scrape"] += 1
            report["lines"] += 1
            report["manual_lines"].append(
                {"product": name, "line": "(whole product)", "ref": None,
                 "qty": 1, "unit": "ea", "frozen_unit_cost": float(oc),
                 "line_cost": float(oc),
                 "why": "sold as bought; the scrape decomposes it into no lines "
                        "at all and an empty recipe costs $0.00"})

        rec = {"product": name, "venue_source": venue}
        # IS THIS A BATCH, OR A SERVE THAT IS ALSO DRAWN ELSEWHERE?
        #
        # cogs_blend excludes anything with a yield from serve costs, because
        # publishing a batch cost against every unit sold is a 4x over-cost (the
        # Dragon Soda case). But BBQ Wings is SOLD and also drawn "1 ea" by 22
        # wings deals: it needs a yield to be divisible and a serve cost to be
        # sold. Under the yield-alone rule it gets a yield and loses its cost.
        #
        # The right test is already written down in cogs_blend's own comment -- a
        # batch declares a yield and carries NO SELL PRICE; a serve carries a
        # sell price and no yield -- it is just not the test the code applies.
        # Recording is_serve here so that fix is a one-line change in the
        # sales-pipeline area rather than an archaeology exercise.
        # ...and only for THIS venue's own products. A batch pulled in from
        # another venue's book is a divisor here, not something Marilyna's sells.
        if not r.get("is_prep") and name in mine:
            rec["is_serve"] = True
        # A serve yield applies to anything DRAWN as a sub-recipe, even when the
        # product is also sold in its own right and lives in this venue -- Caffe
        # Crema draws 30 ml of an Espresso Martini. Keying it off is_prep alone
        # left those with no yield and took the parent dish down with them.
        if is_sub or r.get("is_prep") or name in drawn_as_sub:
            q, u = _yield_for(name, report)
            if (fix := yield_fixes.get(name)) and u != fix:
                u = fix
            if q is None:
                report["no_yield"].append(name)
            else:
                rec["yield_qty"] = _num(q)
                rec["yield_unit"] = u
        rec["ingredients"] = lines
        records.append(rec)

    # CLOSURE. An authored block can reference a batch the scrape never mentions
    # -- Davy's Old Fashioned draws "Davy's Old Fashioned Batch", and both live
    # in data/recipes/stowaway.yaml. The sub-recipe walk above only followed the
    # SCRAPE, so the batch was missing and the dish refused with "no version in
    # force": a recipe losing its cost because its own author's other record was
    # not carried across.
    have = {r["product"] for r in records}
    for _ in range(6):                       # batches can nest a few deep
        wanted = {l["subrecipe"] for r in records
                  for l in (r.get("ingredients") or []) if l.get("subrecipe")}
        missing = wanted - have
        if not missing:
            break
        for nm in sorted(missing):
            if nm in authored:
                vf, blk = authored[nm]
                rec = {"product": nm, "venue_source": venue,
                       "authored_in": f"data/recipes/{vf}.yaml"}
                for k in ("yield_qty", "yield_unit", "effective_from", "entered_by"):
                    if blk.get(k) is not None:
                        rec[k] = blk[k]
                rec["ingredients"] = [dict(l, source="authored") for l in (blk.get("ingredients") or [])]
                records.append(rec)
                have.add(nm)
                report["by_source"]["authored"] += len(rec["ingredients"])
                report["lines"] += len(rec["ingredients"])
                report["pulled_in_for_closure"] = report.get("pulled_in_for_closure", []) + [nm]
            else:
                report["unresolved"].append(
                    {"product": nm, "why": "referenced as a sub-recipe but exists "
                                           "in neither the scrape nor the builder book"})
                have.add(nm)

    report["by_source"] = dict(report["by_source"])
    return records, report


HEADER = """\
# {venue}.yaml — MATERIALISED from the costed scrape book, {when}.
#
# Generated by scripts/materialise_recipes.py. Phase 2a of
# COST_BOOK_ARCHITECTURE_PLAN.md: the corrections that used to be re-applied to a
# regenerating scrape are applied ONCE and absorbed here, so this file is the
# book rather than a patch over one.
#
# Every line carries `source`:
#   weighed  {weighed:>5} — somebody put it on scales
#   derived  {derived:>5} — computed from another line (a size scaling, a cook yield)
#   mirrored {mirrored:>5} — deliberately the same spec as a sibling record
#   rule     {rule:>5} — a written rule with arithmetic proof corrected it
#   scrape   {scrape:>5} — nobody has checked it. This is the number to shrink.
#
# `source: scrape` is not a defect to hide; it is the backlog, made countable.
#
# A `manual: true` line carries its own unit cost because our book does not price
# it and today's P&L is publishing Lightspeed's figure for it. Materialisation
# reproduces today's number deliberately — migration and correction are separate
# steps — but a manual line is a debt, not a fact.
"""


def dump(records, report, path: Path):
    by = report["by_source"]
    path.parent.mkdir(parents=True, exist_ok=True)
    head = HEADER.format(
        venue=report["venue"], when=report["generated"],
        weighed=by.get("weighed", 0), derived=by.get("derived", 0),
        mirrored=by.get("mirrored", 0), rule=by.get("rule", 0),
        scrape=by.get("scrape", 0))
    body = yaml.safe_dump(records, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=100)
    path.write_text(head + "\n" + body, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", default="marilynas")
    ap.add_argument("--promote", action="store_true",
                    help="write to data/recipes/<venue>.yaml — THIS IS THE CUTOVER. "
                         "The P&L prefers the builder book over the costed book, so "
                         "the engine switches the moment this lands. Only after the "
                         "shadow diff has been zero for a week (plan, T2).")
    a = ap.parse_args()

    records, report = materialise(a.venue)
    out = (RECIPES if a.promote else STAGED) / f"{a.venue}.yaml"
    dump(records, report, out)

    rp = DATA / "_shadow" / f"materialise_{a.venue}.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"{out.relative_to(ROOT)}: {len(records)} records "
          f"({report['products']} products + {len(report['subrecipes_pulled_in'])} sub-recipes), "
          f"{report['lines']} lines")
    for k in ("weighed", "derived", "mirrored", "rule", "invoice", "authored", "scrape"):
        if report["by_source"].get(k):
            print(f"    {k:9s} {report['by_source'][k]:5d}")
    if report["manual_lines"]:
        print(f"  {len(report['manual_lines'])} manual line(s): our book prices none of "
              f"them; today's cost is Lightspeed's. See data/_shadow/materialise_{a.venue}.json")
    if report["no_yield"]:
        print(f"  REFUSED a yield for {len(report['no_yield'])}: {report['no_yield']} "
              f"— these cannot be costed as sub-recipes until a yield is declared")
    if report["authored_overlap"]:
        print(f"  {len(report['authored_overlap'])} product(s) also exist hand-authored: "
              f"{[o['product'] for o in report['authored_overlap']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
