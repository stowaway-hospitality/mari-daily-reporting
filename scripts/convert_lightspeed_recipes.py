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
EXPORTS = [("stowaway", ROOT / "data" / "bo_exports" / "stowaway_products.csv"),
           ("harry_gatos", ROOT / "data" / "bo_exports" / "harry_gatos_products.csv")]
OUT = ROOT / "data" / "lightspeed_recipes_costed.json"

_SIZE = re.compile(r"\[[^\]]*\]")
_TRAIL_UNIT = re.compile(r"\b(kgs?|k|gm?|mls?|lt?|litres?|ea|each|box|bunch|punnet|tin|bottle|btl)\s*$", re.I)

# plausible ceiling for a single base unit. A per-ml cost above $0.60 (premium
# spirit) or a per-g above $0.20 (saffron) is a mis-unit seed, not a real price.
_UNIT_CEIL = {"ml": 0.60, "g": 0.20}


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


def load_sell_prices():
    """normalised ProductName -> sell price incl GST (what the menu charges)."""
    out = {}
    for _v, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            try:
                p = float(r.get("SellPriceIncTax") or 0)
            except ValueError:
                p = 0
            n = norm(r["ProductName"])
            if n and p > 0:
                out.setdefault(n, p)
    return out


def load_our_costs():
    """ingredient id -> latest (cost_per_unit, unit) from our cost book."""
    latest = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k = r["ingredient"]
        d = r["observed_on"]
        if k not in latest or d >= latest[k][2]:
            latest[k] = (r["cost_per_unit"], r["unit"], d)
    return {k: (v[0], v[1]) for k, v in latest.items()}


def main() -> int:
    rec = json.loads(RECIPES.read_text())
    bo_by_name, bo_prefixes = load_bo_ids()
    our_costs = load_our_costs()
    sell_by_name = load_sell_prices()
    recnames = {norm(k): k for k in rec}

    def resolve(name):
        n = norm(name)
        if n in recnames and recnames[n] != name:
            return ("subrecipe", recnames[n])
        if n in bo_by_name:
            return ("id", f"lightspeed:{bo_by_name[n]}")
        # truncated scrape name: unique BO product that starts with it
        if len(n) >= 8:
            hits = {pid for pn, pid in bo_prefixes if pn.startswith(n)}
            if len(hits) == 1:
                return ("id", f"lightspeed:{hits.pop()}")
        return (None, None)

    out = {}
    ing_res = Counter()
    for name, body in rec.items():
        lines = []
        for ing in body.get("ingredients", []):
            kind, ref = resolve(ing["name"])
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
            lines.append({"name": ing["name"], "kind": kind, "ref": ref,
                          "qty": ing.get("qty"), "unit": ing.get("unit"),
                          "ls_cost": ing.get("cost"), "our_cost": our})
        out[name] = {"ingredients": lines}

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
        our_tot = ls_tot = 0.0
        full_ours = True
        for ln in r["ingredients"]:
            ls = float(ln["ls_cost"] or 0)
            # a scraped per-line cost whose implied per-ml/g exceeds the ceiling is
            # bad Lightspeed data (e.g. a "Dehydrated Lime Garnish" logged at $274.40
            # for 1 ml). Don't let it poison the total via the LS fallback below.
            q_ls = float(ln["qty"] or 0)
            ceil = _UNIT_CEIL.get(ln.get("unit") or "")
            if ceil and q_ls > 0 and ls / q_ls > ceil:
                ls = 0.0
            ls_tot += ls
            if ln["kind"] == "subrecipe":
                so, sl, _ = cost_of(ln["ref"], stack + (name,))
                # sub qty is a multiplier of the batch (usually ~1 for D/serve rows)
                q = float(ln["qty"] or 1)
                our_tot += (so if so else sl) * (q if q < 5 else 1)
                if not so:
                    full_ours = False
            elif ln["our_cost"] is not None:
                q = float(ln["qty"] or 0)
                our_tot += float(ln["our_cost"]) * q
            else:
                our_tot += ls               # fall back to LS cost for this line
                full_ours = False
        res = (round(our_tot, 4), round(ls_tot, 4), full_ours)
        memo[name] = res
        return res

    # a recipe used as an ingredient by another recipe is a PREP/BATCH, not a menu
    # item — its POS "sell price" (often a $1-$2 placeholder) is meaningless, so we
    # must not compute a GP off it (that's where -1085% garbage came from). Bracket
    # sizes ([Batch], [2Kg], [1L], [Bottle]) mark bulk preps too.
    used_as_sub = {ln["ref"] for r in out.values() for ln in r["ingredients"]
                   if ln["kind"] == "subrecipe" and ln["ref"]}
    PREP_RE = re.compile(r"\[(batch|prep|\d+\s*(kg|g|l|ml))\]|\b(prep|mix|marination|batch|blend)\b", re.I)

    fully_ours = 0
    for name in out:
        o, l, fo = cost_of(name)
        out[name]["our_cost"] = o
        out[name]["ls_cost"] = l
        out[name]["fully_our_book"] = fo
        nl = len(out[name]["ingredients"]) or 1
        res = sum(1 for x in out[name]["ingredients"] if x["kind"])
        out[name]["resolved_pct"] = round(100 * res / nl)
        is_prep = name in used_as_sub or bool(PREP_RE.search(name))
        out[name]["is_prep"] = is_prep
        # sell price (menu) + food GP off OUR cost. Preps and items with a token
        # placeholder price (< $3) are inputs, not menu lines — no GP.
        sell = sell_by_name.get(norm(name))
        out[name]["sell_incl"] = sell
        if sell and o and not is_prep and sell >= 3:
            ex = sell / 1.1                    # ex-GST revenue
            out[name]["gp_pct"] = round(100 * (ex - o) / ex, 1) if ex else None
        else:
            out[name]["gp_pct"] = None
        if fo:
            fully_ours += 1

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
