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
