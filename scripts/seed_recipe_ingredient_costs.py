#!/usr/bin/env python3
"""
Seed the cost book with the Back Office cost of every product a RECIPE actually
uses as an ingredient.

    python3 scripts/seed_recipe_ingredient_costs.py            # review
    python3 scripts/seed_recipe_ingredient_costs.py --apply    # merge into cogs_list

WHY THIS EXISTS (the "part LS" problem)
---------------------------------------
A recipe line reads "our book" only when the product has a cost keyed to its
ProductID. Hundreds of kitchen products never got one: the beverage seed gates on
InventoryType == 1, and Lightspeed leaves that blank on plenty of real stock — the
gluten-free pizza base (used by 47 recipes) is InventoryType '' with a $88.75 cost
sitting right there in the export.

The selection rule here is evidence, not a heuristic: if a scraped recipe uses the
product as an ingredient, it IS an ingredient, whatever the InventoryType says.

WHY SEEDING ALONE FIXES THE LINE (no pack sizes, no yields needed)
------------------------------------------------------------------
The scraped quantities are pack FRACTIONS against a meaningless unit — the GF base
is "0.05 ml", which is 1 base of a 20-base carton (0.05 x $88.75 = $4.44, exactly
Lightspeed's line). You cannot multiply our per-unit cost by that, so the engine
uses its dimensionless ratio path instead:

    line_cost = ls_line x (current_cost / baseline_cost)

Seeding supplies BOTH sides at once, so the ratio is 1.0 and the cost is unchanged
— but the line is now anchored to a ProductID in our book, so the day a real
invoice for that product lands the ratio moves and the recipe reprices itself.
Because ls_line = qty x pack_cost, the ratio yields exactly qty x new_pack_cost.

Invoices still win: these rows are dated in the past, so the as-of lookup prefers
any real invoice observation over this floor.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_ingredients import resolve_pack  # noqa: E402
from scripts.convert_lightspeed_recipes import norm as _norm         # noqa: E402

EXPORTS = [
    ("stowaway", ROOT / "data" / "bo_exports" / "stowaway_products.csv"),
    ("harry_gatos", ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"),
]
COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"
COGS = ROOT / "data" / "cogs_list.csv"
SEED_OUT = ROOT / "data" / "recipe_ingredient_seed.csv"

SEED_DATE = "2026-01-02"          # older than the invoice history: invoices win
SEED_SOURCE = "bo-ingredient-seed"

FIELDS = ["supplier", "supplier_code", "invoice_description", "lightspeed_product",
          "cost_per_unit_incl_gst", "basis", "pack_size", "pack_qty", "pack_unit",
          "cost_per_base_unit", "venue", "source_invoice", "invoice_date", "in_bounds", "note"]


def _cost(r):
    try:
        c = Decimal(str(r.get("CostPriceIncTax") or "0"))
        return c if c > 0 else None
    except (InvalidOperation, TypeError):
        return None


def ingredient_pids() -> set[str]:
    """ProductIDs that a scraped recipe uses as an ingredient — the evidence."""
    if not COSTED.exists():
        return set()
    out = set()
    for r in json.loads(COSTED.read_text()).get("recipes", {}).values():
        for ln in r.get("ingredients", []):
            ref = ln.get("ref") or ""
            if ln.get("kind") == "id" and ref.startswith("lightspeed:"):
                out.add(ref.split(":", 1)[1])
    return out


COSTS = ROOT / "data" / "costs.csv"


def already_costed() -> set[str]:
    """ProductIDs that already carry a USABLE cost.

    Read the built cost book, not cogs_list: a raw cogs row whose pack can't be
    resolved is dropped by build_costs, so it yields no price at all. Several
    pours ("Jack Daniels", $6.55 with no readable pack) have exactly such a row —
    counting those as costed left them blocked AND blocked the fix."""
    if not COSTS.exists():
        return set()
    return {r["ingredient"].split(":", 1)[1]
            for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
            if r.get("ingredient", "").startswith("lightspeed:")}


def sibling_costs() -> dict:
    """base name -> (product name, cost) for every costed product.

    A back bar carries the same spirit twice: the POUR the recipe names ("Jack
    Daniels", no cost) and the STOCK BOTTLE that holds the cost ("Jack Daniels
    [Bottle]", $45.25). They are one product with two SKUs, and norm() already
    strips the bracket/size suffix, so the pair collapses to one key. Costing the
    pour from its own bottle is evidence, not a guess."""
    out = {}
    for _v, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            c = _cost(r)
            if c is None:
                continue
            k = _norm(r["ProductName"])
            # prefer the priciest match: the full bottle, not a sample pour
            if k and (k not in out or c > out[k][1]):
                out[k] = (r["ProductName"].strip(), c)
    return out


def collect():
    pids = ingredient_pids()
    have = already_costed()
    sibs = sibling_costs()
    seed, skipped, seen = [], [], set()
    for venue, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            pid = (r.get("ProductID") or "").strip()
            if pid not in pids or pid in have or pid in seen:
                continue
            cost = _cost(r)
            name = r["ProductName"].strip()
            if cost is None:
                # no cost on this SKU — inherit the twin SKU's cost if there is one,
                # and price it off the TWIN's name so the pack size is read from the
                # bottle ("[700ml]") rather than the bare pour name.
                sib = sibs.get(_norm(name))
                if sib and _norm(sib[0]) == _norm(name) and sib[0] != name:
                    name, cost = sib[0], sib[1]
                else:
                    skipped.append((name, "no BO cost — needs an invoice"))
                    continue
            seen.add(pid)
            # Prefer a real pack read from the name ($/g, $/ml); otherwise price the
            # whole pack as one unit. Either way the ratio path does the work, so an
            # unreadable pack never blocks the fix.
            q, u, _per, _how, bad = resolve_pack(name, cost, basis="", note="", code="")
            if q and u and not bad:
                basis, pq, pu = "", str(q), u
            else:
                basis, pq, pu = "can", "1", "each"
            seed.append({
                "supplier": "Lightspeed", "supplier_code": pid,
                "invoice_description": name, "lightspeed_product": name,
                "cost_per_unit_incl_gst": str(cost), "basis": basis,
                "pack_size": "1", "pack_qty": pq, "pack_unit": pu,
                "cost_per_base_unit": "", "venue": venue,
                "source_invoice": f"{SEED_SOURCE}-{venue}", "invoice_date": SEED_DATE,
                "in_bounds": "yes",
                "note": f"BO export cost for a recipe ingredient; ProductID {pid}",
            })
    return seed, skipped


def _write(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def apply_to_cogs(seed):
    existing = list(csv.DictReader(COGS.open(encoding="utf-8-sig"))) if COGS.exists() else []
    fresh = {r["supplier_code"] for r in seed}
    carried = [r for r in existing
               if (r.get("source_invoice") or "").startswith(SEED_SOURCE)
               and r.get("supplier_code") not in fresh]      # sticky, like the bev seed
    kept = [r for r in existing if not (r.get("source_invoice") or "").startswith(SEED_SOURCE)]
    merged = kept + seed + carried
    merged.sort(key=lambda r: (r.get("invoice_date", ""), r.get("supplier", ""),
                               r.get("supplier_code", ""), r.get("invoice_description", "")))
    _write(COGS, merged)
    return len(merged), len(carried)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    seed, skipped = collect()
    _write(SEED_OUT, seed)
    print(f"{len(seed)} recipe-ingredient cost rows -> {SEED_OUT.relative_to(ROOT)}")
    byu = {}
    for r in seed:
        byu[r["pack_unit"]] = byu.get(r["pack_unit"], 0) + 1
    print("  by unit:", byu)
    print(f"  {len(skipped)} used by a recipe but STILL uncosted (need an invoice):")
    for nm, why in skipped[:25]:
        print(f"    - {nm[:52]:54} {why}")
    if args.apply:
        n, carried = apply_to_cogs(seed)
        print(f"applied -> data/cogs_list.csv now {n} rows ({carried} carried forward)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
