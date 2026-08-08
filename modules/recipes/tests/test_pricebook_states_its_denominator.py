"""
The price book dropped the column that makes its price interpretable.

THE DEFECT
----------
`data/ilg_pricebook.csv` publishes `size_ml` and `book_price_unit` side by side
and they do not describe the same thing. `size_ml` is the size of one ITEM.
`book_price_unit` (the PDF's "UC EX GST") is the price of one SELLING UNIT, and
for 1,334 of the 6,156 rows — 21.7% — a selling unit is a multipack:

    110-668-0  4 Pines Hazy Pale Cans 4pk  375ml  PCK QTY 24  $79.67  U.C. $13.28
    115-376-2  Corona Mexican 6pk          355ml  PCK QTY 24  $51.55  U.C. $12.89
    175-042-0  Antica Formula              1lt    PCK QTY  6  $401.45 U.C. $66.91

79.67/13.28 = 6 four-packs. 51.55/12.89 = 4 six-packs. 401.45/66.91 = 6 bottles.
So the denominators are 4 items, 6 items and 1 item — and the only place that
was written down was the "4pk" in a free-text description. The builder read the
number, called it `group(6)`, and never stored it.

WHY IT MATTERS EVEN THOUGH NOTHING IS WRONG TODAY
-------------------------------------------------
audit_book's price floor only joins rows costed in `ml`, so no live number is
wrong. But this CSV is the API contract — it is the ONLY price in the system
that neither we nor Lightspeed derived, and it is what the floor, the seed
cross-check in build_costs and anything written next will read. A price with an
unstated denominator is not a price, and the failure is silent and flattering:
read a $13.28 four-pack as a $13.28 can and every cost compared against it looks
fine.

It is already load-bearing. corroborated_bottle_ml uses `size_ml x
units_per_selling_unit` to decide a bottle's real size, and Corona is the case
that proves the column is needed rather than tidy: without it the book says
"355 ml, $12.89" and ILG's own delivery note says a can, and the two look like
they agree when they do not.

    (The audit called group(6) "the Case count". It is MIN ORD — that reading
    came from the `Ctn | Case | Book Price` header, which belongs to the
    3-Case-Mix tables, not to these rows. Stored under its real name.)

THE PROOF, NOT THE ASSUMPTION
-----------------------------
`units_per_selling_unit` is derived only where two tests both hold: the carton
price divides by the unit price to within 1% of a whole number, and that whole
number divides PCK QTY exactly. Both hold on 6,156 of 6,156 rows today. Where
they ever do not, the column is blank and a consumer must skip the row.

ADDITIVE ONLY. The eight existing columns keep their names, their order and
their values byte for byte — a live app and a stale browser tab read this feed.
"""

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from build_ilg_pricebook import FIELDS, units_per_selling_unit   # noqa: E402

PRICEBOOK = ROOT / "data" / "ilg_pricebook.csv"
PUBLISHED = ["code", "description", "size_ml", "units_per_carton",
             "book_price_case", "book_price_unit", "rrp", "book_month"]


def _rows():
    return list(csv.DictReader(PRICEBOOK.open(encoding="utf-8-sig")))


# --- the schema contract ---------------------------------------------------

def test_the_eight_published_columns_come_first_and_unrenamed():
    """Schema changes are additive-only. A reader that knows the old file must
    not be able to tell the difference."""
    assert FIELDS[:8] == PUBLISHED
    with PRICEBOOK.open(encoding="utf-8-sig") as fh:
        header = fh.readline().strip().split(",")
    assert header[:8] == PUBLISHED
    assert header[8:] == ["min_order", "units_per_selling_unit"]


# --- the denominator -------------------------------------------------------

def test_the_denominator_is_stated_for_every_row_that_can_prove_one():
    """6,156 of 6,156 rows prove a whole-number denominator today.

    A drop is not a failure — it is the parse changing under us — but it must
    be visible rather than silently blank."""
    rows = _rows()
    assert len(rows) > 6000
    stated = [r for r in rows if r["units_per_selling_unit"]]
    assert len(stated) == len(rows), (
        f"{len(rows) - len(stated)} rows can no longer prove a denominator")


def test_the_stated_denominator_reproduces_the_published_unit_price():
    """The whole claim, checked against the file's own arithmetic:
    book_price_case / (units_per_carton / units_per_selling_unit) is
    book_price_unit."""
    bad = []
    for r in _rows():
        n = int(r["units_per_selling_unit"])
        selling_units = int(r["units_per_carton"]) // n
        implied = float(r["book_price_case"]) / selling_units
        if abs(implied - float(r["book_price_unit"])) > 0.011:
            bad.append(f"{r['code']} {r['description'][:28]}: "
                       f"{implied:.2f} vs stated {r['book_price_unit']}")
    assert not bad, "\n  ".join([""] + bad[:10])


def test_a_multipack_is_not_an_item_and_the_file_now_says_so():
    """The three rows the finding names, asserted as numbers."""
    by = {r["code"]: r for r in _rows()}
    assert by["110-668-0"]["units_per_selling_unit"] == "4"    # a four-pack
    assert by["115-376-2"]["units_per_selling_unit"] == "6"    # a six-pack
    assert by["175-042-0"]["units_per_selling_unit"] == "1"    # one bottle
    multi = [r for r in _rows() if int(r["units_per_selling_unit"]) > 1]
    assert 1200 < len(multi) < 1500, (
        f"{len(multi)} multipack rows; it was 1,334 of 6,156 when measured")


# --- the proof refuses rather than guesses ---------------------------------

def test_an_unprovable_denominator_is_blank_never_guessed():
    """Both tests must pass. A price that does not divide cleanly, or divides
    into a count that cannot come out of the carton, yields None — because the
    wrong denominator is silent and reads in the flattering direction."""
    assert units_per_selling_unit(24, 79.67, 13.28) == 4
    assert units_per_selling_unit(6, 401.45, 66.91) == 1
    assert units_per_selling_unit(24, 100.00, 13.33) is None   # 7.5 selling units
    assert units_per_selling_unit(24, 100.00, 20.00) is None   # 5 does not divide 24
    assert units_per_selling_unit(24, 100.00, 0.0) is None
    assert units_per_selling_unit(0, 100.00, 10.00) is None


# --- the file is still what the PDF says -----------------------------------

def test_the_committed_csv_still_reproduces_from_the_pdf():
    """One-way door: the PDF is gitignored, the CSV is committed. Where the
    corpus IS present the two must still agree, or the committed file has
    drifted from the book it claims to be."""
    pdf = ROOT / "data" / "invoice_corpus" / "ilg_pricebook.pdf"
    if not pdf.exists():
        return                      # not on this machine — see the audit INFO
    try:
        text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                              capture_output=True, text=True).stdout
    except FileNotFoundError:
        return                      # no poppler here
    import build_ilg_pricebook as B
    fresh = B.parse(text)
    assert fresh == [{k: r[k] for k in FIELDS} for r in _rows()]
