"""
NetSuite — deterministic parser (coordinate/header-derived).

NetSuite is a PLATFORM, not a supplier: system@sent-via.netsuite.com is the
From address for every vendor that bills through it. That is the same shape as
post.xero.com (triage log items 16/17/19) and ordermentum.com, so it takes the
same answer — identify the vendor by THE ABN THAT IS NOT OURS, from an explicit
registry, and refuse rather than guess. CUSTOMER_ABNS / ABN_SUPPLIER /
vendor_from_abn are imported from the xero parser rather than copied: our own
ABNs are one fact, and a second copy is a second thing to forget to update.

Registered because the 2026-08-19 Review sweep found 14 stuck documents on this
sender — 12 Bacchus Wine Merchant invoices and 2 Dext subscription bills — and
none of them had ever been visible to the regression harness, because a domain
absent from domains.DOMAIN_KEY is a supplier build_corpus never collects.

THE LAYOUT (Bacchus). One header row, and every boundary is DERIVED from that
row's own word x-positions — never hard-coded. Triage log items 22/33 record
three separate suppliers (Foodlink, Fresh Fruit Team, Gulli) whose parsers rotted
silently on literal x-boundaries; this parser is not going to be the fourth.

    Qty | Units | Item Code | Description | Disc | LUC | Unit Price | Amount |
    WET | GST | Gross Amt

    1  CS(12)  FD2MOTHER 23  First Drop Mothers Milk Shiraz 2023
       15%  14.62  136.00  136.00  39.44  17.54  192.98

WINE, SO THE MONEY COLUMN THAT MATTERS IS THE LAST ONE. Amount is ex-tax, then
WET (29% of Amount) is added, then GST (10% of Amount+WET), giving Gross Amt.
The validator's one load-bearing check is sum(line_total_incl) == total_incl, so
line_total_incl is GROSS AMT. Verified on 3379f8e9af9e to the cent:
136.00 -> WET 39.44 -> GST 17.54 -> Gross 192.98, and the five Gross values sum
to 515.44 which with freight 45.00 x 1.1 is the stated 564.94.

LUC is Bacchus's own per-bottle landed unit cost EX GST but INCL WET —
(136.00 + 39.44) / 12 = 14.62 exactly. It is read for corroboration only; the
unit price this parser emits is Gross / (qty x pack), which is what Lightspeed
wants and what the two hand-reviewed extractions already in data/invoices used
(INV495641 stored 16.0817 = 192.98 / 12).

FREIGHT IS NOT A LINE. It sits in the totals block, below Subtotal Ex Tax, and
only on some invoices. It is emitted as an EXTRA line at freight x 1.1, checked
against this invoice's own GST Total (51.36 line GST 46.86 = 4.50 = 10% of
45.00). If a future invoice taxes freight differently the sum will not reconcile
and the validator will refuse it — which is the direction to fail in.

THE ITEM CODE CARRIES THE VINTAGE, AND ITS SPELLING IS ALREADY CORRUPTED
UPSTREAM. Bacchus prints two tokens in the Item Code cell, "FD2MOTHER" and "23".
Both sit inside the Item Code column, so the code is the pair. data/cogs_list.csv
currently holds THREE spellings of one wine from three LLM extractions —
"PETDETROSE 24", "PETDETROSE24" and "PETDETROSE" — i.e. one product with three
price histories, the item 12/13 failure. This parser emits the JOINED form
("FD2MOTHER23"): it is the only one of the three that passes the harness's
identity audit (no whitespace in a supplier code), it keeps the vintage so a
2023 and a 2024 are not merged at different prices, and it matches the most
recent extraction so the series continues rather than forking a fourth time.
Repairing the three historical spellings is a build_cogs_list DERIVED question
and belongs to Zak, not to a parser.
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

# Header labels -> the field name used below. Matched on LETTERS only, so
# "Gross Amt" and "Gross" both anchor the same column (the xero lesson: a
# fixture built from a mislabelled header found that stripping non-letters
# turned "Quantity(L)" into "QuantityL" and matched nothing).
_HEADER = [
    ("qty", "qty"),
    ("units", "units"),
    ("item", "code"),
    ("description", "desc"),
    ("disc", "disc"),
    ("luc", "luc"),
    ("unit", "unitprice"),
    ("amount", "amount"),
    ("wet", "wet"),
    ("gst", "gst"),
    ("gross", "gross"),
]

MONEY = re.compile(r"^-?\d+\.\d{2}$")
QTY = re.compile(r"^-?\d+(?:\.\d+)?$")
# "CS(12)" -> 12. A bare "EA"/"BT"/blank is one unit.
PACK = re.compile(r"\((\d+)\)")
_ABN = re.compile(r"\b(\d{2}\s?\d{3}\s?\d{3}\s?\d{3})\b")

# Measured across the readable Bacchus corpus: a wrapped description sits
# 4.4-8.8pt from its own money row, two separate line items 14.9-17.6pt apart.
# The midpoint is deliberately well clear of both ends of that gap.
SAME_CELL_PT = 11.0

# Bacchus prints these in the Item Code cell for non-stock rows.
_NON_STOCK_CODES = {"note", "fuellevy", "freight", "shipping", "discount"}


def _dec(s):
    try:
        return Decimal(s)
    except (InvalidOperation, TypeError):
        return None


def _cols_from_header(words):
    """Column boundaries from the header row's own word x-positions.

    Text columns are left-aligned under their label. The money columns are
    RIGHT-aligned and their values run to the left of the label's start, so each
    money column takes a margin. Returns None if this is not the shape we know —
    an unregistered NetSuite vendor with a different template must fall through
    to Review, not be forced into these buckets.
    """
    at: dict[str, float] = {}
    for x0, _x1, t in words:
        key = re.sub(r"[^a-z]", "", t.lower())
        for label, field in _HEADER:
            if key == label and field not in at:
                at[field] = x0
    need = ("qty", "units", "code", "desc", "amount", "gross")
    if not all(k in at for k in need):
        return None

    bounds: list[tuple] = []
    # Qty, Units, Item Code, Description AND Disc are LEFT-aligned under their
    # labels, so they take only a hair's margin. Disc being in that group is not
    # a guess: its values are text, not money ("15%", "07.5%", "List",
    # "Custom"), and every one of them starts at exactly the Disc label's own x
    # across the corpus. Giving Disc the money columns' 20pt margin ate the last
    # word of any description that reached the column — the vintage off
    # "... Pinot Grigio 2025" and the "on" off "Putting on" — which is the
    # item 1 / item 22 defect class (a boundary that steals a word and still
    # reconciles to the cent) reproduced in a brand-new parser.
    for field in ("qty", "units", "code", "desc", "disc"):
        if field in at:
            bounds.append((field, at[field] - 2.0))
    # The money columns are RIGHT-aligned: their values run left of the label.
    for field in ("luc", "unitprice", "amount", "wet", "gst", "gross"):
        if field in at:
            bounds.append((field, at[field] - 20.0))
    bounds.sort(key=lambda b: b[1])
    return bounds


def _venue(text: str) -> Venue:
    """Venue from the BILL TO BLOCK ONLY — never from the whole page.

    This is the one place on a Bacchus invoice where the venue is stated as a
    fact rather than mentioned in passing, and the distinction is not academic:
    every venue on this account bills to the same legal entity ("Stowaway
    Freshwater Pty Ltd"), and it is the trading name printed above it that says
    which one. Measured across the 17 readable Bacchus invoices in the corpus:

      * THREE are genuinely billed to Harry Gatos ("Oliver Laccarino / Harry
        Gatos / Stowaway Freshwater Pty Ltd") — INV493375, INV492625, INV494398.
      * TWO that are billed to STOWAWAY mention Harry Gatos elsewhere on the
        page, inside a free-text note: INV490330 carries "TRE1RRPG is for Harry
        Gatos. Putting on same invoice to keep urgent fees to a minimum".

    So a flat whole-page `re.search(r"gat+os")`, which is what every other
    parser in this tree does, is wrong in BOTH directions here — it would book
    two Stowaway invoices to Harry Gatos, and it only gets the three real ones
    right by accident. Venue picks the product namespace (the two venues have
    different Lightspeed ProductIDs), so getting it wrong writes a cost against
    a product that is not the one bought.
    """
    m = re.search(r"Bill\s*To\s*:?\s*(.{0,200}?)Ship\s*To", text, re.S | re.I)
    block = m.group(1) if m else ""
    if re.search(r"marilyna", block, re.I):
        return Venue.MARILYNAS
    if re.search(r"gat+os", block, re.I):
        return Venue.HARRY_GATOS
    if re.search(r"stowaway", block, re.I):
        return Venue.STOWAWAY
    return Venue.UNKNOWN


def _labelled(text: str, label: str):
    m = re.search(re.escape(label) + r"\s*:?\s*([^\n]+)", text, re.I)
    return m.group(1).strip() if m else None


def _totals(rows, bounds):
    """The totals block, keyed by its own printed labels."""
    out: dict[str, Decimal] = {}
    for _y, words in rows:
        cells = pdf_text.bucket(words, bounds)
        left = (cells.get("qty") or "").strip()
        if not left:
            continue
        key = re.sub(r"[^a-z]", "", left.lower())
        val = None
        for _x0, _x1, t in words:
            tt = t.replace(",", "").replace("$", "")
            if MONEY.match(tt):
                val = _dec(tt)
        if val is None:
            continue
        if key.startswith("subtotalextax"):
            out["subtotal_ex"] = val
        elif key == "freight":
            out["freight"] = val
        elif key.startswith("gsttotal"):
            out["gst_total"] = val
        elif key.startswith("wetgsttotal"):
            out["wet_gst_total"] = val
        elif key == "total":
            out["total"] = val
    return out


@register("sent-via.netsuite.com")
def parse(pdf_bytes: bytes) -> Invoice | None:
    text = pdf_text.text(pdf_bytes)
    vendor = vendor_from_abn(text)
    if not vendor:
        return None                     # unregistered vendor -> Review, never a guess
    supplier_key, supplier_name = vendor

    rows = pdf_text.word_rows_with_y(pdf_bytes)

    hdr_i = bounds = None
    for i, (_y, words) in enumerate(rows):
        b = _cols_from_header(words)
        if b:
            hdr_i, bounds = i, b
            break
    if bounds is None:
        return None                     # not this template

    body = rows[hdr_i + 1:]

    # --- split the body into line items, using the vertical gap -------------
    # A row carrying a Gross value is a money row. A text-only row belongs to
    # the money row it is within SAME_CELL_PT of; anything further away starts
    # a new item. See pdf_text.word_rows_with_y for why the gap and not the
    # row index.
    items: list[dict] = []
    pending: list[tuple] = []           # text-only rows not yet attached
    for y, words in body:
        cells = pdf_text.bucket(words, bounds)
        gross_raw = (cells.get("gross") or "").replace(",", "").replace("$", "").strip()
        qty_raw = (cells.get("qty") or "").strip()
        desc_raw = (cells.get("desc") or "").strip()

        code_raw = (cells.get("code") or "").strip()

        is_money = bool(MONEY.match(gross_raw)) and bool(QTY.match(qty_raw))
        if is_money:
            items.append({"y": y, "cells": cells, "before": [], "after": [],
                          "code_before": [], "code_after": []})
            for py, pcode, pdesc in pending:
                if abs(y - py) <= SAME_CELL_PT:
                    if pdesc:
                        items[-1]["before"].append(pdesc)
                    if pcode:
                        items[-1]["code_before"].append(pcode)
            pending = []
            continue

        # THE ITEM CODE WRAPS TOO, and mid-word. On 55aaa9803359 the code cell
        # reads "PETDETMEDRO" on the row above the money row, nothing on the
        # money row itself, and "SE 24" on the row below — one code,
        # PETDETMEDROSE24, split across three rows. Collecting only the
        # description off a continuation row (which is all this did at first)
        # left that line with supplier_code=None, and core/domain.py
        # ::purchasable_id RAISES on a code-less line: the invoice would have
        # reconciled to the cent and fed the cost book nothing, which is worse
        # than failing because it looks like it worked. Fragments are joined
        # with no separator, in y order, because the break is inside the word.
        if not qty_raw and (desc_raw or code_raw):
            if items and abs(y - items[-1]["y"]) <= SAME_CELL_PT:
                if desc_raw:
                    items[-1]["after"].append(desc_raw)
                if code_raw:
                    items[-1]["code_after"].append(code_raw)
            else:
                pending.append((y, code_raw, desc_raw))
            continue

        # A left-column label with no qty is the totals block: the table is over.
        if qty_raw and not is_money:
            break

    totals = _totals(rows[hdr_i + 1:], bounds)
    stated_total = totals.get("total")
    if stated_total is None or stated_total <= 0:
        return None

    ref = _labelled(text, "Invoice No")
    date_s = _labelled(text, "Date")
    if not ref or not date_s:
        return None
    try:
        inv_date = datetime.strptime(date_s.split()[0], "%d/%m/%Y").date()
    except ValueError:
        return None

    lines: list[InvoiceLine] = []
    for it in items:
        c = it["cells"]
        qty = _dec((c.get("qty") or "").strip())
        gross = _dec((c.get("gross") or "").replace(",", "").replace("$", "").strip())
        if qty is None or gross is None:
            return None

        wet = _dec((c.get("wet") or "").replace(",", "").strip()) or Decimal("0")
        gst = _dec((c.get("gst") or "").replace(",", "").strip()) or Decimal("0")

        code_cell = " ".join(it["code_before"] + [(c.get("code") or "").strip()]
                             + it["code_after"])
        code = re.sub(r"\s+", "", code_cell) or None
        desc = " ".join(x for x in (it["before"] + [(c.get("desc") or "").strip()]
                                    + it["after"]) if x).strip()

        pack_m = PACK.search((c.get("units") or ""))
        pack = int(pack_m.group(1)) if pack_m else 1

        is_extra = (code or "").lower() in _NON_STOCK_CODES or bool(
            re.search(r"freight|fuel\s*levy|delivery|cartage|surcharge", desc, re.I))

        if qty and pack and gross:
            unit = (gross / (qty * pack)).quantize(Decimal("0.0001"))
        else:
            unit = Decimal("0.0000")

        lines.append(InvoiceLine(
            description=desc or code or "",
            qty=qty,
            line_total_incl=gross,
            unit_price_incl=unit,
            pack_size=pack,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.WET if wet > 0 else TaxTreatment.GST,
            cost_basis=(CostBasis.UNKNOWN if is_extra
                        else CostBasis.PER_BOTTLE if pack > 1
                        else CostBasis.PER_UNIT),
            gst_amount=gst,
            wet_amount=wet,
            supplier_code=None if is_extra else code,
            raw_uom=(c.get("units") or "").strip() or None,
        ))

    if not lines:
        return None

    # Freight lives in the totals block, not the table. GST-bearing: on
    # 3379f8e9af9e the printed GST Total exceeds the summed line GST by exactly
    # 10% of the freight. If a future invoice differs, the sum will not
    # reconcile and the validator refuses it.
    freight = totals.get("freight")
    if freight:
        incl = (freight * Decimal("1.1")).quantize(Decimal("0.01"))
        lines.append(InvoiceLine(
            description="Freight", qty=Decimal("1"), line_total_incl=incl,
            unit_price_incl=incl, pack_size=1,
            line_class=LineClass.EXTRA, tax_treatment=TaxTreatment.GST,
            cost_basis=CostBasis.UNKNOWN,
            gst_amount=(incl - freight).quantize(Decimal("0.01"))))

    po = _labelled(text, "PO Number")
    return Invoice(
        supplier_key=supplier_key,
        supplier_name_raw=supplier_name,
        invoice_ref=ref.split()[0],
        invoice_date=inv_date,
        total_incl=stated_total,
        lines=lines,
        venue=_venue(text),
        subtotal_ex=totals.get("subtotal_ex"),
        gst_total=totals.get("gst_total"),
        wet_total=((totals["wet_gst_total"] - totals["gst_total"])
                   if "wet_gst_total" in totals and "gst_total" in totals else None),
        po_refs=[po] if po else [],
    )
