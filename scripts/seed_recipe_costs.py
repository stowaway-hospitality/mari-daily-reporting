#!/usr/bin/env python3
"""
Seed a cost for every STOCK ProductID used in the scraped recipes that our book
doesn't already price — so a food recipe (pizza, roast, batch) costs off OUR book,
not just the beverages.

    python3 scripts/seed_recipe_costs.py            # preview
    python3 scripts/seed_recipe_costs.py --apply    # merge into data/cogs_list.csv

WHERE THE NUMBER COMES FROM
---------------------------
The scrape carries, for every ingredient line, Lightspeed's own cost at a known
qty (e.g. "Big Cheese [2kg]"  142 g  $1.67). So the per-unit cost is cost/qty
($0.01176/g) — Lightspeed's real number, not a pack guess. We take the MEDIAN over
every recipe that uses the item (robust to a rounded line) and seed it under the
bottle/stock ProductID identity. It's a BASELINE: the supplier_code->ProductID
bridge in build_costs makes a real invoice supersede it by date. Beverages already
seeded from the BO export are left alone. Fail toward review: items with no stable
positive (qty,cost) are skipped, not guessed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "data" / "lightspeed_recipes.json"
COSTS = ROOT / "data" / "costs.csv"
COGS = ROOT / "data" / "cogs_list.csv"
EXPORTS = [ROOT / "data" / "bo_exports" / "stowaway_products.csv",
           ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"]
SEED_DATE = "2026-01-02"          # baseline; any real invoice (newer) wins
SEED_SRC = "ls-recipe-seed"

_SIZE = re.compile(r"\[[^\]]*\]")
_TRAIL = re.compile(r"\b(kgs?|k|gm?|mls?|lt?|litres?|ea|each|box|bunch|punnet|tin|bottle|btl)\s*$", re.I)


def norm(s):
    s = _SIZE.sub(" ", (s or "").lower()); s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    p = None
    while s != p:
        p, s = s, _TRAIL.sub("", s).strip()
    return s


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    rec = json.loads(RECIPES.read_text(encoding="utf-8-sig"))

    # BO name -> ProductID (+ display name). Keep prefixes too so we resolve exactly
    # what the converter resolves (it also matches a truncated scrape name to a unique
    # BO product that starts with it) — otherwise those ProductIDs never get seeded
    # and their recipe lines stay "part LS" forever.
    bo, name_of, prefixes = {}, {}, []
    for path in EXPORTS:
        if path.exists():
            for r in csv.DictReader(path.open(encoding="utf-8-sig")):
                n = norm(r["ProductName"])
                if n:
                    bo.setdefault(n, r["ProductID"]); name_of.setdefault(r["ProductID"], r["ProductName"])
                    prefixes.append((n, r["ProductID"]))

    def resolve_pid(nm):
        n = norm(nm)
        if n in bo:
            return bo[n]
        if len(n) >= 8:                       # unique-prefix match, same as converter
            hits = {pid for pn, pid in prefixes if pn.startswith(n)}
            if len(hits) == 1:
                return hits.pop()
        return None

    recnames = {norm(k) for k in rec}
    # "already priced" = a lightspeed:ProductID that a REAL invoice or the beverage
    # seed covers. We must NOT count this seed's OWN prior rows (source ls-recipe-
    # seed), or a re-run can never recompute them — which would freeze in any bad
    # values written before the sanity guard existed. Read from cogs_list so we can
    # see the source and exclude ourselves.
    already = {f"lightspeed:{r['supplier_code']}"
               for r in csv.DictReader(COGS.open(encoding="utf-8-sig"))
               if r.get("supplier_code") and str(r.get("cost_per_base_unit"))
               and r.get("source_invoice") != SEED_SRC
               and r.get("supplier", "").lower() == "lightspeed"}

    # collect per-unit observations per ProductID from every ingredient line
    obs = defaultdict(list)             # pid -> [(per_unit, unit)]
    for body in rec.values():
        for ing in body.get("ingredients", []):
            n = norm(ing["name"])
            if n in recnames:            # a sub-recipe, not a stock item
                continue
            pid = resolve_pid(ing["name"])
            if not pid:
                continue
            try:
                q = float(ing.get("qty") or 0); c = float(ing.get("cost") or 0)
            except (TypeError, ValueError):
                continue
            if q > 0 and c > 0:
                obs[pid].append((c / q, ing.get("unit") or "g"))

    rows_add, preview = [], []
    for pid, lst in obs.items():
        iid = f"lightspeed:{pid}"
        if iid in already:               # beverage seed / invoice already prices it
            continue
        unit = statistics.mode([u for _p, u in lst]) if lst else "g"
        per = statistics.median([p for p, u in lst if u == unit] or [p for p, _ in lst])
        if not (per > 0):
            continue
        # magnitude sanity: a per-ml above $0.60 or per-g above $0.20 means the
        # scraped qty/unit for this item was garbage (e.g. a pizza box logged as
        # "0.02 ml" -> $26.50/ml). Seeding it would blow up any recipe that uses
        # the item in real ml/g. Skip it — the recipe keeps the sane scraped
        # per-line cost, and a real invoice can still price it later.
        if (unit == "ml" and per > 0.60) or (unit == "g" and per > 0.20):
            continue
        # express as a cogs row build_costs will resolve back to this per-unit:
        #   g  -> basis per_kg, pack cost = per_g * 1000
        #   ml -> basis per_L,  pack cost = per_ml * 1000
        if unit == "ml":
            basis, pack_cost = "per_L", per * 1000
        elif unit in ("g",):
            basis, pack_cost = "per_kg", per * 1000
        else:
            basis, pack_cost = "per_unit", per     # each
        rows_add.append({
            "supplier": "Lightspeed", "supplier_code": pid,
            "invoice_description": name_of.get(pid, pid), "lightspeed_product": name_of.get(pid, ""),
            "cost_per_unit_incl_gst": f"{pack_cost:.4f}", "basis": basis,
            "pack_size": "1", "pack_qty": "1", "pack_unit": unit,
            "cost_per_base_unit": f"{per:.6f}", "venue": "stowaway",
            "source_invoice": SEED_SRC, "invoice_date": SEED_DATE, "in_bounds": "yes",
            "note": f"Lightspeed recipe cost (median of {len(lst)}); ProductID {pid}",
        })
        preview.append((name_of.get(pid, pid), unit, per, len(lst)))

    print(f"{len(rows_add)} food/stock ProductIDs to seed (not already on our book)")
    for nm, u, per, n in sorted(preview, key=lambda x: -x[3])[:15]:
        print(f"  {nm[:40]:<42} {per:.5f}/{u}  (median of {n})")

    if a.apply and rows_add:
        FIELDS = ["supplier", "supplier_code", "invoice_description", "lightspeed_product",
                  "cost_per_unit_incl_gst", "basis", "pack_size", "pack_qty", "pack_unit",
                  "cost_per_base_unit", "venue", "source_invoice", "invoice_date", "in_bounds", "note"]
        existing = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
        kept = [r for r in existing if r.get("source_invoice") != SEED_SRC]
        merged = kept + rows_add
        merged.sort(key=lambda r: (r.get("invoice_date", ""), r.get("supplier", ""),
                                   r.get("supplier_code", ""), r.get("invoice_description", "")))
        with COGS.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(merged)
        print(f"applied -> data/cogs_list.csv now {len(merged)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
