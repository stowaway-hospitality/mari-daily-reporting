"""
Deni Foods — deterministic parser (coordinate-based).

Columns:  Qty | Unit | Item | Description | Unit Price | Disc | GST | Total

The money column ("Total") is INCLUSIVE of GST and the "Unit Price" column is
EX — verified on every corpus invoice: 15.6 Kg x $19.89 = $310.30 ex, GST
$31.03, Total column $341.33, and sum(Total column) equals the printed
"TOTALS:" line to the cent. Line totals therefore go straight into
line_total_incl with no conversion; the unit price is multiplied by 1.1 on a
taxable line so unit_price_incl and line_total_incl are on the same footing
(the foodlink.py convention).

THE INVOICE TOTAL IS READ FROM THE "TOTALS:" ROW AND NOWHERE ELSE. Deni prints
"Total Amount Outstanding  $1,029.49" a few rows lower — the whole ACCOUNT
balance, not this bill. It is larger, it is a plausible-looking dollar figure
with a "Total" label beside it, and taking it would reconcile nothing while
booking a payable three times the real one. The TOTALS: row is structural
(Sale Amount | GST | Total, in that order) so it is read by position.

Deni Foods is KITCHEN FOOD — the Smoked Mozzarella & Basil Arancini on the
Stowaway menu — and until this parser existed not one Deni invoice had ever
reached data/invoices, so that product's cost was never in the cost book.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

MONEY = re.compile(r"-?\d[\d,]*\.?\d*")
EXTRA_DESC = re.compile(r"fuel\s*levy|freight|delivery|cartage|surcharge", re.I)

# A wrapped description sits on its own row at the description column's own left
# edge. The neighbours that are ALSO description-only rows — "CARTON QTY: 0",
# "GOODS DELIVERED BY MARIO", "This Invoice", "Print Name::" — all sit 60pt or
# more to the right of it, so the indent separates them with a wide margin. The
# indent is derived per invoice (foodlink.py::_desc_x), never hard-coded: three
# suppliers in this tree have now rotted on literal x-positions.
CONT_INDENT_TOL = 2.0

# Deni's ERP prints the line's TAX CODE as the first token of the description
# cell ("GST SMOKED MOZZARELLA & BASIL ARANICNI"), and it is confirmed by that
# line's own GST column being non-zero. It is not part of the product name, and
# leaving it in would put "GST Smoked Mozzarella..." in front of the chef and
# split the product's identity the moment a line arrives without one.
#
# Only the three codes that cannot begin a food name are stripped. "FREE" is
# deliberately NOT in this list — "FREE RANGE EGGS" is a real product, and the
# lesson this tree keeps re-learning (fresh_fruit_team, gulli, foodlink) is that
# a greedy rule which eats a real word is far more expensive than a token left
# in place. Stripping is cost-neutral either way: the tax treatment is decided
# by the GST column's arithmetic below, never by this token.
_TAXCODE = re.compile(r"^(GST|FRE|EXP)\s+")


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


def _cols_from_header(hrow):
    """
    Derive column x-boundaries from the header row's own word positions.

    The header reads "Qty Unit Item Description Unit Price Disc GST Total", so
    "Unit" appears TWICE — once as the UOM column at x=59 and once as the first
    word of "Unit Price" at x=427. Taking the first occurrence for both would
    collapse the whole right-hand side of the table onto the UOM column, so the
    two are separated by ORDER of appearance rather than by label text.

    Money columns are right-aligned and their values start left of their own
    label, hence the negative offsets. Returns None if the header is not the
    shape we know, so the caller fails rather than inventing a layout.
    """
    at = {}
    units = []
    for x0, _x1, t in hrow:
        k = t.rstrip(".")
        if k == "Unit":
            units.append(x0)
        at.setdefault(k, x0)
    try:
        qty_x, item_x, desc_x = at["Qty"], at["Item"], at["Description"]
        disc_x, gst_x, total_x = at["Disc"], at["GST"], at["Total"]
    except KeyError:
        return None
    if not units:
        return None
    uom_x = units[0]
    # "Unit Price": the second "Unit". If a future template drops the word and
    # prints only "Price", fall back to that label.
    unitprice_x = units[1] if len(units) > 1 else at.get("Price")
    if unitprice_x is None:
        return None
    cols = [("qty", 0.0), ("uom", (qty_x + uom_x) / 2), ("item", (uom_x + item_x) / 2),
            ("desc", (item_x + desc_x) / 2), ("price", unitprice_x - 20),
            ("disc", disc_x - 15), ("gst", gst_x - 15), ("total", total_x - 22)]
    # Boundaries must be strictly increasing or bucket() silently mis-assigns.
    if any(b <= a for (_, a), (_, b) in zip(cols, cols[1:])):
        return None
    return cols


def _desc_x(rows, cols):
    """x of the description's first word on the first LINE ITEM of this invoice."""
    lo, hi = cols[3][1], cols[4][1]          # desc boundary .. price boundary
    for r in rows:
        c = pdf_text.bucket(r, cols)
        if _m(c["qty"]) is None or _m(c["total"]) is None:
            continue
        for x0, _x1, _t in r:
            if lo <= x0 < hi:
                return x0
    return None


def _labelled(rows, *labels):
    """The rightmost token on the first row carrying every one of `labels`."""
    for r in rows:
        toks = [t for _, _, t in r]
        if all(lb in toks for lb in labels):
            return toks[-1]
    return None


@register("denifoods.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    ref = _labelled(rows, "Invoice", "No:") or ""
    date = None
    raw_date = _labelled(rows, "Date:")
    if raw_date and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", raw_date):
        date = datetime.strptime(raw_date, "%d/%m/%Y").date()

    # VENUE FROM THE "Sold To:" ROW ONLY. Deni prints a Deliver To block as
    # well, and the two are not always the same venue — an invoice billed to one
    # venue and dropped at another would otherwise be booked against the wrong
    # Lightspeed product namespace (the netsuite/bacchus lesson, triage item 41).
    sold = ""
    for r in rows:
        toks = [t for _, _, t in r]
        if "Sold" in toks and "To:" in toks:
            sold = " ".join(toks)
            break
    scope = sold or flat
    venue = (Venue.MARILYNAS if re.search(r"marilyna", scope, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", scope, re.I)
             else Venue.STOWAWAY if re.search(r"stowaw", scope, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Qty" in toks and "Description" in toks and "Total" in toks and "Item" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Deni Foods: header row not found")

    cols = _cols_from_header(rows[hi])
    if cols is None:
        raise ValueError("Deni Foods: header row not the expected shape")
    body = rows[hi + 1:]
    dx = _desc_x(body, cols)

    items = []
    for r in body:
        toks = [t for _, _, t in r]
        if "TOTALS:" in toks:
            break                      # the table ends; everything below is footer
        c = pdf_text.bucket(r, cols)
        qty, total = _m(c["qty"]), _m(c["total"])
        if qty is None or total is None or qty == 0 or total == 0:
            # A wrapped description — join it to the item above, but only when
            # the desc bucket is the ONLY one with text AND the row starts at
            # this invoice's own description indent. Both conditions are needed:
            # the bank-details banner spills across every bucket, and "CARTON
            # QTY:" is desc-only but sits 60pt to the right.
            if (dx is not None and items and r
                    and abs(r[0][0] - dx) < CONT_INDENT_TOL
                    and [k for k, v in c.items() if v.strip()] == ["desc"]):
                tail = c["desc"].strip()
                if tail:
                    items[-1].description = f"{items[-1].description} {tail}".strip()
            continue
        price = _m(c["price"])
        gst = _m(c["gst"]) or Decimal("0")
        taxable = gst > 0
        f = Decimal("1.1") if taxable else Decimal("1")
        up_incl = ((price * f) if price is not None else total / qty).quantize(Decimal("0.0001"))
        desc = _TAXCODE.sub("", c["desc"].strip())
        is_extra = bool(EXTRA_DESC.search(desc))
        uom = c["uom"]
        cb = CostBasis.PER_KG if re.fullmatch(r"KG|KILO(GRAM)?|L|LT|LITRE", uom, re.I) \
            else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["item"], qty=qty, line_total_incl=total,
            unit_price_incl=up_incl, pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if taxable else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else cb,
            supplier_code=None if is_extra else (c["item"] or None),
            raw_uom=uom or None, gst_amount=gst if taxable else None))
    if not items:
        raise ValueError("Deni Foods: no line items parsed")

    # "TOTALS:  <Sale Amount>  <GST>  <Total>" — read by POSITION, not by the
    # word "Total", because "Total Amount Outstanding" is the account balance.
    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "TOTALS:" in toks:
            nums = [_m(t) for t in toks if _m(t) is not None]
            if len(nums) >= 3:
                total_incl = nums[-1]
            break
    if total_incl is None:
        raise ValueError("Deni Foods: TOTALS row not found")

    return Invoice(
        supplier_key="deni_foods", supplier_name_raw="Deni Foods Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
