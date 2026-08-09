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


def findings(rows):
    groups = defaultdict(list)
    for r in rows:
        rate = _d(r.get("cost_per_base_unit"))
        if rate is None:
            continue
        groups[(r.get("supplier", ""), r.get("supplier_code", ""),
                (r.get("pack_unit") or "").strip())].append((rate, r))

    out = []
    for (sup, code, unit), members in sorted(groups.items()):
        if len(members) < MIN_GROUP:
            continue
        med = _median([m[0] for m in members])
        if med <= 0:
            continue
        for rate, r in members:
            ratio = rate / med if rate > med else med / rate
            for f in PACK_FACTORS:
                if abs(ratio - f) <= f * TOL:
                    out.append({
                        "supplier": sup, "code": code, "unit": unit,
                        "invoice": r.get("source_invoice", ""),
                        "date": r.get("invoice_date", ""),
                        "description": (r.get("invoice_description") or "")[:38],
                        "rate": rate, "median": med, "factor": f,
                        "direction": "UNDER" if rate < med else "OVER",
                    })
                    break
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
    if not found:
        print(f"pack agreement: ok — no line sits on a whole pack factor of its "
              f"own code's median ({len(rows)} rows)")
        return 0

    under = [f for f in found if f["direction"] == "UNDER"]
    print(f"pack agreement: {len(found)} line(s) sit on an exact pack multiple of "
          f"their own code's median — {len(under)} UNDER (flatters GP), "
          f"{len(found) - len(under)} OVER")
    for f in sorted(found, key=lambda x: (x["direction"] != "UNDER", x["supplier"], x["code"])):
        print(f"  {f['direction']:<5} {f['factor']:>4}x  {f['supplier']:<10} {f['code']:<12} "
              f"{f['description']:<38} {f['invoice']} {f['date']}")
        print(f"            {f['rate']} vs median {f['median']} per {f['unit'] or 'unit'}")
    return 1 if a.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
