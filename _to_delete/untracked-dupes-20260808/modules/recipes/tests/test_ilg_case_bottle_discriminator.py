"""ILG's Qty cell is cases/singles, and the parser used to read the wrong half.

THE BUG
-------
ILG prints Qty as "cases / loose singles", exactly like Paramount's Case/Bottle
column. The parser did:

    qty = _m(qraw.split("/")[-1]) if "/" in qraw else _m(qraw)
    if qty is None or qty == 0: qty = Decimal("1")

which keeps the SINGLES half. So a bare "1" — one whole carton — became one unit,
and a "2/0" would have become 0 and then been forced to 1. raw_qty was never
stored, so the discriminator was destroyed before anything downstream could
recover it.

Invoice 03712630 (11 Jun 2026, Stowaway) is the whole disease on one page:

    BOMBAY DRY GIN               6x700ML  Qty 0/2  $101.70 inc  ->  $50.85/bottle
    ROOSTER ROJO TEQUILA BLANCO  6x700ML  Qty  1   $346.39 inc  ->  $346.39/bottle

Same supplier, same pack, same page, alternating basis, and nothing in the
description says which. The Rooster line is a CASE OF SIX at $57.73 a bottle. The
book carried it at $346.39 a bottle — 6x — and $346.39 sits comfortably inside
the $0.10-$500 per_unit sanity bounds, so the validator saw nothing to say.

WHAT IT COST, MEASURED
----------------------
Over data/invoice_corpus/ilg (54 PDFs, 49 that parse, 344 stock lines):

    148 of 344 lines carried a unit price overstated by exactly the carton count
    $21,950.05 inc GST of purchasing sat on those lines
    the overstatement factors were x24 (78 lines), x6 (40), x12 (21), x8 (6),
        x30 (2), x16 (1) — i.e. always the Pack cell's carton size

Line 117-4213 on invoice 03694253 is HEAPS NORMAL QUIET XPA, 24x375ML, Qty 1,
$64.08 inc — booked at $64.08 a tin. That is the exact number validator.py's
sanity-bounds docstring names as the canonical silent error ("Heaps Normal at
$64.07/tin"). It was not a hypothetical; ILG was feeding it in. The true figure
is $2.67 a tin.

THE PROOF (not a convention, arithmetic)
----------------------------------------
ILG states the CARTON cost and the ex-GST line Total on every line, so a
proposed count is checkable against two money columns the supplier printed:

    whole cartons ("N"):  cost x cases == total            199/199 lines hold
    broken carton ("0/M"): (total - cost x cases) / M, over the carton rate
                           cost/per, lands in [1.00, 1.05]  141/141 lines hold,
                           measured range [1.0243, 1.0261] — ILG's 2.5% repack
                           surcharge, which is why a broken carton is NOT just
                           cost/per x M and the Paramount identity alone fails

Independently, ILG's own footer prints "Cases & Repacks: A B  Kegs: C". The case
and keg counts derived this way match that stated footer on 50 of 50 invoices
that print it — the supplier confirming that a bare "1" means one CARTON.

A miscount can only be wrong by a factor of `per` (6, 8, 12, 16, 24, 30). No such
factor fits inside a 5% band, so the check cannot be fooled by the error it
exists to catch. Where it does not prove, the parser refuses to divide: qty 1 at
the full line total, which is either right (a 1x pack) or high, and high trips
the sanity bounds into review. Under-costing spirits is the flattering direction.

The rows below are real cells transcribed as literals because
data/invoice_corpus/ is gitignored — a test that needs a file CI does not have is
a test CI does not run. The optional sweeps re-derive them when the PDFs are here.
"""

from __future__ import annotations

import re
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices.models import (CostBasis, Invoice, InvoiceLine,  # noqa: E402
                                     LineClass, TaxTreatment, Venue)
from modules.invoices.parsers.ilg import units_on_line  # noqa: E402
from modules.invoices.validator import Severity, Status, Validator  # noqa: E402

# (pack cell, qty cell, cost, ex-GST total, expected units, what it is)
REAL_LINES = [
    # --- the two lines from invoice 03712630 that name this bug --------------
    ("6x700ML",   "1",   "280.10", "280.10",  6, "Rooster Rojo, ONE CARTON of 6 — was booked as 1 bottle at $346.39"),
    ("6x700ML",   "0/2", "267.95",  "91.55",  2, "Bombay gin, two loose bottles — same invoice, other basis"),
    ("6x700ML",   "0/2", "219.86",  "75.12",  2, "Campari, two loose bottles"),
    ("6x1LT",     "0/2", "253.79",  "86.71",  2, "Kahlua, two loose bottles"),
    # --- every carton size ILG actually ships --------------------------------
    ("24x375ML",  "1",    "56.56",  "56.56", 24, "Heaps Normal — the canonical $64.08/tin silent error"),
    ("24x330ML",  "1",    "52.11",  "52.11", 24, "Asahi Super Dry, one carton"),
    ("24x355ML",  "1",    "54.41",  "54.41", 24, "Corona, one carton"),
    ("24x500ML",  "1",    "48.88",  "48.88", 24, "San Pellegrino, one carton"),
    ("30x375ML",  "1",    "63.46",  "63.46", 30, "VB 30-pack"),
    ("16x375ML",  "1",    "77.06",  "77.06", 16, "Two Bays gluten free"),
    ("12x640ML",  "1",    "48.31",  "48.31", 12, "Tsingtao — was $55.00 a bottle"),
    ("12x750ML",  "1",    "35.87",  "35.87", 12, "Bundaberg ginger beer"),
    ("12x1.25LT", "1",    "38.60",  "38.60", 12, "Coca Cola, decimal pack size"),
    ("8x500ML",   "1",    "28.20",  "28.20",  8, "Fever Tree light tonic"),
    ("6x750ML",   "1",   "438.83", "438.83",  6, "Veuve Clicquot — was $484.58 a bottle"),
    ("6x700ML",   "1",   "156.94", "156.94",  6, "Aperol — was $174.49 a bottle"),
    # --- 1x packs: the carton IS the unit, count must not change -------------
    ("1xKEG50",   "2",   "337.26", "674.52",  2, "Sapporo, two 50 L kegs"),
    ("1xKEG49.",  "1",   "160.00", "160.00",  1, "Alehouse Crisp, one keg"),
    ("1x15LT",    "1",    "49.63",  "49.63",  1, "De Bortoli 15 L cask"),
    # --- repacks: already right, must stay right -----------------------------
    ("6x700ML",   "0/1", "282.81",  "48.32",  1, "Buffalo Trace, one loose bottle"),
    ("6x750ML",   "0/1", "150.85",  "25.77",  1, "Noilly Prat, one loose bottle"),
    ("12x700ML",  "0/1", "528.30",  "45.13",  1, "Bacardi Carta Blanca, one loose bottle"),
    ("12x200ML",  "0/1", "181.30",  "15.49",  1, "Angostura bitters, one loose bottle"),
    ("12x150ML",  "0/1", "237.70",  "20.31",  1, "Fee Bros black walnut, one loose bottle"),
    ("3x700ML",   "0/1", "770.96", "263.41",  1, "Yamazaki 12yo, one loose bottle"),
    ("6x500ML",   "0/1", "257.84",  "44.04",  1, "Grand Marnier, one loose bottle"),
    ("12x750ML",  "0/1",  "54.71",   "4.67",  1, "Bickfords raspberry cordial"),
]

BOUNDS = {"per_bottle": {"min": 5.00, "max": 400.00},
          "per_keg": {"min": 100.00, "max": 600.00},
          "per_can": {"min": 0.80, "max": 8.00},
          "per_unit": {"min": 0.10, "max": 500.00},
          "per_kg": {"min": 1.00, "max": 200.00}}
CONFIG = {"sanity_bounds": BOUNDS, "tolerances": {}, "extras_patterns": [], "wos_patterns": []}


def _stock(**kw):
    base = dict(description="X", qty=D("1"), line_total_incl=D("10.00"),
                unit_price_incl=D("10.00"), pack_size=1, line_class=LineClass.STOCK,
                tax_treatment=TaxTreatment.GST, cost_basis=CostBasis.PER_UNIT)
    base.update(kw)
    return InvoiceLine(**base)


def _invoice(lines):
    return Invoice(supplier_key="t", supplier_name_raw="T", invoice_ref="1",
                   invoice_date=None,
                   total_incl=sum((l.line_total_incl for l in lines), D("0")),
                   lines=lines, venue=Venue.STOWAWAY)


# ---------------------------------------------------------------------------
# §5 — the unit count
# ---------------------------------------------------------------------------

def test_every_real_invoice_line_resolves_to_the_right_unit_count():
    """All 27 transcribed cells, covering every pack size ILG ships."""
    for pack, qty, cost, total, want, what in REAL_LINES:
        got = units_on_line(pack, qty, D(cost), D(total))
        assert got == D(want), f"{what}: {pack!r} {qty!r} -> {got}, expected {want}"


def test_the_rooster_case_prices_per_bottle_not_per_case():
    """THE regression. $346.39 inc for Qty "1" of 6x700ML is $57.73 a bottle, not
    $346.39 a bottle. The old parser read the singles half of the cell and got 1."""
    units = units_on_line("6x700ML", "1", D("280.10"), D("280.10"))
    assert units == 6
    assert round(D("346.39") / units, 2) == D("57.73")


def test_heaps_normal_is_not_sixty_four_dollars_a_tin():
    """validator.py names "$64.07/tin" as the canonical silent error. ILG was
    actually feeding it: 24x375ML, Qty 1, $64.08 inc. The truth is $2.67."""
    units = units_on_line("24x375ML", "1", D("56.56"), D("56.56"))
    assert units == 24
    assert round(D("64.08") / units, 2) == D("2.67")


def test_a_two_carton_line_is_not_zero_and_not_one():
    """The shape the old split("/")[-1] turned into 0 and then forced to 1. Two
    cartons of Bombay is twelve bottles, and the cost column proves it."""
    assert units_on_line("6x700ML", "2/0", D("267.95"), D("535.90")) == 12


def test_a_loose_bottle_line_is_unchanged_by_the_fix():
    """141 of 344 corpus lines are "0/M" repacks, which the old code got RIGHT.
    A fix that silently restates prices that were already correct is a new bug."""
    for pack, qty, cost, total, want, what in REAL_LINES:
        if qty.startswith("0/"):
            assert units_on_line(pack, qty, D(cost), D(total)) == D(want), what


def test_it_refuses_when_the_invoices_own_columns_disagree():
    """The count is PROPOSED by the cell and PROVED against Cost and Total. Without
    the proof the cell's shape is a convention inferred from 54 invoices, and
    conventions change without telling you."""
    # cell says two cartons, the Total says one
    assert units_on_line("6x700ML", "2", D("280.10"), D("280.10")) is None
    # unreadable cells
    assert units_on_line("6x700ML", "", D("280.10"), D("280.10")) is None
    assert units_on_line("", "1", D("280.10"), D("280.10")) is None
    assert units_on_line("6x700ML", "1", None, D("280.10")) is None
    assert units_on_line("6x700ML", "1", D("280.10"), None) is None
    assert units_on_line("6x700ML", "0/0", D("280.10"), D("280.10")) is None
    # a WET/exempt flag glued to the figure reads as no figure — stay unproved
    assert units_on_line("1x15LT", "1", None, D("49.63")) is None


def test_a_repack_cheaper_than_the_carton_rate_is_refused():
    """ILG never sells a broken carton below the whole-carton rate — measured 141
    of 141 lines at 2.43-2.61% ABOVE it. A residual under the carton rate means we
    have misread a column, so we refuse rather than book a flattering price."""
    # Bombay's real repack: $91.55 for two off a $267.95 carton of 6 (ratio 1.025)
    assert units_on_line("6x700ML", "0/2", D("267.95"), D("91.55")) == 2
    # the same two bottles at exactly the carton rate minus a hair
    assert units_on_line("6x700ML", "0/2", D("267.95"), D("89.00")) is None
    # and far above the 5% surcharge ceiling
    assert units_on_line("6x700ML", "0/2", D("267.95"), D("120.00")) is None


def test_the_surcharge_band_cannot_swallow_a_pack_size_error():
    """The safety property. A miscount is always wrong by a factor of `per`, and
    the smallest `per` ILG ships is 3 — 200% out, against a 5% band."""
    cost, per = D("267.95"), 6
    carton_rate = cost / per
    for singles in (1, 2, 3):
        wrong = carton_rate * singles * per          # priced the carton, not the bottle
        assert units_on_line("6x700ML", f"0/{singles}", cost, wrong) is None


# ---------------------------------------------------------------------------
# §10 — the validator must actually engage
# ---------------------------------------------------------------------------

def test_a_stock_line_with_no_unit_price_is_flagged_not_skipped():
    """THE §10 hole. _check_line_arithmetic returns early on unit_price_incl=None
    and _check_sanity_bounds returns early on cost_basis=UNKNOWN, so a line with
    both set that way passed BOTH per-line checks by not participating in them —
    silently, with no finding. Paramount emitted exactly that for its whole life:
    47 stock lines over 19 invoices, checked by nothing but the invoice total,
    which a case-total-in-a-per-unit-field reconciles perfectly."""
    inv = _invoice([_stock(description="ROOSTER ROJO BLANCO : 700 ml",
                           qty=D("12"), line_total_incl=D("729.83"),
                           unit_price_incl=None, cost_basis=CostBasis.UNKNOWN)])
    r = Validator(CONFIG).validate(inv)
    assert r.status == Status.REVIEW
    codes = [f.code for f in r.findings if f.severity == Severity.ERROR]
    assert "LINE_UNPRICED" in codes, r.report()


def test_a_priced_line_with_an_unknown_basis_is_not_flagged():
    """Deliberately narrow. Select Fresh prices produce per kg and per bunch off
    one column, so cost_basis stays UNKNOWN — but the line HAS a price, so the
    arithmetic check still runs on it. It is not silent, and flagging it would
    send every produce invoice to review for a suppliers.yaml mapping gap."""
    inv = _invoice([_stock(description="CARROT KG", qty=D("2"),
                           line_total_incl=D("4.80"), unit_price_incl=D("2.40"),
                           cost_basis=CostBasis.UNKNOWN)])
    r = Validator(CONFIG).validate(inv)
    assert r.status == Status.PASS, r.report()


def test_extras_and_wos_lines_are_not_dragged_into_review():
    """Freight has no unit price and never will. The check is for STOCK only."""
    inv = _invoice([
        InvoiceLine(description="Freight", qty=D("1"), line_total_incl=D("37.50"),
                    unit_price_incl=None, line_class=LineClass.EXTRA,
                    cost_basis=CostBasis.UNKNOWN),
        _stock(description="APEROL", qty=D("6"), line_total_incl=D("174.49"),
               unit_price_incl=D("29.0817")),
    ])
    r = Validator(CONFIG).validate(inv)
    assert [f.code for f in r.findings if f.severity == Severity.ERROR] == [], r.report()


def test_a_real_ilg_line_now_survives_the_whole_gate():
    """End to end on the numbers from 03712630: correct counts, prices that pass
    the arithmetic, and a total that reconciles."""
    lines = [
        _stock(description="BOMBAY DRY GIN", qty=D("2"), line_total_incl=D("101.70"),
               unit_price_incl=D("50.8500"), raw_qty="0/2", raw_uom="6x700ML"),
        _stock(description="CAMPARI APERITIF 25%", qty=D("2"), line_total_incl=D("83.62"),
               unit_price_incl=D("41.8100"), raw_qty="0/2", raw_uom="6x700ML"),
        _stock(description="KAHLUA 1 LTR 6PK", qty=D("2"), line_total_incl=D("96.37"),
               unit_price_incl=D("48.1850"), raw_qty="0/2", raw_uom="6x1LT"),
        _stock(description="ROOSTER ROJO TEQUILA BLANCO", qty=D("6"),
               line_total_incl=D("346.39"), unit_price_incl=D("57.7317"),
               raw_qty="1", raw_uom="6x700ML"),
    ]
    inv = _invoice(lines)
    assert inv.total_incl == D("628.08")          # the stated invoice total
    r = Validator(CONFIG).validate(inv)
    assert r.status == Status.PASS, r.report()


# ---------------------------------------------------------------------------
# Optional corpus sweeps — no-ops on a clean checkout (data/invoice_corpus/ is
# gitignored). They assert nothing about HOW MANY lines they find, only that
# every line they DO find proves out.
# ---------------------------------------------------------------------------

def test_the_corpus_agrees_if_the_corpus_is_here():
    """Re-derive both identities from the real PDFs, and check the derived case
    and keg counts against ILG's own "Cases & Repacks: A B  Kegs: C" footer — an
    independent statement by the supplier that a bare "1" means one CARTON."""
    corpus = ROOT / "data" / "invoice_corpus" / "ilg"
    if not corpus.is_dir():
        return
    try:
        from modules.invoices import pdf_text
        from modules.invoices.parsers.ilg import COLS, _first_money
    except Exception:
        return
    for pdf in sorted(corpus.glob("*.pdf")):
        try:
            raw = pdf.read_bytes()
            rows = pdf_text.word_rows(raw)
            flat = pdf_text.text(raw)
        except Exception:
            continue
        hi = next((i for i, r in enumerate(rows)
                   if {"Code", "Pack"} <= {t for _, _, t in r}), None)
        if hi is None:
            continue
        cases = kegs = 0
        for r in rows[hi + 1:]:
            c = pdf_text.bucket(r, COLS)
            if not re.match(r"\d{3}-\d{3,4}", c["code"].strip()):
                continue
            pack, qcell = c["pack"].strip(), c["qty"].strip()
            pm = re.match(r"^(\d+)", pack)
            qm = re.fullmatch(r"(\d+)(?:\s*/\s*(\d+))?", qcell)
            if not pm or not qm:
                continue
            per, n = int(pm.group(1)), int(qm.group(1))
            singles = int(qm.group(2) or 0)
            # the footer counts what was SHIPPED, proved or not
            if "KEG" in pack.upper():
                kegs += n
            else:
                cases += n
            cost, total = _first_money(c["cost"]), _first_money(c["total"])
            units = units_on_line(pack, qcell, cost, total)
            if units is None:
                continue                       # refused — allowed, that is the fallback
            assert units == n * per + singles, f"{pdf.name}: {qcell} on {pack}"
            if singles == 0:
                assert abs(cost * n - total) <= D("0.02"), f"{pdf.name}: {c['desc'][:30]}"
            else:
                ratio = (total - cost * n) / (D(singles) * (cost / per))
                assert D("1") <= ratio <= D("1.05"), f"{pdf.name}: repack ratio {ratio}"
        m = re.search(r"Cases\s*&\s*Repacks:\s*(\d+)\s+(\d+)\s+Kegs:\s*(\d+)", flat)
        if m:
            assert cases == int(m.group(1)), f"{pdf.name}: cases {cases} vs footer {m.group(1)}"
            assert kegs == int(m.group(3)), f"{pdf.name}: kegs {kegs} vs footer {m.group(3)}"


def test_paramount_lines_now_carry_a_price_and_a_basis_if_the_corpus_is_here():
    """§10 end to end: where units_on_line proved the count, the line must state a
    per-unit price and a real cost_basis, so the validator's per-line checks have
    something to bite on. Where it did not prove, both stay unset — an unproved
    basis is not a basis — and LINE_UNPRICED now says so out loud."""
    corpus = ROOT / "data" / "invoice_corpus" / "paramount"
    if not corpus.is_dir():
        return
    from modules.invoices.parsers import paramount
    seen = 0
    for pdf in sorted(corpus.glob("*.pdf")):
        try:
            inv = paramount.parse(pdf.read_bytes())
        except Exception:
            continue                           # consolidated/scanned: the LLM path
        for ln in inv.lines:
            if ln.line_class != LineClass.STOCK:
                continue
            seen += 1
            assert ln.unit_price_incl is not None, f"{pdf.name}: {ln.description}"
            assert ln.cost_basis == CostBasis.PER_UNIT
            assert (ln.qty * ln.unit_price_incl - ln.line_total_incl).copy_abs() <= D("0.02")
    assert seen == 0 or seen >= 40             # 47 stock lines when the corpus is whole
