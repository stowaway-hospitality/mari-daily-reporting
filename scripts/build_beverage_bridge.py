#!/usr/bin/env python3
"""
Extend data/product_map.csv with beverage supplier_code -> Lightspeed ProductID
links, so invoice costs flow onto the bottle's ProductID identity (build_costs
bridge) and supersede the BO seed.

    python3 scripts/build_beverage_bridge.py            # print proposed matches
    python3 scripts/build_beverage_bridge.py --apply    # append confident ones

MATCHING — evidence, not a guess. A liquor invoice line ("BOMBAY DRY GIN") is
matched to a BO stock bottle ("Bombay Dry [Bottle]") by NAME (brand/product token
overlap after stripping sizes, vintages, ABV, pack words) AND corroborated by
PRICE (the invoice cost per bottle within tolerance of the BO cost). Both must
agree for a link to be written; a name-only or price-only hit is left for review.
A wrong link puts the wrong cost on a bottle, so the bar is deliberately high.
"""

from __future__ import annotations

import argparse
import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COGS = ROOT / "data" / "cogs_list.csv"
MAP = ROOT / "data" / "product_map.csv"
EXPORTS = [("stowaway", ROOT / "data" / "bo_exports" / "stowaway_products.csv"),
           ("harry_gatos", ROOT / "data" / "bo_exports" / "harry_gatos_products.csv")]

BEV_SUPPLIERS = {"ILG", "Bacchus", "Grifter", "Lion", "Paramount", "Nelson",
                 "Viticult", "Young & Rashleigh", "Combined Wines", "Cellarhand",
                 "Philter", "Mountain Culture", "4 Pines"}

_STRIP = re.compile(
    r"\[[^\]]*\]|\b\d{2,4}\s?ml\b|\b\d+(\.\d+)?\s?l\b|\b\d{4}\b|\b\d+%\b|"
    r"\b\d+\s?x\s?\d+\b|\b\d+pk\b|\b(bottle|btl|case|ctn|carton|can|cans|keg|stubs?|"
    r"nv|r|p|pk|each|ea)\b", re.I)
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def norm_tokens(name: str) -> set:
    n = _STRIP.sub(" ", (name or "").lower())
    n = _NONWORD.sub(" ", n)
    return {t for t in n.split() if len(t) > 1}


def _dec(s):
    try:
        return Decimal(str(s))
    except (InvalidOperation, TypeError):
        return None


def load_bo():
    out = []
    for _venue, path in EXPORTS:
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            if (r.get("InventoryType") or "").strip() != "1":
                continue
            c = _dec(r.get("CostPriceIncTax"))
            if not c or c <= 0:
                continue
            out.append((r["ProductID"], r["ProductName"], c, norm_tokens(r["ProductName"])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    bo = load_bo()
    existing = {(r["supplier"], r["supplier_code"])
                for r in csv.DictReader(MAP.open(encoding="utf-8-sig"))} if MAP.exists() else set()

    seen, proposals = set(), []
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        sup, code = r.get("supplier"), (r.get("supplier_code") or "").strip()
        if sup not in BEV_SUPPLIERS or not code or (sup, code) in existing or (sup, code) in seen:
            continue
        seen.add((sup, code))
        toks = norm_tokens(r.get("invoice_description", ""))
        inv_cost = _dec(r.get("cost_per_unit_incl_gst"))
        if len(toks) < 1 or inv_cost is None:
            continue
        best = None
        for pid, pname, bo_cost, btoks in bo:
            if not toks or not btoks:
                continue
            overlap = len(toks & btoks)
            jac = overlap / len(toks | btoks)
            # name: strong token overlap; price: invoice-per-bottle within 12% of BO
            price_ok = bo_cost and abs(inv_cost - bo_cost) / bo_cost <= Decimal("0.12")
            score = (overlap, jac, price_ok)
            if overlap >= 2 and jac >= 0.5 and price_ok:
                if best is None or score > best[0]:
                    best = (score, pid, pname, bo_cost)
        if best:
            _, pid, pname, bo_cost = best
            proposals.append((sup, code, pid, pname,
                              str(inv_cost), str(bo_cost), r.get("invoice_description", "")))

    print(f"{len(proposals)} confident beverage links (name + price agree):")
    for sup, code, pid, pname, ic, bc, desc in proposals:
        print(f"  {sup:<8} {code:<12} -> {pid} {pname[:34]:<36} inv={ic} bo={bc}")

    if args.apply and proposals:
        new = MAP.exists()
        fields = ["supplier", "supplier_code", "product_id", "product_name", "venue",
                  "bo_cost", "invoice_cost", "delta", "confidence", "source_invoice", "invoice_date"]
        with MAP.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if not new:
                w.writeheader()
            for sup, code, pid, pname, ic, bc, desc in proposals:
                w.writerow({"supplier": sup, "supplier_code": code, "product_id": pid,
                            "product_name": pname, "venue": "", "bo_cost": bc,
                            "invoice_cost": ic,
                            "delta": str(Decimal(bc) - Decimal(ic)),
                            "confidence": "name+price", "source_invoice": "", "invoice_date": ""})
        print(f"appended {len(proposals)} -> {MAP.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
