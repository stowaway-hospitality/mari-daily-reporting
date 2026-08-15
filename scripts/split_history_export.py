#!/usr/bin/env python3
"""Split the pulled Lightspeed daily history into per-day product exports.

WHERE THE INPUT COMES FROM: the Lightspeed report endpoint
`my.kounta.com/report/salesummarybyproduct` answers one day at a time
(`DateFrom == DateTo`, `SiteID=0` = the full Stowaway site). Pulled day by day
from a logged-in browser session for 2024-10-23 .. 2026-08-14 and concatenated
with a leading Date column. WORKING_HERE.md already documents that endpoint as
the way to recover a day whose export email never fired; this is the same call,
610 times.

WHY A SEPARATE DIRECTORY. These land in `data/insights_history/`, NOT in
`data/insights_<prefix>_<date>.csv`. `build_products_weekly.py` prefers a daily
file over its Looker weekly backfill for any week a daily file covers — so
dropping two years of daily exports into the normal path would silently restate
the published Products view. The product-mix backfill reads these explicitly via
`daily_aggregator.py --insights-file`, and nothing else sees them.

TRUST, MEASURED. 40 of these days are also held as committed exports. 38
reproduce exactly. The 2 that don't are the COMMITTED files being wrong — each
is a to-the-cent duplicate of an earlier day (see the verify script):
    insights_stow_2026-08-10.csv holds 2026-08-03's data
    insights_stow_2026-08-13.csv holds 2026-08-11's data

WHAT THIS DATA CANNOT TELL YOU. The endpoint joins to the CURRENT product
master, so a SKU renamed in place carries its new name on old sales. Deleted
products keep their own names (371 of 1,515 products here are no longer in the
master and kept theirs). Product name is the recipe join key, so old sales of
renamed SKUs will deduct today's recipe. Mix files from this source are stamped
`name_basis: current_master`.

Run: python3 scripts/split_history_export.py <history.csv>
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
OUT_DIR = ROOT / "data" / "insights_history"

# The endpoint's column order, minus the Date column this script strips back out.
COLUMNS = ["Position", "Product Number", "Product", "Quantity",
           "Percent of Quantity", "Sale Amount", "Percent of Sale Amount",
           "Cost", "Percent of Gross Profit"]


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1]).expanduser()
    if not src.exists():
        raise SystemExit(f"missing {src}")

    by_day: dict[str, list[dict]] = defaultdict(list)
    with src.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ["Date"] + COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            # Refuse rather than write empty days: an unreadable export must be a
            # loud failure, exactly as build_products_weekly.py insists.
            raise SystemExit(
                f"unrecognised history schema — missing {missing}\n"
                f"  got: {reader.fieldnames}")
        for r in reader:
            if not (r.get("Product") or "").strip():
                continue          # footer / blank
            by_day[r["Date"]].append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for day, rows in sorted(by_day.items()):
        out = OUT_DIR / f"stow_{day}.csv"
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        written += 1

    days = sorted(by_day)
    print(f"wrote {written:,} day files to {OUT_DIR.relative_to(ROOT)} "
          f"({days[0]} .. {days[-1]}, {sum(len(v) for v in by_day.values()):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
