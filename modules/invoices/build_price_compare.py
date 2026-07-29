#!/usr/bin/env python3
"""
Build the cross-supplier price comparison the app renders at /pricing.

    python3 modules/invoices/build_price_compare.py

Reads data/cogs_list.csv (every ingredient line the invoice pipeline has ever
validated), reduces each row to a canonical $/kg | $/L | $/each, groups rows
that are the SAME ingredient (price_compare.canonical_key), and for each
ingredient records the latest cost PER SUPPLIER plus its movement since the
previous invoice. Writes dashboard/pricing/compare.json.

The value is the multi-supplier groups: "chicken breast — B&E $12.40/kg vs
Foodlink $11.90/kg", cheapest flagged, spread shown. Single-supplier items still
appear as a price list with movement, so a creeping cost gets noticed.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Use the SAME pack resolver the recipe cost engine uses, so an ingredient's unit
# and $/base agree between /pricing and the recipe book. The older
# pack_size.parse_pack mislabelled weight lines ("1.5KG CTN", "125GM x carton") as
# "box", so those never compared per-kg; resolve_pack reads them correctly (and
# folds a carton note in, e.g. camembert = 12 x 125g -> $30.40/kg).
from modules.recipes.pipeline.build_ingredients import resolve_pack   # noqa: E402
from modules.invoices.price_compare import (                     # noqa: E402
    canonical_key, canonical_supplier, display_name, _load_aliases,
)

ROOT = Path(__file__).resolve().parents[2]
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "dashboard" / "pricing" / "compare.json"

# Units that are physically exact and so directly comparable across suppliers.
# Everything else (each / bunch / box / tray / case) is a pack whose real content
# can differ between suppliers, so its comparisons get a tighter suspect gate.
_EXACT_UNITS = {"kg", "l"}


def _dec(s) -> Decimal | None:
    try:
        return Decimal(str(s))
    except (InvalidOperation, TypeError):
        return None


def _base_cost(row: dict) -> tuple[Decimal | None, str | None]:
    """
    ($/base_unit, base_unit) for one cogs row, via the shared recipe resolver.
    Returns (None, None) when the pack can't be read (unresolved lines are left
    out of the comparison rather than compared on a guessed unit).
    """
    price = _dec(row.get("cost_per_unit_incl_gst"))
    if price is None:
        return None, None
    qty, unit, per, how, bad = resolve_pack(
        row.get("invoice_description", ""), price,
        basis=row.get("basis", ""), note=row.get("note") or "",
        code=row.get("supplier_code") or "")
    if per is None or unit is None:
        # pack not readable — keep the line in the price list at its raw selling
        # price (per "each"), but it won't compare on a like unit unless another
        # supplier's line happens to share the same guessed unit.
        return price, "ea"
    # normalise weight -> kg and volume -> L so every supplier lines up on the
    # same scale (resolve_pack returns $/g and $/ml).
    if unit == "g":
        return per * 1000, "kg"
    if unit == "ml":
        return per * 1000, "L"
    return per, unit


def build() -> dict:
    if not COGS.exists():
        return {"generated": date.today().isoformat(), "ingredients": []}
    aliases = _load_aliases()
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))

    # group: (key, unit) -> ingredient; within it, supplier -> list of (date, cost, desc)
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        desc = (r.get("invoice_description") or "").strip()
        supplier = canonical_supplier(r.get("supplier") or "")
        if not desc or not supplier:
            continue
        base, unit = _base_cost(r)
        if base is None or base <= 0:
            continue
        key = canonical_key(desc, aliases)
        if not key:
            continue
        g = groups.setdefault((key, unit), {"key": key, "unit": unit,
                                            "names": {}, "suppliers": {}})
        # remember candidate display names (shortest identity wins — most generic)
        g["names"][display_name(desc)] = g["names"].get(display_name(desc), 0) + 1
        g["suppliers"].setdefault(supplier, []).append(
            (r.get("invoice_date") or "", float(base), desc))

    ingredients = []
    for (key, unit), g in groups.items():
        sup_rows = []
        for supplier, obs in g["suppliers"].items():
            obs.sort(key=lambda o: o[0])                 # by date asc
            d, cost, desc = obs[-1]                       # latest
            prev = next((o for o in reversed(obs[:-1]) if o[1] != cost), None)
            change = round((cost - prev[1]) / prev[1] * 100, 1) if prev and prev[1] else None
            sup_rows.append({
                "supplier": supplier, "cost": round(cost, 4), "date": d,
                "desc": desc, "change_pct": change, "n": len(obs),
                "prev_cost": round(prev[1], 4) if prev else None,
            })
        sup_rows.sort(key=lambda s: s["cost"])
        cheapest = sup_rows[0]["supplier"]
        lo, hi = sup_rows[0]["cost"], sup_rows[-1]["cost"]
        spread = round((hi - lo) / lo * 100, 1) if lo else 0.0
        multi = len(sup_rows) > 1
        # A gap this large between two "same" items is almost always a pack-size
        # mismatch — one priced per tray/dozen, the other per kg — not a real
        # saving. Flag it so the reviewer verifies (and can add an alias) rather
        # than being told to switch supplier on a phantom number.
        #
        # The threshold is unit-aware. $/kg and $/L are EXACT and directly
        # comparable, so a big gap there is usually a genuine price difference —
        # only a huge one (>150%) is suspicious. Discrete units (each / bunch /
        # box / tray) are INEXACT: one supplier's "each" need not be the other's,
        # so a much smaller gap (>80%) already means "verify the pack", not "save".
        exact = unit.lower() in _EXACT_UNITS
        suspect = multi and spread > (150 if exact else 80)
        # display: the most-seen shortest name
        name = min(sorted(g["names"], key=lambda n: (-g["names"][n], len(n))),
                   key=lambda n: (len(n), -g["names"][n]))
        ingredients.append({
            "key": key, "name": name, "unit": unit,
            "suppliers": sup_rows, "cheapest": cheapest,
            "min": round(lo, 4), "max": round(hi, 4), "spread_pct": spread,
            "multi": multi, "suspect": suspect,
        })

    # real comparisons first (biggest spread = biggest saving), then the
    # verify-pack ones, then single-supplier price list A–Z
    ingredients.sort(key=lambda i: (
        not (i["multi"] and not i["suspect"]),
        -i["spread_pct"] if (i["multi"] and not i["suspect"]) else 0,
        not i["suspect"], i["name"].lower()))

    # Cost creep — where a supplier's price rose since the last order. This is the
    # part that matters for the ~95% of items with only one supplier: you can't
    # switch, but you can SEE the rise and question it. A rise of MOVE_MIN% or more
    # (and a real cash move, not a rounding wobble on a cheap line) surfaces here,
    # biggest first, so a supplier creeping prices up gets noticed the week it
    # happens rather than at year-end.
    MOVE_MIN = 5.0
    movers = []
    for ing in ingredients:
        for s in ing["suppliers"]:
            pct, prev = s.get("change_pct"), s.get("prev_cost")
            if pct is None or prev is None or pct < MOVE_MIN:
                continue
            if (s["cost"] - prev) < 0.02:      # ignore sub-2c moves on cheap lines
                continue
            movers.append({
                "name": ing["name"], "supplier": s["supplier"], "unit": ing["unit"],
                "prev": prev, "cost": s["cost"], "pct": pct, "date": s["date"],
            })
    movers.sort(key=lambda m: -m["pct"])

    return {"generated": date.today().isoformat(),
            "count": len(ingredients),
            "compared": sum(1 for i in ingredients if i["multi"]),
            "movers": movers,
            "ingredients": ingredients}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_text(json.dumps(data, indent=2))
    print(f"compare.json: {data['count']} ingredients, "
          f"{data['compared']} compared across >1 supplier -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
