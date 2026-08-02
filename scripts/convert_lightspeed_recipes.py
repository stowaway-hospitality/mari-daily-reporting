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


def _tok(s):
    """word-order-independent key: the alnum tokens, sorted. 'Coke 1.25L' and
    '1.25L Coke' collapse to the same key so a reversed menu name still matches."""
    return " ".join(sorted(re.findall(r"[a-z0-9]+", (s or "").lower())))


# Discontinued products still living under an old name in Produce. The recipe
# keeps the retired name but is poured/priced as the CURRENT product, so its sell
# price must follow the replacement, not the stale POS entry. Keyed recipe-name
# -> current product name (looked up in the priced product sheet). Add a line here
# whenever a product is renamed/replaced but the Produce recipe name lags behind.
RENAMED_TO = {
    "Btl Disco Volante D": "Trutta Streamside Shiraz [Chilled] - Bottle",  # $68 bottle
}


def load_sell_prices():
    """normalised ProductName -> sell price incl GST (what the menu charges).

    Returns (by_norm, by_tok). by_norm is the exact match. by_tok is a
    word-order-tolerant fallback that ONLY carries a token key when every priced
    product sharing that key agrees on ONE price — so 'Coke 1.25L' ($6) rescues
    the recipe named '1.25L Coke', but an ambiguous key like the three-size
    'Trutta Streamside Shiraz' ($18/$27/$68) is dropped rather than guessed."""
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
                by_norm.setdefault(n, p)
                tok_prices.setdefault(_tok(nm), set()).add(round(p, 2))
    by_tok = {t: next(iter(ps)) for t, ps in tok_prices.items() if len(ps) == 1}
    return by_norm, by_tok


def sell_of(name, by_norm, by_tok):
    """current-product override (renamed items) first, then exact normalised name,
    then the unambiguous word-order fallback."""
    lookup = RENAMED_TO.get(name, name)
    return by_norm.get(norm(lookup)) or by_tok.get(_tok(lookup))


def load_our_costs():
    """ingredient id -> latest (cost_per_unit, unit) from our cost book."""
    latest = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k = r["ingredient"]
        d = r["observed_on"]
        if k not in latest or d >= latest[k][2]:
            latest[k] = (r["cost_per_unit"], r["unit"], d)
    return {k: (v[0], v[1]) for k, v in latest.items()}


def load_seed_baseline():
    """ingredient id -> the SEED per-unit (the scrape-time baseline). Used to update
    a line by the ratio latest/baseline without needing the recipe's (often garbage)
    unit to match — a dimensionless price-movement factor, so it can't blow up."""
    base = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        if str(r.get("source_invoice") or "").startswith(("ls-recipe-seed", "bo-seed", "recipe-bridge-seed")):
            base[r["ingredient"]] = (r["cost_per_unit"], r["unit"])
    return base


def main() -> int:
    rec = json.loads(RECIPES.read_text())
    bo_by_name, bo_prefixes = load_bo_ids()
    our_costs = load_our_costs()
    seed_base = load_seed_baseline()
    sell_by_norm, sell_by_tok = load_sell_prices()
    recnames = {norm(k): k for k in rec}

    def resolve(name, parent=None):
        n = norm(name)
        # a recipe used as an ingredient is a SUB-RECIPE and folds in its build cost
        # off our book (via the safe ls-ratio scaling in cost_of). Block only a TRUE
        # self-reference (a recipe listing itself). Many preps — Pizza Dough [Recipe],
        # Sugar Syrup, Gravy Prep — are ALSO products in the export, so we must prefer
        # the recipe here or those lines resolve to an uncosted product ("part LS").
        if n in recnames and recnames[n] != parent:
            return ("subrecipe", recnames[n])
        if n in bo_by_name:
            return ("id", f"lightspeed:{bo_by_name[n]}")
        # truncated scrape name: unique BO product that starts with it
        if len(n) >= 8:
            hits = {pid for pn, pid in bo_prefixes if pn.startswith(n)}
            if len(hits) == 1:
                return ("id", f"lightspeed:{hits.pop()}")
        return (None, None)

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
        lines = []
        for ing in _dedupe_truncated(body.get("ingredients", [])):
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
            lines.append({"name": ing["name"], "kind": kind, "ref": ref,
                          "qty": ing.get("qty"), "unit": ing.get("unit"),
                          "ls_cost": ing.get("cost"), "our_cost": our})
        out[name] = {"ingredients": lines}

    # a recipe used as an ingredient by another recipe is a PREP/BATCH (its POS
    # "sell price" is a placeholder, and it may legitimately carry a big bulk line
    # like $244 of chicken). Bracket sizes ([Batch]/[2Kg]/[1L]) mark bulk preps too.
    _LS_LINE_CAP = 40.0
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
                if sl > 0 and so > 0:
                    eff = so * (ls / sl)
                    full_ours = full_ours and sfo
                else:
                    eff = ls
                    full_ours = False
                our_tot += eff
                ls_tot += ls
            elif ln["our_cost"] is not None:
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
        sell = sell_of(name, sell_by_norm, sell_by_tok)
        menu_priced = bool(sell and sell >= 3)
        name_prep = bool(PREP_RE.search(name))
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
