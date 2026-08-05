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


_BASE = {"kg": ("g", 1000.0), "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "litre": ("ml", 1000.0)}


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
    bo_by_name, bo_prefixes = load_bo_ids()
    our_costs = load_our_costs()
    our_preps = load_our_preps(our_costs)
    packs = load_packs()
    seed_base = load_seed_baseline()
    sell_by_norm, sell_by_tok = load_sell_prices()
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
                        if 0 < q <= 2 and (sell_of(ref, sell_by_norm, sell_by_tok) or 0) >= 3:
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
            if not dine_in and box and not any(_PACKAGING.search(l.get("name") or "")
                                               for l in keep):
                keep.append({"name": box[0], "kind": "id", "ref": box[1], "qty": 1,
                             "unit": "ea", "ls_cost": priced(box[1]),
                             "our_cost": priced(box[1])})
                added += 1
            r["ingredients"] = keep
        # Inserts are NOT synthesised — 80 takeaway pizzas carry one and 19 don't,
        # and unlike the box I have no physical rule that says which is right. Left
        # as found, and flagged, rather than guessed at 11c a go.
        return fixed, added, removed

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
                        and (sell_of(ln["ref"], sell_by_norm, sell_by_tok) or 0) >= 3):
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
                if cur and cur[1] == "ea" and 0 < _qf < 1:
                    eff = float(cur[0])
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
