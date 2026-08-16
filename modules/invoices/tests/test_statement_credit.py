"""
Statement + credit-note gatekeeping — the two document types that must NEVER be
costed or posted as a payable.

  * A STATEMENT lists other invoices with a running balance; it has no products.
    Left unfiltered it reaches the extractor, which reads its summary rows as line
    items (real bug: Inalca; and Fresh Fruit Team emails single-invoice statements
    as "Invoice(s)" that leaked into review as 0-line invoices).
  * A CREDIT NOTE refunds us. It reconciles like a normal invoice, so left alone a
    $48 credit posts as a $48 payable — money owed the wrong way.

These pin real supplier documents so the detectors can't silently regress.

    python3 -m pytest modules/invoices/tests/test_statement_credit.py
"""

from __future__ import annotations

from modules.invoices.run import looks_like_statement, looks_like_credit_note


# ── real documents that MUST be caught ─────────────────────────────────────

FFT_STATEMENT = """STATEMENT
Harry Gatos
As At 24 Jul 2026
Balance due in AUD, Australian Dollar
Date Activity Reference Due Date Invoice Amount Payments Balance AUD
17 Jul 26 Invoice # INB00111593 31 Jul 26 163.48 163.48
BALANCE DUE 163.48
PAYMENT ADVICE
Amount Enclosed
Enter the amount you are paying above"""

GULLI_CREDIT = """Tax Credit Note RINV/2026/08838
Credit Note Date: 03/07/2026
STOWAWAY FRESHWATER
PRODUCT CODE DESCRIPTION QUANTITY UNIT PRICE GST AMOUNT
PBLTB11-U B Flute Lock Top 11" Pizza Boxes x 50 2.000 Unit 21.91000 10% $ 43.82
Total $ 48.20"""


ILG_DIRECT_DEBIT = """ILG Distribution Co-op. Ltd.
Direct Debit Advice
Item: Reference: Description: Due: Amount:
1 03733160 Invoice dated 22/07/26 28/7/26 164.26
2 03733161 Invoice dated 22/07/26 28/7/26 2,939.14
Sub Total: 3,134.84
** Amount Debited: 3,128.57"""


def test_fft_single_invoice_statement_is_caught():
    assert looks_like_statement(FFT_STATEMENT) is True
    assert looks_like_credit_note(FFT_STATEMENT) is False


def test_ilg_direct_debit_advice_is_caught():
    # a payment notice listing other invoices — no products, must never be costed
    assert looks_like_statement(ILG_DIRECT_DEBIT) is True


def test_gulli_credit_note_is_caught():
    assert looks_like_credit_note(GULLI_CREDIT) is True
    # a credit note is not a statement (it has products, just the wrong direction)
    assert looks_like_statement(GULLI_CREDIT) is False


# ── real INVOICES that must NOT be mistaken for either ─────────────────────

FOODLINK_INVOICE = """Foodlink Australia Pty Ltd
Tax Invoice SI4483241
Description Qty UOM Price Amount
FLOUR TORTILLAS 12X91GM 12INCH 1 CTN-6 33.60 33.60
Total AUD Incl. GST 206.61"""

GRIFTER_INVOICE = """TAX INVOICE
The Grifter Brewing Company Pty Ltd
Invoice Number 83246
GRIFTER PALE ALE 50L KEG 1.00 295.00 265.50
TOTAL AUD 396.33"""


def test_real_tax_invoices_are_not_statements_or_credits():
    for doc in (FOODLINK_INVOICE, GRIFTER_INVOICE):
        assert looks_like_statement(doc) is False
        assert looks_like_credit_note(doc) is False


def test_credit_words_do_not_false_trip():
    # "credit card" / "credit terms" on a normal invoice must not read as a credit note
    doc = "TAX INVOICE\nPayment by credit card incurs a fee. Credit terms 7 days.\nTotal 100.00"
    assert looks_like_credit_note(doc) is False
    assert looks_like_statement(doc) is False


# ── Foodlink's untitled ageing statement (2026-08-15) ──────────────────────
# The real one, trimmed. It has NO masthead in the text layer — no "statement",
# no "tax invoice" — it opens straight into the ledger. The `titled and strong`
# rule therefore never fired, so this document was unparseable by construction
# (no line items) yet kept cycling through the Review retry pass forever.
#
# What gives it away is the ageing spread. An invoice states ONE set of terms;
# only a statement ages a balance across buckets.
FOODLINK_STATEMENT = """15079 STOWAWAY 18/1-3 MOORE ROAD FRESHWATER
14/02/26 CR/ADJ NOTE SC324772 29/01/26 88.00 -88.00
03/08/26 INVOICE SI4500784 10/08/26 340.80 340.80 252.80
08/08/26 INVOICE SI4511803 15/08/26 335.40 335.40 1,130.60
22311 HARRY GATOS 18/1-3 MOORE ROAD FRESHWATER
CURRENT 7 DAYS 08 DAYS 14 DAYS 21 DAYS
1,441.20 674.50 512.90 165.30 88.00"""


def test_foodlink_untitled_ageing_statement_is_caught():
    # Regression: this returned False and the document sat in Review forever.
    assert looks_like_statement(FOODLINK_STATEMENT) is True


def test_ageing_buckets_alone_are_enough_without_the_word_statement():
    assert "statement" not in FOODLINK_STATEMENT.lower()
    assert "tax invoice" not in FOODLINK_STATEMENT.lower()
    assert looks_like_statement(FOODLINK_STATEMENT) is True


def test_a_real_invoice_quoting_plain_terms_is_not_aged():
    # One set of terms is not a bucket spread. Must stay an invoice.
    doc = ("Foodlink Australia Pty Ltd\nTax Invoice SI4483241\n"
           "Terms 7 DAYS from Inv Date\nCurrent charges apply\n"
           "Total AUD Incl. GST 206.61")
    assert looks_like_statement(doc) is False


def test_ageing_rule_needs_two_distinct_buckets():
    # "current" plus a single "7 days" terms line is an invoice, not a statement.
    doc = "ACME SUPPLY\nCurrent order\nTerms: 7 DAYS\nAmount 100.00"
    assert looks_like_statement(doc) is False


# ── image-only PDFs never reach the extractor (2026-08-15) ────────────────
# All 17 scans in the corpus were opened and NOT ONE is a printed invoice:
# 15 are Sun Circle's pre-printed order form filled in BY HAND (qty, unit price,
# amount and total are all pen), 1 is a blank ILG Direct Debit Request, 1 is a
# blank B&E Credit Card Authorisation form.
#
# The danger is specific: a model reading handwriting guesses the line amounts
# AND the total from the same strokes, so it can guess CONSISTENTLY and still
# reconcile — a wrong number that validates. Exit 4 keeps them in Review for a
# human and marks them "manual entry", distinct from "no parser yet" (exit 2) so
# future triage runs stop re-investigating them.

def _run(pdf_path, *extra):
    import subprocess, sys as _s
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[3]
    return subprocess.run(
        [_s.executable, str(root / "modules/invoices/run.py"), "--pdf", str(pdf_path),
         "--source", "test", *extra],
        capture_output=True, text=True, cwd=str(root))


def _a_scan():
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[3]
    d = root / "data" / "invoice_corpus" / "sun_circle"
    if not d.exists():
        return None
    return next(iter(sorted(d.glob("*.pdf"))), None)


def test_image_only_pdf_exits_4_and_is_never_extracted():
    scan = _a_scan()
    if scan is None:
        return                      # corpus not present (CI); covered by the unit test below
    r = _run(scan, "--sender", "suncircle.com.au", "--no-llm")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "image-only" in r.stdout


def test_image_only_pdf_exits_4_even_when_the_llm_is_allowed():
    # The important half: WITHOUT --no-llm this used to fall through to the
    # extractor and read handwriting. It must stop here regardless.
    scan = _a_scan()
    if scan is None:
        return
    r = _run(scan, "--sender", "suncircle.com.au")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "never guessed" in r.stdout


def test_has_text_layer_is_what_separates_them():
    from modules.invoices import pdf_text
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[3]
    scan = _a_scan()
    if scan is None:
        return
    assert pdf_text.has_text_layer(scan.read_bytes()) is False
    real = root / "data/invoice_corpus/foodlink"
    if real.exists():
        pf = next(iter(sorted(real.glob("*.pdf"))), None)
        if pf:
            assert pdf_text.has_text_layer(pf.read_bytes()) is True


def test_unreadable_files_still_exit_1_not_4():
    # A corrupt file is an ERROR, not a manual-entry scan. Guards the ordering:
    # pdf_text.text() raises before the has_text_layer check is reached.
    import tempfile
    from pathlib import Path as _P
    for blob in (b"not a pdf %PDF broken", b""):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(blob)
            p = tf.name
        try:
            assert _run(p).returncode == 1
        finally:
            _P(p).unlink(missing_ok=True)


# ── carrier dockets and Xero statement ledgers (2026-08-15) ────────────────
# 13 CartonCloud "Proof Of Delivery" dockets and 9 Xero statements were sitting
# in Review looking like missing parsers. Neither is a bill: the docket carries
# quantities and no prices at all, and the ledger tabulates OTHER invoices'
# amounts against a running balance.

CARTONCLOUD_POD = """MOUNTAIN CULTURE
Proof Of Delivery
Delivered by MOTUS TRANSPORT.
Reference: SOMO28189
KEG: 2 | WGHT: 124 | Value: $0.00
Item Code Description Product Type Quantity
SQUO-50L-01K Status Quo Pale Ale 50L Konvoy General 2 KEGS
Signatureless Proof Of Delivery. Powered by CartonCloud"""

XERO_STATEMENT = """STATEMENT
HARRY GATOS
As At 6 Aug 2026
Account Number 5471
Speed Gas
Date Activity Reference Due Date Invoice Amount Payments Balance AUD
31 Jul 2026 Invoice # INV227158 30 Aug 2026 82.50 0.00 82.50
BALANCE DUE AUD   82.50
Email remittance to: accounts@speedgas.com.au"""

XERO_INVOICE = """TAX INVOICE
STOWAWAY FRESHWATER
Invoice Date 11 Aug 2026
Invoice Number 83676
The Grifter Brewing Company Pty Ltd
Description Quantity Unit Price Discount Amount AUD
GRIFTER PALE ALE 50L KEG (KONVOY KEG) 1.00 295.00 10.00% 265.50
Freight 1.00 5.00 5.00
Subtotal (includes a discount of 29.50) 270.50
TOTAL  GST  10% 27.05
TOTAL AUD 297.55
Due Date: 25 Aug 2026"""


def test_proof_of_delivery_is_not_an_invoice():
    assert looks_like_statement(CARTONCLOUD_POD) is True


def test_xero_statement_ledger_is_caught():
    # Slipped through before: it prints neither "amount enclosed" nor an ageing
    # spread, so titled+strong never fired.
    assert looks_like_statement(XERO_STATEMENT) is True


def test_a_xero_TAX_INVOICE_is_still_an_invoice():
    # The other half of the contract — the new rules must not swallow the real
    # bills that arrive from the same sender.
    assert looks_like_statement(XERO_INVOICE) is False
    assert looks_like_credit_note(XERO_INVOICE) is False


def test_balance_due_alone_does_not_trip_it():
    # An invoice may well say "balance due"; it is the "Invoice Amount" COLUMN
    # beside it that makes a ledger.
    doc = "TAX INVOICE\nInvoice Number 123\nWidget 1 10.00\nTOTAL AUD 10.00\nBalance due 10.00"
    assert looks_like_statement(doc) is False


# ── credit notes as a HARD GATE (2026-08-17) ──────────────────────────────────
# Zak: "I don't want to see statements and credit notes, we only care about this
# for feeding actual prices on invoices through." looks_like_credit_note was
# promoted from a flag (set after extraction) to an EXIT (run.py returns 5,
# before any parser or LLM runs). A false positive now discards a real bill
# instead of raising a spurious warning, so the tests below pin BOTH directions.

GULLI_CREDIT_NOTE = """Gulli Food Distributors Pty Ltd
Product remains the property of Gulli Food Distributors until invoice has been
fully paid. Claims will only be accepted within 48 hours of delivery. Prices
subject to change. We accept credit card. Please note there is a surchase of
1.5% for Visa and MasterCard and 1.85% for Amex.
Untaxed Amount $ 43.82
GST 10% $ 4.38
Total $ 48.20
Tax Credit Note RINV/2026/08838
Credit Note Date: 03/07/2026
PRODUCT CODE DESCRIPTION QUANTITY UNIT PRICE GST AMOUNT
PBLTB11-U B Flute Lock Top 11" Pizza Boxes x 50 2.000 Unit 21.91000 10% $ 43.82"""

FOODLINK_CREDIT_MEMO = """No. Description Qty UOM Weight Unit Price GST Amount
Invoice No. SI4312726:
100848 CORN FLOUR MAIZE GLUTEN FREE 5KG Edlyn 1 EA 23.00 23.00
Reason Code: MISSING
Total AUD Excl. GST 23.00
Total AUD Incl. GST 23.00
Foodlink Australia Pty Ltd
Credit Memo SC338338"""


def test_gulli_tax_credit_note_is_caught():
    # THE ONE THAT MATTERED: this reconciles to the cent and the validator says
    # ok, so only this gate keeps a $48.20 credit from posting as a $48.20
    # payable. It also carries "We accept credit card" in its own footer, which
    # is exactly why the match is a PHRASE and not the word "credit".
    assert looks_like_credit_note(GULLI_CREDIT_NOTE) is True


def test_foodlink_credit_memo_is_caught():
    # Scored as a parse-fail until 2026-08-17 — its header is a different shape
    # from the tax-invoice template, so it read as a missing parser rather than
    # a document no parser should ever read.
    assert looks_like_credit_note(FOODLINK_CREDIT_MEMO) is True


def test_credit_words_in_ordinary_terms_do_not_trip_the_gate():
    # The false-positive direction, which is the expensive one now. Every phrase
    # here appears in real supplier footers in the corpus.
    for terms in (
        "TAX INVOICE\nWe accept credit card. A 1.5% surcharge applies.\nTOTAL 10.00",
        "TAX INVOICE\nClaims must be made within 48 hours and a credit will be issued.\nTOTAL 10.00",
        "TAX INVOICE\nCredit terms: 30 days from end of month.\nTOTAL 10.00",
        "TAX INVOICE\nAccount is subject to our credit application.\nTOTAL 10.00",
    ):
        assert looks_like_credit_note(terms) is False, terms


def test_credit_note_matches_through_pdf_whitespace():
    # The Select Fresh trap: extraction emits the layout's own spacing, so a
    # masthead can arrive as "Credit  Note" across a line break. Harmless while
    # this only set a flag; it decides whether a bill is read at all now.
    assert looks_like_credit_note("Tax Credit\n   Note  RINV/2026/08838") is True


def test_a_real_invoice_is_never_a_credit_note_or_a_statement():
    # Both gates run before any parser, in this order, so a real bill has to
    # survive both.
    assert looks_like_statement(GRIFTER_INVOICE := XERO_INVOICE) is False
    assert looks_like_credit_note(GRIFTER_INVOICE) is False
