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
# FALLBACK ONLY — see _cols_from_header, which is the live path.
#
# These constants were the whole story until 2026-08-15, and the fixed `desc`
# boundary of 198 is what made "Large" a product name. FFT's header does not sit
# still: across the corpus the ITEM anchor ranges 181.7 -> 201.3 and UNIT ranges
# 126.0 -> 146.3 (16 distinct header signatures). 198 sits at the TOP of ITEM's
# range, so on every invoice whose ITEM anchor landed left of 198 the
# description's FIRST WORD fell into the unit bucket instead: raw_uom became
# "Carrot" and the description became "Large".
#
# That is invisible to the money check — the line still reconciles to the cent —
# so FFT scored 52/52 (100%) while 274 lines carried a description word as their
# unit and 51 of 119 supplier codes had split into two identities ("Carrot Large"
# AND "Large" for CLKG). Same failure mode, same day, as the Foodlink defect:
# hard-coded pixel columns rot when a supplier re-templates.
COLS = [("qty", 0), ("sku", 64), ("unit", 143), ("desc", 198),
        ("price", 360), ("gst", 449), ("amt", 506)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")

# The header is 8 tokens in a fixed ORDER, and "UNIT" appears twice ("UNIT" and
# "UNIT PRICE"), so anchors are taken by POSITION, never by label lookup.
_HEADER = ("QTY", "SKU", "UNIT", "ITEM", "UNIT", "PRICE", "GST", "AMOUNT")


def _cols_from_header(hrow):
    """
    Derive column x-boundaries from the header row's own word positions.

    Text columns (SKU, UNIT, ITEM) are LEFT-aligned under their labels, so the
    boundary between two of them is the midpoint of their anchors. Money columns
    (UNIT PRICE, GST, AMOUNT) are RIGHT-aligned and run past their label, so they
    take a small fixed margin to the left of the label instead.

    Returns None if the header is not the 8-token shape we know, so the caller
    falls back to COLS rather than inventing a layout.
    """
    toks = [(x0, t) for x0, _x1, t in hrow]
    if tuple(t for _x, t in toks) != _HEADER:
        return None
    qty_x, sku_x, unit_x, item_x, uprice_x, _price_x, gst_x, amt_x = (x for x, _t in toks)
    cols = [("qty", 0.0),
            ("sku", (qty_x + sku_x) / 2),
            ("unit", (sku_x + unit_x) / 2),
            ("desc", (unit_x + item_x) / 2),
            ("price", uprice_x - 6),
            ("gst", gst_x - 6),
            ("amt", amt_x - 10)]
    if any(b <= a for (_, a), (_, b) in zip(cols, cols[1:])):
        return None
    return cols


# A picking instruction Stowaway typed onto the ORDER, which FFT reprints inside
# the ITEM cell: "Zucchini Green 0.5Kg please", "Tomatoes Roma half box please",
# "please make sure all product are". It is not part of the product name, but it
# reaches the chef's picker as one — and worse, it is a second "spelling" of the
# code's identity, so the same product lands in the cost book twice.
#
# Cutting at the courtesy word is safe: no produce line is named "please" or
# "thank you", and FFT's own product names never contain them. If the note IS the
# whole cell the description comes back empty, which is correct — the caller then
# falls back to the neighbour stitch or the SKU, as it does for any blank cell.
#
# NOT stripped: a trailing size the note left behind ("Chillies Red Long 200g",
# "Tomatoes Roma half box"). That was measured and deliberately left alone —
# pack size is read from raw_uom, never from the description, so these are
# cosmetic and cost-neutral (all three codes resolve to the same pack and the
# same $/kg as their clean spelling). Guessing at trailing sizes with a regex
# risks eating a REAL one, e.g. "Mushroom King Brown (200G Punnet)", where the
# size is the catalogue name — the exact mistake that once booked a 200 g punnet
# as 800 g.
_ORDER_NOTE = re.compile(r"\s*\b(?:please|thank\s+you|thanks)\b.*$", re.I)


def _strip_order_note(desc: str) -> str:
    return _ORDER_NOTE.sub("", desc or "").strip()


def _split_sku(cell: str) -> tuple[str, str]:
    """
    -> (code, swallowed_tail)

    THE SKU CELL SOMETIMES SWALLOWS THE UNIT. FFT's UNIT column does not always
    start right of the 143pt boundary, so bucket() files the unit word under
    "sku" and the code comes out as "CLKG Kilogram" while the unit cell reads
    empty. The unit-stitch below already knew about this ("the SKU cell absorbs
    'Kilogram'") but only defended the UNIT — the CODE kept the extra word.

    That is silent and expensive, because supplier_code is the product IDENTITY.
    "CLKG" and "CLKG Kilogram" are the same carrot, but they become TWO cost-book
    entries: the price history splits, the chef's picker shows the item twice,
    and build_ingredients' "fullest description across the spellings of this
    identity" consolidation can no longer see across them — which is why the
    feed carries fragments like "Large" (Carrot Large), "Ruby Red" (Grapefruit
    Ruby Red) and "Baby Gem" (Lettuce Baby Gem) as if they were product names.

    94 of FFT's 186 distinct codes carried a swallowed word before this fix, and
    the regression suite could not see ANY of it: every one of those invoices
    reconciles to its printed total to the cent, so FFT scored 52/52 (100%) the
    whole time. The harness checks money, not identity.

    An FFT code is a single alphanumeric token (CLKG, TR10BX, AH20T), so the
    first whitespace-delimited token is the code and anything after it is the
    layout bleed — handed back so the caller can use it as the unit.
    """
    parts = (cell or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


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

    cols = _cols_from_header(rows[hi]) or COLS

    def is_money(row):
        cc = pdf_text.bucket(row, cols)
        return (_m(cc["qty"]) is not None and _m(cc["price"]) is not None
                and _m(cc["amt"]) not in (None, Decimal("0")))

    body = rows[hi + 1:]
    items = []
    for idx, r in enumerate(body):
        c = pdf_text.bucket(r, cols)
        qty, price, amt = _m(c["qty"]), _m(c["price"]), _m(c["amt"])
        if qty is None or price is None or amt is None:   # not a stock money row
            continue
        if amt == 0:                                      # substituted / zero-qty
            continue
        # FFT prints the money row in the MIDDLE of a wrapped description, so when
        # this row has no description of its own, stitch in the desc from the rows
        # immediately above and below (which carry no money).
        # Strip the order note BEFORE deciding the cell is empty: on AH20T the
        # whole ITEM cell was the note ("please make sure all product are"), so a
        # strip afterwards would leave nothing and the line would fall back to the
        # bare SKU. Stripping first lets it stitch the real name off the
        # neighbouring rows instead, which is what the wrap handling is for.
        desc = _strip_order_note(c["desc"])
        if not desc:
            parts = []
            if idx - 1 >= 0 and not is_money(body[idx - 1]):
                parts.append(_strip_order_note(pdf_text.bucket(body[idx - 1], cols)["desc"]))
            if idx + 1 < len(body) and not is_money(body[idx + 1]):
                parts.append(_strip_order_note(pdf_text.bucket(body[idx + 1], cols)["desc"]))
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
        sku, sku_tail = _split_sku(c["sku"])
        unit = c["unit"].strip()
        # The word the SKU cell swallowed IS this row's unit, and it is better
        # evidence than the neighbour-stitch below because it came off this very
        # line. Only trust it when it actually names a unit — same guard, same
        # reason as the stitch.
        if not unit and names_a_unit(sku_tail):
            unit = sku_tail
        if not unit:
            uparts = []
            if idx - 1 >= 0 and not is_money(body[idx - 1]):
                uparts.append(pdf_text.bucket(body[idx - 1], cols)["unit"].strip())
            if idx + 1 < len(body) and not is_money(body[idx + 1]):
                uparts.append(pdf_text.bucket(body[idx + 1], cols)["unit"].strip())
            stitched = " ".join(p for p in uparts if p).strip()
            # Only if it actually NAMES a unit. On INB00109089 the layout shifts
            # (the SKU cell absorbs "Kilogram") and the neighbours' unit cells
            # hold description text — stitching that gave "Cabbage 500g" and made
            # a per-kilogram line a 500 g pack. A measure alone is not enough.
            unit = stitched if names_a_unit(stitched) else ""
        cb = CostBasis.PER_KG if re.search(r"kilo|kg", unit, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or sku, qty=qty, line_total_incl=amt + g,
            unit_price_incl=price, pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST if g > 0 else TaxTreatment.GST_FREE),
            cost_basis=cb, supplier_code=sku or None, raw_uom=unit or None, gst_amount=g))
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
