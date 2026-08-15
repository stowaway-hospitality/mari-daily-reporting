#!/usr/bin/env python3
"""Build data/products_daily/<year>.csv — the full daily product mix, every
venue, every day, for the stock ledger to read.

SHARDED BY YEAR ON PURPOSE. This started as one file. At two years of history
it is 133,907 lines / 11 MB, and `daily_pull.yml` does `git add data/` every
morning — so a single file would commit a fresh 11 MB blob DAILY and grow the
repo by roughly a gigabyte a year to add ~600 rows. Sharded by year, only the
current year's file changes; 2024 and 2025 are written once and never touched
again. Read them with `glob("data/products_daily/*.csv")`.

Source: data/product_mix/<prefix>_<date>.json, written by daily_aggregator.py.
Those files are the per-day facts; this is the derived rollup, regenerated
whole on every run (idempotent, so it can never fossilise).

WHY A ROLLUP AND NOT JUST THE FILES: a ledger rebuild reads every day at once.
1,250 per-day files is 1,250 opens per rebuild; this is three.

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
OUT_DIR = ROOT / "data" / "products_daily"

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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_year: dict[str, list[dict]] = {}
    for r in rows:
        by_year.setdefault(r["date"][:4], []).append(r)

    for year, yrows in sorted(by_year.items()):
        out = OUT_DIR / f"{year}.csv"
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(yrows)
        print(f"  {out.relative_to(ROOT)}: {len(yrows):,} lines, "
              f"{len({(r['date'], r['venue']) for r in yrows}):,} venue-days")

    days = {(r["date"], r["venue"]) for r in rows}
    print(f"wrote {len(by_year)} year file(s): {len(rows):,} lines, "
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
