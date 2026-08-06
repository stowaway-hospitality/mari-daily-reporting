#!/usr/bin/env python3
"""
data/invoice_corpus/ilg_pricebook.pdf  ->  data/ilg_pricebook.csv

    python3 scripts/build_ilg_pricebook.py

WHY THIS EXISTS
---------------
ILG send a price list. It is the only INDEPENDENT price we have for a bottle:
Back Office costs are typed by a human, Lightspeed's recipe costs are derived by
Lightspeed, and an invoice line has to survive a pack-size reading before it
means anything. The price book is a supplier telling us, in writing, what a
bottle costs.

It was already load-bearing and nobody could see it. Eighteen costs in
cogs_list.csv say "ILG pricebook 360-269-4" in their note — someone opened the
PDF, read a number, and typed it in. That number could never be re-derived,
re-checked, or used by anything else, because it lived in 7.9 MB of gitignored
binary.

Extracting it changes what the rest of the system can do:

  - a bottle with no invoice can be seeded from the supplier's own price
    instead of staying at $0.00 (which reads as 100% GP, the flattering
    direction);
  - a cost can be CHECKED against it — audit_book reports any bottle costed
    materially below what ILG publishes, which is how the Havana Club seed at
    $29.09 was finally shown to be impossible against a book price of $49.20;
  - a case-vs-single pack reading has a third opinion, so the tie-breaker is no
    longer the number under suspicion.

THE COLUMNS
-----------
    Code | Description | Size | Ctn | Case | Book Price Case | Book Price U.C. | RRP | ...

U.C. is the per-unit book price and is the column to use. It is stated on the
same basis as our cost book: over the 32 products where a Back Office cost and a
price-book line agree on BOTH name and size, the median cost/U.C. ratio is
1.019. (Measured against invoice-derived per-bottle costs instead it comes out
at 1.116 — that comparison is contaminated by the case-vs-single ambiguity and
by the premium ILG charge for breaking a carton. The name+size one is clean.)

THE PDF IS NOT COMMITTED, THE CSV IS
------------------------------------
data/invoice_corpus/ is gitignored — it is 100+ MB of supplier PDFs. So this
script is a one-way door: run it on a machine that has the PDF, commit the CSV,
and everything downstream reads the CSV. If the PDF is absent this exits 0 and
changes nothing, so it is safe in any pipeline. When a new book arrives, drop it
in and re-run; the CSV carries the book's own month so a stale one is visible.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "invoice_corpus" / "ilg_pricebook.pdf"
OUT = ROOT / "data" / "ilg_pricebook.csv"

# "360-126-7  Milagro Reposado Tequila   700ml   6   1   $399.93   $66.66  $84.99"
#  code       description                size    ctn cs  case      U.C.    RRP
LINE = re.compile(
    r"^\s*(\d{3}-\d{3}-\d)\s+"          # code
    r"(.{4,50}?)\s{2,}"                 # description (2+ spaces ends it)
    r"(\d+(?:\.\d+)?)\s*(ml|lt|l)\s+"   # size + unit
    r"(\d+)\s+(\d+)\s+"                 # ctn, case
    r"\$([\d,]+\.\d\d)\s+"              # book price per case
    r"\$([\d,]+\.\d\d)"                 # book price per unit (U.C.)
    r"(?:\s+\$([\d,]+\.\d\d))?",        # RRP, when printed
    re.I)
MONTH = re.compile(r"\((\w{3}\s+20\d\d)\)")

FIELDS = ["code", "description", "size_ml", "units_per_carton",
          "book_price_case", "book_price_unit", "rrp", "book_month"]


def _n(s):
    return float(s.replace(",", ""))


def parse(text: str) -> list[dict]:
    month = ""
    m = MONTH.search(text)
    if m:
        month = m.group(1).upper().replace("  ", " ")
    seen, out = set(), []
    for ln in text.splitlines():
        m = LINE.match(ln)
        if not m:
            continue
        code = m.group(1)
        if code in seen:                # the Top Sellers table repeats codes
            continue
        seen.add(code)
        size = float(m.group(3)) * (1000.0 if m.group(4).lower() in ("lt", "l") else 1.0)
        out.append({
            "code": code,
            "description": m.group(2).strip(),
            "size_ml": f"{size:g}",
            "units_per_carton": m.group(5),
            "book_price_case": f"{_n(m.group(7)):.2f}",
            "book_price_unit": f"{_n(m.group(8)):.2f}",
            "rrp": f"{_n(m.group(9)):.2f}" if m.group(9) else "",
            "book_month": month,
        })
    return out


def main() -> int:
    if not PDF.exists():
        print(f"no {PDF.relative_to(ROOT)} — nothing to do (the corpus is gitignored)")
        return 0
    try:
        import subprocess
        r = subprocess.run(["pdftotext", "-layout", str(PDF), "-"],
                           capture_output=True, text=True)
        text = r.stdout
    except FileNotFoundError:
        print("pdftotext not found — install poppler-utils")
        return 2
    rows = parse(text)
    if not rows:
        print("parsed 0 rows — the price book layout changed; NOT overwriting")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    month = rows[0]["book_month"] or "unknown month"
    print(f"{len(rows)} products ({month}) -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
