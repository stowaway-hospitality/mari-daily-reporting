"""
Paramount Liquor — deterministic parser (coordinate-based).

Standard single-invoice template (system-generated, real text layer). Columns:

    Code | Description | Size | Case/Bottle | Base Cost | Total Net | WET | GST
         | LUC Ex GST | Total Inc GST

The Case/Bottle column is a real unit count and is read (see units_on_line) —
"2 / 0" is two cartons, "0 / 1" is one loose bottle, a bare "2" is two cartons.
It used to be discarded, which priced a two-carton line as a single bottle.

Every line carries a per-line "Total Inc GST" figure in the rightmost column,
and those sum exactly to the stated "Invoice Total" — WET and GST are already
folded into each line, so we reconcile on that column directly and never have to
untangle the invoice-level WET/GST split (which the flattened text layer renders
in an ambiguous order). Read by word x-position (pdf_text.word_rows/bucket) so a
wrapped product name or the "0 / 1" bottle-break qty can not shift a figure.

A row is a line item iff its Code cell is a bare product/charge code (all digits)
AND it has a value in the Total-Inc-GST column — that cleanly excludes the
totals/payment footer. MISC charges (Carton Freight, Fuel Levy, Minimum Delivery
Top-Up) are captured as EXTRA lines.

Consolidated statements and the occasional 2-page multi-invoice PDF do not carry
this single-invoice header; they raise and fall back to the LLM.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

# Column x-starts from the header row (Code 33, Description 155, Size 303,
# Case/Bottle 357, Base Cost 422, Total Net 489, WET 556, GST 611, LUC Ex GST
# 651, Total Inc GST 711). Boundaries sit just left of each header/value.
COLS = [("code", 0), ("desc", 75), ("size", 290), ("qty", 356), ("base", 415),
        ("net", 485), ("wet", 548), ("gst", 600), ("luc", 645), ("incgst", 705)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")
# MISC / charge lines that are never entered on a Lightspeed receive.
EXTRA_RE = re.compile(r"freight|fuel levy|delivery|top-?up|surcharge|cartage", re.I)

# The Case/Bottle cell, in its two shapes: "2 / 0" (2 cases, 0 loose bottles) and
# a bare "1" (that many of whatever the Size cell prices as one unit — a carton
# for the "C" packs, a single 20 L drum for "1/20000 ml").
QTY_SPLIT = re.compile(r"^(\d+)\s*/\s*(\d+)$")
# The Size cell: "6/700 ml", "8/500 ml C", "1/20000 ml" -> units per carton.
PACK_RE = re.compile(r"^(\d+)\s*/\s*([\d.]+)\s*(ml|l|g|kg)\b", re.I)
CENT = Decimal("0.02")


def _m(s):
    s = (s or "").replace(",", "").strip()
    m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def units_on_line(size: str, qty_cell: str, base, net):
    """How many DESCRIPTION-sized units this line actually bought.

    THE DEFECT THIS FIXES
    ---------------------
    qty was hardcoded to Decimal("1"), so a line that bought two cartons was
    priced as though it bought one bottle. Invoice 5419664 billed $729.83 for
    "ROOSTER ROJO BLANCO : 700 ml" on a "2 / 0" — two cartons, twelve bottles,
    $60.82 each. The book saw one 700 ml bottle at $729.83, i.e. $1,042 a litre.
    That number was so absurd resolve_pack refused it and the line was dropped
    entirely, which is why the defect never showed up as a wrong price: $1,394 of
    Stowaway's HOUSE tequila simply never reached the cost book. A less absurd
    multiple would have been believed.

    WHY THIS IS ARITHMETIC AND NOT A GUESS
    --------------------------------------
    Paramount states Base Cost (the carton price) and Total Net on every line, so
    the unit count is recoverable and, more importantly, CHECKABLE:

        base x units / per_carton == net

    Over all 21 invoices in data/invoice_corpus/paramount that identity holds on
    42 of 42 stock lines and fails on none. So the count is not inferred from the
    cell's shape — it is proposed from the cell and then proved against two money
    columns the supplier printed. If it does not prove, this returns None and the
    caller keeps the old qty=1 behaviour: a line we can't read stays unread,
    rather than becoming a confident wrong number.

    -> Decimal(units) | None
    """
    pm = PACK_RE.match((size or "").strip())
    if not pm or base is None or net is None or base <= 0 or net <= 0:
        return None
    per = int(pm.group(1))
    if per <= 0:
        return None
    cell = (qty_cell or "").strip().replace(",", "")
    m = QTY_SPLIT.match(cell)
    if m:
        units = int(m.group(1)) * per + int(m.group(2))
    elif cell.isdigit():
        units = int(cell) * per          # bare count = that many cartons
    else:
        return None
    if units <= 0:
        return None
    if abs(base * Decimal(units) / Decimal(per) - net) > CENT:
        return None                      # the invoice's own columns disagree — refuse
    return Decimal(units)


@register("paramountliquor.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Code" in toks and "Description" in toks and "Net" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Paramount: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        code = c["code"].strip()
        inc = _m(c["incgst"])
        if not re.fullmatch(r"\d{3,}", code):     # real product/charge code only
            continue
        if inc is None or inc == 0:               # zero / substituted line
            continue
        desc = (c["desc"] or code).strip()
        is_extra = c["size"].strip().upper() == "MISC" or bool(EXTRA_RE.search(desc))
        wet = _m(c["wet"])
        # A charge line's "qty" cell counts cents of a $0.01 charge, not units of
        # anything — 1,370 of them is a $13.70 delivery top-up. Only stock lines
        # carry a real unit count.
        units = (None if is_extra
                 else units_on_line(c["size"], c["qty"], _m(c["base"]), _m(c["net"])))
        items.append(InvoiceLine(
            description=desc, qty=units or Decimal("1"), line_total_incl=inc,
            unit_price_incl=None, pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=(TaxTreatment.WET if (wet and wet > 0) else TaxTreatment.GST),
            cost_basis=CostBasis.UNKNOWN, supplier_code=code,
            raw_uom=c["size"] or None))
    if not items:
        raise ValueError("Paramount: no line items parsed")

    ref = ""
    for r in rows:
        toks = [t for _, _, t in r]
        if "Invoice" in toks and "#" in toks:
            for _, _, t in r:
                if re.fullmatch(r"\d{5,}", t):
                    ref = t
                    break
            if ref:
                break

    date = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "Invoice" in toks and "Date:" in toks:
            for _, _, t in r:
                m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
                if m:
                    date = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
                    break
            if date:
                break

    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"harry|gatt?os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "Invoice" in toks and "Total" in toks:
            for _, _, t in r:
                v = _m(t)
                if v is not None and v > 0:
                    total_incl = v
            if total_incl is not None:
                break
    if total_incl is None:
        raise ValueError("Paramount: invoice total not found")

    return Invoice(
        supplier_key="paramount", supplier_name_raw="Marlau Nominees Pty Ltd T/A Paramount Liquor",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
