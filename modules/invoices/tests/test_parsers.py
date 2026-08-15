"""
Parser-layer unit tests.

The parsers themselves read real PDFs (tested against the corpus by
parser_regression.py). Here we lock the coordinate PRIMITIVE they all rely on —
bucketing a visual row's words into columns by x-position — since that's the
pure, PDF-free part and the thing most likely to silently drift.
"""

import sys
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
