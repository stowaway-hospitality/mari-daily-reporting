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
INVOICES = ROOT / "data" / "invoices"
OUT = ROOT / "dashboard" / "pricing" / "compare.json"

# How far back "how much you buy" looks, for the dollar-saving estimate.
SPEND_WINDOW_DAYS = 90


def _to_comp_unit(per: Decimal, unit: str) -> tuple[Decimal, str]:
    """resolve_pack returns $/g and $/ml; the comparison works in $/kg and $/L."""
    if unit == "g":
        return per * 1000, "kg"
    if unit == "ml":
        return per * 1000, "L"
    return per, unit


def _purchase_stats() -> dict:
    """
    From the parsed invoices, how MUCH of each ingredient you actually bought and
    what you spent, per supplier, over the recent window. Keyed the SAME way as the
    comparison (canonical_key, comparison-unit) so the two line up. This is what
    turns "31% cheaper" into "$Xxx" — a 40% gap on a herb you buy twice a year is
    noise; a 10% gap on your carrots is real money.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=SPEND_WINDOW_DAYS)).date()
    stats: dict[tuple[str, str], dict] = {}
    for p in sorted(INVOICES.glob("*.json")) if INVOICES.exists() else []:
        try:
            inv = json.loads(p.read_text(encoding="utf-8-sig")).get("invoice", {})
        except Exception:
            continue
        try:
            idate = datetime.fromisoformat((inv.get("invoice_date") or "")[:10]).date()
        except Exception:
            continue
        if idate < cutoff:
            continue
        supplier = canonical_supplier(inv.get("supplier_name_raw") or inv.get("supplier_key") or "")
        for ln in inv.get("lines", []):
            if ln.get("line_class") != "stock":
                continue
            desc = (ln.get("description") or "").strip()
            qty = _dec(ln.get("qty"))
            spend = _dec(ln.get("line_total_incl"))
            unitp = _dec(ln.get("unit_price_incl")) or _dec(ln.get("cost_per_unit_incl_gst"))
            if not desc or qty is None or spend is None or unitp is None:
                continue
            q2, unit, per, how, bad = resolve_pack(desc, unitp, basis=ln.get("cost_basis") or "",
                                                   note="", code=ln.get("supplier_code") or "")
            if per is None or unit is None:
                continue
            _, cu = _to_comp_unit(per, unit)
            # base units bought = (qty selling units) x (base units per selling unit)
            per_selling = (q2 / 1000) if unit in ("g", "ml") else q2
            vol = float(qty * per_selling)
            key = (canonical_key(desc), cu)
            s = stats.setdefault(key, {}).setdefault(supplier, {"spend": 0.0, "volume": 0.0})
            s["spend"] += float(spend)
            s["volume"] += vol
    return stats

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
    stats = _purchase_stats()          # how much of each you actually buy

    # group: (key, unit) -> ingredient; within it, supplier -> list of (date, cost, desc)
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        desc = (r.get("invoice_description") or "").strip()
        supplier = canonical_supplier(r.get("supplier") or "")
        if not desc or not supplier:
            continue
        # Beverage costs seeded from the Lightspeed BO export ("Lightspeed" supplier)
        # are a COST SOURCE, not a supplier price to compare — a POS cost has no
        # rival quote. Keep them out of the cross-supplier page (they're in the cost
        # book for recipes, which is where they belong).
        if supplier.lower() == "lightspeed" or (r.get("source_invoice") or "").startswith("bo-seed"):
            continue
        base, unit = _base_cost(r)
        if base is None or base <= 0:
            continue
        key = canonical_key(desc, aliases)
        if not key:
            continue
        g = groups.setdefault((key, unit), {"key": key, "unit": unit,
                                            "names": {}, "suppliers": {},
                                            "low": None, "low_date": None})
        # remember candidate display names (shortest identity wins — most generic)
        g["names"][display_name(desc)] = g["names"].get(display_name(desc), 0) + 1
        d0 = r.get("invoice_date") or ""
        g["suppliers"].setdefault(supplier, []).append((d0, float(base), desc))
        # lowest price EVER seen for this ingredient (across suppliers + history)
        if g["low"] is None or float(base) < g["low"]:
            g["low"], g["low_date"] = round(float(base), 4), d0

    ingredients = []
    for (key, unit), g in groups.items():
        gstats = stats.get((key, unit), {})
        sup_rows = []
        for supplier, obs in g["suppliers"].items():
            obs.sort(key=lambda o: o[0])                 # by date asc
            d, cost, desc = obs[-1]                       # latest
            prev = next((o for o in reversed(obs[:-1]) if o[1] != cost), None)
            change = round((cost - prev[1]) / prev[1] * 100, 1) if prev and prev[1] else None
            sst = gstats.get(supplier, {})
            sup_rows.append({
                "supplier": supplier, "cost": round(cost, 4), "date": d,
                "desc": desc, "change_pct": change, "n": len(obs),
                "prev_cost": round(prev[1], 4) if prev else None,
                "spend": round(sst.get("spend", 0.0), 2),      # $ bought, recent window
                "volume": round(sst.get("volume", 0.0), 3),    # base units bought
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

        # DOLLAR SAVING — turn the % gap into money. For each supplier you buy this
        # from that ISN'T the cheapest, you'd have saved (units you bought) x (their
        # rate - cheapest rate) had you bought it from the cheapest instead. Summed
        # over the recent window, at current prices. A real number a chef can act on,
        # not a percentage on a jar of saffron you buy once a year.
        est_saving = 0.0
        for s in sup_rows[1:]:                          # everyone dearer than cheapest
            if s["volume"] > 0:
                est_saving += s["volume"] * (s["cost"] - lo)
        est_saving = round(est_saving, 2)

        # SWITCH THESE — your MAIN supplier for this item (the one you spend the most
        # with) isn't the cheapest, and it's a real, non-suspect comparison with money
        # on the table. This is the "act on it" list: not "a cheaper price exists
        # somewhere" but "you are actively buying this from the dearer one".
        main = max(sup_rows, key=lambda s: s["spend"]) if any(s["spend"] for s in sup_rows) else None
        switch = bool(multi and not suspect and main and main["supplier"] != cheapest
                      and est_saving >= 5.0)

        ingredients.append({
            "key": key, "name": name, "unit": unit,
            "suppliers": sup_rows, "cheapest": cheapest,
            "min": round(lo, 4), "max": round(hi, 4), "spread_pct": spread,
            "multi": multi, "suspect": suspect,
            "low": g["low"], "low_date": g["low_date"],   # lowest ever seen
            "est_saving": est_saving,
            "main": main["supplier"] if main else None,
            "switch": switch,
        })

    # "switch these" first (real money you're leaving on the table, biggest $ first),
    # then the rest of the real comparisons by spread, then verify-pack, then the
    # single-supplier price list A–Z.
    ingredients.sort(key=lambda i: (
        not i["switch"],
        -i["est_saving"] if i["switch"] else 0,
        not (i["multi"] and not i["suspect"]),
        -i["spread_pct"] if (i["multi"] and not i["suspect"]) else 0,
        not i["suspect"], i["name"].lower()))

    # The action list + the headline number: every item you're buying from a dearer
    # supplier, and the total you'd save at current prices by moving each to its
    # cheapest. This is the one figure that answers "is this module worth it?".
    switches = [{
        "name": i["name"], "unit": i["unit"], "from": i["main"],
        "to": i["cheapest"], "saving": i["est_saving"],
        "from_cost": next((s["cost"] for s in i["suppliers"] if s["supplier"] == i["main"]), None),
        "to_cost": i["min"],
    } for i in ingredients if i["switch"]]
    switches.sort(key=lambda s: -s["saving"])
    total_saving = round(sum(s["saving"] for s in switches), 2)

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
            "window_days": SPEND_WINDOW_DAYS,
            "total_saving": total_saving,
            "switches": switches,
            "movers": movers,
            "aliases": aliases,        # manual merges, so the UI can offer an undo
            "ingredients": ingredients}


def main() -> int:
    # stdout is output too — see build_costs.py. An em-dash in a progress line
    # under an ASCII locale kills a run whose files are already correct.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"compare.json: {data['count']} ingredients, "
          f"{data['compared']} compared across >1 supplier -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
