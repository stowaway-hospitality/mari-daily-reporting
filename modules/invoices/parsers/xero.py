"""
Xero-issued invoices — deterministic parser (coordinate-based).

Xero mails on behalf of MANY suppliers from one address, so this is the first
parser where the sender domain does not name the vendor. That matters more than
it sounds: get the vendor wrong and several suppliers share one identity and
their price histories merge, which is the most expensive class of bug in this
codebase (see the 2026-08-15 entries in parser_regression's triage log).

THE VENDOR IS THE ABN THAT IS NOT OURS.
The obvious key — "the first ABN on the page" — is a trap. Xero prints the
CUSTOMER'S ABN above the supplier's on about half these templates, so Stowaway's
own 17 606 243 921 comes out first on invoices from Urbun Bakery, Canton Group,
Twin Fin Studio, MODA Sparkling AND Philter. Keying on it would file five
suppliers as one.

Every invoice does carry the vendor's ABN somewhere, and an ABN is a real
identifier rather than a spelling. So: take every ABN on the page, drop OUR own,
and if exactly one remains and it is registered below, that is the vendor.
Anything else — no ABN, an unregistered one, or two candidates — returns None and
the document stays in Review. This parser never invents a supplier identity.
(The ambiguous case is real: one SYMSAFE credit note references a second party.)

Layout. The table header names its own columns and the shape varies by supplier —
an "Item" code column may or may not exist, "Unit Price" is sometimes bare
"Price", the penultimate column is "GST" or "Discount" or "Tax", and one gas
supplier writes "Quantity(L)". Boundaries are therefore derived from the header
row and there are no hard-coded x-positions, which is the mistake that rotted
Foodlink and Fresh Fruit Team on the day this was written.

    [Item] | Description | Quantity | [Unit] Price | [GST|Discount|Tax] | Amount AUD

Line amounts are EX-GST and GST is applied at the FOOTER, not per line
("TOTAL GST 10%"). Reconcile target is the stated total, which the newer template
calls "Amount due" rather than "TOTAL AUD".
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

_ABN = re.compile(r"ABN[:\s]*((?:\d[ ]?){11})")
_BRACKETED = re.compile(r"\(.*?\)")
_LETTERS = re.compile(r"[^A-Za-z]")


def _label(tok: str) -> str:
    """A header label reduced to what identifies it.

    Xero qualifies a column head with a unit in brackets — Cordless Filter's gas
    invoices say "Quantity(L)" — so the brackets come off BEFORE the letters do.
    Stripping only non-letters would give "QuantityL", which matches nothing and
    is why that supplier's invoices would not parse.
    """
    return _LETTERS.sub("", _BRACKETED.sub("", tok or ""))

# OUR OWN ABNs. Anything here is the customer side of the invoice and can never
# be the vendor. 17 606 243 921 is Stowaway Freshwater Pty Ltd and appears on
# invoices addressed to Harry Gatos too.
CUSTOMER_ABNS = {"17606243921"}

# Vendor ABN -> (supplier_key, display name). EXPLICIT on purpose: an unknown
# vendor must fall through to Review rather than be guessed at. Keys reuse the
# ones already in data/invoices where the supplier is known by another route
# (Urbun Bakery bills as Mallia Industries; Cordless Filter Machine is Cookers).
ABN_SUPPLIER: dict[str, tuple[str, str]] = {
    # kitchen food
    "25617284705": ("mallia_industries", "Urbun Bakery"),
    "92634099844": ("canton_group", "Canton Group Pty Ltd"),
    # beverage
    "53158357450": ("grifter", "The Grifter Brewing Company Pty Ltd"),
    "39616427340": ("philter", "Philter Brewing Pty Ltd"),
    "15145358836": ("moda_sparkling", "MODA Sparkling"),
    "56167206260": ("sigurd_wines", "Sigurd Wines Pty Ltd"),
    # services / consumables
    "55096609166": ("speed_gas", "Speed Gas Pty Limited"),
    "33110257086": ("cookers", "Cordless Filter Machine Pty Ltd"),
    "83105791419": ("symsafe", "Symsafe Pty Ltd"),
    "27616048643": ("beerline_cleaning", "The Beerline Cleaning Company Pty Ltd"),
    "50677241833": ("twin_fin_studio", "Twin Fin Studio Pty Ltd"),
}


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+\.\d{2}", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def vendor_from_abn(text: str) -> tuple[str, str] | None:
    """-> (supplier_key, display name), or None if the vendor is not identifiable."""
    abns = [re.sub(r"\s", "", a) for a in _ABN.findall(text or "")]
    theirs = [a for a in dict.fromkeys(abns) if a not in CUSTOMER_ABNS]
    if len(theirs) != 1:
        return None            # none found, or ambiguous -> Review, never a guess
    return ABN_SUPPLIER.get(theirs[0])


def _cols_from_header(hrow):
    """Boundaries from the header's own word positions.

    Text columns are left-aligned under their label; the money columns are
    right-aligned and run past theirs, so they take a margin to the left. Header
    labels are matched on their LETTERS only, so "Quantity(L)" is still Quantity.
    Returns None if this is not a shape we know.
    """
    at = {}
    for x0, _x1, t in hrow:
        at.setdefault(_label(t), x0)
    if not {"Description", "Quantity", "Amount"} <= set(at):
        return None
    price_x = at.get("Unit", at.get("Price"))
    if price_x is None:
        return None
    cols = [("item", 0.0)] if "Item" in at else []
    cols.append(("desc", (at["Item"] + at["Description"]) / 2 if "Item" in at else 0.0))
    cols.append(("qty", at["Quantity"] - 18))
    cols.append(("price", price_x - 12))
    mid_x = at.get("GST", at.get("Discount", at.get("Tax")))
    if mid_x is not None:
        cols.append(("mid", mid_x - 10))
    cols.append(("amt", at["Amount"] - 12))
    if any(b <= a for (_, a), (_, b) in zip(cols, cols[1:])):
        return None
    return cols


@register("post.xero.com")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    who = vendor_from_abn(flat)
    if who is None:
        raise ValueError("Xero: vendor ABN not identifiable — refusing to guess")
    supplier_key, supplier_name = who

    m = re.search(r"Invoice\s+Number\s*\n?\s*(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    date = None
    m = re.search(r"Invoice\s+Date\s*\n?\s*(\d{1,2}\s+\w{3}\s+\d{4})", flat, re.I)
    if m:
        try:
            date = datetime.strptime(m.group(1), "%d %b %Y").date()
        except ValueError:
            pass

    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"har+y?\s*gat+os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I)
             else Venue.UNKNOWN)

    hi = cols = None
    for i, r in enumerate(rows):
        if {"Description", "Quantity", "Amount"} <= {_label(t) for _, _, t in r}:
            c = _cols_from_header(r)
            if c:
                hi, cols = i, c
                break
    if hi is None:
        raise ValueError("Xero: header row not found")

    # Footer totals. Lines are ex-GST and the tax is added once at the bottom.
    gst_total = Decimal("0")
    total_incl = amount_due = None
    for r in rows:
        toks = {t.lower() for _, _, t in r}
        raw = [t for _, _, t in r]
        nums = [v for _, _, t in r if (v := _m(t)) is not None]
        if not nums:
            continue
        # "Total GST 10%  27.05" is the tax. A second row "Total GST Free 0.00"
        # exists on some templates and is NOT the tax — it is the GST-free
        # subtotal — so the rate token is what identifies the real one.
        if "gst" in toks and any(t.endswith("%") for t in raw):
            gst_total = nums[-1]
            continue
        # "TOTAL AUD", "Total", "Invoice Total AUD". Excluded: "Subtotal" (its own
        # token), any GST row, and "Total Net Payments AUD" — a payments line.
        if ("total" in toks and "gst" not in toks
                and "net" not in toks and "payments" not in toks):
            total_incl = nums[-1]
        elif "amount" in toks and "due" in toks:
            amount_due = nums[-1]
    if total_incl is None:
        total_incl = amount_due          # newer template states only "Amount due"
    if total_incl is None:
        raise ValueError("Xero: invoice total not found")

    raw = []
    for r in rows[hi + 1:]:
        toks = {t.lower() for _, _, t in r}
        if "subtotal" in toks or "total" in toks:
            break                        # the totals block ends the line table
        c = pdf_text.bucket(r, cols)
        qty, amt = _m(c.get("qty")), _m(c.get("amt"))
        if qty is None or amt is None or amt == 0 or qty == 0:
            continue
        raw.append((qty, amt, (c.get("desc") or "").strip(),
                    (c.get("item") or "").strip() or None,
                    "free" in (c.get("mid") or "").lower()))
    if not raw:
        raise ValueError("Xero: no line items parsed")

    # IS THE AMOUNT COLUMN EX-GST OR INC-GST? Xero prints BOTH, and which one you
    # are looking at is not stated in any one place:
    #
    #   Grifter    Subtotal 270.50 + "TOTAL GST 10% 27.05" -> TOTAL AUD 297.55
    #              amounts are EX; the tax is added at the foot.
    #   Speed Gas  one line at 64.40, "INCLUDES GST 10% 5.85", TOTAL AUD 64.40
    #              the same-looking column is already INC.
    #
    # Guessing by keyword is how this goes wrong quietly, so decide by ARITHMETIC
    # against the printed total and take whichever hypothesis actually reconciles.
    # If neither does, emit the amounts untouched and let the validator refuse the
    # invoice — that is the whole point of validating against the stated total.
    subtotal = sum(a for _q, a, *_ in raw)
    if subtotal == total_incl:
        inclusive = True                      # amounts already carry the GST
    elif gst_total and subtotal + gst_total == total_incl:
        inclusive = False                     # tax added at the foot
    else:
        inclusive = True                      # unknown shape: change nothing

    items = []
    for qty, amt, desc, code, gst_free in raw:
        taxable = (not inclusive) and gst_total > 0 and not gst_free
        incl = (amt * Decimal("1.1")).quantize(Decimal("0.01")) if taxable else amt
        is_extra = bool(re.search(r"freight|delivery|cartage|fuel\s*levy", desc, re.I))
        items.append(InvoiceLine(
            description=desc or code or "", qty=qty, line_total_incl=incl,
            unit_price_incl=(incl / qty).quantize(Decimal("0.0001")),
            pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST if (taxable or (inclusive and gst_total > 0
                                                            and not gst_free))
                           else TaxTreatment.GST_FREE),
            cost_basis=CostBasis.UNKNOWN if is_extra else CostBasis.PER_UNIT,
            supplier_code=None if is_extra else code))

    return Invoice(
        supplier_key=supplier_key, supplier_name_raw=supplier_name,
        invoice_ref=ref, invoice_date=date, total_incl=total_incl,
        lines=items, venue=venue)
