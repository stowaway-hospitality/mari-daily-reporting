"""
Parser-layer unit tests.

The parsers themselves read real PDFs (tested against the corpus by
parser_regression.py). Here we lock the coordinate PRIMITIVE they all rely on —
bucketing a visual row's words into columns by x-position — since that's the
pure, PDF-free part and the thing most likely to silently drift.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices import pdf_text  # noqa: E402
from modules.invoices.parsers import paramount  # noqa: E402

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


# ---------------------------------------------------------------------------
# BULK containers. A "1/20000 ml" Size cell is ONE 20 L drum, not one bottle,
# and at ~$1,013 it is both correct and far over per_unit's $500 ceiling. Six
# of the twenty Paramount invoices in the corpus carry that line, and before
# bulk_litres() existed all six reconciled perfectly and were then failed by
# SANITY_BOUNDS — a 65% pass rate for one product. These pin the boundary so a
# later tidy-up of PACK_RE cannot quietly widen or narrow it.
# ---------------------------------------------------------------------------

def test_a_single_unit_twenty_litre_pack_is_bulk():
    # WHITE LIGHT VODKA ORIGINAL : 20000ml, code 32051, invoice 5408825.
    assert paramount.bulk_litres("1/20000 ml") == Decimal("20")


def test_a_fifteen_litre_wine_cask_is_bulk():
    # DE BORTOLI GOLD SEAL SPECIAL DRY RED — the other bulk family, at $55.90.
    # It sits at the bottom of per_bulk's range, which is why that range is not
    # allowed to start above $15.
    assert paramount.bulk_litres("1/15000 ml") == Decimal("15")


def test_a_carton_of_bottles_is_not_bulk_however_large_the_carton():
    # 6 x 700 ml is 4.2 L of liquid, but the UNIT is a 700 ml bottle. Reading
    # total volume instead of unit volume here would push ordinary spirits into
    # the bulk net and lose them per_unit's $500 ceiling.
    assert paramount.bulk_litres("6/700 ml") is None
    assert paramount.bulk_litres("12/1000 ml") is None
    assert paramount.bulk_litres("2/5000 ml") is None      # MASSENEZ BIB 2PACK


def test_an_ordinary_single_bottle_is_not_bulk():
    assert paramount.bulk_litres("1/700 ml") is None
    assert paramount.bulk_litres("1/3000 ml") is None      # 3 L jeroboam
    assert paramount.bulk_litres("1/4000 ml") == Decimal("4")   # threshold is >=


def test_bulk_ignores_weight_packs_and_junk():
    assert paramount.bulk_litres("1/20000 g") is None
    assert paramount.bulk_litres("MISC") is None
    assert paramount.bulk_litres("") is None
    assert paramount.bulk_litres(None) is None


def test_the_twenty_litre_drum_price_sits_inside_per_bulk_and_outside_per_unit():
    # The measured number, straight off invoice 5408825: $920.71 ex + $92.07
    # GST. If a future edit moves per_bulk's ceiling under this, or per_unit's
    # over it, the change that made six invoices free is silently undone.
    cfg = yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text())
    b = cfg["sanity_bounds"]
    drum = Decimal("1012.78")
    assert Decimal(str(b["per_bulk"]["min"])) <= drum <= Decimal(str(b["per_bulk"]["max"]))
    assert drum > Decimal(str(b["per_unit"]["max"]))
    # And the $55.90 cask has to fit the same range.
    assert Decimal(str(b["per_bulk"]["min"])) <= Decimal("55.90")


def test_per_unit_ceiling_was_not_raised_to_buy_the_drum():
    # The whole point of a separate basis. per_unit is the net that catches a
    # case total in a per-unit field; it stays where it was.
    cfg = yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text())
    assert Decimal(str(cfg["sanity_bounds"]["per_unit"]["max"])) == Decimal("500.00")
    assert Decimal(str(cfg["sanity_bounds"]["per_keg"]["max"])) == Decimal("600.00")


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


# --------------------------------------------------------------------------
# Fresh Fruit Team — the SKU cell swallowing the UNIT word.
#
# FFT's UNIT column does not always start right of its x-boundary, so bucket()
# files the unit under "sku" and the code arrives as "CLKG Kilogram". Money was
# never affected (FFT scored 52/52 on the regression the whole time this was
# happening) because supplier_code plays no part in reconciling to the printed
# total. What it corrupts is IDENTITY: "CLKG" and "CLKG Kilogram" are the same
# carrot, so the cost book carried the product twice, price history split across
# the pair, and build_ingredients could no longer consolidate the fullest name
# across them — which is how fragments like "Large" (Carrot Large) and "Ruby
# Red" (Grapefruit Ruby Red) reached the chef's picker as product names.
#
# Before the fix: 169 distinct FFT codes, 84 carrying a swallowed word, 50
# products split into two identities. After: 119 codes, 0 dirty, 0 split.


def test_fft_split_sku_strips_a_swallowed_unit_word():
    from modules.invoices.parsers.fresh_fruit_team import _split_sku
    assert _split_sku("CLKG Kilogram") == ("CLKG", "Kilogram")
    assert _split_sku("TR10BX Box") == ("TR10BX", "Box")
    assert _split_sku("AH20T Tray") == ("AH20T", "Tray")
    assert _split_sku("CL20KGBX 20kg") == ("CL20KGBX", "20kg")


def test_fft_split_sku_leaves_a_clean_code_alone():
    from modules.invoices.parsers.fresh_fruit_team import _split_sku
    # The overwhelmingly common case: the unit landed in its own column.
    assert _split_sku("CLKG") == ("CLKG", "")
    assert _split_sku("GRR12BX") == ("GRR12BX", "")
    assert _split_sku("") == ("", "")
    assert _split_sku(None) == ("", "")


def test_fft_the_two_spellings_of_one_carrot_collapse_to_one_identity():
    # The whole point: these two must not be two products.
    from modules.invoices.parsers.fresh_fruit_team import _split_sku
    assert _split_sku("CLKG Kilogram")[0] == _split_sku("CLKG")[0]


def test_fft_a_swallowed_word_is_only_taken_as_the_unit_if_it_names_one():
    # The swallowed tail is used as the row's unit, but ONLY through the same
    # names_a_unit guard the neighbour-stitch uses. A description word bleeding
    # into the SKU cell must NOT become the unit — taking "Cabbage" or "Herb" as
    # a UOM is what turns a per-kilogram line into a pack and misprices it.
    from modules.invoices.pack_size import names_a_unit
    assert names_a_unit("Kilogram") and names_a_unit("Box") and names_a_unit("Tray")
    assert not names_a_unit("Herb")
    assert not names_a_unit("Lettuce")
    assert not names_a_unit("Cauliflower")


# --- Foodlink: column boundaries derived from the header, not hard-coded ------
# 2026-08-15. Foodlink re-templated and shifted its table right by ~25pt. The
# parser's hard-coded COLS put the Qty. VALUE (x=290) past the uom boundary
# (270), so c["qty"] came back empty, the `qty is None` guard skipped every row,
# and the parser raised "no line items parsed" on EVERY invoice from 2026-07-29
# on — 10 of them sat in Review for 2.5 weeks. The corpus could not catch it:
# build_corpus.py only scans the INBOX, and these had already been moved to the
# Invoices Review folder. So the regression harness said foodlink 59/59 (100%)
# while production was failing 100% of new invoices.
#
# These two rows are the REAL header word positions from each layout.

FL_HEADER_OLD = _row((32.6, "No."), (70.9, "Description"), (251.0, "Qty."),
                     (272.5, "UOM"), (327.1, "Weight"), (429.5, "Disc"),
                     (451.0, "%"), (462.9, "GST"))
FL_HEADER_NEW = _row((34.0, "No."), (73.6, "Description"), (277.5, "Qty."),
                     (306.4, "UOM"), (345.1, "Weight"), (432.5, "Disc"),
                     (454.0, "%"), (468.1, "GST"))


def test_foodlink_old_layout_row_buckets_correctly():
    from modules.invoices.parsers.foodlink import _cols_from_header
    cols = _cols_from_header(FL_HEADER_OLD)
    # SOUR CREAM FULL 2LT Brancourts, 3 EA @ 19.00 = 57.00
    row = _row((33, "102638"), (71, "SOUR"), (99, "CREAM"), (134, "FULL"),
               (159, "2LT"), (177, "Brancourts"), (263, "3"), (276, "EA"),
               (384, "19.00"), (529, "57.00"))
    c = pdf_text.bucket(row, cols)
    assert c["code"] == "102638"
    assert c["qty"] == "3"
    assert c["uom"] == "EA"
    assert c["price"] == "19.00"
    assert c["total"] == "57.00"


def test_foodlink_new_layout_row_buckets_correctly():
    from modules.invoices.parsers.foodlink import _cols_from_header
    cols = _cols_from_header(FL_HEADER_NEW)
    # ARANCINI TRUFFLED PORCINI, origin AU, 1 CTN @ 124.00 = 124.00
    row = _row((34, "103742"), (74, "ARANCINI"), (119, "TRUFFLED"), (207, "AU"),
               (290, "1"), (308, "CTN"), (399, "124.00"), (527, "124.00"))
    c = pdf_text.bucket(row, cols)
    assert c["code"] == "103742"
    assert c["qty"] == "1", "the regression: qty fell into uom under hard-coded COLS"
    assert c["uom"] == "CTN"
    assert c["price"] == "124.00"
    assert c["total"] == "124.00"
    assert "AU" in c["desc"]        # origin folds into description, as before


def test_foodlink_hardcoded_cols_would_have_missed_the_new_qty():
    # Guards the diagnosis itself: if this ever stops being true, the bug
    # described above was something else and the comment needs revisiting.
    from modules.invoices.parsers.foodlink import COLS
    row = _row((290, "1"), (308, "CTN"))
    c = pdf_text.bucket(row, COLS)
    assert c["qty"] == ""
    assert c["uom"] == "1 CTN"


def test_foodlink_gst_flag_column_survives_both_layouts():
    from modules.invoices.parsers.foodlink import _cols_from_header
    for hdr, gx, tx in ((FL_HEADER_OLD, 463, 529), (FL_HEADER_NEW, 468, 537)):
        cols = _cols_from_header(hdr)
        c = pdf_text.bucket(_row((71, "Fuel"), (91, "Levy"), (263, "1"),
                                 (389, "3.00"), (gx, "GST"), (tx, "3.00")), cols)
        assert c["gstflag"] == "GST"
        assert c["total"] == "3.00"


def test_foodlink_unreadable_header_falls_back_rather_than_inventing_columns():
    from modules.invoices.parsers.foodlink import _cols_from_header
    assert _cols_from_header(_row((10, "No."), (50, "Description"))) is None
    assert _cols_from_header([]) is None


# --- Foodlink: a WRAPPED description is joined, not dropped -------------------
# 2026-08-17. A Foodlink description wraps onto its own row, and that row has no
# qty and no total, so the `qty is None` guard threw it away. Measured on the
# corpus: 435 wrapped rows across 129 invoices — two thirds of all line items
# lost text — while the table read foodlink 129/129 (100%) throughout, because a
# description takes no part in reconciliation. Same blind spot as FFT above.
#
# It carries money. Foodlink's UOM column only ever says EA/CTN, so the PACK
# SIZE lives in the description and it is usually the part that wrapped:
# "GRAVY MIX RICH BROWN G/FREE " + "7KG Executive Chef". Without "7KG", code
# 101239 costs $57.61/ea instead of $8.23/kg and needs_pack_review goes true.
#
# The continuation is identified by its indent, DERIVED per invoice: the corpus
# holds two description left edges (70.9 and 73.6) and Foodlink's only other
# desc-only rows are its two footer boilerplate lines at 90.6.


# Real word positions lifted from the corpus, one invoice per layout — NOT
# invented. (Inventing them is how the Xero fixture of 2026-08-15 ended up
# labelled with the wrong supplier's header.) A synthetic row built from the old
# layout's x-positions does not even reach the qty column under the new header,
# so a made-up fixture here would fail for the wrong reason.
#   old: 91076827dc2a, description indent 70.9
#   new: 10439c457ba4, description indent 73.6
FL_LAYOUTS = (
    # header, desc indent, line-item row, its wrapped tail
    (FL_HEADER_OLD, 70.9,
     _row((32.6, "105001"), (70.9, "MAYONNAISE"), (133.4, "WHOLE"), (168.9, "EGG"),
          (191.4, "20KG"), (263.5, "1"), (275.5, "EA"), (379.4, "130.00"), (524.5, "130.00")),
     _row((70.9, "Plate"), (93.9, "&"), (102.4, "Platter"))),
    (FL_HEADER_NEW, 73.6,
     _row((34.0, "100710"), (73.6, "CHOCOLATE"), (132.1, "DARK"), (159.6, "1KG"),
          (290.0, "3"), (310.9, "EA"), (403.8, "14.50"), (531.7, "43.50")),
     _row((73.6, "Natures"), (107.6, "Secret"))),
)

# Foodlink's two footer boilerplate lines, on every invoice in the corpus.
FL_FOOTER = _row((90.6, "MSC"), (120.0, "Certification"), (180.0, "code:"))


def test_foodlink_wrapped_description_is_joined_to_the_line_above():
    from modules.invoices.parsers.foodlink import _cols_from_header, _desc_x, CONT_INDENT_TOL
    for header, indent, item, tail in FL_LAYOUTS:
        cols = _cols_from_header(header)
        dx = _desc_x([item, tail, FL_FOOTER], cols)
        assert dx == indent, "the indent is read off the first line item, not hard-coded"
        c = pdf_text.bucket(tail, cols)
        # all three conditions the parser requires of a continuation
        assert [k for k, v in c.items() if v.strip()] == ["desc"]
        assert abs(tail[0][0] - dx) < CONT_INDENT_TOL
        assert c["desc"] in ("Plate & Platter", "Natures Secret")


def test_foodlink_footer_boilerplate_is_not_joined_as_a_description():
    # The dangerous neighbour: "MSC Certification code: ..." and "no." are also
    # desc-only rows. They sit at x=90.6 on every invoice — ~17pt clear of both
    # description indents — so the indent test is the only thing keeping them
    # out. If this ever fails, product names are about to grow a certification
    # number.
    from modules.invoices.parsers.foodlink import _cols_from_header, _desc_x, CONT_INDENT_TOL
    for header, _indent, item, _tail in FL_LAYOUTS:
        cols = _cols_from_header(header)
        dx = _desc_x([item, FL_FOOTER], cols)
        c = pdf_text.bucket(FL_FOOTER, cols)
        assert [k for k, v in c.items() if v.strip()] == ["desc"], "desc-only, like a real tail"
        assert abs(FL_FOOTER[0][0] - dx) >= CONT_INDENT_TOL, "excluded by indent alone"


def test_foodlink_dropping_the_tail_is_what_lost_the_pack_size():
    # Pins the diagnosis, not just the fix. Under the OLD behaviour the tail row
    # was skipped by the `qty is None` guard, so 101239 read "GRAVY MIX RICH
    # BROWN G/FREE" and its 7KG — the only pack size Foodlink states anywhere,
    # since the UOM column just says EA — went with it.
    from modules.invoices.parsers.foodlink import _cols_from_header, _m
    cols = _cols_from_header(FL_HEADER_NEW)
    tail = _row((73.6, "7KG"), (103.6, "Executive"), (153.6, "Chef"))
    c = pdf_text.bucket(tail, cols)
    assert _m(c["qty"]) is None and _m(c["total"]) is None, "no qty, no total"
    assert c["desc"] == "7KG Executive Chef", "and the pack size is all of it"


def test_foodlink_desc_x_returns_none_when_there_is_no_line_item():
    # No anchor -> no joining at all. Inventing an indent is how a footer line
    # becomes a product name.
    from modules.invoices.parsers.foodlink import _cols_from_header, _desc_x
    cols = _cols_from_header(FL_HEADER_NEW)
    assert _desc_x([_row((90.6, "MSC"), (120, "Certification"))], cols) is None


def test_foodlink_delivery_note_spills_across_columns_so_is_never_a_continuation():
    # "**Enter via Moore Lane, up wheelchair ramp ..." runs the full width of the
    # page, so it fills the code/qty/uom buckets too. The desc-only test excludes
    # it without needing to match on its wording.
    from modules.invoices.parsers.foodlink import _cols_from_header
    cols = _cols_from_header(FL_HEADER_NEW)
    note = _row((37.3, "**Enter"), (74, "via"), (100, "Moore"), (140, "Lane,"),
                (290, "enter"), (308, "through"), (400, "door,"), (529, "on"))
    c = pdf_text.bucket(note, cols)
    assert [k for k, v in c.items() if v.strip()] != ["desc"]


# --- FFT: column boundaries derived from the header, not hard-coded -----------
# 2026-08-15, the sibling of the Foodlink defect found the same day. FFT's header
# does not sit still: across the corpus the ITEM anchor ranges 181.7 -> 201.3.
# The hard-coded desc boundary was 198 — the TOP of that range — so on every
# invoice whose ITEM anchor sat left of 198, the description's FIRST WORD fell
# into the unit bucket: raw_uom "Carrot", description "Large".
#
# The money still reconciled to the cent on every one of those invoices, so the
# regression table read 52/52 (100%) while 274 lines carried a description word
# as their unit and 51 of 119 codes had split into two descriptions. Fixing the
# boundaries took it to 32 bad units (31 of which are the REAL unit "Market
# Bunch") and 6 split codes, with the money unchanged.

FFT_HEADER_WIDE = _row((30.0, "QTY"), (68.0, "SKU"), (143.3, "UNIT"), (198.3, "ITEM"),
                       (362.0, "UNIT"), (389.5, "PRICE"), (450.9, "GST"), (508.9, "AMOUNT"))
FFT_HEADER_NARROW = _row((30.4, "QTY"), (68.8, "SKU"), (130.5, "UNIT"), (185.5, "ITEM"),
                         (357.9, "UNIT"), (385.4, "PRICE"), (448.3, "GST"), (507.7, "AMOUNT"))


def test_fft_narrow_layout_keeps_the_first_description_word():
    from modules.invoices.parsers.fresh_fruit_team import _cols_from_header
    cols = _cols_from_header(FFT_HEADER_NARROW)
    # "Carrot Large", 1 Kilogram @ 1.32 — the row that produced raw_uom="Carrot".
    row = _row((30, "1"), (69, "CLKG"), (131, "Kilogram"), (186, "Carrot"),
               (214, "Large"), (392, "1.32"), (455, "0.00"), (521, "1.32"))
    c = pdf_text.bucket(row, cols)
    assert c["sku"] == "CLKG"
    assert c["unit"] == "Kilogram"
    assert c["desc"] == "Carrot Large", "the regression: 'Carrot' bled into the unit column"
    assert c["price"] == "1.32"
    assert c["amt"] == "1.32"


def test_fft_wide_layout_still_buckets_correctly():
    from modules.invoices.parsers.fresh_fruit_team import _cols_from_header
    cols = _cols_from_header(FFT_HEADER_WIDE)
    row = _row((30, "1"), (68, "CLKG"), (143, "Kilogram"), (199, "Carrot"),
               (227, "Large"), (392, "1.32"), (455, "0.00"), (521, "1.32"))
    c = pdf_text.bucket(row, cols)
    assert c["sku"] == "CLKG" and c["unit"] == "Kilogram"
    assert c["desc"] == "Carrot Large"


def test_fft_hardcoded_cols_would_have_eaten_the_narrow_layout_word():
    # Pins the diagnosis: under the old fixed boundaries the same row loses
    # "Carrot" to the unit cell. If this stops being true, the story above is
    # wrong and the comments need revisiting.
    from modules.invoices.parsers.fresh_fruit_team import COLS, _split_sku
    row = _row((30, "1"), (69, "CLKG"), (131, "Kilogram"), (186, "Carrot"),
               (214, "Large"), (392, "1.32"), (455, "0.00"), (521, "1.32"))
    c = pdf_text.bucket(row, COLS)
    # One root cause, both documented symptoms on a single row: the SKU cell
    # swallows the unit AND the unit cell swallows the first description word.
    assert c["sku"] == "CLKG Kilogram"
    assert _split_sku(c["sku"]) == ("CLKG", "Kilogram")
    assert c["unit"] == "Carrot"
    assert c["desc"] == "Large"


def test_fft_unreadable_header_falls_back_rather_than_inventing_columns():
    from modules.invoices.parsers.fresh_fruit_team import _cols_from_header
    assert _cols_from_header(_row((30, "QTY"), (68, "SKU"))) is None
    assert _cols_from_header([]) is None
    # Right shape, wrong labels -> fall back, don't guess.
    assert _cols_from_header(_row(*[(x, "X") for x in (30, 68, 143, 198, 362, 389, 450, 508)])) is None


def test_fft_order_note_is_not_part_of_the_product_name():
    from modules.invoices.parsers.fresh_fruit_team import _strip_order_note
    assert _strip_order_note("Zucchini Green 0.5Kg please") == "Zucchini Green 0.5Kg"
    assert _strip_order_note("please make sure all product are") == ""
    assert _strip_order_note("Avocado Hass good and best quality. thank you") \
        == "Avocado Hass good and best quality."
    # A real name is untouched, including one whose size IS the catalogue name.
    assert _strip_order_note("Mushroom King Brown (200G Punnet)") \
        == "Mushroom King Brown (200G Punnet)"
    assert _strip_order_note("Carrot Large") == "Carrot Large"
    assert _strip_order_note("") == ""
    assert _strip_order_note(None) == ""


# --- JFC Australia: a separate company from Jun Pacific ----------------------
# 2026-08-15. Every JFC invoice that reached data/invoices before today carries
# supplier_key "jun_pacific" — they were LLM-extracted before either had a parser
# and nothing checked the ABN. They are different companies on different invoice
# systems:
#     Jun Pacific Corporation   ABN 71 054 434 061   "Tax Invoice: NB10482429"
#     JFC Australia Co Pty Ltd  ABN 36 003 080 260   "INVOICE No.  001910089"
# Their codes do not collide (JFC numeric, Jun Pacific alphanumeric), so the two
# separate cleanly; build_cogs_list re-labels the five historical rows onto "JFC"
# so the cost series stays continuous across the correction.

JFC_HEADER = _row((12, "ITEM"), (45, "PRODUCT"), (87, "DESCRIPTION"), (317, "QTY"),
                  (335, "þ"), (354, "UNIT"), (385, "LIST"), (434, "UNIT"),
                  (466, "AMOUNT"))
JFC_HEADER2 = _row((533, "GST"), (573, "WET"))


def test_jfc_columns_come_from_the_header():
    from modules.invoices.parsers.jfc import _cols_from_header
    cols = _cols_from_header(JFC_HEADER, JFC_HEADER2)
    assert cols is not None
    # A real money row off invoice 001910089.
    row = _row((11, "30562"), (45, "SOMI"), (71, "Shoyu"), (101, "G"), (110, "10/1kg"),
               (327, "1"), (351, "EACH"), (397, "18.70"), (440, "11.00"),
               (487, "11.00"), (532, "0.00"), (575, "0.00"))
    c = pdf_text.bucket(row, cols)
    assert c["item"] == "30562"
    assert c["desc"] == "SOMI Shoyu G 10/1kg"
    assert c["qty"] == "1"
    assert c["udesc"] == "EACH"
    assert c["listp"] == "18.70"      # LIST price is not what we cost off
    assert c["unitp"] == "11.00"
    assert c["amt"] == "11.00"
    assert c["gst"] == "0.00"
    assert c["wet"] == "0.00"


def test_jfc_two_unit_headers_are_taken_in_order():
    # "UNIT" appears twice — "UNIT DESC." and "UNIT PRICE". A label lookup would
    # collapse them and put the price in the unit-of-measure cell.
    from modules.invoices.parsers.jfc import _cols_from_header
    cols = dict(_cols_from_header(JFC_HEADER, JFC_HEADER2))
    assert cols["udesc"] < cols["listp"] < cols["unitp"] < cols["amt"]


def test_jfc_unreadable_header_falls_back_rather_than_inventing_columns():
    from modules.invoices.parsers.jfc import _cols_from_header
    assert _cols_from_header(_row((12, "ITEM"), (45, "PRODUCT")), JFC_HEADER2) is None
    assert _cols_from_header(JFC_HEADER, []) is None          # no GST/WET row
    assert _cols_from_header([], []) is None


def test_jfc_line_counter_is_not_a_product_code():
    # The wrap row carries "< 3 >" in the ITEM column; it must never be read as a
    # supplier code, and its description tail belongs to the line above.
    from modules.invoices.parsers.jfc import _COUNTER
    for s in ("< 1 >", "<2>", "< 10 >"):
        assert _COUNTER.match(s)
    for s in ("30562", "HA8204612", "", "<>"):
        assert not _COUNTER.match(s)


def test_jfc_direct_debit_notice_is_not_an_invoice():
    from modules.invoices.run import looks_like_statement
    doc = ("EFT Direct Debit Notice\nABN 36 003 080 260\nSTOWAWAY\n"
           "The following amount will be debited from your account: 223.50\n"
           "Payment Amount Invoice Number Doc Type Invoice Date\n"
           "223.50 07/08/2026 Invoice 001900310\nTotal: 223.50")
    assert looks_like_statement(doc) is True


# --- Xero: one sender, many vendors ------------------------------------------
# 2026-08-15. The first parser where the sender domain does NOT name the supplier
# — every Xero-issued invoice arrives from post.xero.com. Getting the vendor wrong
# merges several suppliers' price histories, so the identification is pinned hard.

def test_xero_vendor_is_the_abn_that_is_not_ours():
    from modules.invoices.parsers.xero import vendor_from_abn
    # Urbun Bakery: OUR ABN is printed FIRST, above the vendor's. Keying on the
    # first ABN would have filed five different suppliers under Stowaway's own.
    urbun = "ABN: 17 606 243 921\nInvoice\nABN 25 617 284 705\nMallia Industries"
    assert vendor_from_abn(urbun) == ("mallia_industries", "Urbun Bakery")
    # Grifter prints only its own.
    assert vendor_from_abn("ABN 53 158 357 450")[0] == "grifter"


def test_xero_refuses_to_guess_a_vendor():
    from modules.invoices.parsers.xero import vendor_from_abn
    assert vendor_from_abn("") is None                       # no ABN at all
    assert vendor_from_abn("ABN 17 606 243 921") is None      # only ours
    assert vendor_from_abn("ABN 11 111 111 111") is None      # not registered
    # AMBIGUOUS: two non-customer ABNs. Two candidates must never be resolved by
    # picking one — an invented vendor merges two suppliers' price histories.
    two = "ABN 83 105 791 419\nABN 53 158 357 450"
    assert vendor_from_abn(two) is None
    # THIS PAIR IS NOT AMBIGUOUS, and believing it was is what kept the SYMSAFE
    # credit note in Review. 38 760 949 765 is OUR OWN second ABN (it is printed
    # in the ship-to block of 33 corpus invoices, never on a letterhead), so
    # dropping the customer side leaves exactly one vendor. Pinned because the
    # earlier reading — "references a second party" — was recorded in the triage
    # log as fact and would otherwise be re-derived.
    assert vendor_from_abn("ABN 83 105 791 419\nABN 38 760 949 765") == (
        "symsafe", "Symsafe Pty Ltd")


def test_our_own_abn_can_never_be_a_vendor():
    from modules.invoices.parsers.xero import CUSTOMER_ABNS, ABN_SUPPLIER
    assert CUSTOMER_ABNS & set(ABN_SUPPLIER) == set(), (
        "an ABN cannot be both the customer and a vendor")


XERO_HEADER_ITEM = _row((31, "Item"), (85, "Description"), (262, "Quantity"),
                        (339, "Unit"), (357, "Price"), (400, "Discount"),
                        (469, "GST"), (515, "Amount"), (547, "AUD"))
XERO_HEADER_PLAIN = _row((31, "Description"), (316, "Quantity"), (393, "Unit"),
                         (411, "Price"), (453, "Discount"), (515, "Amount"),
                         (547, "AUD"))
XERO_HEADER_QTY_L = _row((28, "Description"), (233, "Quantity(L)"), (334, "Unit"),
                         (354, "Price"), (425, "GST"), (512, "Amount"), (548, "AUD"))


def test_xero_header_shapes_all_resolve():
    from modules.invoices.parsers.xero import _cols_from_header
    for h in (XERO_HEADER_ITEM, XERO_HEADER_PLAIN, XERO_HEADER_QTY_L):
        assert _cols_from_header(h) is not None
    # "Quantity(L)" (Speed Gas) must be recognised as Quantity — letters only.
    cols = dict(_cols_from_header(XERO_HEADER_QTY_L))
    assert cols["qty"] < cols["price"] < cols["amt"]


def test_xero_item_column_only_exists_when_the_header_says_so():
    from modules.invoices.parsers.xero import _cols_from_header
    assert "item" in dict(_cols_from_header(XERO_HEADER_ITEM))
    assert "item" not in dict(_cols_from_header(XERO_HEADER_PLAIN))


def test_xero_philters_two_token_code_stays_in_the_item_column():
    # "XPA 200" is Philter's real code, printed as two tokens; the description
    # starts cleanly at its own anchor. This is the row the identity audit flags,
    # and it is a false positive — the whitespace is the supplier's.
    from modules.invoices.parsers.xero import _cols_from_header
    cols = _cols_from_header(XERO_HEADER_ITEM)
    row = _row((31, "XPA"), (47, "200"), (85, "Philter"), (111, "XPA"), (127, "4.2%"),
               (169, "50L"), (184, "Keg"), (279, "2.00"), (350, "299.00"),
               (402, "20.00%"), (468, "10%"), (539, "478.40"))
    c = pdf_text.bucket(row, cols)
    assert c["item"] == "XPA 200"
    assert c["desc"].startswith("Philter XPA")
    assert c["amt"] == "478.40"


def test_xero_unreadable_header_falls_back_rather_than_inventing_columns():
    from modules.invoices.parsers.xero import _cols_from_header
    assert _cols_from_header(_row((31, "Description"))) is None
    assert _cols_from_header([]) is None


# The Beerline Cleaning Company bills a fixed monthly fee per venue: the invoice
# sells an agreement, so there is no unit to count and the header carries neither
# Quantity nor Unit Price.
XERO_HEADER_SERVICES = _row((31, "Description"), (469, "GST"),
                            (515, "Amount"), (547, "AUD"))
# Xero's payment RECEIPT. Also has no Quantity, also states a total that would
# reconcile — and it is a record of an invoice ALREADY PAID, so parsing one would
# book the same money twice.
XERO_HEADER_RECEIPT = _row((31, "Invoice"), (76, "Date"), (140, "Reference"),
                           (250, "Payment"), (287, "Reference"), (380, "Invoice"),
                           (410, "Total"), (470, "Amount"), (500, "Paid"),
                           (540, "Still"), (562, "Owing"))


def test_xero_services_header_with_no_quantity_resolves():
    from modules.invoices.parsers.xero import _cols_from_header
    cols = _cols_from_header(XERO_HEADER_SERVICES)
    assert cols is not None, "a fixed-fee services invoice is still an invoice"
    names = dict(cols)
    assert "qty" not in names and "price" not in names
    assert names["desc"] < names["mid"] < names["amt"]


def test_xero_payment_receipt_is_not_read_as_an_invoice():
    # The guard is the Description column, not a keyword: a receipt tabulates
    # OTHER invoices and so never has one. If this ever starts returning columns,
    # every payment receipt in the mailbox becomes a duplicate purchase.
    from modules.invoices.parsers.xero import _cols_from_header
    assert _cols_from_header(XERO_HEADER_RECEIPT) is None


def test_xero_reduced_header_never_outranks_a_full_one():
    # Order matters in parse(): a full header must win even when a reduced match
    # appears EARLIER on the page, or a normal invoice silently loses its qty and
    # unit-price columns and every line reads as one unit at the line total.
    from modules.invoices.parsers.xero import _cols_from_header
    full = dict(_cols_from_header(XERO_HEADER_ITEM))
    assert "qty" in full and "price" in full
    # the same row must not be mistaken for the services shape
    assert _cols_from_header(XERO_HEADER_ITEM) != _cols_from_header(XERO_HEADER_SERVICES)


def test_a_service_suppliers_lines_are_not_stock():
    # A STOCK line is bounds-checked as a purchasable AND offered to the chef as
    # a recipe ingredient. Twin Fin's "Social Media Management" at $4,400 was
    # held by SANITY_BOUNDS for being outside the plausible per_unit range —
    # the guard working exactly as designed on a number that is not a unit price
    # for a thing. These vendors sell services and nothing else, so it is a
    # supplier-level fact and there is no per-line judgement to get wrong.
    from modules.invoices.parsers.xero import SERVICE_SUPPLIERS, ABN_SUPPLIER
    keys = {k for k, _n in ABN_SUPPLIER.values()}
    assert SERVICE_SUPPLIERS <= keys, (
        "a service supplier must also be a registered vendor, or it can never "
        f"be reached: {SERVICE_SUPPLIERS - keys}")
    # the food and beverage vendors must NOT be in it — their lines are goods
    for goods in ("mallia_industries", "canton_group", "grifter", "philter",
                  "moda_sparkling", "sigurd_wines", "speed_gas"):
        assert goods not in SERVICE_SUPPLIERS, (
            f"{goods} sells goods; marking it a service would take its lines out "
            f"of the cost book entirely")


# --- Xero: Canton Group, a bare-integer quantity ------------------------------
# 2026-08-18. Canton Group (bao buns and spring rolls for Harry Gatos — KITCHEN
# FOOD) had never once parsed. Its header is an ordinary full header, its money
# reconciles to the cent, and the vendor ABN was registered from the day the
# parser was written; the whole document was refused because it types its
# quantity as "3" and the parser read that column with the MONEY reader, which
# insists on exactly two decimal places. Every line was skipped and parse()
# raised "no line items parsed".
#
# Coordinates below are the REAL ones off corpus 164542cc0a23 (INV-5096), not
# invented — a hand-made row does not reach the right buckets under a derived
# header, which is the lesson the Xero fixtures learned the first time.
XERO_HEADER_CANTON = _row((36.0, "Description"), (344.2, "Quantity"),
                          (417.8, "Price"), (484.5, "Tax"), (527.2, "Amount"))
XERO_ROW_CANTON = _row((36.0, "DAVIDSON"), (84.8, "PLUM"), (113.2, "BBQ"),
                       (134.2, "PORK"), (161.2, "BUNS"), (189.0, "(20"),
                       (205.5, "PCS)"), (375.0, "3"), (414.0, "40.00"),
                       (485.2, "0%"), (531.0, "120.00"))


def test_xero_canton_header_resolves_and_buckets_its_own_line():
    from modules.invoices.parsers.xero import _cols_from_header
    cols = _cols_from_header(XERO_HEADER_CANTON)
    assert cols is not None
    names = dict(cols)
    assert "item" not in names                      # Canton prints no code column
    assert names["desc"] < names["qty"] < names["price"] < names["mid"] < names["amt"]
    c = pdf_text.bucket(XERO_ROW_CANTON, cols)
    assert c["desc"] == "DAVIDSON PLUM BBQ PORK BUNS (20 PCS)"
    assert c["qty"] == "3"
    assert c["price"] == "40.00"
    assert c["amt"] == "120.00"


def test_xero_bare_integer_quantity_is_read():
    # The defect itself. A quantity is printed however the VENDOR typed it into
    # Xero; it is not money and must not be read with the money reader.
    from modules.invoices.parsers.xero import _q
    assert _q("3") == Decimal("3")
    assert _q("2") == Decimal("2")
    assert _q("1.00") == Decimal("1.00")            # the common spelling still works
    assert _q("0.5") == Decimal("0.5")
    assert _q("1,200") == Decimal("1200")


def test_xero_money_reader_still_refuses_a_bare_integer():
    # PINS THE DIAGNOSIS: under the old code the quantity went through _m, and
    # _m returning None on "3" is exactly why every Canton line was skipped.
    # It must KEEP returning None — that two-decimal strictness is what keeps
    # "0%", "10%" and date fragments out of the AMOUNT column.
    from modules.invoices.parsers.xero import _m
    assert _m("3") is None
    assert _m("2") is None
    assert _m("120.00") == Decimal("120.00")


def test_xero_quantity_reader_is_not_a_free_for_all():
    # Loosening the column must not let a percentage, a code or a stray word in.
    from modules.invoices.parsers.xero import _q
    for junk in ("0%", "10%", "", "   ", "INV-5096", "3 PCS", "$3.00", "3."):
        assert _q(junk) is None, junk


def test_xero_invoice_date_is_taken_by_label_never_by_position():
    # Canton's template prints "Due date 20 Aug 2026" IMMEDIATELY ABOVE
    # "Issue date 13 Aug 2026". Taking the first date on the page would book the
    # cost in the wrong week and mis-order its price history.
    from modules.invoices.parsers.xero import invoice_date
    canton = ("Amount due\n$320.00\nDue date\n20 Aug 2026\n"
              "Issue date\n13 Aug 2026\nInvoice number\nINV-5096\n")
    assert invoice_date(canton) == date(2026, 8, 13)
    # the usual label still wins, and wins FIRST when both are present
    assert invoice_date("Invoice Date\n6 Jul 2026\n") == date(2026, 7, 6)
    assert invoice_date("Invoice Date\n6 Jul 2026\nIssue date\n1 Jan 2020\n") == date(2026, 7, 6)
    # no labelled date at all -> None, never a guess off some other date
    assert invoice_date("Due date\n20 Aug 2026\n") is None
    assert invoice_date("") is None


# --- Gulli: column boundaries derived from the header, not hard-coded ---------
# 2026-08-16. The third parser in this family, and the last one that was still
# bucketing on literal x-positions. The 2026-08-15 (fifth pass) triage entry
# named it as the remaining risk; opening it found the defect had ALREADY fired.
#
# Gulli lays its table out to fit its CONTENT, so the columns move invoice to
# invoice rather than drifting once at a re-template: across 33 corpus invoices
# the DESCRIPTION anchor ranges 122.8 -> 166.5 and QUANTITY 336.3 -> 394.5. The
# hard-coded boundaries were 125 and 335 — INSIDE both ranges. So the split was
# effectively decided per invoice by how wide that invoice's content happened to
# be, and it had gone wrong in BOTH directions on real documents:
#
#   * narrow layout (DESCRIPTION 122.8, below the 125 split) — the description's
#     FIRST word falls into the code cell and is dropped:
#         "Barbaro- Soppressata Hot (Zig Zag) r/w 2.5kg" -> "Soppressata Hot ..."
#   * wide layout (QUANTITY 394.5, so the description runs past the 335 split) —
#     the description's LAST words fall into the numeric cell and are dropped:
#         "Sweet Baby Rays ... Barbeque Sauce" -> "Sweet Baby Rays ... Barbeque"
#
# Both still reconcile to the cent — the money never touches the description —
# so the regression table read gulli 31/32 throughout and the identity/unit
# audits stayed clean, exactly as they did for Foodlink and FFT. Deriving the
# boundaries from the header repairs 3 of 309 line rows with the money unchanged.

GU_HEADER_NARROW = _row((31.8, "PRODUCT"), (77.4, "CODE"), (122.8, "DESCRIPTION"),
                        (336.3, "QUANTITY"), (395.1, "UNIT"), (418.6, "PRICE"),
                        (454.6, "DISC.%"), (489.7, "GST"), (521.9, "AMOUNT"))
GU_HEADER_TYPICAL = _row((31.8, "PRODUCT"), (77.4, "CODE"), (157.4, "DESCRIPTION"),
                         (376.9, "QUANTITY"), (435.1, "UNIT"), (458.6, "PRICE"),
                         (490.3, "GST"), (521.9, "AMOUNT"))
GU_HEADER_WIDE = _row((31.8, "PRODUCT"), (77.4, "CODE"), (147.1, "DESCRIPTION"),
                      (394.5, "QUANTITY"), (471.5, "UNIT"), (498.8, "GST"),
                      (521.9, "AMOUNT"))


def _gu_split(row, desc_lo, num_lo):
    """The code/description split exactly as gulli.parse() performs it."""
    code = next((t for x0, _, t in row if x0 < desc_lo and t.strip()), "")
    desc = " ".join(t for x0, _, t in row if desc_lo <= x0 < num_lo)
    return code, desc


def test_gulli_narrow_layout_keeps_the_first_description_word():
    from modules.invoices.parsers.gulli import _cols_from_header
    desc_lo, num_lo = _cols_from_header(GU_HEADER_NARROW)
    # The real row off corpus b381fb197ab6 (Gulli CI-437314).
    row = _row((31.8, "BARSOPHOT-KC2"), (122.8, "Barbaro-"), (160.2, "Soppressata"),
               (210.8, "Hot"), (227.8, "(Zig"), (244.9, "Zag)"), (264.3, "r/w"),
               (280.1, "2.5kg"), (348.4, "1.400"), (373.3, "kg"), (406.0, "31.19000"))
    code, desc = _gu_split(row, desc_lo, num_lo)
    assert code == "BARSOPHOT-KC2"
    assert desc == "Barbaro- Soppressata Hot (Zig Zag) r/w 2.5kg", \
        "the regression: 'Barbaro-' fell into the product-code cell and was dropped"


def test_gulli_wide_layout_keeps_the_last_description_words():
    from modules.invoices.parsers.gulli import _cols_from_header
    desc_lo, num_lo = _cols_from_header(GU_HEADER_WIDE)
    # The real row off corpus 7adf09f0baa3 — the widest layout in the corpus.
    row = _row((31.8, "SAUCEHICKORYBBQ-"), (147.1, "Sweet"), (180.0, "Baby"),
               (208.0, "Rays"), (238.0, "Hickory"), (280.0, "&"), (292.0, "Brown"),
               (330.0, "Sugar"), (352.0, "Barbeque"), (378.0, "Sauce"),
               (398.0, "1.000"), (475.0, "Unit"), (505.0, "9.29000"))
    code, desc = _gu_split(row, desc_lo, num_lo)
    assert code == "SAUCEHICKORYBBQ-"
    assert desc.endswith("Barbeque Sauce"), \
        "the regression: 'Sauce' fell past the numeric boundary and was dropped"


def test_gulli_typical_layout_is_unchanged():
    # The 30 of 33 invoices that were already correct must stay correct — this is
    # a repair, not a re-interpretation.
    from modules.invoices.parsers.gulli import _cols_from_header
    desc_lo, num_lo = _cols_from_header(GU_HEADER_TYPICAL)
    row = _row((31.8, "MOZZARELLA2KG-UC4"), (157.4, "Big"), (180.0, "Cheese-"),
               (222.0, "Shredded"), (272.0, "Mozzarella"), (330.0, "2kg"),
               (375.0, "6.000"), (438.0, "Unit"), (462.0, "24.66338"))
    assert _gu_split(row, desc_lo, num_lo) == \
        ("MOZZARELLA2KG-UC4", "Big Cheese- Shredded Mozzarella 2kg")
    # ... and identically under the old constants, since this layout never broke.
    from modules.invoices.parsers.gulli import FALLBACK_DESC_LO, FALLBACK_NUM_LO
    assert _gu_split(row, FALLBACK_DESC_LO, FALLBACK_NUM_LO) == \
        _gu_split(row, desc_lo, num_lo)


def test_gulli_hardcoded_bounds_would_have_eaten_both_words():
    # Pins the diagnosis itself. If either assertion stops holding, the story in
    # the comment block above is wrong and the comments need revisiting.
    from modules.invoices.parsers.gulli import FALLBACK_DESC_LO, FALLBACK_NUM_LO
    narrow = _row((31.8, "BARSOPHOT-KC2"), (122.8, "Barbaro-"), (160.2, "Soppressata"),
                  (210.8, "Hot"), (348.4, "1.400"))
    assert _gu_split(narrow, FALLBACK_DESC_LO, FALLBACK_NUM_LO)[1] == "Soppressata Hot"
    wide = _row((31.8, "SAUCEHICKORYBBQ-"), (147.1, "Sweet"), (352.0, "Barbeque"),
                (378.0, "Sauce"), (398.0, "1.000"))
    assert _gu_split(wide, FALLBACK_DESC_LO, FALLBACK_NUM_LO)[1] == "Sweet"


def test_gulli_unreadable_header_falls_back_rather_than_inventing_columns():
    from modules.invoices.parsers.gulli import _cols_from_header
    assert _cols_from_header(_row((31.8, "PRODUCT"), (77.4, "CODE"))) is None
    assert _cols_from_header([]) is None
    # Right shape, wrong labels -> fall back, don't guess.
    assert _cols_from_header(_row(*[(x, "X") for x in (31, 77, 157, 376, 435, 490)])) is None
    # Labels present but out of order -> refuse rather than emit a negative span.
    assert _cols_from_header(_row((400.0, "CODE"), (157.4, "DESCRIPTION"),
                                  (376.9, "QUANTITY"))) is None


def test_gulli_derived_bounds_clear_every_observed_anchor():
    # The margins the derivation relies on, stated as a test so a future layout
    # change that eats them fails here rather than silently truncating names:
    # a description's first word starts exactly AT the DESCRIPTION anchor, and
    # the earliest quantity value observed sits at QUANTITY - 1.2.
    from modules.invoices.parsers.gulli import _cols_from_header
    for hdr, desc_x, qty_x in ((GU_HEADER_NARROW, 122.8, 336.3),
                               (GU_HEADER_TYPICAL, 157.4, 376.9),
                               (GU_HEADER_WIDE, 147.1, 394.5)):
        desc_lo, num_lo = _cols_from_header(hdr)
        assert desc_lo <= desc_x, "would drop the description's first word"
        assert num_lo <= qty_x - 1.2, "would drop the quantity"
        assert desc_lo < num_lo
