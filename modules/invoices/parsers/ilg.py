"""
Independent Liquor Group (ILG) — deterministic parser (coordinate-based).

Columns:  Code | Description | Pack | Qty | Cost | Total | FRT/case | LUC ex GST | TOT inc GST
The right-most "TOT inc GST" column already has this line's share of freight,
fuel levy and GST folded in, so the column sums straight to the footer "Total"
(inc) — no separate freight/GST EXTRA lines, or we'd double-count. Venue from
the "Bill To" (left) block, not "Deliver To" on the right (Zak: billed-to wins).
Ignore the "Discounted Invoice Total" (a pay-early direct-debit figure).

The Qty cell is CASES / LOOSE SINGLES, exactly like Paramount's Case/Bottle
column, and it is read as such (see units_on_line) — it used to be split on "/"
and the SINGLES half kept, which read "2/0" as zero and then forced it to 1.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 0), ("desc", 65), ("pack", 225), ("qty", 278), ("cost", 308),
        ("total", 355), ("frt", 405), ("luc", 448), ("totinc", 498)]
MONEY = re.compile(r"^-?[\d,]+\.?\d*$")

# The Qty cell in its two shapes: "0/2" (0 cases, 2 loose bottles) and a bare
# "1" (that many whole cartons/kegs of whatever the Pack cell describes).
QTY_SPLIT = re.compile(r"^(\d+)\s*/\s*(\d+)$")
# The Pack cell: "6x700ML", "24x330ML", "1xKEG50", "1x15LT" -> units per carton.
PACK_RE = re.compile(r"^(\d+)\s*[xX]")
CENT = Decimal("0.02")
# A broken carton costs MORE per bottle than a whole one: ILG adds a repack
# surcharge, measured at 2.43%-2.61% on all 141 repack lines in the corpus.
# The ceiling is ~2x that; the floor is "never cheaper than the carton rate".
REPACK_MIN, REPACK_MAX = Decimal("1"), Decimal("1.05")


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _first_money(cell):
    """First numeric token in a column cell.

    The Cost/Total cells carry a bare "W" (WET) or "E" (GST-exempt) flag beside
    the figure on wine and exempt lines — 32 of 344 corpus lines. Parsing the
    joined cell throws those away entirely; taking the first numeric token keeps
    the figure. A flag glued to the number ("49.63W") still reads as nothing,
    which is the safe direction: the line simply stays unproved.
    """
    for tok in (cell or "").split():
        v = _m(tok)
        if v is not None:
            return v
    return None


def units_on_line(pack: str, qty_cell: str, cost, total):
    """How many DESCRIPTION-sized units (bottles / cans / kegs) this line bought.

    THE DEFECT THIS FIXES
    ---------------------
    The Qty cell is cases/singles. The old code did `qty_cell.split("/")[-1]`,
    which reads the SINGLES half — so a bare "1" (one carton) became 1 unit and
    a "2/0" would become 0 and then be forced to 1. On invoice 03712630 a whole
    case of ROOSTER ROJO TEQUILA (6x700ML, Qty "1", $346.39 inc) was booked as
    ONE bottle at $346.39; the same invoice's BOMBAY DRY GIN ("0/2", $101.70)
    was booked correctly at $50.85 a bottle. Same code, same page, alternating
    basis, no marker in the description — and $346.39 sits inside the $0.10-$500
    per_unit sanity bounds, so nothing complained. Across the 54-invoice corpus
    148 of 344 stock lines ($21,950 inc GST) carried a unit price overstated by
    exactly the carton count: x6, x8, x12, x16, x24 or x30.

    WHY THIS IS ARITHMETIC AND NOT A GUESS
    --------------------------------------
    ILG states the CARTON cost and the ex-GST line Total on every line, so the
    proposed count is checkable against two money columns the supplier printed:

        whole cartons (Qty "N", or "N/0"):   cost x cases == total
        broken carton (Qty "0/M"):           (total - cost x cases) / M, over the
                                             carton rate cost/per, lands in
                                             [1.00, 1.05] — the repack surcharge

    Measured over data/invoice_corpus/ilg (54 PDFs, 344 stock lines): the carton
    identity holds on 199 of 199 whole-carton lines and fails on none; the repack
    ratio lands in [1.0243, 1.0261] on 141 of 141 repack lines. Independently,
    ILG's own footer states "Cases & Repacks: A B  Kegs: C" — the case and keg
    counts derived this way match the stated footer on 50 of 50 invoices that
    print it, which is the supplier confirming that a bare "1" means one CARTON.

    A miscount can only be off by a factor of `per` (6, 8, 12, 16, 24, 30), and
    no such factor fits inside a 5% band, so the check cannot be fooled by the
    error it exists to catch. If it does not prove, this returns None and the
    caller refuses to assert a count.

    -> Decimal(units) | None
    """
    pm = PACK_RE.match((pack or "").strip())
    if not pm or cost is None or total is None or cost <= 0 or total <= 0:
        return None
    per = int(pm.group(1))
    if per <= 0:
        return None
    cell = (qty_cell or "").strip().replace(",", "")
    m = QTY_SPLIT.match(cell)
    if m:
        cases, singles = int(m.group(1)), int(m.group(2))
    elif cell.isdigit():
        cases, singles = int(cell), 0      # bare count = that many whole cartons
    else:
        return None
    units = cases * per + singles
    if units <= 0:
        return None

    if singles == 0:                       # whole cartons only — exact identity
        if abs(cost * Decimal(cases) - total) > CENT:
            return None
        return Decimal(units)

    # Broken carton: the money not explained by whole cartons buys `singles`
    # bottles at the repack rate, which must sit just above the carton rate.
    residual = total - cost * Decimal(cases)
    if residual <= 0:
        return None
    ratio = residual / (Decimal(singles) * (cost / Decimal(per)))
    if ratio < REPACK_MIN or ratio > REPACK_MAX:
        return None
    return Decimal(units)


def _venue(rows) -> Venue:
    # "Bill To" is the left block (x < 300); "Deliver To" is on the right.
    start = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Bill" in toks and "To:" in toks:
            start = i
            break
    blob = ""
    if start is not None:
        for r in rows[start:start + 4]:
            blob += " " + " ".join(t for x0, _, t in r if x0 < 300)
    if re.search(r"marilyna", blob, re.I):
        return Venue.MARILYNAS
    if re.search(r"gatt?os", blob, re.I):
        return Venue.HARRY_GATOS
    if re.search(r"stowaway", blob, re.I):
        return Venue.STOWAWAY
    return Venue.UNKNOWN


@register("ilg.com.au", "members.ilg.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Invoice\s+No\.?\s*(\d+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Invoice\s+Date\s*(\d{1,2}-[A-Z]{3}-\d{4})", flat, re.I)
    date = datetime.strptime(m.group(1).upper(), "%d-%b-%Y").date() if m else None
    venue = _venue(rows)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Code" in toks and "Pack" in toks and ("Qty" in toks or "Cost" in toks):
            hi = i
            break
    if hi is None:
        raise ValueError("ILG: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        code = c["code"].strip()
        # an item line = a product code (NNN-NNNN); the TOT-inc column (right of
        # x498) carries this line's GST-inclusive total, freight+levy already
        # allocated, so the column sums straight to the footer Total.
        if not re.match(r"\d{3}-\d{3,4}", code):
            continue
        totinc = None
        for x0, _, t in r:
            if x0 >= 498:
                v = _m(t)               # skip trailing markers like "3FA"
                if v is not None:
                    totinc = v
        if totinc is None or totinc == 0:
            continue
        qraw = c["qty"].strip()
        qty = units_on_line(c["pack"], qraw, _first_money(c["cost"]),
                            _first_money(c["total"]))
        notes = []
        if qty is None:
            # The count is not provable from the supplier's own columns, so we
            # do not assert one. One unit at the whole line total is the only
            # reading that cannot UNDER-cost us: it is either right (a 1x pack)
            # or high, and high trips the sanity bounds into review. Dividing by
            # an unproved count is how a case became a bottle in the first place.
            qty = Decimal("1")
            notes.append("unit count not provable from Pack/Qty/Cost/Total — "
                         "priced as one unit at the full line total")
        items.append(InvoiceLine(
            description=c["desc"] or code, qty=qty, line_total_incl=totinc,
            unit_price_incl=(totinc / qty).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.STOCK, tax_treatment=TaxTreatment.GST,
            cost_basis=CostBasis.PER_UNIT, supplier_code=code or None,
            raw_qty=qraw or None, raw_uom=(c["pack"].strip() or None),
            notes=notes))
    if not items:
        raise ValueError("ILG: no line items parsed")

    # Grand total = footer "Total" in the right-hand totals column (x ~303).
    # Not "Sub Total:" (x~245) nor "Discounted Invoice Total:" (left block).
    total_incl = None
    for r in rows:
        for x0, _, t in r:
            if t == "Total" and 295 <= x0 <= 315:
                nums = [_m(tt) for _, _, tt in r if _m(tt) is not None]
                if nums:
                    total_incl = nums[-1]
    if total_incl is None:
        raise ValueError("ILG: invoice total not found")

    return Invoice(
        supplier_key="ilg", supplier_name_raw="Independent Liquor Group",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
