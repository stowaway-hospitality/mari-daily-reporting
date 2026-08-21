"""
B&E Foods — deterministic parser (coordinate-based).

Columns:  Item Code | Description | Ordered | Shipped | UOM | Ship Doc | Item Price | GST | Line Total
Shipped is the delivered qty; Line Total is GST-INCLUSIVE (ex + the GST column),
so it sums straight to the invoice's "Total" (incl). Venue from the "Sold To"
(billed-to) column on the right, not the "Deliver To" on the left (Zak: billed-to
wins). Non-food lines (chemicals, napkins) come through as ordinary items —
harmless; the recipe side only ever picks food.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 0), ("desc", 70), ("ordered", 215), ("shipped", 260), ("uom", 310),
        ("shipdoc", 340), ("price", 400), ("gst", 460), ("total", 510)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")
EXTRA_DESC = re.compile(r"fuel\s*levy|freight|delivery|cartage", re.I)

# A B&E description WRAPS onto its own row, and that row carries no shipped qty
# and no line total — so the `qty is None` guard below silently DROPPED it.
# Measured on the corpus: 1,145 wrapped rows across 132 invoices, against
# Foodlink's 435 across 129. It never showed in the PASS column because a
# description plays no part in reconciliation; b_e read 130/132 (98%) the whole
# time. This is the fourth instance of that class (FFT, Gulli, Foodlink, B&E)
# and the biggest — B&E is the largest kitchen supplier in the file.
#
# It is not cosmetic. B&E's UOM column only ever says UNIT / KG / CTN, so on a
# UNIT line the PACK SIZE is stated ONLY in the description, and the size is
# usually the part that wrapped:
#     "BEKSUL BLACK (DARK " + "BROWN) SUGAR 1KG(16) CJ" + "FOODS KRN #186167"
#     "FZ PIPI CLAM - WHOLE IN " + "SHELL COOKED 40/60 1KG" + "(10) (I)"
# Both are stored truncated mid-phrase in data/invoices today, one with an
# unbalanced bracket. 48 of the 141 needs_pack_review ingredients in the live
# picker are B&E — the largest single bucket.
#
# A continuation row is identified by its INDENT, derived per invoice from the
# first line item's own description x, never hard-coded (Foodlink's 2026-08-15
# re-template is why). Measured across all 132 corpus invoices the indent is
# 76.5 on every one, and the ONLY desc-only rows that are not continuations are
# B&E's own header boilerplate — "Receiver Name:" at x=164.3 (132 invoices) and
# a wrapped depot address "Blacktown NSW 2148" at x=134.3 (21) — both ~58pt and
# ~58pt clear of the indent. 1,145 rows match, 153 do not, and all 153 are
# those two lines.
CONT_INDENT_TOL = 2.0


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.match(s if s.startswith("-") or s[:1].isdigit() else "$" + s)
    if not m:
        m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def _desc_x(rows):
    """
    x of the description's first word on the first LINE ITEM of this invoice.

    Pre-scanned over the whole table rather than filled in as we go: a wrapped
    description can be the very first row after the header (a page break can
    orphan one above its parent), and a lazily-filled anchor is still None at
    that point. Returns None if no line item has a description, in which case
    the caller does no joining at all rather than guessing an indent.
    """
    lo, hi = COLS[1][1], COLS[2][1]          # desc boundary .. ordered boundary
    for r in rows:
        c = pdf_text.bucket(r, COLS)
        qty, total = _m(c["shipped"]), _m(c["total"])
        if qty is None or total is None or qty == 0 or total == 0:
            continue
        if not c["desc"].strip():
            continue
        for x0, _x1, _t in r:
            if lo <= x0 < hi:
                return x0
    return None


def _venue(rows) -> Venue:
    # "Sold To" is the right-hand column (x >= 270). Collect its text from the
    # rows just after the "Sold To:" header and read the billed-to name.
    start = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Sold" in toks and "To:" in toks:
            start = i
            break
    blob = ""
    if start is not None:
        for r in rows[start:start + 5]:
            blob += " " + " ".join(t for x0, _, t in r if x0 >= 270)
    if re.search(r"marilyna", blob, re.I):
        return Venue.MARILYNAS
    if re.search(r"gatt?os", blob, re.I):
        return Venue.HARRY_GATOS
    if re.search(r"stowaway", blob, re.I):
        return Venue.STOWAWAY
    return Venue.UNKNOWN


@register("befoods.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Invoice\s+No:\s*(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Invoice\s+Date:\s*(\d{2}/\d{2}/\d{4})", flat, re.I)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = _venue(rows)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Description" in toks and "Shipped" in toks and "Total" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("B&E: header row not found")

    dx = _desc_x(rows[hi + 1:])

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        qty, total = _m(c["shipped"]), _m(c["total"])
        if qty is None or total is None or qty == 0 or total == 0:
            # A WRAPPED DESCRIPTION, not a line — join it to the item above.
            # Three conditions, all of them necessary:
            #   * the desc bucket is the ONLY one with text, so the repeated
            #     page header and any address block spilling across columns are
            #     excluded structurally rather than by keyword.
            #   * it starts at this invoice's own description indent. The only
            #     other desc-only rows on any of the 132 corpus invoices are
            #     "Receiver Name:" (x=164.3) and a wrapped depot address
            #     (x=134.3), both ~58pt clear of the 76.5 indent.
            #   * an item exists to join it to. A continuation orphaned above
            #     the first line item belongs to a page already read; it is
            #     skipped, never attached to the wrong product.
            if (dx is not None and items and r
                    and abs(r[0][0] - dx) < CONT_INDENT_TOL
                    and [k for k, v in c.items() if v.strip()] == ["desc"]):
                tail = c["desc"].strip()
                if tail:
                    items[-1].description = f"{items[-1].description} {tail}".strip()
            continue
        gst = _m(c["gst"]) or Decimal("0")
        desc = c["desc"]
        is_extra = bool(EXTRA_DESC.search(desc))
        uom = c["uom"]
        cb = CostBasis.PER_KG if re.fullmatch(r"KG|KILO(GRAM)?", uom, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["code"], qty=qty, line_total_incl=total,
            unit_price_incl=(total / qty).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if gst > 0 else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else cb,
            supplier_code=None if is_extra else (c["code"] or None),
            raw_uom=uom or None, gst_amount=gst))
    if not items:
        raise ValueError("B&E: no line items parsed")

    # Grand total: the "Total" row with a value and NO 'Ex' (that one is the
    # ex-GST subtotal). Avoid the account 'OUTSTANDING AMOUNT'.
    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if toks and toks[0] == "Total" and "Ex" not in toks:
            nums = [_m(t) for _, _, t in r if _m(t) is not None]
            if nums:
                total_incl = nums[-1]
    if total_incl is None:
        raise ValueError("B&E: invoice total not found")

    return Invoice(
        supplier_key="be_foods", supplier_name_raw="B&E Foods Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
