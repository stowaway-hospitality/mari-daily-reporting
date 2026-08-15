#!/usr/bin/env python3
"""Find SKUs that were RENAMED IN PLACE, so the ledger can tell a re-label from
a different dish.

THE PROBLEM. The Lightspeed report endpoint joins to the CURRENT product master,
so the daily history pull (data/insights_history/) carries today's names on old
sales. Two very different things look identical in that data:

  * "Jala Marg Duo (2) - PartyJar [6 serves]" -> "Jala Marg PartyJar [6 serves]"
    SAME product, tidier name. History is fine as-is.
  * "Bread & Butter Pud" -> "Apple Crumble"
    DIFFERENT dish. The SKU was reused so nobody had to build a new product.
    Old pudding sales now read as Apple Crumble, and a stock ledger would
    deduct apple, flour and oats for puddings that were actually served.

Only a human knows which is which, so this script does not decide. It finds the
candidates and writes them to data/product_renames.yaml with
`identity: unreviewed`, for Zak to mark `same_product` or `different_product`.

HOW CANDIDATES ARE FOUND. data/products_weekly.csv carries names as at its own
export (the Looker backfill, plus as-at-sale names from the daily feed). The
history pull carries names as at today. Roll both to Mon-Sun weeks per venue and
a rename shows up as: one name that only the old source has, one name that only
the new source has, in the same week, with the SAME quantity and the same
revenue. Same underlying till rows, two labels.

That match is deliberately strict. A coincidence would need two different
products to sell identical units for identical money in the same week at the
same venue.

WHAT IT CANNOT SEE. A rename that happened before products_weekly.csv's own
export date is already baked into both sources and is invisible here. That is a
floor on what can be known, not a bug — record it and move on.

Run: python3 scripts/detect_product_renames.py [--write]
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_products_weekly import normalize_product          # noqa: E402

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
WEEKLY = DATA / "products_weekly.csv"
DAILY_DIR = DATA / "products_daily"
HISTORY_DIR = DATA / "insights_history"
OUT = DATA / "product_renames.yaml"

QTY_TOLERANCE = 0.001
REV_TOLERANCE_PCT = 1.0


def week_ending(d: date) -> str:
    return (d + timedelta(days=(6 - d.weekday()))).isoformat()


def load_weekly() -> dict[tuple[str, str, str], list[float]]:
    """(week, venue, normalised name) -> [sales_ex, qty] as at the OLD export."""
    agg: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    with WEEKLY.open() as f:
        for r in csv.DictReader(f):
            k = (r["week_ending"], r["venue"], normalize_product(r["product_name"]))
            agg[k][0] += float(r.get("sales_ex_gst") or 0)
            agg[k][1] += float(r.get("qty") or 0)
    return agg


def load_daily() -> dict[tuple[str, str, str], list[float]]:
    """Same shape, from data/insights_history/ — names as at TODAY.

    Read the RAW history pull, not the mix rollup. The rollup prefers a
    committed export wherever one exists, so for the 40 recent days it carries
    as-at-sale names — the same names as the old side, which makes every recent
    rename invisible. The whole point is to diff the two name eras, so the new
    side has to be the re-fetch.

    The pull is the whole Stow site, so venue attribution isn't applied here.
    Compare against every venue's weekly rows and let the qty+revenue match
    decide; a rename is a rename whichever venue's slice it sits in.
    """
    agg: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for path in sorted(HISTORY_DIR.glob("stow_*.csv")):
        day = date.fromisoformat(path.stem[len("stow_"):])
        w = week_ending(day)
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("Product") or "").strip()
                if not name:
                    continue
                k = (w, "site", normalize_product(name))
                agg[k][0] += float(r.get("Sale Amount") or 0) / 1.1
                agg[k][1] += float(r.get("Quantity") or 0)
    return agg


def main() -> int:
    old, new = load_weekly(), load_daily()

    weeks = sorted({k[0] for k in old} & {k[0] for k in new})
    # Drop the boundary week. The pull ends mid-week, so the last week is a
    # partial on one side and whole on the other, and the qty+revenue matcher
    # happily pairs whatever leftovers remain — it produced
    # "Carafe Sparkling Water -> Cranberry Juice Glass" ($5, 1 unit) that way.
    # A rename detector that invents renames is worse than none.
    covered = {week_ending(date.fromisoformat(p.stem[len("stow_"):]))
               for p in HISTORY_DIR.glob("stow_*.csv")}
    last_full = max((w for w in covered
                     if sum(1 for p in HISTORY_DIR.glob("stow_*.csv")
                            if week_ending(date.fromisoformat(p.stem[len("stow_"):])) == w) >= 6),
                    default=max(covered, default=""))
    weeks = [w for w in weeks if w <= last_full]
    by_week_old, by_week_new = defaultdict(dict), defaultdict(dict)
    # The old side is split by venue; the pull is the whole site. Collapse the
    # old side to the same site-level total so like is compared with like.
    for (w, _v, n), val in old.items():
        slot = by_week_old[(w, "site")].setdefault(n, [0.0, 0.0])
        slot[0] += val[0]
        slot[1] += val[1]
    for (w, v, n), val in new.items():
        by_week_new[(w, v)][n] = val

    # candidate pair -> evidence
    pairs: dict[tuple[str, str, str], list] = defaultdict(
        lambda: [0, None, None, 0.0, 0.0])       # [weeks, first, last, qty, rev]

    for w in weeks:
        for venue in ("site",):
            o, n = by_week_old.get((w, venue), {}), by_week_new.get((w, venue), {})
            if not o or not n:
                continue
            only_old = {k: v for k, v in o.items() if k not in n}
            only_new = {k: v for k, v in n.items() if k not in o}
            if not only_old or not only_new:
                continue
            used = set()
            for oname, (orev, oqty) in sorted(only_old.items()):
                if not oqty:
                    continue
                for nname, (nrev, nqty) in sorted(only_new.items()):
                    if nname in used:
                        continue
                    if abs(oqty - nqty) > QTY_TOLERANCE:
                        continue
                    if orev and abs(orev - nrev) / abs(orev) * 100 > REV_TOLERANCE_PCT:
                        continue
                    used.add(nname)
                    e = pairs[(venue, oname, nname)]
                    e[0] += 1
                    e[1] = min(e[1] or w, w)
                    e[2] = max(e[2] or w, w)
                    e[3] += oqty
                    e[4] += orev
                    break

    ranked = sorted(pairs.items(), key=lambda kv: -kv[1][4])
    print(f"{len(ranked)} candidate rename(s) across {len(weeks)} overlapping weeks\n")
    print(f"{'venue':6} {'weeks':>5} {'qty':>8} {'rev ex':>11}  old name -> new name")
    for (venue, oname, nname), (nweeks, first, last, qty, rev) in ranked:
        print(f"{venue:6} {nweeks:5d} {qty:8.0f} {rev:11,.0f}  {oname}  ->  {nname}")
        print(f"{'':6} {'':5} {'':8} {'':11}  seen {first} .. {last}")

    if "--write" in sys.argv:
        lines = [
            "# Products RENAMED IN PLACE in Lightspeed.",
            "#",
            "# The daily history pull carries TODAY's product names on OLD sales, because",
            "# the report endpoint joins to the current product master. This file is how we",
            "# tell the two kinds of rename apart. Nothing may be deducted from a history",
            "# day for a product still marked `unreviewed`.",
            "#",
            "#   same_product       cosmetic re-label. The history is correct as it stands.",
            "#   different_product  the SKU was REUSED for a different dish. Sales before",
            "#                      `changed_on` are the OLD product and must not inherit",
            "#                      the new product's recipe.",
            "#",
            "# `changed_on` is only needed for different_product: it is the first date the",
            "# NEW dish was actually sold. Everything before it gets relabelled to old_name.",
            "#",
            "# Generated by scripts/detect_product_renames.py — regenerate to find new",
            "# candidates; adjudications already recorded here are preserved by hand.",
            "",
            "renames:",
        ]
        existing = OUT.read_text() if OUT.exists() else ""
        for (venue, oname, nname), (nweeks, first, last, qty, rev) in ranked:
            lines += [
                f"  - venue: {venue}",
                f"    old_name: {oname!r}",
                f"    new_name: {nname!r}",
                f"    identity: unreviewed        # same_product | different_product",
                f"    changed_on:                 # required if different_product",
                f"    evidence:",
                f"      weeks_matched: {nweeks}",
                f"      first_week: {first}",
                f"      last_week: {last}",
                f"      qty: {qty:.0f}",
                f"      revenue_ex_gst: {rev:.2f}",
                "",
            ]
        if existing:
            print(f"\n{OUT.name} already exists — writing {OUT.name}.new so hand "
                  f"adjudications are not clobbered")
            OUT.with_suffix(".yaml.new").write_text("\n".join(lines))
        else:
            OUT.write_text("\n".join(lines))
            print(f"\nwrote {OUT.relative_to(ROOT)} — every entry is `unreviewed`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
