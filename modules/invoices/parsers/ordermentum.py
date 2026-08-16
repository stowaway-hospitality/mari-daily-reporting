"""
Ordermentum — deterministic parser (line-based).

Ordermentum is a B2B ordering PLATFORM, not a supplier: notifications@
ordermentum.com is the sender for every invoice raised through it, and the
actual vendor differs per document. That is the same shape as post.xero.com
(triage log items 16/17/19), so it takes the same answer — identify the vendor
by THE ABN THAT IS NOT OURS, from an explicit registry, and refuse rather than
guess. CUSTOMER_ABNS and vendor_from_abn are imported from the xero parser
rather than copied: our own two ABNs are one fact, and a second copy of them is
a second thing to forget to update.

Layout (the whole invoice is ~680 characters of text; there are no coordinates
worth bucketing, so this reads labelled lines):

    INVOICE NUMBER  OMI7874
    INVOICE DATE  06/08/2026
    TO      Stowaway ... ABN: 17 606 243 921      <- ours
    FROM    Barrel One Coffee Roasters ... ABN: 41643628844
    ITEM / QTY / PRICE / SUBTOTAL
    * Barrel One Coffee Concentrate 750ml (6)
    2
    $83.25
    $166.50
    Subtotal $166.50   Freight $0.00   *GST $16.65   Total $183.15

MONEY IS EX-GST ON THE LINE and GST is added at the foot, so a line's incl total
is ex x 1.1 — but that is CHECKED ARITHMETICALLY against the printed total
rather than assumed, the way the xero parser tells its two tax conventions
apart. If neither reading reconciles, this parser changes nothing and lets the
validator refuse the invoice.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register
from modules.invoices.parsers.xero import vendor_from_abn

# CONFIRMED PRODUCT CODES — (supplier_key, exact description) -> supplier code.
#
# WHY THIS EXISTS. Ordermentum prints NO product code: the ITEM cell of OMI7874
# is "* Barrel One Coffee Concentrate 750ml (6)" and word_rows confirms there is
# nothing else on the row. core/domain.py::purchasable_id RAISES on a code-less
# line — "There is no natural key, so there is no identity. Do NOT fall back to
# the description -- that is how ALEHOUSE CRISP KEG becomes the wrong $27.50
# keg" — so without a code this parser would file the invoice, reconcile it, and
# feed the cost book NOTHING, which is worse than failing because it looks like
# it worked.
#
# WHY IT IS NOT THE THING domain.py FORBIDS. That warning is about FUZZY matching
# at read time: two ALEHOUSE kegs $27.50 apart both match /ALEHOUSE .* KEG/ and
# the sensible guess is backwards. This table is the opposite shape —
#   * EXACT match on the whole description, never a prefix or a regex;
#   * a CLOSED, per-vendor list, so an unrecognised product yields no code and
#     the line is skipped exactly as it is today — it FAILS CLOSED;
#   * each entry is a HUMAN CONFIRMATION, the same standing as a pack_overrides
#     entry, recorded with who confirmed it and when.
#
# THE CODE IS OURS, NOT THEIRS, and that is worth knowing: Barrel One publishes
# none, so "BOCC750-6" is minted here and documented as minted. If they ever
# start printing a real code, the real one wins and this entry must be retired,
# or one product will carry two price histories (triage log items 12 and 13).
CONFIRMED_CODES: dict[tuple[str, str], str] = {
    # Zak, 2026-08-17: "yes barrel one coffee is a supplier we need to make sure
    # feeds our cogs" — confirming this line is the Coffee Concentrate already in
    # the cost book as lightspeed:20484935 (Stowaway) / :20750115 (Harry Gatos).
    # data/product_map.csv carries the bridge to both. Corroborated by arithmetic
    # before it was written: $83.25 per case of 6 x 750 mL = $13.875/bottle ex,
    # $15.2625 inc, $0.020350/mL inc — against seeds of $13.88 (HG, ex), $15.26
    # (Stow, inc) and $0.020389/mL (recipe). All three to the cent, so the alias
    # is confirmed by the numbers as well as by Zak.
    ("barrel_one", "barrel one coffee concentrate 750ml (6)"): "BOCC750-6",
}

MONEY = re.compile(r"-?\$?\s*([\d,]+\.\d{2})")
# QTY is a BARE INTEGER on this template ("2"), while every money value carries
# two decimals. Matching qty with the money pattern silently found nothing, the
# item loop broke on the first row and the parser raised "no line items parsed"
# — so the two are read with different patterns on purpose.
NUM = re.compile(r"-?\$?\s*([\d,]+(?:\.\d+)?)\s*$")
# A line item opens with "* " on this template and its money sits on the lines
# below. Anchor on the asterisk, not on the description text.
ITEM_START = re.compile(r"^\*\s*(.+?)\s*$")
TOL = Decimal("0.02")


def _m(s):
    m = MONEY.search((s or "").replace(",", ""))
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def _n(s):
    """Any number, decimal or not — for the qty cell. See NUM."""
    m = NUM.match((s or "").replace(",", "").strip())
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def _labelled(lines, label):
    """Value for a 'LABEL  value' row, or the row immediately after a bare LABEL."""
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.upper().startswith(label):
            rest = s[len(label):].strip(" :\t")
            if rest:
                return rest
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return None


@register("ordermentum.com")
def parse(pdf_bytes: bytes) -> Invoice:
    flat = pdf_text.text(pdf_bytes)
    nonblank = [x.strip() for x in flat.splitlines() if x.strip()]

    who = vendor_from_abn(flat)
    if who is None:
        # Unregistered vendor on a shared platform sender. Never guessed — the
        # Xero lesson (item 17): reading "the first ABN" returns OURS on half
        # these templates and would file several vendors under one identity.
        return None
    supplier_key, supplier_name = who

    ref = _labelled(nonblank, "INVOICE NUMBER")
    if not ref:
        raise ValueError("Ordermentum: invoice number not found")
    raw_date = _labelled(nonblank, "INVOICE DATE") or ""
    m = re.search(r"(\d{2}/\d{2}/\d{4})", raw_date)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None

    # Venue from the TO block. Marilyna's and Harry Gatos before Stowaway, which
    # also appears as the delivery address on HG paperwork (the select_fresh rule).
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    total_incl = subtotal = freight = None
    gst_total = Decimal("0")
    for i, s in enumerate(nonblank):
        low = s.lower().lstrip("*").strip()
        nxt = nonblank[i + 1] if i + 1 < len(nonblank) else ""
        val = _m(s) if _m(s) is not None else _m(nxt)
        if val is None:
            continue
        if low.startswith("subtotal") and subtotal is None:
            subtotal = val
        elif low.startswith("gst") and gst_total == 0:
            gst_total = val
        elif low.startswith("freight") and freight is None:
            freight = val
        elif low.startswith("total") and not low.startswith("total quantity"):
            total_incl = val
    if total_incl is None:
        raise ValueError("Ordermentum: invoice total not found")

    # ---- line items: "* <description>" then qty, unit price, line subtotal ---
    raw = []
    for i, s in enumerate(nonblank):
        mm = ITEM_START.match(s)
        if not mm:
            continue
        desc = mm.group(1).strip()
        if not desc or desc.lower().startswith("gst"):
            continue                      # "*GST" is the tax row, not a product
        nums = []
        for t in nonblank[i + 1:i + 5]:
            v = _n(t)
            if v is None:
                break
            nums.append(v)
        if len(nums) < 3:
            continue
        qty, _unit_ex, line_ex = nums[0], nums[1], nums[2]
        if qty == 0 or line_ex == 0:
            continue
        raw.append((desc, qty, line_ex))
    if not raw:
        raise ValueError("Ordermentum: no line items parsed")

    # WHICH TAX CONVENTION, decided by ARITHMETIC and not by a keyword — the
    # xero parser's rule. Whichever hypothesis reconciles to the printed total
    # wins; if neither does, nothing is assumed and the validator refuses it.
    body = sum(l for _d, _q, l in raw)
    extra = freight or Decimal("0")
    if abs(body + extra + gst_total - total_incl) <= TOL:
        exclusive = True                  # line money is EX; GST added at the foot
    elif abs(body + extra - total_incl) <= TOL:
        exclusive = False                 # line money already includes tax
    else:
        exclusive = gst_total > 0

    def _incl(v):
        return (v * Decimal("1.1")).quantize(Decimal("0.01")) if exclusive else v

    tax = TaxTreatment.GST if gst_total > 0 else TaxTreatment.GST_FREE
    items = []
    for desc, qty, line_ex in raw:
        incl = _incl(line_ex)
        items.append(InvoiceLine(
            description=desc, qty=qty, line_total_incl=incl,
            unit_price_incl=(incl / qty).quantize(Decimal("0.0001")),
            # THE PACK IS NOT DIVIDED HERE, deliberately. The description carries
            # its own pack ("... 750ml (6)" is a case of six 750 mL bottles at
            # $83.25, i.e. $13.875 a bottle ex), and dividing it in the parser is
            # how a case rate silently becomes a bottle rate — the 6x/12x class
            # of error the broccolini and Coke Zero notes in pack_overrides.yaml
            # exist to record. One purchasable case, PER_UNIT; the case->bottle
            # conversion belongs in pack_overrides where a human confirms it.
            pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=tax, cost_basis=CostBasis.PER_UNIT,
            # No code is printed; an EXACT, human-confirmed alias supplies one,
            # or the line goes out without identity exactly as before. See
            # CONFIRMED_CODES above for why this is not a description fallback.
            supplier_code=CONFIRMED_CODES.get(
                (supplier_key, " ".join(desc.lower().split()))),
            raw_uom=None))

    if freight:
        items.append(InvoiceLine(
            description="Freight", qty=Decimal("1"), line_total_incl=_incl(freight),
            unit_price_incl=_incl(freight), pack_size=1, line_class=LineClass.EXTRA,
            tax_treatment=tax, cost_basis=CostBasis.UNKNOWN))

    return Invoice(
        supplier_key=supplier_key, supplier_name_raw=supplier_name,
        invoice_ref=ref, invoice_date=date, total_incl=total_incl,
        lines=items, venue=venue)
