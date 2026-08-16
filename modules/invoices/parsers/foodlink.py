"""
Foodlink Australia — deterministic parser (coordinate-based).

Columns:  No. | Description | Qty. | UOM | Weight | Unit Price Excl GST | GST | Total Amount Excl GST
Line amounts are EX-GST; a "GST" token in the GST column marks a taxable line,
so its incl total is ex x 1.1 (GST-free lines are unchanged). Fuel Levy is a
taxable line but classed EXTRA. Reconcile target: "Total AUD Incl GST".
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

# FALLBACK ONLY — the 2026-04-era x-positions. Kept so a header we cannot read
# still parses the old layout, but `_cols_from_header` below is the live path.
#
# 2026-08-15: Foodlink shifted its table right by ~25pt (Qty. 251 -> 277,
# UOM 272 -> 306, Weight 327 -> 345). With these constants the Qty. VALUE at
# x=290 fell past the uom boundary (270), so c["qty"] came back empty, every
# line was skipped by the `qty is None` guard, and the parser raised "no line
# items parsed" on EVERY invoice from 2026-07-29 onward — 10 invoices, silently
# stuck in Review. Hard-coded pixel columns are a latent time-bomb for any
# supplier that re-templates, so the boundaries are now DERIVED from the header
# row's own word positions and these numbers are only the safety net.
COLS = [("code", 0), ("desc", 60), ("qty", 245), ("uom", 270), ("weight", 320),
        ("price", 360), ("gstflag", 455), ("total", 490)]
MONEY = re.compile(r"-?\d[\d,]*\.?\d*")   # search, not full-match: weight-priced
                                          # lines show price as "23.00/kg"
EXTRA_DESC = re.compile(r"fuel\s*levy|freight|delivery|cartage", re.I)

# A Foodlink description WRAPS onto its own row, and that row carries no qty and
# no total — so the `qty is None` guard below silently DROPPED it. Measured on
# the corpus: 435 wrapped rows across 129 invoices, i.e. two thirds of all line
# items lost text. It never showed in the PASS column because a description
# plays no part in reconciliation (the same blind spot as FFT's 52/52 and
# Gulli's 31/32 — see the TRIAGE LOG in parser_regression.py).
#
# It is not cosmetic here. Foodlink's UOM column is only ever "EA"/"CTN", so the
# PACK SIZE lives in the description and it is usually the part that wrapped:
#   "GRAVY MIX RICH BROWN G/FREE " + "7KG Executive Chef"
# Losing "7KG" turned a $8.23/kg row into a $57.61/ea row with needs_pack_review
# set — a 7x cost error on any recipe that used it, on 101239, live today.
#
# A continuation row is identified by its INDENT, derived per invoice, never
# hard-coded (the 2026-08-15 re-template is why). The description column's own
# left edge varies between templates — both 70.9 and 73.6 occur in the corpus —
# so it is read off the FIRST line item on this invoice. Footer boilerplate is
# the only other desc-only row and it sits at 90.6, ~17-20pt clear of either.
CONT_INDENT_TOL = 2.0


def _cols_from_header(hrow):
    """
    Derive column x-boundaries from the header row's own word positions.

    Anchors are the header LABELS; the offsets below convert each label anchor
    into the boundary that separates it from the column on its left, and they
    are the only place layout knowledge lives:

      * qty / weight  — values are RIGHT-aligned and end near the next label,
                        so they start a little LEFT of their own label.
      * uom           — short text sitting just under its label.
      * price         — no reliable label of its own ("Unit Price Excl. GST"
                        wraps onto a second header line), so it is bisected
                        between Weight and Disc.
      * total         — everything right of the GST flag column.

    Returns None if the header is not the shape we know, so the caller can fall
    back to COLS rather than invent a layout.
    """
    at = {}
    for x0, _x1, t in hrow:
        at.setdefault(t.rstrip("."), x0)
    try:
        no_x, desc_x = at["No"], at["Description"]
        qty_x, uom_x, wt_x, gst_x = at["Qty"], at["UOM"], at["Weight"], at["GST"]
    except KeyError:
        return None
    disc_x = at.get("Disc")
    price_x = ((wt_x + disc_x) / 2) if disc_x else ((wt_x + gst_x) / 2)
    cols = [("code", 0.0), ("desc", (no_x + desc_x) / 2), ("qty", qty_x - 12),
            ("uom", uom_x - 6), ("weight", wt_x - 10), ("price", price_x),
            ("gstflag", gst_x - 8), ("total", gst_x + 20)]
    # Boundaries must be strictly increasing or bucket() silently mis-assigns.
    if any(b <= a for (_, a), (_, b) in zip(cols, cols[1:])):
        return None
    return cols


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


def _desc_x(rows, cols):
    """
    x of the description's first word on the first LINE ITEM of this invoice.

    Pre-scanned over the whole table rather than filled in as we go: a wrapped
    description can be the very first row after a repeated page header, and a
    lazily-filled anchor is still None at that point (measured: 2 rows in the
    corpus). Returns None if no line item has a description, in which case the
    caller does no joining at all rather than guessing an indent.
    """
    lo, hi = cols[1][1], cols[2][1]          # desc boundary .. qty boundary
    for r in rows:
        c = pdf_text.bucket(r, cols)
        qty, total = _m(c["qty"]), _m(c["total"])
        if qty is None or total is None or qty == 0 or total == 0:
            continue
        if not c["desc"].strip():
            continue
        for x0, _x1, _t in r:
            if lo <= x0 < hi:
                return x0
    return None


@register("foodlinkaustralia.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Tax\s+Invoice\s+(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Date:\s*(\d{2}/\d{2}/\d{4})", flat)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Description" in toks and "Qty." in toks and ("UOM" in toks or "Total" in toks):
            hi = i
            break
    if hi is None:
        raise ValueError("Foodlink: header row not found")

    cols = _cols_from_header(rows[hi]) or COLS
    dx = _desc_x(rows[hi + 1:], cols)

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, cols)
        qty, total = _m(c["qty"]), _m(c["total"])
        if qty is None or total is None or qty == 0 or total == 0:
            # A WRAPPED DESCRIPTION, not a line — join it to the item above.
            # Three conditions, all of them necessary:
            #   * the desc bucket is the ONLY one with text. The delivery note
            #     ("**Enter via Moore Lane ...") spills across every bucket and
            #     the repeated page header fills them too, so both are excluded
            #     here rather than by a keyword.
            #   * it starts at this invoice's own description indent. The only
            #     other desc-only rows are Foodlink's two footer boilerplate
            #     lines ("no.", "MSC Certification code: ...") at x=90.6, which
            #     is ~17pt clear of both observed indents (70.9 / 73.6).
            #   * an item exists to join it to. A continuation orphaned above
            #     the first line item belongs to a page we have already read;
            #     it is skipped, never attached to the wrong product.
            if (dx is not None and items and r
                    and abs(r[0][0] - dx) < CONT_INDENT_TOL
                    and [k for k, v in c.items() if v.strip()] == ["desc"]):
                tail = c["desc"].strip()
                if tail:
                    items[-1].description = f"{items[-1].description} {tail}".strip()
            continue
        # A "23.00/kg" cell is a per-kg RATE, not a per-unit price, so qty x it
        # won't equal the line — derive the unit price from the line total instead.
        price = None if "/" in c["price"] else _m(c["price"])
        taxable = "GST" in c["gstflag"].upper()
        f = Decimal("1.1") if taxable else Decimal("1")
        incl = (total * f).quantize(Decimal("0.01"))
        up_incl = ((price * f) if price is not None else incl / qty).quantize(Decimal("0.0001"))
        desc = c["desc"]
        is_extra = bool(EXTRA_DESC.search(desc))
        uom = c["uom"]
        cb = CostBasis.PER_KG if re.fullmatch(r"KG|KILO(GRAM)?", uom, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["code"], qty=qty, line_total_incl=incl,
            unit_price_incl=up_incl, pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if taxable else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else cb,
            supplier_code=None if is_extra else (c["code"] or None), raw_uom=uom or None))
    if not items:
        raise ValueError("Foodlink: no line items parsed")

    # Reconcile target: "Total AUD Incl GST" — the row carrying 'Incl' + a number.
    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "Incl." in toks or "Incl" in toks:
            nums = [_m(t) for _, _, t in r if _m(t) is not None]
            if nums:
                total_incl = nums[-1]
                break
    if total_incl is None:
        raise ValueError("Foodlink: incl-GST total not found")

    return Invoice(
        supplier_key="foodlink", supplier_name_raw="Foodlink Australia Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
