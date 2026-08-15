#!/usr/bin/env python3
"""The top sellers by VOLUME, and whether we can deduct stock for them.

INVENTORY_ARCHITECTURE.md: "recipe coverage of the TOP SELLERS was ~0% of
revenue — the recipes that exist are the tiki/sake/cocktail program, not the
beers, wines, burgers and classic cocktails that carry the volume ... the order
is: recipes for the top 50 sellers by VOLUME -> sale-deduction -> variance.
Nothing else on this page matters more than that list."

This is that list. It could not be built before, because the daily product mix
was truncated to the top 20 by revenue — which is a different question, and the
wrong one: a $4 side of chips outsells a $95 bottle of wine many times over and
depletes far more stock. Volume is what empties a shelf.

BY VOLUME, NOT REVENUE, and the difference is the point. Ranking by revenue puts
the expensive bottles on top; ranking by units puts the things the kitchen and
bar actually go through on top, which is what an inventory system needs.

A product counts as COVERED when the costed recipe book holds a recipe for it
whose ingredients all resolve to real stock items. `fully_our_book` in
data/lightspeed_recipes_costed.json is the book's own answer to that.

Run: python3 scripts/recipe_coverage_worklist.py [--top 50] [--venue stow]
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DAILY_DIR = ROOT / "data" / "products_daily"
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"


def main() -> int:
    top = 50
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    venue = None
    if "--venue" in sys.argv:
        venue = sys.argv[sys.argv.index("--venue") + 1]

    book = json.loads(BOOK.read_text())["recipes"]
    # name -> (has recipe, fully costed from our book, ingredient count)
    have = {name: (True, bool(r.get("fully_our_book")), len(r.get("ingredients") or []))
            for name, r in book.items()}

    qty: dict[str, float] = defaultdict(float)
    rev: dict[str, float] = defaultdict(float)
    venues: dict[str, set] = defaultdict(set)
    days = set()
    for path in sorted(DAILY_DIR.glob("*.csv")):
        with path.open() as f:
            for r in csv.DictReader(f):
                if venue and r["venue"] != venue:
                    continue
                n = r["product_name"]
                qty[n] += float(r["qty"] or 0)
                rev[n] += float(r["rev_ex_gst"] or 0)
                venues[n].add(r["venue"])
                days.add(r["date"])

    if not qty:
        raise SystemExit("no rows in data/products_daily/ — run build_products_daily.py")

    ranked = sorted(qty, key=lambda n: -qty[n])
    scope = f" [{venue}]" if venue else ""
    print(f"top {top} products by UNITS SOLD{scope}, across {len(days)} trading days "
          f"({min(days)} .. {max(days)})\n")

    covered_u = total_u = covered_r = total_r = 0.0
    print(f"{'#':>3} {'units':>9} {'rev ex':>11}  {'recipe?':9} product")
    for i, name in enumerate(ranked[:top], 1):
        hit = have.get(name)
        if hit and hit[1]:
            mark, c = "OURS", True
        elif hit:
            mark, c = "partial", False
        else:
            mark, c = "NONE", False
        total_u += qty[name]
        total_r += rev[name]
        if c:
            covered_u += qty[name]
            covered_r += rev[name]
        print(f"{i:3} {qty[name]:9,.0f} {rev[name]:11,.0f}  {mark:9} {name[:52]}")

    print(f"\nof the top {top} by volume:")
    print(f"  fully costed from our book: {covered_u/total_u*100:5.1f}% of units, "
          f"{covered_r/total_r*100:5.1f}% of revenue")

    # And the same question across everything, which is the number the design
    # note quotes.
    all_u = sum(qty.values())
    all_cov = sum(q for n, q in qty.items() if have.get(n, (0, 0, 0))[1])
    print(f"  across ALL {len(qty):,} products: {all_cov/all_u*100:5.1f}% of units")

    missing = [n for n in ranked[:top] if not have.get(n, (0, 0, 0))[1]]
    print(f"\n{len(missing)} of the top {top} still need a recipe. In volume order:")
    for n in missing:
        print(f"  {qty[n]:9,.0f} units  ${rev[n]:>10,.0f}  {n[:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
