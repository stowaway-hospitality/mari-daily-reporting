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
_UNIT_YIELD: dict = {}


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
    """batch name -> corrected yield unit, derived rather than hand-listed.

    Fires only when all three of these hold, which is deliberately narrow:

      1. prep_yields.yaml states the basis as a "sum of ingredients" -- i.e. the
         number is a mixed-unit sum and its unit label is arbitrary;
      2. every recipe drawing on the batch uses ONE unit, and it is not the unit
         the yield claims;
      3. the batch's own dominant component is measured in that same unit.

    Two independent readings of the batch -- what the kitchen draws from it, and
    what it is mostly made of -- have to agree before the label moves. Neither is
    a density assumption; a density is never applied, and a batch that fails any
    of the three keeps its declared unit and stays refused, which is correct.
    """
    est = _prep_yield_estimates()
    drawn = {}
    for r in book.values():
        for ln in (r.get("ingredients") or []):
            if ln.get("kind") == "subrecipe":
                drawn.setdefault(ln["ref"], set()).add((ln.get("unit") or "").lower())

    out = {}
    for name, units in drawn.items():
        e = est.get(name)
        if not e or not _SUM_BASIS.search(e.get("basis") or ""):
            continue
        units = {u for u in units if u}
        if len(units) != 1:
            continue
        want = next(iter(units))
        have = (e.get("yield_unit") or "").lower()
        if want == have or want not in ("g", "ml"):
            continue
        if _dominant_unit(book.get(name) or {}) != want:
            continue
        out[name] = want
        report["unit_relabels"].append(
            {"batch": name, "from": have, "to": want,
             "why": "yield basis is a mixed-unit sum; every drawing line and the "
                    "batch's own dominant component are both in " + want,
             "basis": (e.get("basis") or "")[:120]})
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
    return _UNIT_YIELD.get(name, (None, None))

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
                agrees = (engine is not None and eff is not None
                          and abs(engine - eff) <= max(Decimal("0.0005"), abs(eff) / 1000))
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

            lines.append(_line_out(ln, src, ev, frozen))
            report["by_source"][src] += 1
            report["lines"] += 1

        rec = {"product": name, "venue_source": venue}
        if is_sub or r.get("is_prep"):
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
