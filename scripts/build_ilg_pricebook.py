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
The page header the data lines actually sit under reads:

    CODE | DESCRIPTION | SIZE | PCK QTY | MIN ORD | GROUP NB | UC EX GST | SUGG RRP | GP%

(The `Ctn | Case | Book Price` header also in the PDF belongs to the 3-Case-Mix
tables, not to these rows. Reading THAT header onto THESE columns is what made
group(6) look like a case count; it is the minimum order quantity.)

U.C. is the per-unit book price and is the column to use. It is stated on the
same basis as our cost book: over the 32 products where a Back Office cost and a
price-book line agree on BOTH name and size, the median cost/U.C. ratio is
1.019. (Measured against invoice-derived per-bottle costs instead it comes out
at 1.116 — that comparison is contaminated by the case-vs-single ambiguity and
by the premium ILG charge for breaking a carton. The name+size one is clean.)

...BUT U.C. IS PER SELLING UNIT, AND A SELLING UNIT IS NOT ALWAYS AN ITEM
------------------------------------------------------------------------
`size_ml` is the size of ONE ITEM. `book_price_unit` is the price of one
SELLING UNIT, and for 1,334 of the 6,156 rows (21.7%) a selling unit is a
multipack, so the two do not describe the same thing:

    110-668-0  4 Pines Hazy Pale Cans 4pk  375ml  PCK QTY 24  $79.67  U.C. $13.28
    115-376-2  Corona Mexican 6pk          355ml  PCK QTY 24  $51.55  U.C. $12.89
    175-042-0  Antica Formula              1lt    PCK QTY  6  $401.45 U.C. $66.91

79.67/13.28 = 6 four-packs; 51.55/12.89 = 4 six-packs; 401.45/66.91 = 6 bottles.
So the denominator is 4, 6 and 1 items respectively, and NOTHING in the file
said so — the only place it existed was the "4pk" in a free-text description.

That matters because this CSV is the API contract: it is what audit_book's
price floor, the seed-pack cross-check in build_costs, and any future consumer
read. A price with an unstated denominator is not a price, and the failure is
silent and flattering — treat a $13.28 four-pack as a $13.28 can and every
comparison against it says our cost is fine.

`units_per_selling_unit` states it. It is proved, never assumed: the carton
price must divide by the unit price to within 1% of a whole number AND that
whole number must divide PCK QTY exactly. It does on 6,156 of 6,156 rows today;
where it ever does not, the column is left EMPTY rather than guessed, and a
consumer that needs it must skip the row.

SCHEMA IS ADDITIVE-ONLY. `min_order` and `units_per_selling_unit` are APPENDED;
the eight existing columns keep their names, order and values byte for byte, so
a live app or a stale browser tab reading this feed cannot notice.

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
#  code       description                size    pck min group      U.C.    RRP
#                                                qty ord nb         ex gst  sugg
LINE = re.compile(
    r"^\s*(\d{3}-\d{3}-\d)\s+"          # code
    r"(.{4,50}?)\s{2,}"                 # description (2+ spaces ends it)
    r"(\d+(?:\.\d+)?)\s*(ml|lt|l)\s+"   # size + unit
    r"(\d+)\s+(\d+)\s+"                 # PCK QTY (items per carton), MIN ORD
    r"\$([\d,]+\.\d\d)\s+"              # book price per carton (GROUP NB)
    r"\$([\d,]+\.\d\d)"                 # book price per selling unit (UC EX GST)
    r"(?:\s+\$([\d,]+\.\d\d))?",        # SUGG RRP, when printed
    re.I)
MONTH = re.compile(r"\((\w{3}\s+20\d\d)\)")

# APPEND ONLY. The first eight are the published contract; the last two are new.
FIELDS = ["code", "description", "size_ml", "units_per_carton",
          "book_price_case", "book_price_unit", "rrp", "book_month",
          "min_order", "units_per_selling_unit"]


def _n(s):
    return float(s.replace(",", ""))


def units_per_selling_unit(units_per_carton: int, carton_price: float,
                           unit_price: float):
    """How many ITEMS does one `book_price_unit` buy?  -> int | None

    PROVED, NOT ASSUMED, and by the same propose-then-prove shape the ILG
    invoice parser uses. Two conditions must both hold:

      * carton_price / unit_price must land within 1% of a whole number — that
        whole number is how many selling units the carton splits into;
      * it must divide PCK QTY exactly — 24 cans cannot come as 5 packs.

    Antica: 401.45/66.91 = 6.00 selling units, 6/6 = 1 item each (a bottle).
    Corona 6pk: 51.55/12.89 = 4.00 selling units, 24/4 = 6 items each.

    None where either test fails. An unproved denominator is left blank because
    the wrong one is silent and flattering: read a $13.28 four-pack as a $13.28
    can and every cost compared against it looks fine.
    """
    if not (units_per_carton > 0 and carton_price > 0 and unit_price > 0):
        return None
    per_carton = carton_price / unit_price
    n = round(per_carton)
    if n < 1 or abs(per_carton - n) > 0.01 * per_carton:
        return None
    if units_per_carton % n:
        return None
    return units_per_carton // n


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
        ups = units_per_selling_unit(int(m.group(5)), _n(m.group(7)), _n(m.group(8)))
        out.append({
            "code": code,
            "description": m.group(2).strip(),
            "size_ml": f"{size:g}",
            "units_per_carton": m.group(5),
            "book_price_case": f"{_n(m.group(7)):.2f}",
            "book_price_unit": f"{_n(m.group(8)):.2f}",
            "rrp": f"{_n(m.group(9)):.2f}" if m.group(9) else "",
            "book_month": month,
            "min_order": m.group(6),
            "units_per_selling_unit": str(ups) if ups else "",
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
