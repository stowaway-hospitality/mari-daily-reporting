"""
JFC Australia Co Pty Ltd — deterministic parser (coordinate-based).

NOT Jun Pacific, despite years of the two being filed together. They are separate
legal entities on separate invoice systems:

    Jun Pacific Corporation   ABN 71 054 434 061   "Tax Invoice: NB10482429"
    JFC Australia Co Pty Ltd  ABN 36 003 080 260   "INVOICE No.  001910089"

Both sell Japanese groceries into Harry Gatos, which is presumably why every JFC
invoice that ever reached data/invoices carries supplier_key "jun_pacific" — they
were LLM-extracted before either had a parser, and nothing checked the ABN. Their
product codes do not collide (JFC numeric "30562", Jun Pacific alphanumeric
"HA8204612"), so separating them costs nothing and stops two companies sharing a
price history. build_cogs_list re-labels the historical rows onto "JFC" so the
cost series stays continuous across the correction.

Columns:  ITEM No | PRODUCT DESCRIPTION | QTY | UNIT DESC. | LIST PRICE |
          UNIT PRICE | AMOUNT (EXCL. GST) | GST | WET

Line amounts are EX-GST and each line states its own GST and WET, so a line's
incl total is AMOUNT + GST + WET. The description wraps onto the row below, which
also carries a "< n >" line counter in the ITEM column. Reconcile target is the
stated "INVOICE TOTAL".
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
# The ITEM column also carries a "< 3 >" line counter on the wrap row; it is not
# part of the description and must never become one.
_COUNTER = re.compile(r"^<\s*\d+\s*>$")


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _cols_from_header(hrow, gstrow):
    """
    Column boundaries derived from the header's own word positions.

    Hard-coded pixel columns are how the Foodlink and Fresh Fruit Team parsers
    both rotted (see their module docstrings), so this one never has any. "UNIT"
    appears TWICE in the header — "UNIT DESC." and "UNIT PRICE" — so the two are
    taken in order rather than by lookup.

    Text columns are left-aligned under their label; money columns are right
    aligned and run past it, so they take a small margin to the left instead.
    Returns None if the header is not the shape we know.
    """
    at = {}
    units = []
    for x0, _x1, t in hrow:
        if t == "UNIT":
            units.append(x0)
        else:
            at.setdefault(t, x0)
    try:
        item_x, desc_x = at["ITEM"], at["PRODUCT"]
        qty_x, list_x, amt_x = at["QTY"], at["LIST"], at["AMOUNT"]
    except KeyError:
        return None
    if len(units) < 2:
        return None
    udesc_x, uprice_x = units[0], units[1]
    gst_x = wet_x = None
    for x0, _x1, t in gstrow or []:
        if t == "GST" and gst_x is None:
            gst_x = x0
        elif t == "WET" and wet_x is None:
            wet_x = x0
    if gst_x is None or wet_x is None:
        return None
    cols = [("item", 0.0),
            ("desc", desc_x - 5),
            ("qty", qty_x - 15),
            ("udesc", udesc_x - 8),
            ("listp", list_x - 7),
            ("unitp", uprice_x - 6),
            ("amt", amt_x + 4),
            ("gst", gst_x - 8),
            ("wet", wet_x - 8)]
    if any(b <= a for (_, a), (_, b) in zip(cols, cols[1:])):
        return None
    return cols


@register("jfcaust.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "ITEM" in toks and "PRODUCT" in toks and "QTY" in toks and "AMOUNT" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("JFC: header row not found")
    gstrow = rows[hi + 1] if hi + 1 < len(rows) else []
    cols = _cols_from_header(rows[hi], gstrow)
    if cols is None:
        raise ValueError("JFC: header shape not recognised")

    # "INVOICE No." and its value sit on the SAME visual row, in the right-hand
    # block; the value is simply the last token. Reading it off the flat text
    # instead would collide with "INVOICE TOTAL" and "INVOICE TO:".
    ref = ""
    date = None
    for r in rows[:hi]:
        toks = [t for _, _, t in r]
        if "INVOICE" in toks and "No." in toks and len(toks) > 2:
            ref = toks[-1]
        if "DATE" in toks and "ISSUE" in toks:
            try:
                date = datetime.strptime(toks[-1], "%d-%b-%y").date()
            except ValueError:
                pass
    if not ref:
        m = re.search(r"INVOICE\s+No\.?\s*(\S+)", flat, re.I)
        ref = m.group(1) if m else ""

    # DELIVER TO decides the venue: the bill goes to Stowaway as the account
    # holder, but the goods (and therefore the cost) belong to whoever received
    # them. Note JFC spells it "HARY GATOS" on some invoices.
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"har+y?\s*gat+os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    items = []
    body = rows[hi + 1:]
    for idx, r in enumerate(body):
        c = pdf_text.bucket(r, cols)
        qty, amt = _m(c["qty"]), _m(c["amt"])
        unitp = _m(c["unitp"])
        if qty is None or amt is None or unitp is None or qty == 0:
            continue
        code = (c["item"] or "").strip()
        if not code or _COUNTER.match(code):
            continue
        desc = (c["desc"] or "").strip()
        # The description wraps to the next row, which carries the "< n >"
        # counter in the ITEM column and no money of its own.
        if idx + 1 < len(body):
            nxt = pdf_text.bucket(body[idx + 1], cols)
            if _m(nxt["amt"]) is None and _COUNTER.match((nxt["item"] or "").strip()):
                tail = (nxt["desc"] or "").strip()
                if tail:
                    desc = f"{desc} {tail}".strip()
        gst = _m(c["gst"]) or Decimal("0")
        wet = _m(c["wet"]) or Decimal("0")
        incl = amt + gst + wet
        uom = (c["udesc"] or "").strip()
        items.append(InvoiceLine(
            description=desc or code, qty=qty, line_total_incl=incl,
            unit_price_incl=(incl / qty).quantize(Decimal("0.0001")),
            pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST if gst > 0 else TaxTreatment.GST_FREE),
            cost_basis=CostBasis.PER_UNIT, supplier_code=code,
            raw_uom=uom or None, gst_amount=gst if gst > 0 else None))
    if not items:
        raise ValueError("JFC: no line items parsed")

    # Reconcile target: the stated "INVOICE TOTAL". Anchored on the row carrying
    # BOTH words, so "INVOICE No." and "INVOICE TO:" cannot match.
    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "INVOICE" in toks and "TOTAL" in toks:
            nums = [v for _, _, t in r if (v := _m(t)) is not None]
            if nums:
                total_incl = nums[-1]
                break
    if total_incl is None:
        raise ValueError("JFC: invoice total not found")

    return Invoice(
        supplier_key="jfc", supplier_name_raw="JFC Australia Co Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl,
        lines=items, venue=venue)
