"""
One (invoice, product) is one cost observation, whatever wrote the file.

THE GAP
-------
modules/invoices/build_cogs_list.py has always known the identity of a cost row —
`_key(source_invoice, supplier_code or description)` — and its docstring claims
the result is idempotent: "run twice, same result". The check only ever guarded
lines the script was about to ADD. A writer that appends straight to
data/cogs_list.csv never meets it, and three rows got in that way. All three are
Paramount invoice 5441124, present twice at an identical price, date, code and
basis, differing only in the diagnostic `note`:

    10015926  CARPANO CLASSICO VERMOUTH : 750ml  $23.1700  "0/1 repack; WET"
                                                           "0/1 repack; WET 4.74"
    44583     DE BORTOLI GOLD SEAL DRY RED 15L   $55.8950  "cask; WET"
                                                           "cask; WET 22.85"
    98541     SPRITE PET : 1250 ml               $ 4.2654  "LUC would be 10.9x high"
                                                           "LUC per-CASE = 10.9x high"

costs.csv carried all six through, and each of the three also bridged to a
ProductID, so five duplicated observations reached the cost book.

WHY IT MATTERS, AND WHY NOTHING CAUGHT IT
-----------------------------------------
as_of is indifferent: same day, same price, so the answer is the same whichever
row it lands on. CostSeries.rolling is not. It is the LIVE menu-costing path — a
trailing 30-day mean, equal-weighted while the invoice pipeline has no quantities
— so a duplicated row counts twice and drags the working cost toward whatever
that one delivery happened to be. Two deliveries at $10.00 and $30.00 average
$20.00; duplicate the $30.00 and the same month averages $23.33, a 17% move with
no invoice behind it.

WHERE THE FIX GOES, AND WHY THERE
---------------------------------
On the READ. The identity now lives in core.domain.cogs_row_key so the writer and
the consumer share one definition, and build_costs applies it when it loads the
file. A check on the way in can be bypassed — this one was. A check on the way
out cannot.

data/cogs_list.csv is an append-only fact table and both notes are evidence, so
the duplicate rows stay in it; build_cogs_list reports them instead of deleting
them, and the derived table is where they do not survive.

WHAT THIS GUARDS
----------------
- the writer's key and the reader's key are the same function
- a duplicated row cannot reach the cost fact table however it got into the file
- a rolling average is not moved by one
- rows that merely LOOK similar (same product, different invoice) are untouched
- and the invariant on the real file
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.domain import CostObservation, CostSeries, cogs_row_key      # noqa: E402
from modules.invoices import build_cogs_list as bcl                    # noqa: E402
from modules.recipes.pipeline import build_costs as bc                 # noqa: E402

COGS = ROOT / "data" / "cogs_list.csv"
COSTS = ROOT / "data" / "costs.csv"

COLS = ["supplier", "supplier_code", "invoice_description", "cost_per_unit_incl_gst",
        "basis", "pack_qty", "pack_unit", "cost_per_base_unit", "venue",
        "source_invoice", "invoice_date", "note"]


def _row(**kw):
    base = dict(supplier="Paramount", supplier_code="98541",
                invoice_description="SPRITE PET : 1250 ml",
                cost_per_unit_incl_gst="4.2654", basis="per_unit", pack_qty="",
                pack_unit="", cost_per_base_unit="", venue="stowaway",
                source_invoice="5441124", invoice_date="2026-07-14", note="")
    base.update(kw)
    return base


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


# --- one definition, two users --------------------------------------------

def test_the_writer_and_the_reader_key_a_row_the_same_way():
    """They were two copies of the same idea. A duplicate that satisfies one and
    not the other is exactly how this got in."""
    assert bcl._key("5441124", "98541", "SPRITE PET : 1250 ml") == \
        cogs_row_key("5441124", "98541", "SPRITE PET : 1250 ml")


def test_the_note_is_not_part_of_a_rows_identity():
    """"WET" and "WET 4.74" are two spellings of one diagnostic. The row is
    identified by the invoice and the product, and nothing else."""
    assert cogs_row_key("5441124", "44583", "DE BORTOLI") == \
        cogs_row_key(" 5441124 ", "44583", "DE BORTOLI GOLD SEAL")


def test_a_codeless_row_falls_back_to_its_description():
    assert cogs_row_key("INV1", "", "Fresh Basil")[1] == "FRESH BASIL"


# --- the reader ------------------------------------------------------------

def test_a_duplicated_row_does_not_reach_the_cost_book(tmp_path, monkeypatch):
    """The finding. Two rows differing only in `note` are one observation."""
    _write(tmp_path / "cogs.csv", [_row(note="LUC would be 10.9x high"),
                                   _row(note="LUC per-CASE = 10.9x high")])
    monkeypatch.setattr(bc, "COGS", tmp_path / "cogs.csv")
    monkeypatch.setattr(bc, "OUT", tmp_path / "costs.csv")
    monkeypatch.setattr(bc, "PACK_OVERRIDES", tmp_path / "none.yaml")
    monkeypatch.setattr(bc, "PRODUCT_MAP", tmp_path / "none.csv")
    monkeypatch.setattr(bc, "ROOT", tmp_path)
    bc.main()
    rows = list(csv.DictReader((tmp_path / "costs.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 1, rows


def test_two_real_deliveries_of_the_same_product_both_survive(tmp_path, monkeypatch):
    """The dedupe must not swallow history. Two invoices for one product are two
    facts, even at the same price on the same day."""
    _write(tmp_path / "cogs.csv", [_row(source_invoice="5441124"),
                                   _row(source_invoice="5441125")])
    monkeypatch.setattr(bc, "COGS", tmp_path / "cogs.csv")
    monkeypatch.setattr(bc, "OUT", tmp_path / "costs.csv")
    monkeypatch.setattr(bc, "PACK_OVERRIDES", tmp_path / "none.yaml")
    monkeypatch.setattr(bc, "PRODUCT_MAP", tmp_path / "none.csv")
    monkeypatch.setattr(bc, "ROOT", tmp_path)
    bc.main()
    rows = list(csv.DictReader((tmp_path / "costs.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 2


# --- why it matters: the live cost --------------------------------------------

def test_a_duplicate_observation_moves_the_rolling_average():
    """Stated as a measurement, not an opinion. This is what the fix prevents:
    equal-weighted while quantities are unknown, one duplicated row is one extra
    delivery that never happened."""
    def series(obs):
        return CostSeries([CostObservation(
            ingredient="paramount:98541", observed_on=d,
            cost_per_unit=Decimal(c), unit="bottle", venue="stowaway")
            for d, c in obs])

    honest = series([(date(2026, 7, 1), "10.00"), (date(2026, 7, 14), "30.00")])
    doubled = series([(date(2026, 7, 1), "10.00"), (date(2026, 7, 14), "30.00"),
                      (date(2026, 7, 14), "30.00")])
    on = date(2026, 7, 20)
    assert honest.rolling("paramount:98541", on).cost_per_unit == Decimal("20")
    assert doubled.rolling("paramount:98541", on).cost_per_unit > Decimal("23")


# --- the invariant on the real book ---------------------------------------

def test_the_real_cost_book_holds_each_invoice_line_once():
    """The regression, on the real file. Before the fix five rows in costs.csv
    were second copies of an observation already there."""
    if not COSTS.exists():
        return                     # clean checkout: nothing generated yet
    seen, dupes = set(), []
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        k = (r["ingredient"], r["observed_on"], r["source_invoice"],
             r["cost_per_unit"], r["unit"], r["description"])
        if k in seen:
            dupes.append(k)
        seen.add(k)
    assert not dupes, ("duplicated cost observations:\n  "
                       + "\n  ".join(str(d) for d in dupes[:10]))


def test_the_fact_table_still_holds_both_notes():
    """The duplicates are NOT deleted. cogs_list.csv is append-only and each note
    is evidence; the fix is on the read, and this says so out loud so nobody
    'tidies' the file later and calls it the same change."""
    if not COGS.exists():
        return
    notes = [r["note"] for r in csv.DictReader(COGS.open(encoding="utf-8-sig"))
             if r["source_invoice"].strip() == "5441124"
             and r["supplier_code"].strip() == "98541"]
    assert len(notes) == 2 and len(set(notes)) == 2, notes
