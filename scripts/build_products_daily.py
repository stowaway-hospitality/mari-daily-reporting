#!/usr/bin/env python3
"""Build data/products_daily.csv — the full daily product mix, every venue,
every day, in one file the stock ledger can read in a single pass.

Source: data/product_mix/<prefix>_<date>.json, written by daily_aggregator.py.
Those files are the per-day facts; this is the derived rollup, regenerated
whole on every run (idempotent, so it can never fossilise).

WHY A ROLLUP AND NOT JUST THE FILES: a ledger rebuild reads every day at once.
155 days x 3 venues is 465 file opens per rebuild; this is one.

Columns:
    date,venue,prefix,till,dept,product_name,qty,rev_ex_gst,rev_inc_gst,
    cost,cost_source,reconciled

  qty         unit counts. A 5-piece arancini serve is 1, not 5.
  rev_ex_gst  ex-GST, on the basis the source day settled on.
  till        the POS the line was rung on — 'stow' lines can belong to hg/mari
              (single-till model); attribution is already applied to `venue`.
  dept        f = Kitchen, b = Bar/FOH, as classify_product() decided.
  reconciled  false = that day's mix does not sum to that day's revenue.
              DO NOT deduct stock from those rows until it is explained.

Product names are written VERBATIM. Size variants ("- Pint", "- Regular") stay
separate rows on purpose: a recipe is per SKU, and collapsing them here would
deduct a pint of beer for a schooner sold. build_products_weekly.py collapses
them because that file feeds a human trends view; this one feeds arithmetic.

Run: python3 scripts/build_products_daily.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
MIX_DIR = ROOT / "data" / "product_mix"
OUT = ROOT / "data" / "products_daily.csv"

COLUMNS = ["date", "venue", "prefix", "till", "dept", "product_name", "qty",
           "rev_ex_gst", "rev_inc_gst", "cost", "cost_source", "reconciled"]

VENUE_CODE = {"stowaway": "stow", "harry": "hg", "marilynas": "mari"}


def build() -> int:
    if not MIX_DIR.exists():
        raise SystemExit(f"missing {MIX_DIR} — run daily_aggregator.py first")

    files = sorted(MIX_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"no mix files in {MIX_DIR}")

    rows: list[dict] = []
    unreconciled: list[str] = []

    for f in files:
        with f.open() as fh:
            doc = json.load(fh)

        if doc.get("schema") != "product_mix/1":
            print(f"  skipping {f.name}: unknown schema {doc.get('schema')!r}")
            continue
        if doc.get("truncated"):
            # Belt and braces. A truncated mix must never reach a deduction.
            print(f"  *** SKIPPING {f.name}: marked truncated.")
            continue

        day = doc["date"]
        venue = VENUE_CODE.get(doc["venue"], doc["venue"])
        reconciled = bool(doc.get("reconciled"))
        if not reconciled:
            unreconciled.append(f"{venue} {day}")

        for p in doc["products"]:
            rows.append({
                "date": day,
                "venue": venue,
                "prefix": doc["prefix"],
                "till": p.get("till", doc["prefix"]),
                "dept": p.get("dept", ""),
                "product_name": p["name"],
                "qty": p["qty"],
                "rev_ex_gst": p["rev_ex"],
                "rev_inc_gst": p["rev_inc"],
                "cost": p.get("cost", ""),
                "cost_source": p.get("cost_source", ""),
                "reconciled": "true" if reconciled else "false",
            })

    rows.sort(key=lambda r: (r["date"], r["venue"], -float(r["rev_ex_gst"] or 0),
                             r["product_name"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    days = {(r["date"], r["venue"]) for r in rows}
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows):,} lines, "
          f"{len(days):,} venue-days, {len({r['date'] for r in rows}):,} dates "
          f"({min(r['date'] for r in rows)} .. {max(r['date'] for r in rows)})")

    if unreconciled:
        # Loud, not fatal: the rows are still written and flagged, so a stock
        # consumer can exclude them. Silence here would be the flattering error.
        print(f"*** {len(unreconciled)} venue-day(s) DID NOT RECONCILE and are "
              f"marked reconciled=false:")
        for d in unreconciled[:20]:
            print(f"      {d}")
        if len(unreconciled) > 20:
            print(f"      ... and {len(unreconciled) - 20} more")

    return len(rows)


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
