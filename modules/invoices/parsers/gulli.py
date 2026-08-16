"""
Gulli Food Distributors — deterministic parser (coordinate-based).

Columns:  Product Code | Description | Quantity | (UOM) | Unit Price | GST% | Amount
The Amount column is EX-GST and the GST column is a per-line rate (0% / 10%), so
each line's incl total is amount x (1 + rate). Reconcile target: footer "Total".
"Standard Delivery" lines come through as EXTRA. Venue from the customer code /
ship-to name in the flat text.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

MONEY = re.compile(r"^-?[\d,]+\.?\d*$")
EXTRA_DESC = re.compile(r"delivery|freight|fuel\s*levy|cartage", re.I)

# FALLBACK ONLY — the literal x-boundaries this parser used until 2026-08-16.
# `_cols_from_header` below is the live path; see the note there for why.
FALLBACK_DESC_LO = 125.0
FALLBACK_NUM_LO = 335.0


def _cols_from_header(hrow):
    """
    Derive the two column boundaries from the header row's own word positions.

    Gulli's table is laid out to fit its CONTENT, so the columns do not sit
    still between invoices: across the corpus the DESCRIPTION anchor ranges
    122.8 -> 166.5 and QUANTITY 336.3 -> 394.5. The literal 125 / 335 splits
    this parser used were inside both ranges, i.e. one invoice's content width
    away from mis-bucketing, and the low end had ALREADY been crossed (see the
    2026-08-16 triage entry). Measured over the corpus:

      * a description's first word starts EXACTLY at the DESCRIPTION anchor
        (margin 0.0 on all 309 line rows), and the nearest product-code token
        is 91-135pt to its left — so `DESCRIPTION - 2` splits code from
        description with no ambiguity at either end.
      * the earliest quantity value sits at `QUANTITY - 1.2`, so `QUANTITY - 8`
        clears every qty while staying right of the description text.

    Returns None if the header is not the shape we know, so the caller falls
    back to the constants above rather than inventing a layout.
    """
    at = {}
    for x0, _x1, t in hrow:
        at.setdefault(t.rstrip("."), x0)
    try:
        code_x, desc_x, qty_x = at["CODE"], at["DESCRIPTION"], at["QUANTITY"]
    except KeyError:
        return None
    desc_lo, num_lo = desc_x - 2, qty_x - 8
    if not (code_x < desc_lo < num_lo):
        return None
    return desc_lo, num_lo


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


@register("gullifood.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Tax\s+Invoice\s+(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Invoice\s+Date:\s*(\d{2}/\d{2}/\d{4})", flat, re.I)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = (Venue.MARILYNAS if re.search(r"marilyna|MARI0", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT|HGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway|STOW0", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "DESCRIPTION" in toks and "AMOUNT" in toks and ("QUANTITY" in toks or "GST" in toks):
            hi = i
            break
    if hi is None:
        raise ValueError("Gulli: header row not found")

    bounds = _cols_from_header(rows[hi])
    desc_lo, num_lo = bounds if bounds else (FALLBACK_DESC_LO, FALLBACK_NUM_LO)

    # Row parsing anchored on the GST% token (qty/price column x-positions drift
    # so much between invoices that a fixed split can't separate them, but the
    # GST% cell is stable): the two numbers LEFT of GST% are qty (first) and unit
    # price (last); the amount is the number RIGHT of it. Footer rows (Total /
    # "GST 10% on $..") have <2 numbers left of their %, so they fall out.
    items = []
    for r in rows[hi + 1:]:
        gi = next(((x0, m) for x0, _, t in r if (m := re.match(r"(\d+)%$", t))), None)
        if not gi:
            continue
        gst_x, gm = gi
        left = [v for x0, _, t in r if num_lo <= x0 < gst_x and (v := _m(t)) is not None]
        amt = next((v for x0, _, t in reversed(r) if x0 > gst_x and (v := _m(t)) is not None), None)
        if len(left) < 2 or amt is None:
            continue
        qty, price = left[0], left[-1]
        code = next((t for x0, _, t in r if x0 < desc_lo and t.strip()), "")
        desc = " ".join(t for x0, _, t in r if desc_lo <= x0 < num_lo)
        pct = Decimal(gm.group(1))
        f = 1 + pct / 100
        incl = (amt * f).quantize(Decimal("0.01"))
        if incl == 0:
            continue
        is_extra = bool(EXTRA_DESC.search(desc))
        items.append(InvoiceLine(
            description=desc or code, qty=qty, line_total_incl=incl,
            unit_price_incl=(price * f).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if pct > 0 else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else CostBasis.PER_UNIT,
            supplier_code=None if is_extra else (code.strip() or None), raw_uom=None))
    if not items:
        raise ValueError("Gulli: no line items parsed")

    # Grand total: footer "Total" (x ~345). Not "Account Balance".
    total_incl = None
    for r in rows:
        for x0, _, t in r:
            if t == "Total" and 330 <= x0 <= 360:
                nums = [_m(tt) for _, _, tt in r if _m(tt) is not None]
                if nums:
                    total_incl = nums[-1]
    if total_incl is None:
        raise ValueError("Gulli: invoice total not found")

    return Invoice(
        supplier_key="gulli", supplier_name_raw="Gulli Food Distributors Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
