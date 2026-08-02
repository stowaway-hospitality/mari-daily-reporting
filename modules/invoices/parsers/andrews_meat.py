"""
Andrews Meat Industries — deterministic parser (coordinate-based).

Columns:  Code | Description | Qty | Unit | Unit Price | GST | Total
Qty x Unit Price = Total. Fresh meat is GST-free; the footer prints
Gross / Discount / GST / Net Invoice — Net Invoice is the payable total (label
and value share a positional row, the flat text reorders them). Statements
(no line items, "STATEMENT" header) are left for the statement/credit guard.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 20), ("desc", 72), ("qty", 515), ("unit", 558),
        ("price", 615), ("gst", 705), ("total", 758)]
MONEY = re.compile(r"-?\d[\d,]*\.?\d*")
UNIT_BASIS = {"KG": CostBasis.PER_KG, "K": CostBasis.PER_KG,
              "EACH": CostBasis.PER_UNIT, "EA": CostBasis.PER_UNIT,
              "CTN": CostBasis.PER_UNIT, "BOX": CostBasis.PER_UNIT,
              "PC": CostBasis.PER_UNIT, "PKT": CostBasis.PER_UNIT, "BAG": CostBasis.PER_UNIT}


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


@register("andrewsmeat.com")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)
    L = [x.strip() for x in flat.splitlines() if x.strip()]

    ref = ""
    m = re.search(r"\b(INV\d+)\b", flat)
    if m:
        ref = m.group(1)
    date = None
    for x in L:
        m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", x)
        if m:
            date = datetime.strptime(m.group(1), "%d/%m/%Y").date()
            break
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT|HAR038", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway|STOW", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Code" in toks and "Description" in toks and "Total" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Andrews Meat: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        qty, price, total = _m(c["qty"]), _m(c["price"]), _m(c["total"])
        if qty is None or price is None or total is None or total == 0:
            continue
        desc = (c["desc"] or c["code"]).strip()
        if not desc:
            continue
        unit = (c["unit"] or "").strip()
        cb = UNIT_BASIS.get(unit.upper(), CostBasis.PER_UNIT)
        has_gst = _m(c["gst"]) not in (None, Decimal("0"))
        items.append(InvoiceLine(
            description=desc, qty=qty, line_total_incl=total, unit_price_incl=price,
            pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if has_gst else TaxTreatment.GST_FREE,
            cost_basis=cb, supplier_code=(c["code"] or None), raw_uom=unit or None))
    if not items:
        raise ValueError("Andrews Meat: no line items parsed")

    # grand total = "Net Invoice" value (label + value share a positional row)
    def phrase_money(*words):
        want = [w.upper() for w in words]
        for r in rows:
            toks = [t.rstrip(":").upper() for _, _, t in r]
            if all(w in toks for w in want):
                vals = [_m(t) for _, _, t in r if _m(t) is not None]
                if vals:
                    return vals[-1]
        return None

    total_incl = phrase_money("Net", "Invoice") or phrase_money("Total", "Due")
    if total_incl is None:
        raise ValueError("Andrews Meat: invoice total not found")

    return Invoice(
        supplier_key="andrews_meat", supplier_name_raw="Andrews Meat Industries Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
