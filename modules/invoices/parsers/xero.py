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
#
# 38 760 949 765 IS ALSO OURS, and missing it is why the 2026-08-15 entry in the
# triage log recorded the SYMSAFE credit note as "an ambiguous vendor ... it
# references a second party". There is no second party: that document carries
# SYMSAFE's ABN and OUR OTHER ONE, so dropping only 17606243921 left two
# candidates and the parser correctly refused to choose. Evidence, not a guess —
# 38 760 949 765 appears in 33 corpus documents and in EVERY ONE of them it is
# printed inside the ship-to block, directly under "STOWAWAY FRESHWATER / SHOP
# 18, 1-3 MOORE ROAD"; it is never on the letterhead side. A wrong entry here is
# the expensive direction (it would blind us to a real vendor), so it was checked
# across the whole corpus rather than on the one invoice that needed it.
CUSTOMER_ABNS = {"17606243921", "38760949765"}

# Vendor ABN -> (supplier_key, display name). EXPLICIT on purpose: an unknown
# vendor must fall through to Review rather than be guessed at. Keys reuse the
# ones already in data/invoices where the supplier is known by another route
# (Urbun Bakery bills as Mallia Industries; Cordless Filter Machine is Cookers).
ABN_SUPPLIER: dict[str, tuple[str, str]] = {
    # kitchen food
    "25617284705": ("mallia_industries", "Urbun Bakery"),
    "92634099844": ("canton_group", "Canton Group Pty Ltd"),
    # beverage
    # Barrel One Coffee Roasters, ABN 41 643 628 844 — PRINTED on its own
    # invoice under "FROM ... 5/36 Campbell Avenue, Cromer, NSW, 2099". Not
    # looked up. It bills through Ordermentum rather than Xero, and the
    # ordermentum parser imports this registry rather than keeping a second
    # copy of our own ABNs.
    "41643628844": ("barrel_one", "Barrel One Coffee Roasters"),
    "53158357450": ("grifter", "The Grifter Brewing Company Pty Ltd"),
    "39616427340": ("philter", "Philter Brewing Pty Ltd"),
    "15145358836": ("moda_sparkling", "MODA Sparkling"),
    "56167206260": ("sigurd_wines", "Sigurd Wines Pty Ltd"),
    # Added 2026-08-15. Every one of these names is PRINTED ON THE PAGE of the
    # invoice carrying that ABN — none is looked up, inferred from a product
    # range, or guessed from a suburb:
    #   98610948813  "Wine Enterprises Pty Ltd", Copacabana NSW (4 invoices)
    #   98146579053  "Australian Wine Company, 31 Drayton Street, Bowden SA"
    #   26681889154  masthead reads "IA WINE & SPIRITS PTY LTD" — the logo eats
    #                the first letters, so the name is taken from the bank block
    #                on the same page, "Account Name: Australia Wine & Spirits
    #                Pty Ltd". Sells Massenez liqueurs; Brighton VIC.
    "98610948813": ("wine_enterprises", "Wine Enterprises Pty Ltd"),
    "98146579053": ("australian_wine_company", "Australian Wine Company"),
    "26681889154": ("australia_wine_spirits", "Australia Wine & Spirits Pty Ltd"),
    # services / consumables
    "48540665321": ("prime_catering_repairs", "Prime Catering Repairs"),
    "55096609166": ("speed_gas", "Speed Gas Pty Limited"),
    "33110257086": ("cookers", "Cordless Filter Machine Pty Ltd"),
    "83105791419": ("symsafe", "Symsafe Pty Ltd"),
    "27616048643": ("beerline_cleaning", "The Beerline Cleaning Company Pty Ltd"),
    "50677241833": ("twin_fin_studio", "Twin Fin Studio Pty Ltd"),
}

# SUPPLIERS WHO SELL NO GOODS. Their lines are services — a monthly retainer, a
# line clean, a callout — so they are emitted as EXTRA rather than STOCK.
#
# That is not cosmetic. A STOCK line is (a) bounds-checked as if it were a
# purchasable and (b) offered to the chef as a recipe ingredient. Twin Fin's
# "Stowaway and Harry Gatos - Social Media Management" at $4,400 was held by
# SANITY_BOUNDS for being outside the plausible per_unit range $0.10-$500 —
# which is the guard working exactly as designed on a number that is simply not
# a unit price for a thing. Nobody will ever put social media management in a
# recipe either.
#
# Deliberately a SUPPLIER-level fact, not a per-line guess: these vendors sell
# services and nothing else, so there is no judgement to get wrong. Measured
# before writing: of 441 parsed invoices in the corpus only TWO are held by
# SANITY_BOUNDS, and the other is farmer_joes CHICKEN BONES at $0.80/kg — a real
# price under a global floor, documented as a non-defect since 2026-08-08. So
# this is one document today; it is fixed at the cause rather than by widening a
# bound that is protecting everything else.
SERVICE_SUPPLIERS = {
    "twin_fin_studio",      # social media / design retainer
    "symsafe",              # safety compliance
    "beerline_cleaning",    # line cleaning
    "cookers",              # oil filtration service
    "prime_catering_repairs",  # fryer / cooking equipment repairs, charged by callout
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
    if not {"Description", "Amount"} <= set(at):
        return None
    mid_x = at.get("GST", at.get("Discount", at.get("Tax")))

    if "Quantity" not in at:
        # SERVICES TEMPLATE — "Description | GST | Amount AUD", no quantity and
        # no unit price. The Beerline Cleaning Company bills a fixed monthly fee
        # per venue this way, and it is a whole shape rather than a one-off: the
        # invoice sells an agreement, so there is no unit to count.
        #
        # A missing quantity is NOT a reason to refuse the document — it is one
        # implied unit of the thing described, and unit_price then equals the
        # line amount, which is true. What WOULD be wrong is inventing a
        # quantity where the column exists and is simply unread, so this branch
        # only fires when the header genuinely has no Quantity, no Unit and no
        # Price label.
        #
        # It is also deliberately narrow because of what sits next to it in the
        # corpus: Xero's payment RECEIPT ("Total AUD paid", "Amount Paid",
        # "Still Owing") also lacks a Quantity column, states a total, and would
        # reconcile — booking one as a bill would double-count an invoice we
        # have already recorded. It carries no Description column, so requiring
        # Description keeps every one of them out. Verified: the three SYMSAFE
        # receipts in the corpus still do not parse after this change.
        if {"Item", "Unit", "Price"} & set(at):
            return None
        cols = [("desc", 0.0)]
        if mid_x is not None:
            cols.append(("mid", mid_x - 10))
        cols.append(("amt", at["Amount"] - 12))
        if any(b <= a for (_, a), (_, b) in zip(cols, cols[1:])):
            return None
        return cols

    price_x = at.get("Unit", at.get("Price"))
    if price_x is None:
        return None
    cols = [("item", 0.0)] if "Item" in at else []
    cols.append(("desc", (at["Item"] + at["Description"]) / 2 if "Item" in at else 0.0))
    cols.append(("qty", at["Quantity"] - 18))
    cols.append(("price", price_x - 12))
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

    # TWO PASSES, AND THE ORDER IS THE POINT. A full header (with Quantity) is
    # always preferred; the reduced services header is only looked for when no
    # full one exists anywhere on the page. Searching for both at once would let
    # a stray "Description ... Amount" row earlier in a normal invoice win the
    # `break` and become the header, silently re-columning a supplier that
    # parses correctly today. This way every currently-passing invoice takes the
    # identical path it took before.
    hi = cols = None
    for want in ({"Description", "Quantity", "Amount"}, {"Description", "Amount"}):
        for i, r in enumerate(rows):
            if want <= {_label(t) for _, _, t in r}:
                c = _cols_from_header(r)
                if c:
                    hi, cols = i, c
                    break
        if hi is not None:
            break
    if hi is None:
        raise ValueError("Xero: header row not found")
    has_qty = any(name == "qty" for name, _ in cols)

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
        # No Quantity column means one implied unit — see _cols_from_header.
        qty = _m(c.get("qty")) if has_qty else Decimal("1")
        amt = _m(c.get("amt"))
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
        is_extra = (supplier_key in SERVICE_SUPPLIERS
                    or bool(re.search(r"freight|delivery|cartage|fuel\s*levy", desc, re.I)))
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
