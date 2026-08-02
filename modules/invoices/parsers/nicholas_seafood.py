"""
Nicholas Seafood (Seafood Buyfood Pty Ltd) — deterministic parser (coordinate-based).

Columns:  QTY | ITEM NO | DESCRIPTION | PRICE | UNIT | DISC% | EXTENDED | tax
Read by word x-position (the flat text order is scrambled on this template).
QTY x PRICE = EXTENDED. Seafood is GST-free ("FRE" in the tax column); a GST
line and any freight land as EXTRA lines. Billed-to wins for venue.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("qty", 40), ("code", 100), ("desc", 155), ("price", 330),
        ("unit", 372), ("disc", 405), ("ext", 445), ("tax", 515)]
MONEY = re.compile(r"-?\d[\d,]*\.?\d*")
UNIT_BASIS = {"KG": CostBasis.PER_KG, "K": CostBasis.PER_KG,
              "EACH": CostBasis.PER_UNIT, "EA": CostBasis.PER_UNIT,
              "PKT": CostBasis.PER_UNIT, "PC": CostBasis.PER_UNIT,
              "BAG": CostBasis.PER_UNIT, "BOX": CostBasis.PER_UNIT,
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


@register("nicholasseafood.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)
    L = [x.strip() for x in flat.splitlines() if x.strip()]

    ref = ""
    for i, x in enumerate(L):
        m = re.search(r"Invoice\s+No\.?:?\s*(\S+)", x)
        if m:
            ref = m.group(1)
            break
        if x.rstrip(":").lower() == "invoice no." and i + 1 < len(L):
            ref = L[i + 1].strip()
            break
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
        toks = [t for _, _, t in r]
        if "QTY" in toks and "DESCRIPTION" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Nicholas Seafood: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        qty, price, ext = _m(c["qty"]), _m(c["price"]), _m(c["ext"])
        if qty is None or price is None or ext is None or ext == 0:
            continue
        desc = (c["desc"] or c["code"]).strip()
        if not desc:
            continue
        unit = (c["unit"] or "").strip()
        cb = UNIT_BASIS.get(unit.upper(), CostBasis.PER_UNIT)
        gst_free = "FRE" in (c["tax"] or "").upper()
        items.append(InvoiceLine(
            description=desc, qty=qty, line_total_incl=ext, unit_price_incl=price,
            pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=TaxTreatment.GST_FREE if gst_free else TaxTreatment.GST,
            cost_basis=cb, supplier_code=(c["code"] or None), raw_uom=unit or None))
    if not items:
        raise ValueError("Nicholas Seafood: no line items parsed")

    # Totals live on positional rows (label + value share a row; the flat text
    # reorders them). The "Total:" row carries the invoice total. Seafood is
    # GST-free, so the stock lines already sum to it; GST/freight, if ever present,
    # would break reconciliation and route to review — the safe outcome.
    def row_money(label):
        for r in rows:
            toks = [t for _, _, t in r]
            if any(t.rstrip(":").upper() == label for t in toks):
                vals = [_m(t) for _, _, t in r if _m(t) is not None]
                if vals:
                    return vals[-1]
        return None

    total_incl = row_money("TOTAL")
    if total_incl is None:
        raise ValueError("Nicholas Seafood: invoice total not found")

    return Invoice(
        supplier_key="nicholas_seafood", supplier_name_raw="Nicholas Seafood Traders",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
