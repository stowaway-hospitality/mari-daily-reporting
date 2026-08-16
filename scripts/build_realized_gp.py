#!/usr/bin/env python3
"""
What the till actually collected, versus what the menu says. T7.

    "list prices lie"  — Zak, 15 Aug 2026

GP computed off the Lightspeed product sheet answers a question nobody asked:
what the margin WOULD be if every plate went out at full price. Happy hours,
comps, staff feeds, EatClub and delivery discounts mean it does not, so the
menu-price GP is optimistic by an amount nobody has ever measured.

    realized_price = revenue_ex / qty        what the till collected
    list_price     = the product sheet       what the menu says
    discount drag  = theoretical GP − achieved GP

Discount drag is the number this exists to produce. It makes happy hour, comps
and EatClub a visible line item per product instead of a vague sense that
weekends are worse than they look. Nearly free: `data/products_weekly.csv` has
been carrying revenue and quantity per product per week for 96 weeks, and
nothing has ever divided one by the other.

NO NEW INGESTION AND NO BROWSER. Every input is already committed.

WHERE THE COST COMES FROM: `data/lightspeed_recipes_costed.json`, which is what
the P&L uses today. After Phase 2 cuts over this should read the book instead
(`data/recipes/{venue}.yaml` via modules.recipes.cost) — the numbers are within
$6 across 895 recipes, so the shape of this report does not change, but the
provenance does. Marked with a TODO rather than guessed at now.

    python3 scripts/build_realized_gp.py              # write the feed + summary
    python3 scripts/build_realized_gp.py --weeks 26
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WEEKLY = DATA / "products_weekly.csv"
COSTED = DATA / "lightspeed_recipes_costed.json"
BO = [("stow", DATA / "bo_exports" / "stowaway_products.csv"),
      ("hg", DATA / "bo_exports" / "harry_gatos_products.csv")]
OUT = DATA / "realized_gp.json"

GST = Decimal("1.1")
VENUE_LABEL = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's"}


def _dec(x):
    if x in (None, ""):
        return None
    try:
        return Decimal(str(x).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def list_prices() -> dict:
    """product name (lower) -> ex-GST list price from the Back Office sheet.

    Marilyna's has no export of its own -- it has no till (CLAUDE.md) -- so its
    products appear in Stowaway's sheet and are found there by name.
    """
    out = {}
    for _venue, f in BO:
        if not f.exists():
            continue
        for row in csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines()):
            nm = (row.get("ProductName") or "").strip().lower()
            inc = _dec(row.get("SellPriceIncTax"))
            if nm and inc and inc > 0:
                out.setdefault(nm, inc / GST)
    return out


def book_costs() -> dict:
    """product name (lower) -> our cost per serve. Batches excluded: a batch is
    not a sold product, and $0 is the absence of a price rather than a price."""
    if not COSTED.exists():
        return {}
    book = json.loads(COSTED.read_text(encoding="utf-8-sig"))["recipes"]
    out = {}
    for name, r in book.items():
        if r.get("is_prep"):
            continue
        c = _dec(r.get("our_cost"))
        if c and c > 0:
            out[name.strip().lower()] = c
    return out


def build(weeks: int) -> dict:
    rows = list(csv.DictReader(WEEKLY.read_text(encoding="utf-8-sig").splitlines()))
    all_weeks = sorted({r["week_ending"] for r in rows})
    window = set(all_weeks[-weeks:]) if weeks else set(all_weeks)

    lp, bc = list_prices(), book_costs()

    agg = defaultdict(lambda: {"qty": Decimal(0), "rev": Decimal(0)})
    groups = {}
    for r in rows:
        if r["week_ending"] not in window:
            continue
        q, rev = _dec(r.get("qty")), _dec(r.get("sales_ex_gst"))
        if not q or q <= 0 or rev is None:
            continue
        key = (r["venue"], (r["product_name"] or "").strip())
        agg[key]["qty"] += q
        agg[key]["rev"] += rev
        groups[key] = r.get("reporting_group") or ""

    products, skipped = [], defaultdict(int)
    for (venue, name), v in agg.items():
        low = name.lower()
        cost = bc.get(low)
        if cost is None:
            skipped["no costed recipe"] += 1
            continue
        realized = v["rev"] / v["qty"]
        if realized <= 0:
            skipped["zero realized price"] += 1
            continue
        listed = lp.get(low)
        achieved = (realized - cost) / realized
        row = {
            "venue": venue, "product": name, "reporting_group": groups[(venue, name)],
            "qty": float(v["qty"]), "revenue_ex": float(v["rev"]),
            "realized_price_ex": float(round(realized, 4)),
            "cost_per_serve": float(round(cost, 4)),
            "achieved_gp": float(round(achieved, 4)),
        }
        if listed and listed > 0:
            theoretical = (listed - cost) / listed
            row["list_price_ex"] = float(round(listed, 4))
            row["theoretical_gp"] = float(round(theoretical, 4))
            row["discount_drag_gp"] = float(round(theoretical - achieved, 4))
            # POSITIVE = collected LESS than list (a discount). NEGATIVE =
            # collected MORE (delivery uplift).
            #
            # It was called revenue_foregone until Marilyna's came out at
            # -$2,252 and made the name nonsense. Mari is not discounting: her
            # Regular Pepperoni lists at $13.64 and collects $18.00, because the
            # delivery menu is marked up to absorb Uber's commission -- which is
            # the `uplift_required` the plan's gp_targets anticipates. One number,
            # two opposite meanings, and a name that only admitted one of them
            # would have had somebody reading uplift as lost revenue.
            row["price_variance_vs_list"] = float(round((listed - realized) * v["qty"], 2))
            # A realized price under half of list is more likely an attribution
            # artefact than a price -- a bundle booking its revenue elsewhere, a
            # modifier counted as a product. Flagged, not hidden, and not
            # asserted as a discount.
            if realized < listed / 2:
                row["suspect_price"] = ("realized is under half of list; check this "
                                        "is a real price and not a bundle or "
                                        "modifier booking revenue elsewhere")
        products.append(row)

    products.sort(key=lambda r: -(r.get("price_variance_vs_list") or 0))
    by_venue = {}
    for v in {p["venue"] for p in products}:
        mine = [p for p in products if p["venue"] == v]
        rev = sum(p["revenue_ex"] for p in mine)
        cogs = sum(p["cost_per_serve"] * p["qty"] for p in mine)
        var = [p.get("price_variance_vs_list") or 0 for p in mine]
        discounted = sum(x for x in var if x > 0)
        uplifted = -sum(x for x in var if x < 0)
        by_venue[v] = {
            "label": VENUE_LABEL.get(v, v), "products": len(mine),
            "revenue_ex": round(rev, 2),
            "achieved_gp": round((rev - cogs) / rev, 4) if rev else None,
            # Reported separately on purpose: netting a discount against an
            # uplift produces a number that describes neither.
            "discounted_below_list": round(discounted, 2),
            "uplifted_above_list": round(uplifted, 2),
            "net_vs_list": round(discounted - uplifted, 2),
            "suspect_prices": sum(1 for p in mine if p.get("suspect_price")),
        }

    return {
        "generated_at": date.today().isoformat(),
        "weeks": weeks, "window_from": min(window), "window_to": max(window),
        "note": "realized_price = revenue_ex / qty. All money ex-GST. Cost is "
                "the costed book (what the P&L uses today); after Phase 2 "
                "cutover it should read data/recipes/{venue}.yaml. TODO.",
        "skipped": dict(skipped),
        "by_venue": by_venue,
        "products": products,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=13)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()

    d = build(a.weeks)
    OUT.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"realized GP over {d['weeks']} weeks ({d['window_from']} -> {d['window_to']})\n")
    print(f"  {'venue':12s} {'products':>8s} {'revenue':>12s} {'achieved GP':>12s} "
          f"{'discounted':>12s} {'uplifted':>11s}")
    for v, sv in sorted(d["by_venue"].items(), key=lambda kv: -kv[1]["revenue_ex"]):
        gp = f"{100*sv['achieved_gp']:.1f}%" if sv["achieved_gp"] is not None else "-"
        print(f"  {sv['label']:12s} {sv['products']:>8d} ${sv['revenue_ex']:>11,.0f} "
              f"{gp:>12s} ${sv['discounted_below_list']:>11,.0f} "
              f"${sv['uplifted_above_list']:>10,.0f}")

    drags = [p for p in d["products"]
             if (p.get("price_variance_vs_list") or 0) > 0 and not p.get("suspect_price")]
    print(f"\n  DISCOUNT DRAG — collected less than the menu says:")
    print(f"  {'product':36s} {'venue':6s} {'list':>8s} {'got':>8s} {'GP now':>8s} {'gap':>10s}")
    for p in drags[:a.top]:
        print(f"  {p['product'][:36]:36s} {p['venue']:6s} "
              f"${p['list_price_ex']:>7.2f} ${p['realized_price_ex']:>7.2f} "
              f"{100*p['achieved_gp']:>7.1f}% ${p['price_variance_vs_list']:>9,.0f}")

    ups = sorted([p for p in d["products"] if (p.get("price_variance_vs_list") or 0) < 0],
                 key=lambda p: p["price_variance_vs_list"])
    if ups:
        print(f"\n  UPLIFT — collected more than the menu says (delivery absorbing commission):")
        for p in ups[:8]:
            print(f"  {p['product'][:36]:36s} {p['venue']:6s} "
                  f"${p['list_price_ex']:>7.2f} ${p['realized_price_ex']:>7.2f} "
                  f"{100*p['achieved_gp']:>7.1f}% ${-p['price_variance_vs_list']:>9,.0f}")

    susp = [p for p in d["products"] if p.get("suspect_price")]
    if susp:
        print(f"\n  {len(susp)} price(s) under half of list — check before believing:")
        for p in susp[:6]:
            print(f"    {p['product'][:36]:36s} list ${p['list_price_ex']:.2f} "
                  f"got ${p['realized_price_ex']:.2f}  ({p['qty']:.0f} sold)")
    if d["skipped"]:
        print(f"\n  skipped: {d['skipped']}")
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
