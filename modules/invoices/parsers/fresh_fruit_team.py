"""
The Fresh Fruit Team — deterministic parser (coordinate-based).

Columns:  QTY | SKU | UNIT | ITEM | UNIT PRICE | GST | AMOUNT
Descriptions and units sometimes wrap to extra visual rows, but the reconcile
fields (qty, sku, price, gst, amount) always sit on ONE "money row" — so we read
by word x-position (pdf_text.word_rows/bucket) and treat any row carrying
qty+price+amount as a line item. Footer Delivery Fee / Fuel Levy become EXTRA
lines; the stated "Total" is the reconcile target.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.pack_size import names_a_unit
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

# Column x-starts from the header row (QTY 30, SKU 68, UNIT 145, ITEM 200,
# UNIT PRICE 363, GST 451, AMOUNT 508).
COLS = [("qty", 0), ("sku", 64), ("unit", 143), ("desc", 198),
        ("price", 360), ("gst", 449), ("amt", 506)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")


def _m(s):
    s = (s or "").replace(",", "").strip()
    m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


@register("tfft.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"\bINB\d+\b", flat)
    ref = m.group(0) if m else ""
    date = None
    for x in flat.splitlines():
        if re.match(r"^\s*\d{1,2} [A-Za-z]{3} \d{4}\s*$", x):
            date = datetime.strptime(x.strip(), "%d %b %Y").date()
            break
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"harry\s*gatt?os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "QTY" in toks and "SKU" in toks and "AMOUNT" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("FFT: header row not found")

    def is_money(row):
        cc = pdf_text.bucket(row, COLS)
        return (_m(cc["qty"]) is not None and _m(cc["price"]) is not None
                and _m(cc["amt"]) not in (None, Decimal("0")))

    body = rows[hi + 1:]
    items = []
    for idx, r in enumerate(body):
        c = pdf_text.bucket(r, COLS)
        qty, price, amt = _m(c["qty"]), _m(c["price"]), _m(c["amt"])
        if qty is None or price is None or amt is None:   # not a stock money row
            continue
        if amt == 0:                                      # substituted / zero-qty
            continue
        # FFT prints the money row in the MIDDLE of a wrapped description, so when
        # this row has no description of its own, stitch in the desc from the rows
        # immediately above and below (which carry no money).
        desc = c["desc"].strip()
        if not desc:
            parts = []
            if idx - 1 >= 0 and not is_money(body[idx - 1]):
                parts.append(pdf_text.bucket(body[idx - 1], COLS)["desc"].strip())
            if idx + 1 < len(body) and not is_money(body[idx + 1]):
                parts.append(pdf_text.bucket(body[idx + 1], COLS)["desc"].strip())
            desc = " ".join(p for p in parts if p).strip()
        g = _m(c["gst"]) or Decimal("0")
        # THE UNIT COLUMN WRAPS TOO. FFT prints "200g punnet" across two rows and
        # the money row lands between them, so the money row's own unit cell is
        # empty and the line went to the cost book with NO stated unit at all —
        # leaving pack_size.parse_pack to scavenge a size out of the description.
        # On MKB500PUNN the description had wrapped to "Punnet) 8 x 100g packs
        # supplied for", the scavenge read 8 x 100 g, and a 200 g punnet was
        # booked as 800 g: King Brown mushrooms at $7.56/kg against the $30.25/kg
        # every other delivery of the same code states. Same stitch as the
        # description, for the same reason, from the same neighbouring rows.
        unit = c["unit"].strip()
        if not unit:
            uparts = []
            if idx - 1 >= 0 and not is_money(body[idx - 1]):
                uparts.append(pdf_text.bucket(body[idx - 1], COLS)["unit"].strip())
            if idx + 1 < len(body) and not is_money(body[idx + 1]):
                uparts.append(pdf_text.bucket(body[idx + 1], COLS)["unit"].strip())
            stitched = " ".join(p for p in uparts if p).strip()
            # Only if it actually NAMES a unit. On INB00109089 the layout shifts
            # (the SKU cell absorbs "Kilogram") and the neighbours' unit cells
            # hold description text — stitching that gave "Cabbage 500g" and made
            # a per-kilogram line a 500 g pack. A measure alone is not enough.
            unit = stitched if names_a_unit(stitched) else ""
        cb = CostBasis.PER_KG if re.search(r"kilo|kg", unit, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["sku"], qty=qty, line_total_incl=amt + g,
            unit_price_incl=price, pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST if g > 0 else TaxTreatment.GST_FREE),
            cost_basis=cb, supplier_code=c["sku"] or None, raw_uom=unit or None, gst_amount=g))
    if not items:
        raise ValueError("FFT: no line items parsed")

    L = [x.strip() for x in flat.splitlines() if x.strip()]

    def _amount_after(label: str) -> Decimal:
        for i, x in enumerate(L):
            if x == label and i + 1 < len(L):
                v = _m(L[i + 1])
                if v and v > 0:
                    return v
        return Decimal("0")

    # Footer extras. The produce is GST-free, so the invoice's GST sits entirely
    # on the taxable extras (Delivery Fee, Fuel Levy). The invoice prints those
    # EX-GST plus a separate "GST Total". We must NOT emit a bare "GST" line — GST
    # is a tax rate in Xero, not a line item, so the bill would drop it and come up
    # short. Instead fold the GST INTO the taxable extras as GST-inclusive lines,
    # distributed by value, so the sum reconciles AND Xero computes the right GST.
    taxable = [(lbl, _amount_after(lbl)) for lbl in ("Delivery Fee", "Fuel Levy")]
    taxable = [(lbl, v) for lbl, v in taxable if v > 0]
    gst_total = _amount_after("GST Total")
    ex_sum = sum((v for _, v in taxable), Decimal("0"))
    allocated = Decimal("0")
    for idx, (label, ex) in enumerate(taxable):
        if idx < len(taxable) - 1 and ex_sum:
            share = (gst_total * ex / ex_sum).quantize(Decimal("0.01"))
            allocated += share
        else:
            share = gst_total - allocated            # remainder to the last extra
        incl = ex + share
        items.append(InvoiceLine(
            description=label, qty=Decimal("1"), line_total_incl=incl, unit_price_incl=incl,
            pack_size=1, line_class=LineClass.EXTRA,
            tax_treatment=(TaxTreatment.GST if share > 0 else TaxTreatment.GST_FREE),
            cost_basis=CostBasis.UNKNOWN, gst_amount=share if share > 0 else None))
    # Edge: GST printed but no taxable extra found — keep it as a line so the sum
    # still reconciles (rare; a human reviews it since the bill would be short).
    if gst_total > 0 and not taxable:
        items.append(InvoiceLine(
            description="GST", qty=Decimal("1"), line_total_incl=gst_total, unit_price_incl=gst_total,
            pack_size=1, line_class=LineClass.EXTRA, tax_treatment=TaxTreatment.GST,
            cost_basis=CostBasis.UNKNOWN))

    total = None
    for i, x in enumerate(L):
        if x == "Total" and i + 1 < len(L) and _m(L[i + 1]) is not None:
            total = _m(L[i + 1])
            break
    if total is None:
        raise ValueError("FFT: invoice total not found")

    return Invoice(
        supplier_key="fresh_fruit_team", supplier_name_raw="The Fresh Fruit Team Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total, lines=items, venue=venue)
