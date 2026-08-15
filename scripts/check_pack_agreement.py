#!/usr/bin/env python3
"""
Find lines where a price and its pack disagree — the ILG case/bottle bug, looked
for everywhere instead of one supplier at a time.

    python3 scripts/check_pack_agreement.py            # report
    python3 scripts/check_pack_agreement.py --strict   # exit 1 on any finding

THE SIGNATURE. A pack misread does not move a cost by a plausible-looking
amount; it moves it by a WHOLE PACK FACTOR. The same supplier code, delivered
week after week at a steady rate, suddenly reads 6x or 12x or 24x off on one
invoice — because one line divided by a bottle where its neighbours divided by a
case, or the reverse. Ordinary price movement is a few percent and never lands
on 6.000. That gap is what makes this checkable without knowing the true price.

So: group every invoice-derived cost row by (supplier, code), take the MEDIAN
rate as the group's own opinion of itself, and report any member sitting within
1% of an exact pack multiple of it. Refusing to guess the truth, and refusing to
average it away — just naming the outliers and the factor they are out by.

WHY THE MEDIAN AND NOT THE MEAN: when the ILG history was wrong it was wrong
TOGETHER, and a mean would have been dragged with it. A median survives a
minority of bad rows, which is the situation this is for.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COGS = ROOT / "data" / "cogs_list.csv"

# The pack factors a carton actually comes in. A misread lands on one of these;
# ordinary price drift does not. (Not a magic list — these are the multipliers
# printed on the suppliers' own Pack columns.)
PACK_FACTORS = [Decimal(n) for n in (2, 3, 4, 6, 8, 10, 12, 15, 16, 20, 24, 30, 48)]
TOL = Decimal("0.01")          # within 1% of an exact factor
MIN_GROUP = 3                  # need a few deliveries before a median means anything


def _d(s):
    try:
        v = Decimal((s or "").strip())
        return v if v > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _outlier_in_time(members, when, med) -> bool:
    """Is this rate an OUTLIER, or just the price before it changed?

    ONE definition, used by both passes. A pack misread is one delivery sitting
    apart from the deliveries either side of it. A price change is not: it takes
    effect on a date and everything after it is the new price. So a rate only
    counts when the median rate is observed BOTH BEFORE AND AFTER it.

    This lived only in pass 2 until 2026-08-14, and pass 1 went red on the very
    case pass 2's docstring already named as real: Fresh Fruit Team coriander at
    $15.40 on 1 and 4 May against a $7.70 median — exactly 2.00x, and exactly a
    market line moving. $7.70 does not appear anywhere before those dates, so
    there is nothing for the $15.40 to be an outlier FROM. Two passes disagreeing
    about the same rule is worse than either answer.

    members: (rate, when, ...) tuples. `when` is a date string; ordering is
    lexicographic, which is correct for ISO dates and is what both callers hold.
    """
    near = lambda v: abs(v - med) <= med * TOL           # noqa: E731
    before = any(near(m[0]) for m in members if m[1] and m[1] < when)
    after = any(near(m[0]) for m in members if m[1] and m[1] > when)
    return bool(before and after)


def _scan(groups, field, label):
    out = []
    for key, members in sorted(groups.items()):
        if len(members) < MIN_GROUP:
            continue
        med = _median([m[0] for m in members])
        if med <= 0:
            continue
        for rate, when, r in members:
            ratio = rate / med if rate > med else med / rate
            for f in PACK_FACTORS:
                if abs(ratio - f) <= f * TOL:
                    if not _outlier_in_time(members, when, med):
                        break            # a regime change, not an outlier
                    out.append({
                        "supplier": key[0], "code": key[1],
                        "unit": (r.get("pack_unit") or "").strip(),
                        "invoice": r.get("source_invoice", ""),
                        "date": r.get("invoice_date", ""),
                        "description": (r.get("invoice_description") or "")[:38],
                        "rate": rate, "median": med, "factor": f, "on": label,
                        "direction": "UNDER" if rate < med else "OVER",
                    })
                    break
    return out


def findings(rows):
    """Two passes, because a pack misread often changes the UNIT as well.

    PASS 1 — the canonical rate, grouped by (supplier, code, pack_unit). This is
    the like-for-like comparison and it is the one that catches a bottle priced
    as a case in $/L.

    PASS 2 — see book_findings(). Pass 1 has a blind spot exactly where the bug
    is worst: Foodlink BEANS BLACK WHOLE TIN A10 is $8.70 "ea" twice and $52.20
    "box" once — one carton of six booked as one tin — and grouping by pack_unit
    filed the outlier apart from the very siblings that prove it wrong, leaving
    each group under MIN_GROUP and invisible. When the unit moves WITH the price,
    holding the unit constant hides the evidence.
    """
    by_rate = defaultdict(list)
    for r in rows:
        rate = _d(r.get("cost_per_base_unit"))
        if rate is not None:
            by_rate[(r.get("supplier", ""), r.get("supplier_code", ""),
                     (r.get("pack_unit") or "").strip())].append(
                (rate, r.get("invoice_date", ""), r))
    return _scan(by_rate, "cost_per_base_unit", "rate")


def book_findings(rows):
    """PASS 2 — the same test against data/costs.csv, THE BOOK ITSELF.

    Pass 1 reads what the invoice said. This reads what a recipe actually costs
    off, which is the number that matters and is not always the same thing: the
    supplier's raw price legitimately differs when it bills one code two ways,
    and the book is expected to reconcile that. Foodlink 100487 camembert is
    $3.80 a 125 g piece and $45.60 a CTN-12 — a 12x spread on the invoice, and
    $0.0304/g in the book both times, which is correct and must not be reported.

    So this compares the BOOK's per-unit rate, in the book's own unit, per
    ingredient — and it caught what pass 1 could not:

        foodlink:100175  BEANS BLACK WHOLE TIN A10
            2026-04-23  $0.0029/g
            2026-05-08  $0.0174/g   <- 6x, a CTN-6 carton divided by one tin
            2026-07-24  $0.0029/g

    AN OUTLIER IN TIME, NOT A REGIME. A pack misread is one delivery sitting
    apart from the deliveries either side of it. A price change is not: it takes
    effect on a date and everything after it is the new price. Fresh Fruit Team
    coriander went $15.40 to $7.70 at the end of May and stayed there — exactly
    2.00x, and entirely real, herbs being a market line. So a rate is only
    reported when the median rate is observed BOTH BEFORE AND AFTER it. That one
    condition is what separates a misread from a market.
    """
    by_ing = defaultdict(list)
    for r in rows:
        rate = _d(r.get("cost_per_unit"))
        if rate is not None:
            by_ing[(r.get("ingredient", ""), (r.get("unit") or "").strip())].append(
                (rate, r.get("observed_on", ""), r))

    out = []
    for (ing, unit), members in sorted(by_ing.items()):
        if len(members) < MIN_GROUP:
            continue
        med = _median([m[0] for m in members])
        if med <= 0:
            continue
        for rate, when, r in members:
            ratio = rate / med if rate > med else med / rate
            hit = next((f for f in PACK_FACTORS if abs(ratio - f) <= f * TOL), None)
            if hit is None:
                continue
            if not _outlier_in_time(members, when, med):
                continue                 # a regime change, not an outlier
            out.append({
                "supplier": ing.split(":")[0], "code": ing, "unit": unit,
                "invoice": r.get("source_invoice", ""), "date": when,
                "description": (r.get("description") or "")[:38],
                "rate": rate, "median": med, "factor": hit, "on": "book",
                "direction": "UNDER" if rate < med else "OVER",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if anything is found (for CI)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    found = findings(rows)
    book = ROOT / "data" / "costs.csv"
    if book.exists():
        found += book_findings(list(csv.DictReader(book.open(encoding="utf-8-sig"))))
    if not found:
        print(f"pack agreement: ok — no line sits on a whole pack factor of its "
              f"own code's median ({len(rows)} rows)")
        return 0

    under = [f for f in found if f["direction"] == "UNDER"]
    print(f"pack agreement: {len(found)} line(s) sit on an exact pack multiple of "
          f"their own code's median — {len(under)} UNDER (flatters GP), "
          f"{len(found) - len(under)} OVER")
    for f in sorted(found, key=lambda x: (x["direction"] != "UNDER", x["supplier"], x["code"])):
        print(f"  {f['direction']:<5} {f['factor']:>4}x  [{f['on']}] {f['supplier']:<10} {f['code']:<12} "
              f"{f['description']:<38} {f['invoice']} {f['date']}")
        print(f"            {f['rate']} vs median {f['median']} per {f['unit'] or 'unit'}")
    return 1 if a.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
