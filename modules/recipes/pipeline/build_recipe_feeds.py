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
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
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
            entry = {
                "product": r.product,
                "venue": v,
                "yield_qty": _dec(r.yield_qty) if r.yield_qty else None,
                "yield_unit": r.yield_unit,
                "usable_as_subrecipe": bool(r.yield_qty and r.yield_unit),
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
                if r.yield_qty:
                    entry["cost_per_yield_unit"] = _dec((c / r.yield_qty).quantize(Decimal("0.000001")))
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
                if r.yield_qty:
                    entry["cost_per_yield_unit_with_prep"] = _dec((tot / r.yield_qty).quantize(Decimal("0.000001")))
            except Exception:
                pass
            items.append(entry)

    # ALSO surface the Lightspeed-scraped PREPS as sub-recipes, so the builder's
    # picker shows them (Pico de Gallo, Achiote Chicken, Guacamole, ...). A recipe
    # saved in the builder above wins on name. Yield comes from the bracket size in
    # the name ("[2.5kg]", "[1L]", "[24 pcs]"); its our-book cost is the batch cost.
    import re as _re
    have = {e["product"] for e in items}
    ls_path = DATA / "lightspeed_recipes_costed.json"
    if ls_path.exists():
        _Y = _re.compile(r"\[(\d+(?:\.\d+)?)\s*(kg|g|l|ml|lt|litre|pcs|pc|units|unit|each|ea)\]", _re.I)
        for name, r in json.loads(ls_path.read_text()).get("recipes", {}).items():
            if not r.get("is_prep") or name in have:
                continue
            m = _Y.search(name)
            yq = yu = None
            if m:
                q = Decimal(m.group(1)); u = m.group(2).lower()
                if u == "kg":
                    yq, yu = q * 1000, "g"
                elif u in ("l", "lt", "litre"):
                    yq, yu = q * 1000, "ml"
                elif u == "g":
                    yq, yu = q, "g"
                elif u == "ml":
                    yq, yu = q, "ml"
                else:
                    yq, yu = q, "each"
            cost = Decimal(str(r.get("our_cost") or 0))
            items.append({
                "product": name, "venue": "stowaway",
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
    m = json.loads(p.read_text()) if p.exists() else {}
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
        docs = _yaml.safe_load(p.read_text()) or []
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
                "product": d["product"], "venue": v,
                "sell_incl_gst": d.get("sell_incl_gst"),
                "yield_qty": d.get("yield_qty"), "yield_unit": d.get("yield_unit"),
                "lines": lines,
            })
    return {"generated_at": date.today().isoformat(), "recipes": out}


def main() -> int:
    (DATA / "labour_rate.json").write_text(json.dumps(labour_rate(), indent=2))
    idx = recipes_index()
    (DATA / "recipes_index.json").write_text(json.dumps(idx, indent=2))
    (DATA / "recipes_full.json").write_text(json.dumps(recipes_full(), indent=2))
    (DATA / "employees.json").write_text(json.dumps(employees(), indent=2))
    print(f"labour_rate.json, recipes_index.json ({len(idx['recipes'])} recipes), employees.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
