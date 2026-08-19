"""
MYOB — deterministic parser (coordinate reader), TWO templates, one platform.

noreply@apps.myob.com is a PLATFORM sender, not a supplier: every vendor that
bills through MYOB mails from it. That is the same shape as post.xero.com
(triage log items 16/17), ordermentum.com and sent-via.netsuite.com, so it takes
the same answer — identify the vendor by THE ABN THAT IS NOT OURS, from an
explicit registry, and REFUSE rather than guess. CUSTOMER_ABNS, ABN_SUPPLIER,
SERVICE_SUPPLIERS and vendor_from_abn are imported from the xero parser rather
than copied: our own two ABNs are one fact, and a second copy of them is a
second thing to forget to update (item 19(a) is what forgetting costs).

WHY THIS SUPPLIER, TODAY. Triage log item 40 counted 8 documents stuck on
apps.myob.com and named AQUARIUS FISHERIES among them — a seafood supplier,
already in KITCHEN_SUPPLIERS since item 12, so it feeds recipe costs. Its own
statement (corpus 60977141183f) lists TEN invoices between 2026-01-08 and
2026-04-30 — roughly fortnightly — while data/invoices holds exactly TWO, both
LLM-extracted back when this task still spent credit. Its costs have not been
reaching the cost book at all.

TWO TEMPLATES ARRIVE ON THIS DOMAIN and they are told apart by their HEADER, not
by the vendor, because a vendor can re-template (that is the item 15 lesson):

  AQUARIUS — MYOB's customised print form, with a tear-off remittance slip:
    Quantity | Item Code | Description | Unit Price (ex-GST) | CARTON COUNT | Total (ex-GST)
            1  APDW31      White Prawn Meat 31/40    $175.00       1 Box        $175.00
    ... then "Total: $175.00" and, down in the remittance slip, "Amount Due:".

  MODERN — MYOB's standard hosted invoice (VMA Ventilation, and any other
  vendor on the current template):
    Item ID | Description | Qty | Unit price (excluding tax) | Tax | Amount ($)
    ... then Subtotal (exc. tax) / Tax / Total Amount (inc. tax) / Total paid /
    Balance due.

COLUMN BOUNDARIES ARE DERIVED FROM THE HEADER'S OWN WORD POSITIONS, never
hard-coded. Foodlink (item 15), Fresh Fruit Team (item 1) and Gulli (item 22)
each rotted on literal x-constants — twice at a re-template and once because the
table is laid out to fit its content and moves invoice to invoice. There is no
reason to believe a fourth would be different, and the derivation costs nothing.
The margins used here (-18 before a right-aligned quantity, -12 before a
right-aligned money column, the midpoint between the item and description
anchors for a centred description cell) are the xero parser's, reused
deliberately so there is one convention in the tree rather than five.

THE CENTRED DESCRIPTION IS THE TRAP ON THE AQUARIUS TEMPLATE, and it is the
item-1/22/33 defect class waiting to happen. Its description cell is CENTRED,
not left-aligned: on 318421f41e14 the label "Description" starts at x=224.5 but
the value "White Prawn Meat 31/40" starts at 184.6 — 40pt to the LEFT of its own
label, and only 34pt right of the end of the Item Code column. A boundary taken
at the label (or at the midpoint of the label GAP, 187.7) puts the first word of
every description into the Item Code cell, which still reconciles to the cent
and would still read 100% here. The boundary is the midpoint between the ITEM
and DESCRIPTION anchors instead, which sits at 165.3 — clear of the code value's
end (131.1) and clear of the description value's start (184.6).

TAX IS DECIDED BY ARITHMETIC, not by a keyword — the xero rule (item 17). The
Aquarius column says "(ex-GST)" and its seafood is GST-free, so ex == inc and
the lines already carry the total; the modern template's Amount column is
genuinely ex and the tax is added at the foot. Whichever hypothesis reconciles
to the PRINTED total wins, and if neither does, nothing is assumed and the
validator refuses the invoice.

THE TOTALS BLOCK IS READ ONLY BELOW THE LINE TABLE, which is a deliberate
difference from the xero parser. The modern template prints the word "GST" in
the TAX column of every line row, so a totals scan over the whole page would
read a line's own amount as the invoice's GST. Bounding the scan structurally
(everything after the row that ends the table) is cheaper and safer than a
keyword exclusion, and it also keeps the Aquarius remittance slip's "Amount
paid: $______" and "Invoice #:" out of the way.

REFUSED, on purpose, and each one stays in Review rather than being forced into
these buckets:
  * Aquarius's monthly STATEMENT (corpus 60977141183f) — already caught upstream
    by run.py::looks_like_statement, verified, so it never reaches this parser.
  * a vendor whose ABN is not registered -> vendor_from_abn returns None.
  * a header this parser does not recognise -> None.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register
from modules.invoices.parsers.xero import (ABN_SUPPLIER, CUSTOMER_ABNS,
                                           SERVICE_SUPPLIERS, invoice_date)

TOL = Decimal("0.02")

# THE DOTTED ABN, and why this is not xero._ABN widened in place.
#
# xero._ABN is r"ABN[:\s]*((?:\d[ ]?){11})" and it finds NOTHING on an Aquarius
# invoice, because that letterhead prints "A.B.N. 46 003 857 618" with stops.
# The obvious fix is to loosen the shared pattern — and that is the change NOT
# made here. vendor_from_abn refuses whenever it sees more than one non-customer
# ABN, so making the shared regex match MORE strings can only ever ADD
# candidates to a document, and adding a candidate to an invoice that currently
# names exactly one vendor turns a passing invoice into a refusal. Xero,
# Ordermentum and NetSuite are 130+ passing corpus documents between them; there
# is no reason to put them at risk to read a supplier none of them sends.
#
# The A.C.N. printed directly above it is NOT a hazard: an ACN is 9 digits
# ("A.C.N. 003 857 618") and this pattern requires 11, so it cannot match.
_ABN_DOTTED = re.compile(r"A\.?\s*B\.?\s*N\.?[:\s]*((?:\d[ ]?){11})", re.I)


def _label(t: str) -> str:
    """A header label reduced to its letters, so '($)' and 'price' compare.

    Brackets come off BEFORE non-letters do — the item-17 lesson, where
    stripping non-letters first turned 'Quantity(L)' into 'QuantityL' and
    matched nothing.
    """
    return re.sub(r"[^A-Za-z]", "", re.sub(r"\(.*?\)", "", t or "")).title()


def _m(s):
    """MONEY: exactly two decimals, optional $ and thousands separators."""
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+\.\d{2}", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _q(s):
    """QUANTITY, which is not money and must not be read with the money reader.

    _m insists on two decimal places and that strictness is load-bearing for the
    AMOUNT column. A quantity is printed however the vendor typed it: Aquarius
    prints '1', the modern template prints '1'. Reading a quantity with _m is
    exactly the defect that kept every Canton Group invoice in Review (item 37).
    """
    s = (s or "").replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _toks(row) -> set[str]:
    """Row tokens, lowercased with punctuation stripped.

    'Total:' and 'Total' are the same label; the Aquarius template writes the
    colon and the modern one does not.
    """
    return {re.sub(r"[^a-z]", "", t.lower()) for _x0, _x1, t in row} - {""}


def _anchors(row) -> dict[str, float]:
    at: dict[str, float] = {}
    for x0, _x1, t in row:
        lab = _label(t)
        if lab:
            at.setdefault(lab, x0)
    return at


def _cols_from_header(row):
    """Boundaries derived from this header row's own word x-positions.

    -> ([(name, x_start), ...], template) or None if this is not a shape we know.
    """
    at = _anchors(row)
    have = set(at)

    # ---- AQUARIUS: Quantity | Item Code | Description | Unit Price | CARTON COUNT | Total
    if {"Quantity", "Item", "Code", "Description", "Unit", "Total"} <= have:
        cols = [
            ("qty", 0.0),
            ("code", at["Item"] - 12),
            # THE CENTRED DESCRIPTION. See the module docstring: the value sits
            # LEFT of its own label, so the boundary is the midpoint between the
            # Item and Description anchors, never the Description anchor itself.
            ("desc", (at["Item"] + at["Description"]) / 2),
            ("price", at["Unit"] - 12),
            ("uom", at["Carton"] - 12) if "Carton" in have else None,
            # 'Total' and its '(ex-GST)' sub-label are two separate words and the
            # sub-label sits FURTHER LEFT (501.7 vs 510.5). Take the leftmost of
            # the two, or the money value overflows past the boundary.
            ("total", min(at["Total"], at.get("Exgst", at["Total"])) - 12),
        ]
        cols = [c for c in cols if c]
        return (cols, "aquarius") if _ascending(cols) else None

    # ---- MODERN MYOB: Item ID | Description | Qty | Unit price | Tax | Amount
    if {"Description", "Qty", "Amount"} <= have and "Unit" in have:
        cols = [
            ("code", 0.0),
            ("desc", (at["Item"] + at["Description"]) / 2 if "Item" in have else 0.0),
            ("qty", at["Qty"] - 18),
            ("price", at["Unit"] - 12),
            ("tax", at["Tax"] - 10) if "Tax" in have else None,
            ("amt", at["Amount"] - 12),
        ]
        cols = [c for c in cols if c]
        return (cols, "modern") if _ascending(cols) else None

    return None


def _ascending(cols) -> bool:
    return all(a < b for (_, a), (_, b) in zip(cols, cols[1:]))


def vendor_from_abn(text: str):
    """-> (supplier_key, display name), or None if the vendor is not identifiable.

    Same rule as xero.vendor_from_abn — every ABN on the page, ours dropped,
    and a REFUSAL unless exactly one candidate remains — over a pattern that
    also reads the dotted form. See _ABN_DOTTED for why the shared one is left
    alone.
    """
    abns = [re.sub(r"\s", "", a) for a in _ABN_DOTTED.findall(text or "")]
    theirs = [a for a in dict.fromkeys(abns) if a not in CUSTOMER_ABNS]
    if len(theirs) != 1:
        return None            # none found, or ambiguous -> Review, never a guess
    return ABN_SUPPLIER.get(theirs[0])


def _norm(t: str) -> str:
    return re.sub(r"[^a-z]", "", (t or "").lower())


def _field(rows, labels: list[str], pattern: str):
    """The value of a LABELLED field, read by coordinate. -> str | None

    Looks for the label tokens consecutively in a row, then takes the first
    value matching `pattern` either (a) to the RIGHT of the label on the same
    row — the Aquarius form's 'Invoice:  185244' — or (b) in one of the next two
    rows AT THE LABEL'S OWN X — the modern template's 'Invoice number | Issue
    date | Due date' header over its value row.

    It is deliberately NOT 'the first thing matching `pattern` after the label'.
    On the modern template that reads straight past 'Issue date' into the DUE
    date sitting beside it, which is how a cost gets booked in the wrong week.
    The x-window (label start - 8 .. label end + 8) is what keeps the three
    side-by-side fields apart; measured, the values sit within 1.5pt of their
    own label's span and 40pt+ from the next one.
    """
    pat = re.compile(pattern)
    n = len(labels)
    for i, r in enumerate(rows):
        toks = [_norm(t) for _x0, _x1, t in r]
        for k in range(len(toks) - n + 1):
            if toks[k:k + n] != labels:
                continue
            for _x0, _x1, t in r[k + n:]:
                if pat.fullmatch(t):
                    return t
            lo, hi_x = r[k][0] - 8, r[k + n - 1][1] + 8
            for rr in rows[i + 1:i + 3]:
                for x0, _x1, t in rr:
                    if lo <= x0 <= hi_x and pat.fullmatch(t):
                        return t
    return None


def _uom_word(cell: str):
    """The UNIT out of a 'CARTON COUNT' cell like '1 Box'.

    The number restates the line quantity; the WORD is the unit. Returning the
    whole cell would put a digit in raw_uom, which the regression harness's
    identity audit reads (correctly) as another column's text having bled in.
    """
    words = re.findall(r"[A-Za-z]+", cell or "")
    return " ".join(words) or None


@register("apps.myob.com")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    who = vendor_from_abn(flat)
    if who is None:
        # Unregistered vendor on a shared platform sender. NEVER guessed — the
        # item 16/17 lesson: 'the first ABN on the page' is ours on half these
        # templates and would file several vendors under one identity.
        raise ValueError("MYOB: vendor ABN not identifiable — refusing to guess")
    supplier_key, supplier_name = who

    hi = cols = template = None
    for i, r in enumerate(rows):
        got = _cols_from_header(r)
        if got:
            hi, (cols, template) = i, got
            break
    if hi is None:
        raise ValueError("MYOB: header row not found")

    # ONE PASS DOWN THE PAGE, and the line table and the totals block are told
    # apart STRUCTURALLY rather than by keyword.
    #
    # A LINE is a row that fills both the quantity and the money column. A
    # TOTALS row is anything below the header that does not. That distinction is
    # what keeps the modern template's per-line "GST" (printed in the Tax column
    # of EVERY line row) from being read as the invoice's tax total — a
    # whole-page keyword scan reads a $50.00 line amount as $50.00 of GST.
    #
    # It also fixes the mirror error: VMA #4403 prints its tax row ("Tax $4.55")
    # ABOVE "Total Amount", with no Subtotal at all, so a totals scan that
    # started at the first subtotal/total/balance row skipped the tax entirely
    # and the invoice came out GST-FREE when it is not.
    money_col = "total" if template == "aquarius" else "amt"
    raw = []
    subtotal = total_incl = amount_due = None
    gst_total = Decimal("0")
    in_table = True
    for r in rows[hi + 1:]:
        t = _toks(r)
        nums = [v for _x0, _x1, w in r if (v := _m(w)) is not None]
        if in_table:
            c = pdf_text.bucket(r, cols)
            qty = _q(c.get("qty"))
            amt = _m(c.get(money_col))
            desc = (c.get("desc") or "").strip()
            if (qty and amt and not ({"subtotal", "total", "balance"} & t)):
                raw.append([qty, amt, desc, (c.get("code") or "").strip() or None,
                            _uom_word(c.get("uom", "")),
                            "free" in (c.get("tax") or "").lower()])
                continue
            # A DESCRIPTION-ONLY ROW IS A WRAPPED CELL, and dropping it is how
            # Foodlink lost the pack size off two thirds of its lines (item 33).
            # Attached only when the row carries nothing but description text
            # and there is already an item to attach it to.
            if desc and raw and not any(
                    (c.get(k) or "").strip() for k in c if k != "desc"):
                raw[-1][2] = (raw[-1][2] + " " + desc).strip()
                continue
        if not nums:
            continue
        if {"subtotal", "total", "balance", "tax", "gst"} & t:
            in_table = False        # the totals block has started
        if "subtotal" in t:
            subtotal = subtotal if subtotal is not None else nums[-1]
        elif ({"tax", "gst"} & t) and not ({"total", "subtotal", "balance"} & t):
            gst_total = gst_total or nums[-1]
        elif "total" in t and not ({"paid", "quantity"} & t):
            # 'Total paid $0.00' is a PAYMENT, not the invoice total. Booking it
            # would state a $0 invoice; booking 'Balance due' instead of 'Total
            # Amount' would understate a partly-paid one.
            total_incl = total_incl if total_incl is not None else nums[-1]
        elif {"amount", "due"} <= t:
            amount_due = amount_due if amount_due is not None else nums[-1]
    if total_incl is None:
        total_incl = amount_due
    if total_incl is None:
        raise ValueError("MYOB: invoice total not found")

    # REF AND DATE ARE READ BY COORDINATE, NOT OUT OF THE FLAT TEXT, and that is
    # not a stylistic preference — both templates lay these out as a label row
    # above (or beside) a value row, and pdftotext's reading order interleaves
    # them with the address block. Two measured failures from doing it the easy
    # way, on these exact documents:
    #   * flat text runs "... Tax Invoice / 7/05/2026 / 185244", so a regex for
    #     "Invoice <number>" returned the DATE as the invoice reference;
    #   * the Aquarius form prints its due date top-left ABOVE the letterhead
    #     (30/06/2026) and repeats "Due date:" in the modern template's footer,
    #     so "the first Date: on the page" is a due date, not an issue date.
    #     Booking that as the invoice date puts the cost weeks late and
    #     mis-orders the price history against it — the item-37(b) mistake.
    ref = _field(rows, ["invoice", "number"], r"\d[\w/-]*") \
        or _field(rows, ["invoice"], r"\d{3,}[\w/-]*") or ""
    date = (_date(_field(rows, ["issue", "date"], _DATE_RE))
            or _date(_field(rows, ["invoice", "date"], _DATE_RE))
            or _date(_field(rows, ["date"], _DATE_RE))
            or invoice_date(flat))

    # VENUE FROM THE PAGE, Marilyna's and Harry Gatos BEFORE Stowaway — the
    # select_fresh rule. Both of those bill to the Stowaway address (Shop 18,
    # 1-3 Moore Rd), so a Stowaway-first match would book every Marilyna's
    # seafood delivery to the wrong venue, and venue picks the product
    # namespace (the item 41 note on Bacchus).
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"har+y?\s*gat+os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I)
             else Venue.UNKNOWN)

    if not raw:
        raise ValueError("MYOB: no line items parsed")

    # EX OR INC? Decided by ARITHMETIC against the printed total (item 17), never
    # by the '(ex-GST)' caption — Aquarius prints that caption on a GST-FREE
    # invoice, where ex and inc are the same number.
    body = sum(a for _q_, a, *_ in raw)
    if abs(body - total_incl) <= TOL:
        inclusive = True
    elif gst_total and abs(body + gst_total - total_incl) <= TOL:
        inclusive = False
    else:
        inclusive = True            # unknown shape: change nothing, let the
                                    # validator refuse it against the total

    items = []
    for qty, amt, desc, code, uom, gst_free in raw:
        taxable = (not inclusive) and gst_total > 0 and not gst_free
        incl = (amt * Decimal("1.1")).quantize(Decimal("0.01")) if taxable else amt
        is_extra = (supplier_key in SERVICE_SUPPLIERS
                    or bool(re.search(r"freight|delivery\s*fee|cartage|fuel\s*levy",
                                      desc, re.I)))
        items.append(InvoiceLine(
            description=desc or code or "", qty=qty, line_total_incl=incl,
            unit_price_incl=(incl / qty).quantize(Decimal("0.0001")),
            pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST
                           if (taxable or (inclusive and gst_total > 0 and not gst_free))
                           else TaxTreatment.GST_FREE),
            # PER_UNIT: a box of prawn meat is ONE purchasable thing at the
            # stated price. The box -> kg conversion belongs in pack_overrides
            # where a human confirms the weight, not in a parser that would be
            # guessing it (the pack_overrides 12x lesson, item 14).
            cost_basis=CostBasis.UNKNOWN if is_extra else CostBasis.PER_UNIT,
            # A SERVICE LINE'S 'Item ID' IS A LINE NUMBER, NOT A PRODUCT CODE.
            # VMA prints '1' there. Emitting that as a supplier_code would mint
            # the identity 'vma:1' and merge every service ever billed under it.
            supplier_code=None if is_extra else code,
            raw_uom=uom))

    return Invoice(
        supplier_key=supplier_key, supplier_name_raw=supplier_name,
        invoice_ref=ref, invoice_date=date, total_incl=total_incl,
        lines=items, venue=venue)


_DATE_RE = r"\d{1,2}/\d{1,2}/\d{4}"


def _date(s):
    """'7/05/2026' -> date. Day-first: these are Australian invoices."""
    from datetime import datetime
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None
