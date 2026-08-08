#!/usr/bin/env python3
"""
Bridge Zak-CONFIRMED recipe products to the invoice data we already hold.

For each confirmed match we (a) write a baseline cost row (source
'recipe-bridge-seed') keyed lightspeed:<PID> at the invoice per-unit so the
converter can cost the line off our book — the recipe number does NOT move
(ratio 1.0, the line stays at Lightspeed's own reliable per-line cost), the
baseline just gives future invoices a reference to move against — and (b) a
product_map row so the NEXT invoice for that supplier code supersedes it and
the cost updates automatically.

Matches were reviewed by Zak; EXCLUDE holds the ones he rejected.

    python3 scripts/build_recipe_bridge.py            # preview
    python3 scripts/build_recipe_bridge.py --apply    # write cogs_list + product_map
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"
COSTS = ROOT / "data" / "costs.csv"
COGS = ROOT / "data" / "cogs_list.csv"
PRODUCT_MAP = ROOT / "data" / "product_map.csv"
EXPORTS = [ROOT / "data" / "bo_exports" / "stowaway_products.csv",
           ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"]
SRC = "recipe-bridge-seed"

# Reviewed by Zak — rejected as wrong-product auto-matches.
EXCLUDE = {"818 tequila blanco", "almond meal 1kg natures secret"}

_STOP = {"the", "and", "with", "kg", "kgs", "g", "gm", "ml", "l", "lt", "each", "ea",
         "box", "bunch", "punnet", "tin", "pack", "tray", "bottle", "btl", "large",
         "small", "fresh", "no", "s", "off", "imp", "plain", "house"}


def toks(s):
    s = re.sub(r"\[[^\]]*\]", " ", (s or "").lower())
    return [t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 2 and t not in _STOP]


def matches():
    R = json.loads(COSTED.read_text(encoding="utf-8-sig"))["recipes"]
    # exclude our OWN bridge rows so a re-run still sees the original uncosted set
    # (otherwise the second run finds them "priced" and drops them).
    priced = {r["ingredient"] for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))
              if str(r.get("source_invoice") or "") != SRC}
    name_of = {}
    for p in EXPORTS:
        if p.exists():
            for r in csv.DictReader(p.open(encoding="utf-8-sig")):
                name_of.setdefault(r["ProductID"], r["ProductName"])
    need = {}
    for rec in R.values():
        for ln in rec["ingredients"]:
            if ln["kind"] == "id" and ln["our_cost"] is None and ln["ref"] not in priced:
                pid = ln["ref"].split(":", 1)[1]
                need.setdefault(pid, ln["name"])
    inv = []
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        if str(r.get("source_invoice") or "").startswith(("bo-seed", "ls-recipe-seed", SRC)):
            continue
        if (r.get("supplier") or "").lower() == "lightspeed":
            continue
        try:
            per = float(r.get("cost_per_base_unit") or 0)
        except ValueError:
            per = 0
        if per > 0 and r.get("supplier") and r.get("supplier_code"):
            inv.append((set(toks(r.get("invoice_description", "") + " " + r.get("lightspeed_product", ""))), per, r))
    out = []
    for pid, rname in sorted(need.items(), key=lambda x: x[1]):
        nm = name_of.get(pid, rname)
        wl = toks(nm)
        if not wl or nm.lower() in EXCLUDE:
            continue
        want, dist = set(wl), max(wl, key=len)
        best, sc = None, 0
        for itk, per, r in inv:
            if dist in itk:
                ov = len(want & itk)
                if ov > sc:
                    sc, best = ov, (per, r)
        if best and sc >= 2:
            per, r = best
            # keep the invoice's own pack unit (kg/L/ea/box). This deliberately does
            # NOT match the recipe's g/ml unit, so the converter uses the dimensionless
            # current/baseline RATIO (number stays exactly at the reliable LS line) —
            # never our_cost x a possibly-garbage recipe qty, which blew a ginger-beer
            # line to -512% GP. The baseline only anchors future invoice updates.
            out.append((pid, nm, r["supplier"], r["supplier_code"], per, (r.get("pack_unit") or "unit").strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    m = matches()
    print(f"{len(m)} confirmed products to bridge")
    for pid, nm, sup, code, per, unit in m:
        print(f"  {nm[:38]:40} <- {sup} {code}  ${per:.4f}/{unit}")

    if args.apply and m:
        cogs = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
        cf = list(cogs[0].keys())
        kept = [r for r in cogs if r.get("source_invoice") != SRC]
        for pid, nm, sup, code, per, unit in m:
            row = {k: "" for k in cf}
            row.update(supplier="Lightspeed", supplier_code=pid, invoice_description=nm,
                       lightspeed_product=nm, cost_per_unit_incl_gst=f"{per:.6f}",
                       basis="per_unit", pack_size="1", pack_qty="1", pack_unit=unit,
                       cost_per_base_unit=f"{per:.6f}", venue="stowaway", source_invoice=SRC,
                       invoice_date="2026-01-03", in_bounds="yes", note=f"confirmed bridge from {sup} {code}")
            kept.append({k: row.get(k, "") for k in cf})
        kept.sort(key=lambda r: (r.get("invoice_date", ""), r.get("supplier", ""),
                                 r.get("supplier_code", ""), r.get("invoice_description", "")))
        with COGS.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cf); w.writeheader(); w.writerows(kept)
        pm = list(csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")))
        pf = list(pm[0].keys())
        have = {(r["supplier"], r["supplier_code"], r["product_id"]) for r in pm}
        pm = [r for r in pm if r.get("confidence") != "recipe-bridge"]  # idempotent
        added = 0
        for pid, nm, sup, code, per, unit in m:
            if (sup, code, pid) in have and any(r.get("confidence") != "recipe-bridge" for r in pm):
                pass
            row = {k: "" for k in pf}
            row.update(supplier=sup, supplier_code=code, product_id=pid, product_name=nm,
                       confidence="recipe-bridge")
            pm.append({k: row.get(k, "") for k in pf}); added += 1
        with PRODUCT_MAP.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=pf); w.writeheader(); w.writerows(pm)
        print(f"applied: {len(m)} baselines -> cogs_list, {added} rows -> product_map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
