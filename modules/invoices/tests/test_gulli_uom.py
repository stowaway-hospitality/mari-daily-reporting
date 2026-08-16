"""
Gulli's UOM column: captured, and refused when it is not understood.

MEASURED FIRST, THEN WRITTEN. Across the whole Gulli corpus — 309 line rows in
33 invoices — the UNIT column holds only four values:

    "Unit"  274      "Box"  33      (blank)  1      "kg"  1

Both real values are COUNTS. That is why this parser could hard-code PER_UNIT
from the day it was written and be right on every promoted line, and it is why
capturing the UOM reprices NOTHING today: a re-parse of all 33 corpus PDFs
against the 276 lines stored in data/invoices moved zero basis values, zero unit
prices and zero line totals. data/cogs_list.csv rebuilt byte-identical.

So this is not a repricing. It is closing a silent path: the one "kg" row proves
the template CAN express a weight, and the old code would have taken such a line
as PER_UNIT, reconciled it to the cent, and written a wrong $/ea into the cost
book with nothing anywhere to show for it. (That "kg" row happens to sit on the
$0.00 sample docket CI-437314, which no parser can promote — so the weight path
has genuinely never fired on real data.)
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices.models import CostBasis          # noqa: E402
from modules.invoices.parsers.gulli import _basis      # noqa: E402


@pytest.mark.parametrize("uom", ["Unit", "unit", "UNIT", "Box", "box", "BOX",
                                 "ea", "Each", "PCS", "Ctn", "Carton", "Pkt"])
def test_a_count_uom_is_per_unit(uom):
    assert _basis(uom, "Paesanella- Bocconcini 1kg") is CostBasis.PER_UNIT


def test_the_two_uoms_that_actually_appear_are_both_per_unit():
    # 307 of the 309 corpus rows. If either of these ever stops being PER_UNIT,
    # every Gulli line in the cost book reprices — so they are pinned explicitly.
    assert _basis("Unit", "x") is CostBasis.PER_UNIT
    assert _basis("Box", "Australian Garlic Bread- 9\" x 40") is CostBasis.PER_UNIT


def test_a_blank_uom_is_per_unit_which_is_how_gulli_prints_an_ordinary_line():
    assert _basis(None, "Big Cheese- Shredded Mozzarella 2kg") is CostBasis.PER_UNIT
    assert _basis("", "x") is CostBasis.PER_UNIT
    assert _basis("   ", "x") is CostBasis.PER_UNIT


@pytest.mark.parametrize("uom", ["kg", "KG", "Kg", "kgs", "kilogram"])
def test_a_weight_uom_is_per_kg_not_silently_per_unit(uom):
    # The whole reason the UOM is captured. Before this, a weight line was
    # priced per unit and looked perfectly healthy.
    assert _basis(uom, "Barbaro- Soppressata Hot (Zig Zag) r/w 2.5kg") is CostBasis.PER_KG


def test_an_unrecognised_uom_refuses_rather_than_assuming_per_unit():
    # Fail toward review. Assuming per-unit is the flattering error: it produces
    # a plausible $/ea that reconciles, so nothing downstream ever questions it.
    with pytest.raises(ValueError, match="unrecognised UOM"):
        _basis("Litre", "Some new liquid line")
    with pytest.raises(ValueError, match="unrecognised UOM"):
        _basis("Metre", "x")


def test_the_refusal_names_the_uom_and_the_line_so_it_can_be_actioned():
    # A refusal a human cannot act on just becomes a stuck invoice nobody
    # understands. The message has to carry both facts and the fix.
    with pytest.raises(ValueError) as e:
        _basis("Drum", "Guzzardi- Basil Pesto Caterers 2kg")
    msg = str(e.value)
    assert "Drum" in msg and "Basil Pesto" in msg
    assert "COUNT_UOMS" in msg or "WEIGHT_UOMS" in msg


def test_a_trailing_full_stop_does_not_defeat_the_vocabulary():
    assert _basis("Unit.", "x") is CostBasis.PER_UNIT
    assert _basis("kg.", "x") is CostBasis.PER_KG


def test_every_uom_in_the_corpus_is_understood():
    """
    The measurement this file's docstring rests on, kept executable.

    If Gulli adds a fifth UOM, this fails against the real corpus rather than
    waiting for a wrong number to reach the cost book. Skips when the corpus is
    absent (it is gitignored real invoices, so CI does not have it).
    """
    import re
    from modules.invoices import pdf_text
    from modules.invoices.parsers.gulli import _cols_from_header, _m

    corpus = ROOT / "data" / "invoice_corpus" / "gulli"
    pdfs = sorted(corpus.glob("*.pdf")) if corpus.exists() else []
    if not pdfs:
        pytest.skip("no local Gulli corpus")

    seen = set()
    for pdf in pdfs:
        rows = pdf_text.word_rows(pdf.read_bytes())
        hi = hdr = None
        for i, r in enumerate(rows):
            toks = [t for _, _, t in r]
            if "DESCRIPTION" in toks and "AMOUNT" in toks:
                hi, hdr = i, r
                break
        if hi is None:
            continue
        bounds = _cols_from_header(hdr)
        if not bounds:
            continue
        _desc_lo, num_lo = bounds
        for r in rows[hi + 1:]:
            gi = next(((x0, m) for x0, _, t in r if (m := re.match(r"(\d+)%$", t))), None)
            if not gi:
                continue
            gx = gi[0]
            if len([1 for x0, _, t in r if num_lo <= x0 < gx and _m(t) is not None]) < 2:
                continue
            u = " ".join(t for x0, _, t in r
                         if num_lo <= x0 < gx and _m(t) is None).strip()
            if u:
                seen.add(u)

    assert seen, "corpus present but no UOM read — the column moved"
    for u in sorted(seen):
        _basis(u, "corpus line")          # raises if a new UOM appeared
    assert seen <= {"Unit", "Box", "kg"}, (
        f"a UOM outside the measured set appeared: {sorted(seen)} — re-read the "
        f"docstring's counts before trusting them")
