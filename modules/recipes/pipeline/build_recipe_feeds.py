#!/usr/bin/env python3
"""
Publish the small read-only feeds the recipe builder needs in the browser.

    python3 modules/recipes/pipeline/build_recipe_feeds.py

Three files, all derived, none hand-maintained:

  data/labour_rate.json   the team-average $/min for the LIVE "GP after labour"
                          estimate. A mean — no individual's wage is in it. The
                          real per-person costing stays server-side.

  data/recipes_index.json existing recipes that can be used as sub-recipes:
                          product, venue, yield, and current (rolling) cost per
                          yield-unit. This is how the builder offers "add a
                          sauce/batch" without shipping every recipe's guts.

  data/employees.json     Deputy id -> name, so the Team page can link each
                          login to a real employee (whose rate costs their prep).
                          Names only; no pay.

Generated at build time (build_site.py runs this), never committed — same class
as data/ingredients.json.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# --- ONE yield rule, used by BOTH paths below --------------------------------
# A prep is usable as a sub-recipe only if it has a yield, and a yield can come
# from the size bracket in its name ("[2.5kg]", "[1L]", "[24 pcs]") or, when the
# Produce recipe carries none, from the measured estimate in prep_yields.yaml.
#
# This used to be resolved ONLY on the Lightspeed-scraped path. A prep saved in
# the builder took the other path, and that path read nothing but the saved
# yield field — so saving a prep SHADOWED its own scraped twin and threw the
# estimate away. `usable_as_subrecipe` went False, every dish that used it froze
# the line as manual, and the builder printed it as "(imported)" — the exact
# thing freezing was supposed to avoid. Pizza Sauce was the whole blast radius:
# 60-odd pizzas, every one of them showing "Pizza Sauce [Recipe] (imported)"
# beside a live "Pizza Dough [Recipe]", whose only difference was that nobody
# had ever saved the dough. Editing a recipe silently downgraded it.
#
# Cost-neutral when it landed ($37.19/9338 g = $3.98/kg reproduces every frozen
# eff_cost to the cent) — it buys re-costing, not a new number.
_YIELD_BRACKET = re.compile(
    r"\[(\d+(?:\.\d+)?)\s*(kg|g|l|ml|lt|litre|pcs|pc|units|unit|each|ea)\]", re.I)


def _prep_yield_estimates() -> dict:
    f = ROOT / "data" / "prep_yields.yaml"
    if not f.exists():
        return {}
    import yaml as _yaml
    return _yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}


# A line may be wired LIVE (rate x qty, re-costing from invoices forever) only if
# rate x qty still lands on the number the recipe book audited (eff_cost). When it
# doesn't, the qty is lying — a "1.5 ml" that means 1.5 kg, a "1 ml" glass of wine
# — and the line freezes at the audited figure instead. One definition, used by the
# sub-recipe branch and the ingredient branch, so the two can never drift apart.
_AGREE_TOLERANCE = 0.25


def _agrees(rate, qty, eff) -> bool:
    """Does live `rate x qty` reproduce the book's audited `eff` for this line?"""
    if not rate or not qty or qty <= 0:
        return False
    try:
        eff = float(eff)
    except (TypeError, ValueError):
        return False
    if not eff:
        return False
    return abs(float(rate) * float(qty) - eff) <= _AGREE_TOLERANCE * abs(eff)


def resolve_yield(name: str, estimates: dict | None = None):
    """(qty, unit) for a prep, from its name bracket then prep_yields.yaml.

    A MEASURED yield in the name always beats an estimate. Returns (None, None)
    when neither knows — the caller must then treat the prep as unusable rather
    than invent one.
    """
    m = _YIELD_BRACKET.search(name or "")
    if m:
        q, u = Decimal(m.group(1)), m.group(2).lower()
        if u == "kg":
            return q * 1000, "g"
        if u in ("l", "lt", "litre"):
            return q * 1000, "ml"
        if u in ("g", "ml"):
            return q, u
        return q, "each"
    e = (estimates if estimates is not None else _prep_yield_estimates()).get(name)
    if e:
        return Decimal(str(e["yield_qty"])), e["yield_unit"]
    return None, None

# --- which venue does a recipe belong to? ------------------------------------
# It was hard-coded "stowaway" for every Lightspeed recipe, so all 144 of
# Marilyna's pizzas were filed under Stowaway and the only recipes tagged
# marilynas were the six sauces keyed in by hand. All three venues ring through
# ONE till (CLAUDE.md), so the NAME cannot tell you the venue — but the Sales
# Product API already splits them by reporting group, and CLAUDE.md names it the
# authority for product questions. So ask it instead of guessing.
_VENUE_BY_PRODUCT: dict[str, str] | None = None


def venue_of(product_name: str, default: str = "stowaway") -> str:
    global _VENUE_BY_PRODUCT
    if _VENUE_BY_PRODUCT is None:
        _VENUE_BY_PRODUCT = {}
        base = ROOT / "dashboard" / "sales" / "products"
        for venue, fn in (("stowaway", "rollup_stow.json"),
                          ("harry_gatos", "rollup_hg.json"),
                          ("marilynas", "rollup_mari.json")):
            f = base / fn
            if not f.exists():
                continue
            for prod in (json.loads(f.read_text(encoding="utf-8-sig")).get("products") or []):
                nm = (prod.get("name") or "").strip().lower()
                if nm:
                    _VENUE_BY_PRODUCT.setdefault(nm, venue)
    nm = (product_name or "").strip().lower()
    hit = _VENUE_BY_PRODUCT.get(nm)
    if hit:
        return hit
    # A variant with no sales row of its own — a [Dine-in] twin, a Wings Deal, a
    # kids' size. Ask the same dish in another size before falling back, or the
    # venue split gets decided by which variants happen to have sold.
    stem = re.sub(r"\s*\[dine-in\]\s*$", "", nm)
    stem = re.sub(r"\s+wings deal$", "", stem)
    if stem != nm and (hit := _VENUE_BY_PRODUCT.get(stem)):
        return hit
    bare = re.sub(r"^(large|regular|gluten-free|kids|family)\s+", "", stem)
    if bare != stem:
        for pre in ("large ", "regular ", "gluten-free ", "kids ", "family "):
            if (hit := _VENUE_BY_PRODUCT.get(pre + bare)):
                return hit
    return default

sys.path.insert(0, str(ROOT))

from core.domain import CostSeries, load_cost_observations       # noqa: E402
from modules.recipes.cost import cost_on, load_recipes, RECIPES_DIR  # noqa: E402
from modules.recipes.labour import (load_prep_sessions,          # noqa: E402
                                    product_labour,
                                    venue_estimate_rate_per_minute)

DATA = ROOT / "data"
PREP_DIR = DATA / "prep_sessions"
VENUES = ["stowaway", "harry_gatos", "marilynas"]


def _dec(x) -> str:
    return format(x, "f")


def labour_rate() -> dict:
    out = {"generated_at": date.today().isoformat(), "note": "team-average estimate for live display only; real cost is per-recorder server-side", "venues": {}}
    for v in VENUES:
        r = venue_estimate_rate_per_minute(v)
        out["venues"][v] = {"rate_per_minute": _dec(r)} if r is not None else None
    # a default, so the builder always has something to estimate with
    default = venue_estimate_rate_per_minute(None)
    out["default_rate_per_minute"] = _dec(default) if default is not None else None
    return out


def recipes_index() -> dict:
    try:
        costs = CostSeries(load_cost_observations())
    except FileNotFoundError:
        costs = CostSeries([])
    today = date.today()
    sessions = load_prep_sessions(PREP_DIR)   # what the prep timer logged
    prep_estimates = _prep_yield_estimates()  # read once; both paths share it
    items = []
    for v in VENUES:
        venue_sessions = [s for s in sessions if s.venue == v]
        recipes = load_recipes(v)
        # latest version per product
        latest: dict[str, object] = {}
        for r in recipes:
            cur = latest.get(r.product)
            if cur is None or (r.effective_from or date.min) >= (cur.effective_from or date.min):
                latest[r.product] = r
        for r in latest.values():
            # A saved recipe's OWN yield wins. Only when it has none do we fall
            # back to the same rule the scraped path uses, so saving a prep can
            # never downgrade it to a frozen "(imported)" line (see resolve_yield).
            yq, yu = r.yield_qty, r.yield_unit
            if not (yq and yu):
                yq, yu = resolve_yield(r.product, prep_estimates)
            entry = {
                "product": r.product,
                "venue": v,
                "yield_qty": _dec(yq) if yq else None,
                "yield_unit": yu,
                "usable_as_subrecipe": bool(yq and yu),
                "yield_is_estimated": bool(yq and not (r.yield_qty and r.yield_unit)),
                "cost": None,
                "cost_per_yield_unit": None,
                "prep_minutes_avg": None,   # mean of the last 4 preps (display)
                "prep_count": 0,            # how many preps logged (confidence)
                "prep_cost": None,          # last-4 prep labour, $, at real rates
                "cost_with_prep": None,     # food + own prep + sub-recipe prep share
                "cost_per_yield_unit_with_prep": None,
            }
            try:
                c = cost_on(r, costs, today, price_mode="rolling", recipes=recipes)
                entry["cost"] = _dec(c.quantize(Decimal("0.0001")))
                if yq:
                    entry["cost_per_yield_unit"] = _dec((c / yq).quantize(Decimal("0.000001")))
            except Exception:
                pass   # a recipe we can't fully cost yet still lists for selection

            # prep labour: the LAST 4 timed preps of this batch flow into its cost
            prod_sessions = [s for s in venue_sessions if s.product == r.product]
            if prod_sessions:
                last4 = sorted(prod_sessions, key=lambda s: s.recorded_on, reverse=True)[:4]
                entry["prep_minutes_avg"] = _dec(
                    (sum((s.minutes for s in last4), Decimal("0")) / len(last4)).quantize(Decimal("0.1")))
                entry["prep_count"] = len(prod_sessions)
                pl = product_labour(r.product, venue_sessions, on=today, last_n=4)
                if pl is not None:
                    entry["prep_cost"] = _dec(pl.quantize(Decimal("0.0001")))
            # true total: food + this recipe's own prep + a share of each
            # sub-recipe's prep (sessions folds the sub prep in; own prep added here)
            try:
                cwp = cost_on(r, costs, today, price_mode="rolling",
                              recipes=recipes, sessions=venue_sessions)
                own = product_labour(r.product, venue_sessions, on=today, last_n=4) or Decimal("0")
                tot = (cwp + own)
                entry["cost_with_prep"] = _dec(tot.quantize(Decimal("0.0001")))
                if yq:
                    entry["cost_per_yield_unit_with_prep"] = _dec((tot / yq).quantize(Decimal("0.000001")))
            except Exception:
                pass
            items.append(entry)

    # ALSO surface the Lightspeed-scraped PREPS as sub-recipes, so the builder's
    # picker shows them (Pico de Gallo, Achiote Chicken, Guacamole, ...). A recipe
    # saved in the builder above wins on name. Yield comes from the bracket size in
    # the name ("[2.5kg]", "[1L]", "[24 pcs]"); its our-book cost is the batch cost.
    have = {e["product"] for e in items}
    ls_path = DATA / "lightspeed_recipes_costed.json"
    if ls_path.exists():
        for name, r in json.loads(ls_path.read_text(encoding="utf-8-sig")).get("recipes", {}).items():
            if not r.get("is_prep") or name in have:
                continue
            yq, yu = resolve_yield(name, prep_estimates)
            cost = Decimal(str(r.get("our_cost") or 0))
            items.append({
                "product": name, "venue": venue_of(name),
                "yield_qty": _dec(yq) if yq else None, "yield_unit": yu,
                "usable_as_subrecipe": bool(yq and yu),
                "cost": _dec(cost.quantize(Decimal("0.0001"))) if cost else None,
                "cost_per_yield_unit": _dec((cost / yq).quantize(Decimal("0.000001"))) if yq else None,
                "prep_minutes_avg": None, "prep_count": 0, "prep_cost": None,
                "cost_with_prep": None, "cost_per_yield_unit_with_prep": None,
                "source": "lightspeed",
            })

    return {"generated_at": today.isoformat(), "recipes": items}


def employees() -> dict:
    p = DATA / "employee_map.json"
    m = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    people = [{"id": str(k), "name": v} for k, v in m.items()]
    people.sort(key=lambda e: e["name"].lower())
    return {"generated_at": date.today().isoformat(), "employees": people}


def recipes_full() -> dict:
    """Every builder-saved recipe with its FULL lines, so the app can load one back
    for editing. Latest version per product (venue-scoped). Only the recipes saved
    through the builder (data/recipes/*.yaml) — those carry ingredient ids that map
    to the picker; the scraped Lightspeed book uses a different id space."""
    import yaml as _yaml
    out = []
    for v in VENUES:
        p = RECIPES_DIR / f"{v}.yaml"
        if not p.exists():
            continue
        docs = _yaml.safe_load(p.read_text(encoding="utf-8-sig")) or []
        latest: dict[str, dict] = {}
        for d in docs:
            prod = d.get("product")
            if not prod:
                continue
            ef = d.get("effective_from") or ""
            cur = latest.get(prod)
            if cur is None or ef >= (cur.get("effective_from") or ""):
                latest[prod] = d
        for d in latest.values():
            lines = []
            for ln in d.get("ingredients", []):
                if ln.get("subrecipe"):
                    lines.append({"subrecipe": ln["subrecipe"], "qty": ln.get("qty"),
                                  "unit": ln.get("unit")})
                elif ln.get("id"):
                    lines.append({"id": ln["id"], "qty": ln.get("qty"), "unit": ln.get("unit")})
            out.append({
                "product": d["product"], "venue": v, "source": "builder",
                "sell_incl_gst": d.get("sell_incl_gst"),
                "yield_qty": d.get("yield_qty"), "yield_unit": d.get("yield_unit"),
                "lines": lines,
            })

    # ALSO make every Lightspeed-scraped recipe editable, WIRED to real ingredients:
    #   * an id line -> {id: lightspeed:<PID>} — a first-class ingredient (see
    #     build_ingredients) that costs LIVE off the invoice-fed book and can be
    #     swapped/searched in the picker;
    #   * a sub-recipe line -> {subrecipe: name} — resolves against the sub list;
    #   * only a genuinely unmatched line (no product id) falls back to a frozen
    #     MANUAL line carrying its scrape cost, so nothing is lost.
    # Builder-saved recipes above win on name.
    have = {e["product"] for e in out}
    # which lightspeed:<PID> ids are actually IN THE PICKER (ingredients.json) — a
    # line wired to one of these costs live. Anything else (uncosted product, a
    # sub-recipe with no yield, an unmatched line) falls back to a named manual line
    # carrying its costed-feed per-use cost, so it still shows a real number, never $0.
    costable: set[str] = set()
    _ing = DATA / "ingredients.json"
    if _ing.exists():
        for _i in json.loads(_ing.read_text(encoding="utf-8-sig")).get("ingredients", []):
            if str(_i.get("id", "")).startswith("lightspeed:"):
                costable.add(_i["id"])
    ls_path = DATA / "lightspeed_recipes_costed.json"
    if ls_path.exists():
        # sub-recipes the picker can actually cost (they carry a yield), and the
        # live per-unit rate of every costable ingredient
        _idxf = DATA / "recipes_index.json"
        usable_subs = set()
        sub_rate: dict[str, float] = {}   # product -> $ per yield-unit, for the agreement check
        if _idxf.exists():
            for e in json.loads(_idxf.read_text(encoding="utf-8-sig")).get("recipes", []):
                if not e.get("usable_as_subrecipe"):
                    continue
                usable_subs.add(e["product"])
                try:
                    sub_rate[e["product"]] = float(e["cost_per_yield_unit"])
                except (TypeError, ValueError, KeyError):
                    pass
        ing_rate, ing_pack = {}, {}
        # The PACK COUNT (50 boxes to a carton) lives in pack_overrides — by the
        # time a cost reaches ingredients.json it is already per-unit, so the
        # count is gone. This is what turns a "0.02" pack fraction back into 1 box.
        try:
            from core.pack_overrides import load_pack_overrides as _lpo
            for _k, _v in (_lpo(DATA / "pack_overrides.yaml") or {}).items():
                ing_pack[_k] = (float(_v[0]), str(_v[1]))
        except Exception:
            pass
        _ingf = DATA / "ingredients.json"
        if _ingf.exists():
            for _i in json.loads(_ingf.read_text(encoding="utf-8-sig")).get("ingredients", []):
                try:
                    if not _i.get("needs_pack_review"):
                        ing_rate[_i["id"]] = float(_i["cost_per_base_unit"])
                except (TypeError, ValueError, KeyError):
                    pass
        LSR = json.loads(ls_path.read_text(encoding="utf-8-sig")).get("recipes", {})

        for name, r in LSR.items():
            if name in have:
                continue
            lines = []
            for ln in r.get("ingredients", []):
                kind, ref = ln.get("kind"), ln.get("ref")
                # A line is wired LIVE (id x qty) ONLY when the recipe book costed it
                # cleanly — our_cost set means the unit matched and the magnitude was
                # sane, so id x qty equals the book AND keeps updating from invoices.
                # EVERY other line (a sub-recipe, a product referenced by a garbage qty
                # like a 1 ml glass of wine, a capped garnish, a pour-only 'recipe')
                # uses the book's EXACT per-line contribution (eff_cost) — the number
                # that already sums to the audited recipe cost — so the builder can
                # never blow up ($3216 nip), never read $0, always matches the book.
                _eff = ln.get("eff_cost")
                try:
                    _q = float(ln.get("qty") or 0)
                except (TypeError, ValueError):
                    _q = 0.0
                # A SUB-RECIPE with a usable yield is wired as a real sub-recipe, so
                # the builder shows "Pizza Sauce [Recipe]" and re-costs it whenever
                # that batch changes. (Freezing these was why the pizzas still read
                # "(imported)" even though the prep was right there.)
                # PACK FRACTIONS. The scrape records a pizza box as "0.02 ml" —
                # 1/50th of a carton of 50 — so freezing it showed the box at
                # $29,160/L. Multiply by the pack count and it is what it really
                # is: 1 box at $0.64. Only accepted when the result lands on a
                # whole unit, which is the proof the fraction meant a pack.
                _pk = ing_pack.get(ref)
                if (kind == "id" and _pk and _pk[0] > 1 and _q > 0
                        and (ln.get("unit") or "").lower() != _pk[1].lower()):
                    _real = _q * _pk[0]
                    if abs(_real - round(_real)) < 0.02 and round(_real) >= 1:
                        lines.append({"id": ref, "qty": round(_real), "unit": _pk[1]})
                        continue
                # ...but ONLY when the live number agrees with the audited book,
                # the same test the `id` branch below already applies. Salsa Rosa
                # records its pizza sauce as "1.5 ml"; it means 1.5 kg. Wired live
                # off the qty as written that line reads $0.006 instead of $5.50 —
                # a 1000x UNDERSTATEMENT that flows into Black Beans, Pulled
                # Mushroom, Burrito Rice Sauce and every burrito on the menu. An
                # error that flatters (CLAUDE.md); so a sub-recipe whose live cost
                # misses the book freezes at the audited eff_cost instead.
                if kind == "subrecipe" and ref in usable_subs and _agrees(
                        sub_rate.get(ref), _q, _eff):
                    lines.append({"subrecipe": ref, "qty": ln.get("qty"), "unit": ln.get("unit")})
                # An ingredient is wired LIVE when the book prices it AND the live
                # number agrees with the audited line cost. our_cost being None only
                # means the converter took a safer route to the same figure — if the
                # live price still lands on it, the line is genuinely live (pepperoni
                # at $0.0163/g vs the book's $0.0171/g).
                elif kind == "id" and ref in costable and (
                        ln.get("our_cost") is not None
                        or _agrees(ing_rate.get(ref), _q, _eff)):
                    lines.append({"id": ref, "qty": ln.get("qty"), "unit": ln.get("unit")})
                else:
                    try:
                        q = float(ln.get("qty") or 0)
                    except (TypeError, ValueError):
                        q = 0
                    eff = ln.get("eff_cost")
                    if eff is not None:
                        per = (float(eff) / q) if q > 0 else float(eff)
                    else:
                        our = ln.get("our_cost")
                        ls = float(ln.get("ls_cost") or 0)
                        per = float(our) if our is not None else (ls / q if q > 0 else ls)
                    lines.append({"manual": True, "name": ln.get("name", ""),
                                  "qty": ln.get("qty"), "unit": ln.get("unit"),
                                  "unit_cost_incl": round(per, 6)})
            # Same rule as recipes_index — including the prep_yields.yaml fallback,
            # which this copy used to lack, so a prep with an estimated yield
            # published yield_qty: null here while reading fine over there.
            yq, yu = resolve_yield(name, _prep_yield_estimates())
            yq = float(yq) if yq is not None else None
            out.append({
                "product": name, "venue": venue_of(name), "source": "lightspeed",
                "sell_incl_gst": r.get("sell_incl"),
                "yield_qty": yq, "yield_unit": yu, "lines": lines,
            })
    return {"generated_at": date.today().isoformat(), "recipes": out}


def main() -> int:
    (DATA / "labour_rate.json").write_text(json.dumps(labour_rate(), indent=2), encoding="utf-8")
    idx = recipes_index()
    (DATA / "recipes_index.json").write_text(json.dumps(idx, indent=2), encoding="utf-8")
    (DATA / "recipes_full.json").write_text(json.dumps(recipes_full(), indent=2), encoding="utf-8")
    (DATA / "employees.json").write_text(json.dumps(employees(), indent=2), encoding="utf-8")
    print(f"labour_rate.json, recipes_index.json ({len(idx['recipes'])} recipes), employees.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
