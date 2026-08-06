"""
Parser-layer unit tests.

The parsers themselves read real PDFs (tested against the corpus by
parser_regression.py). Here we lock the coordinate PRIMITIVE they all rely on —
bucketing a visual row's words into columns by x-position — since that's the
pure, PDF-free part and the thing most likely to silently drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.invoices import pdf_text  # noqa: E402

# Select Fresh column starts.
COLS = [("code", 0), ("desc", 78), ("order", 290), ("supply", 348),
        ("unit", 378), ("price", 460), ("total", 525)]


def _row(*words):
    # words: (x0, text) -> (x0, x1, text)
    return [(x, x + 20, t) for x, t in words]


def test_bucket_assigns_words_to_columns_by_x():
    row = _row((32, "CUCLK"), (80, "CUCUMBER"), (129, "LEBANESE"), (173, "KG"),
               (308, "2.00"), (357, "2.00"), (381, "KG"), (488, "4.10"), (547, "8.20"))
    c = pdf_text.bucket(row, COLS)
    assert c["code"] == "CUCLK"
    assert c["desc"] == "CUCUMBER LEBANESE KG"    # wrapped size stays in description
    assert c["supply"] == "2.00"
    assert c["unit"] == "KG"
    assert c["price"] == "4.10"
    assert c["total"] == "8.20"


def test_bucket_left_of_first_boundary_falls_in_first_column():
    c = pdf_text.bucket(_row((5, "X")), COLS)
    assert c["code"] == "X"


def test_word_rows_and_bucket_ignore_empty_columns():
    # a money row with no description (wrapped away) still yields the numbers
    row = _row((38, "4"), (68, "MKB500"), (381, "6.05"), (450, "0.00"), (516, "24.20"))
    c = pdf_text.bucket(row, [("qty", 0), ("sku", 64), ("unit", 143), ("desc", 198),
                              ("price", 360), ("gst", 449), ("amt", 506)])
    assert c["qty"] == "4" and c["sku"] == "MKB500"
    assert c["desc"] == "" and c["unit"] == ""
    assert c["price"] == "6.05" and c["amt"] == "24.20"


# Paramount Liquor column starts (Code | Description | Size | Case/Bottle |
# Base Cost | Total Net | WET | GST | LUC Ex GST | Total Inc GST).
PARAMOUNT_COLS = [("code", 0), ("desc", 75), ("size", 290), ("qty", 356),
                  ("base", 415), ("net", 485), ("wet", 548), ("gst", 600),
                  ("luc", 645), ("incgst", 705)]


def test_paramount_bucket_reads_total_inc_gst_on_a_wet_line():
    # CARPANO row: bottle-break qty "0 / 1", per-case base cost, WET present.
    # The reconcile figure is the rightmost Total-Inc-GST cell ($23.17).
    row = _row((21, "10015926"), (81, "CARPANO"), (128, "CLASSICO"),
               (176, "VERMOUTH"), (235, "750ml"), (296, "6/750"), (321, "ml"),
               (372, "0"), (379, "/"), (384, "1"), (428, "$98.00"),
               (494, "$16.33"), (555, "$4.74"), (609, "$2.11"), (650, "$21.07"),
               (727, "$23.17"))
    c = pdf_text.bucket(row, PARAMOUNT_COLS)
    assert c["code"] == "10015926"
    assert c["desc"].startswith("CARPANO")
    assert c["base"] == "$98.00"
    assert c["net"] == "$16.33"
    assert c["incgst"] == "$23.17"     # this is what the parser reconciles on


def test_paramount_bucket_reads_a_misc_charge_line():
    # Carton Freight (MISC) — captured as an EXTRA line; incgst = $7.15.
    row = _row((21, "9000000"), (81, "Carton"), (111, "Freight"), (296, "MISC"),
               (378, "5"), (431, "$1.30"), (496, "$6.50"), (609, "$0.65"),
               (650, "$1.30"), (730, "$7.15"))
    c = pdf_text.bucket(row, PARAMOUNT_COLS)
    assert c["code"] == "9000000"
    assert c["size"] == "MISC"
    assert c["desc"] == "Carton Freight"
    assert c["incgst"] == "$7.15"


# Andrews Meat column starts (Code | Description | Qty | Unit | Unit Price |
# GST | Total). Coordinates taken from a real invoice (INV3364687).
ANDREWS_COLS = [("code", 20), ("desc", 72), ("qty", 515), ("unit", 558),
                ("price", 615), ("gst", 705), ("total", 758)]


def test_andrews_bucket_reads_a_gst_free_meat_line():
    # BEEF TOPSIDE WAGYU line: qty 5.63 KG x $25.00 = $140.75, GST-free (gst
    # cell empty). Description wraps across four words but stays in one cell.
    row = _row((33, "1890K"), (80, "BEEF"), (111, "TOPSIDE"), (163, "WAGYU"),
               (207, "MB8+"), (537, "5.63"), (564, "KG"), (640, "$25.00"),
               (769, "$140.75"))
    c = pdf_text.bucket(row, ANDREWS_COLS)
    assert c["code"] == "1890K"
    assert c["desc"] == "BEEF TOPSIDE WAGYU MB8+"
    assert c["qty"] == "5.63"
    assert c["unit"] == "KG"
    assert c["price"] == "$25.00"
    assert c["gst"] == ""              # GST-free meat -> empty gst cell
    assert c["total"] == "$140.75"     # qty x price reconciles here


def test_andrews_meat_is_wired_to_its_invoice_domain():
    # The parser is registered on the INVOICE domain (accountsreceivable@
    # andrewsmeat.com); statements come from andrewsmeat.com.au. Guard the
    # mapping so the regression harness keeps scoring this parser.
    from modules.invoices.domains import DOMAIN_KEY
    from modules.invoices.parsers import DOMAIN_TO_PARSER
    assert DOMAIN_KEY.get("andrewsmeat.com") == "andrews_meat"
    assert "andrewsmeat.com" in DOMAIN_TO_PARSER


# --------------------------------------------------------------------------
# looks_like_statement — the guard that decides whether a PDF is a bill at all.
# A false positive here is the expensive direction: the invoice is thrown away
# silently and the supplier stops costing. These fixtures hold the real text.
# --------------------------------------------------------------------------

SELECT_FRESH_HEAD = """lic. 2024-13434
order enquiries
www.selectprovidores.com.au
TAX  INVOICE   2986250
invoice date
01-apr-26
stowaway
lvl 1, shp 18, 1-3 moore road
LEMON KG            1.00   KG    3.80    3.80
INVOICE TOTAL       55.20
terms: 14 days
please email remittance advice to accounts@selectprovidores.com.au
"""


def test_select_fresh_double_spaced_masthead_is_not_a_statement():
    # REGRESSION, and an expensive one. Select Fresh renders "TAX  INVOICE" with
    # two spaces, so the `"tax invoice"` escape hatch missed it; the footer's
    # "please email remittance advice to ..." then matched the remittance rule and
    # every one of their invoices was discarded as a statement — four months of
    # produce, herbs and citrus never reached data/invoices or COGS.
    from modules.invoices.run import looks_like_statement
    assert looks_like_statement(SELECT_FRESH_HEAD) is False


def test_a_tax_invoice_is_never_a_statement_however_it_is_spaced():
    from modules.invoices.run import looks_like_statement
    for masthead in ("TAX INVOICE", "TAX  INVOICE", "TAX\nINVOICE", "Tax   Invoice"):
        body = masthead + "\nremittance advice\nstatement\ncurrent 7 days 14 days\n"
        assert looks_like_statement(body) is False, masthead


ANDREWS_STATEMENT = """Andrews Meat Industries Pty Ltd
STATEMENT
38 Birnie Ave, Lidcombe NSW 2141
STOWAWAY FRESHWATER P/L (HAR038)
Total Due 253.50
DATE REFERENCE DESCRIPTION DEBIT CREDIT O/S AMOUNT
11/07/2026 INV3413956 Sales Order 253.50 253.50
28 Days+ 21 Days+ 14 Days 7 Days Current
0 0.00 0.00 253.50 0.00
"""

FARMER_JOES_STATEMENT = """STATEMENT
STOWAWAY BAR
Account Statement For Trading Terms: 7 DAYS NET
F J Chickens Pty Ltd
14/11/2024 4446142 INVOICE INV 4446142 140.00
CURRENT 7 DAYS 14 DAYS 21 +DAYS
$280.00 $280.00 $0.00 $0.00 $0.00
TOTAL DUE $280.00
ARSTARPT  V2.01 2
"""


def test_ageing_buckets_identify_a_statement():
    # Andrews Meat and Farmer Joes head these "STATEMENT" but print none of the
    # older balance phrases — they age the balance across day columns instead.
    # Both were reaching the LLM and burning an extraction apiece.
    from modules.invoices.run import looks_like_statement
    assert looks_like_statement(ANDREWS_STATEMENT) is True
    assert looks_like_statement(FARMER_JOES_STATEMENT) is True


def test_one_terms_line_is_not_an_ageing_table():
    # "Terms: 7 DAYS NET" plus the word "current" must NOT make an invoice a
    # statement — the rule needs two DISTINCT day buckets.
    from modules.invoices.run import looks_like_statement
    assert looks_like_statement(
        "STATEMENT of deliveries\ncurrent\nterms: 7 days net\n") is False


def test_direct_debit_request_form_is_not_an_invoice():
    # Paramount's and Foodlink's authority forms carry no line items and no total.
    # Paramount's renders through a broken font map 29 codepoints low, so the
    # title extracts as "',5(&7'(%,75(48(67".
    from modules.invoices.run import looks_like_statement
    assert looks_like_statement("Foodlink Australia\nDirect Debit Request (DRR)\n") is True
    assert looks_like_statement("$&1  $)6/ ',5(&7'(%,75(48(67 3K )D[") is True


def test_gulli_payment_receipt_is_not_an_invoice():
    # Lists the invoices it settles; "statement" never appears near the top.
    from modules.invoices.run import looks_like_statement
    assert looks_like_statement(
        "Gulli Food Distributors Pty Ltd\nPayment Receipt: PCBAAU/2026/10542\n"
        "Payment Amount: $ 8,821.38\nINVOICE DATE INVOICE NUMBER REFERENCE AMOUNT\n"
    ) is True


def test_farmer_joes_and_nicholas_seafood_are_wired_to_their_domains():
    # Both parsers existed and worked in production (run.py passes the real sender
    # domain), but were missing from DOMAIN_KEY — so parser_regression scored them
    # 0% and build_corpus could never grow their corpora.
    from modules.invoices.domains import DOMAIN_KEY
    from modules.invoices.parsers import DOMAIN_TO_PARSER
    for dom, key in (("farmerjoes.com.au", "farmer_joes"),
                     ("nicholasseafood.com.au", "nicholas_seafood")):
        assert DOMAIN_KEY.get(dom) == key
        assert dom in DOMAIN_TO_PARSER


def test_every_parser_domain_has_a_domain_key_entry():
    # The gap above must not reopen for the next parser someone adds.
    from modules.invoices.domains import DOMAIN_KEY
    from modules.invoices.parsers import DOMAIN_TO_PARSER
    missing = sorted(d for d in DOMAIN_TO_PARSER
                     if d not in DOMAIN_KEY and not d.startswith("members."))
    assert not missing, f"parser domains absent from DOMAIN_KEY: {missing}"
