"""
Farmer Joe's / F J Chickens Pty Ltd — deterministic parser (coordinate-based).

Columns:  PRODUCT / DESCRIPTION | SHIPPED | PRICE | PER | GST$ | AMOUNT
SHIPPED x PRICE = AMOUNT. Fresh poultry is GST-free (GST$ column blank), so the
stock lines sum to the printed TOTAL. Statements (ARSTARPT, "STATEMENT" header)
carry no line items and fall through to the statement guard.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("desc", 0), ("shipped", 293), ("price", 345), ("per", 393),
        ("gst", 443), ("amount", 498)]
MONEY = re.compile(r"-?\d[\d,]*\.?\d*")
UNIT_BASIS = {"KG": CostBasis.PER_KG, "K": CostBasis.PER_KG,
              "EACH": CostBasis.PER_UNIT, "EA": CostBasis.PER_UNIT,
              "CTN": CostBasis.PER_UNIT, "BOX": CostBasis.PER_UNIT,
              "TRAY": CostBasis.PER_UNIT, "DOZ": CostBasis.PER_UNIT}


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


@register("farmerjoes.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)
    L = [x.strip() for x in flat.splitlines() if x.strip()]

    ref = ""
    m = re.search(r"INVOICE\s+NO\.?\s*(\d+)", flat, re.I)
    if m:
        ref = m.group(1)
    date = None
    for x in L:
        m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", x)
        if m:
            date = datetime.strptime(m.group(1), "%d/%m/%Y").date()
            break
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway|STOW", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = " ".join(t for _, _, t in r).upper()
        if "DESCRIPTION" in toks and "SHIPPED" in toks and "AMOUNT" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Farmer Joes: header row not found")

    items = []
    seen = set()
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        qty, price, amount = _m(c["shipped"]), _m(c["price"]), _m(c["amount"])
        if qty is None or price is None or amount is None or amount == 0:
            continue
        desc = (c["desc"] or "").strip()
        if not desc or desc.upper().startswith(("PAID", "TOTAL", "GOODS")):
            continue
        key = (desc, str(amount))
        if key in seen:              # the template repeats the block per page
            continue
        seen.add(key)
        unit = (c["per"] or "").strip()
        cb = UNIT_BASIS.get(unit.upper(), CostBasis.PER_UNIT)
        has_gst = _m(c["gst"]) not in (None, Decimal("0"))
        items.append(InvoiceLine(
            description=desc, qty=qty, line_total_incl=amount, unit_price_incl=price,
            pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if has_gst else TaxTreatment.GST_FREE,
            cost_basis=cb, supplier_code=None, raw_uom=unit or None))
    if not items:
        raise ValueError("Farmer Joes: no line items parsed")

    total_incl = None
    for r in rows:
        toks = [t.rstrip(":").upper() for _, _, t in r]
        if "TOTAL" in toks:
            vals = [_m(t) for _, _, t in r if _m(t) is not None]
            if vals:
                total_incl = vals[-1]
                break
    if total_incl is None:
        total_incl = sum(i.line_total_incl for i in items)

    return Invoice(
        supplier_key="farmer_joes", supplier_name_raw="F J Chickens Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
