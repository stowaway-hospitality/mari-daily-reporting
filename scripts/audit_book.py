#!/usr/bin/env python3
"""Adversarial sweep of the whole cost + recipe book.

    python3 scripts/audit_book.py            # every finding, grouped
    python3 scripts/audit_book.py --severe   # only the ones that misstate money

WHY THIS EXISTS
---------------
Every defect this project has shipped looked like a valid number at the time. A
$0 bottle reads as 100% GP. A missed dict key reads as "0 sold". A per-ml rate in
a per-pack column reads as a cheap ingredient. None of them throw. The only way
to catch that class is to state, out loud, what a SANE book looks like and list
everything that isn't.

Findings are graded:
  SEVERE  — the number shown to a human is wrong and flatters or alarms
  WARN    — probably wrong, needs an eye
  INFO    — known/accepted, listed so it stays visible

Exit code is 1 if any SEVERE remains, so CI can hold the line.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"
INGREDIENTS = ROOT / "data" / "ingredients.json"
COSTS = ROOT / "data" / "costs.csv"
COGS = ROOT / "data" / "cogs_list.csv"

# What a sane bar/kitchen ingredient costs, per BASE unit, incl GST. Anything past
# these is a pack/unit confusion, not a real price. Calibrated on the real book:
# the dearest legitimate per-g item is saffron-class spice; the dearest per-ml is
# a top-shelf spirit at roughly $0.35/ml.
CEIL = {"g": 0.20, "ml": 0.60}
# Verified against the invoice and genuinely this dear, so the ceiling would
# only ever cry wolf. Select Fresh 3064370: "KUTJERA BUSH TOMATO WHOLE100GM"
# $48.00 for 100g — a premium native spice used a pinch at a time.
DEAR_BUT_REAL = {"select-fresh:BUSHTOMG"}
FLOOR = 0.000_01          # a real ingredient is never free
ABSURD_SERVE = 120.0      # no single non-prep menu item costs more than this
GP_FLATTER = 95.0
# Below this a POS "price" is a placeholder or a staff/comp SKU, not a menu
# price — the cost engine already refuses to compute GP under it, and an
# auditor that ignores the threshold just reports the engine working.
MENU_PRICED = 3.0         # a 95%+ GP on a food/drink item means a missing cost


def money(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def audit():
    recipes = json.loads(COSTED.read_text())["recipes"]
    ing_raw = json.loads(INGREDIENTS.read_text())
    ings = ing_raw["ingredients"] if isinstance(ing_raw, dict) else ing_raw
    by_id = {i["id"]: i for i in ings}

    F = defaultdict(list)   # (severity, rule) -> [detail]

    def add(sev, rule, detail):
        F[(sev, rule)].append(detail)

    # ---------- RECIPES ----------
    for name, r in sorted(recipes.items()):
        prep = bool(r.get("is_prep"))
        sell, cost = money(r.get("sell_incl")), money(r.get("our_cost"))
        lines = r.get("ingredients") or []
        gp = r.get("gp_pct")

        if sell >= MENU_PRICED and cost == 0 and not prep:
            add("SEVERE", "sells for money but costs $0 (reads as 100% GP)",
                f"${sell:>7.2f}  {name}")
        if not lines and not prep:
            add("WARN", "no ingredient lines at all", name)
        if cost > ABSURD_SERVE and not prep:
            add("SEVERE", f"single serve costs more than ${ABSURD_SERVE:.0f}",
                f"${cost:>8.2f}  {name}")
        if sell >= MENU_PRICED and cost > sell and not prep:
            add("SEVERE", "costs more than it sells for",
                f"cost ${cost:>7.2f} vs sell ${sell:>7.2f}  {name}")
        if gp is not None and gp >= GP_FLATTER and not prep:
            add("WARN", f"GP >= {GP_FLATTER:.0f}% — a cost is probably missing",
                f"{gp:>5.1f}%  ${cost:>6.2f} -> ${sell:>7.2f}  {name}")
        if gp is not None and gp < 0 and not prep:
            add("SEVERE", "negative GP", f"{gp:>7.1f}%  {name}")

        for ln in lines:
            if not ln.get("kind"):
                add("WARN", "ingredient line resolves to nothing",
                    f"{name} -> {ln.get('name') or ln.get('id')}")
            elif money(ln.get("eff_cost")) == 0 and money(ln.get("qty")) > 0:
                add("WARN", "line contributes $0 despite a real quantity",
                    f"{name} -> {ln.get('name') or ln.get('product') or ln.get('id')}"
                    f" ({ln.get('qty')}{ln.get('unit') or ''})")

    # collisions: two recipes that normalise to one name double-count in rollups
    seen = defaultdict(list)
    for n in recipes:
        seen[re.sub(r"[^a-z0-9]+", "", n.lower())].append(n)
    for k, v in seen.items():
        if len(v) > 1:
            add("INFO", "names collide once normalised (keep them distinct)", " | ".join(v))

    # ---------- INGREDIENTS ----------
    for i in ings:
        rate, unit = money(i.get("cost_per_base_unit")), (i.get("pack_unit") or "").lower()
        desc = i.get("description") or i.get("id")
        if unit in CEIL and rate > CEIL[unit] and i.get("id") not in DEAR_BUT_REAL:
            add("SEVERE", "per-unit rate above anything real (pack/unit confusion)",
                f"${rate:.4f}/{unit}  {desc}")
        if rate and rate < FLOOR:
            add("WARN", "priced at effectively zero", f"${rate:.8f}  {desc}")
        if i.get("needs_pack_review"):
            add("WARN", "pack size unconfirmed", desc)

    # ---------- MONEY SPENT THAT NEVER REACHES THE COST BOOK ----------
    # A line resolve_pack can't read is SKIPPED — correctly, because guessing a pack
    # is how a $76 bottle becomes $12.75. But a skip is silent, and the product then
    # costs off whatever stale seed it had. $1,583 of Bombay Dry gin was invoiced
    # since June and never reached the book; 13 cocktails priced off a January seed.
    # Ranked by spend, because that is the order worth fixing them in.
    priced = {r["ingredient"] for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))}
    spend, seen = defaultdict(float), {}
    if COGS.exists():
        for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
            sup, code = (r.get("supplier") or ""), (r.get("supplier_code") or "").strip()
            if not code or sup == "Lightspeed" or (r.get("invoice_date") or "") < "2026-06-01":
                continue
            iid = f"{re.sub(r'[^a-z0-9]+', '-', sup.lower()).strip('-')}:{code.upper()}"
            if iid in priced:
                continue
            spend[iid] += money(r.get("cost_per_unit_incl_gst"))
            seen[iid] = r.get("invoice_description")
    for iid, amt in sorted(spend.items(), key=lambda x: -x[1]):
        if amt < 100:
            continue
        add("WARN", "bought since June but never reached the cost book (pack unreadable)",
            f"${amt:>9,.0f}  {str(seen[iid])[:38]:40} {iid}")

    # ---------- COST BOOK: PRICE OUTLIERS ----------
    # A pack misread doesn't look wrong on its own — it looks like a price. It only
    # shows up NEXT TO the same ingredient's other invoices. Foodlink's black beans
    # invoiced at $8.70 a tin twice and $52.20 once (a CTN-6 carton read as one tin);
    # the camembert went the other way, a per-piece price divided by a carton of 12.
    # Comparing each observation to its own median catches both directions.
    hist = defaultdict(list)
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        c = money(r.get("cost_per_unit"))
        if c > 0:
            hist[(r["ingredient"], r["unit"])].append((c, r))
    for (iid, _u), obs in hist.items():
        if len(obs) < 3:
            continue
        med = sorted(x[0] for x in obs)[len(obs) // 2]
        if med <= 0:
            continue
        for c, r in obs:
            if c > 3 * med or c < med / 3:
                newest = max(x[1]["observed_on"] for x in obs)
                live = " <-- THIS IS THE LIVE PRICE" if r["observed_on"] == newest else ""
                add("SEVERE" if live else "WARN",
                    "invoice price way off this ingredient's own history (pack misread)",
                    f"{c / med:>5.1f}x median  ${c:<10.6f} {str(r.get('description'))[:26]:28}"
                    f" {r.get('source_invoice', '')[:12]} {r['observed_on']}{live}")

    # ---------- COST BOOK ----------
    rows = list(csv.DictReader(COSTS.open(encoding="utf-8-sig")))
    for r in rows:
        if money(r.get("cost_per_unit")) == 0:
            add("WARN", "cost book row priced at $0",
                f"{r.get('ingredient')} {r.get('description')}")
    return F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--severe", action="store_true", help="only money-misstating findings")
    args = ap.parse_args()

    F = audit()
    order = {"SEVERE": 0, "WARN": 1, "INFO": 2}
    keys = sorted(F, key=lambda k: (order[k[0]], -len(F[k])))
    n_sev = sum(len(F[k]) for k in F if k[0] == "SEVERE")

    for sev, rule in keys:
        if args.severe and sev != "SEVERE":
            continue
        items = F[(sev, rule)]
        print(f"\n[{sev}] {rule} — {len(items)}")
        for d in items[:20]:
            print(f"    {d}")
        if len(items) > 20:
            print(f"    ... and {len(items) - 20} more")

    print(f"\n{'=' * 62}\nSEVERE {n_sev} | "
          f"WARN {sum(len(F[k]) for k in F if k[0] == 'WARN')} | "
          f"INFO {sum(len(F[k]) for k in F if k[0] == 'INFO')}")
    return 1 if n_sev else 0


if __name__ == "__main__":
    raise SystemExit(main())
