"""Paramount's Case/Bottle column is a unit count, and it used to be thrown away.

The parser hardcoded qty=1. Invoice 5419664 billed $729.83 for "ROOSTER ROJO
BLANCO : 700 ml" on a Case/Bottle cell of "2 / 0" — two cartons, twelve bottles,
$60.82 each. The book read one 700 ml bottle at $729.83, which is $1,042 a litre.

The damage was not a wrong price. It was worse and quieter: $1,042/L is so
absurd that resolve_pack refused the line and dropped it, so $1,394 of
Stowaway's HOUSE tequila never reached the cost book at all, and every margarita
priced off a January seed. A defect that produces a believable number gets
caught by the audit; one that produces an unbelievable number gets silently
discarded.

The rows below are real cells from data/invoice_corpus/paramount, transcribed as
literals so this runs on a clean checkout — the corpus is gitignored, and a test
that needs a file CI does not have is a test CI does not run. (Learned the hard
way, twice.) The optional sweep at the bottom re-derives them from the PDFs when
they happen to be present.
"""

from __future__ import annotations

import re
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices.parsers.paramount import units_on_line  # noqa: E402

# (size cell, qty cell, base, net, expected units, what it is)
REAL_LINES = [
    ("6/700 ml",     "2 / 0", "331.74",  "663.48", 12, "Rooster Rojo, two cartons — the line that started this"),
    ("6/700 ml",     "0 / 1", "482.34",   "80.39",  1, "Casamigos, one loose bottle"),
    ("6/700 ml",     "0 / 1", "376.38",   "62.73",  1, "Germana Caetano, one loose bottle"),
    ("6/750 ml",     "0 / 2", "425.64",  "141.88",  2, "400 Conejos, two loose bottles"),
    ("8/500 ml C",   "1",     " 31.35",   "31.35",  8, "Fever Tree, one carton of 8"),
    ("12/1250 ml C", "2",     " 46.53",   "93.06", 24, "Sprite, two cartons of 12"),
    ("1/20000 ml",   "1",     "920.71",  "920.71",  1, "White Light Vodka, one 20 L drum"),
    ("1/15000 ml C", "2",     " 39.39",   "78.78",  2, "De Bortoli, two 15 L casks"),
    ("12/150 ml",    "0 / 1", "266.16",   "22.18",  1, "Fee Bros bitters, one bottle"),
    ("4/1000 ml",    "0 / 1", "137.20",   "34.30",  1, "Monin coconut puree, one bottle"),
]


def test_the_real_invoice_lines_resolve_to_the_right_unit_count():
    for size, qty, base, net, want, what in REAL_LINES:
        got = units_on_line(size, qty, D(base.strip()), D(net.strip()))
        assert got == D(want), f"{what}: {size!r} {qty!r} -> {got}, expected {want}"


def test_the_rooster_line_prices_per_bottle_not_per_line():
    """The specific number that was wrong, stated as money."""
    units = units_on_line("6/700 ml", "2 / 0", D("331.74"), D("663.48"))
    per_bottle = D("729.83") / units
    assert round(per_bottle, 2) == D("60.82")


def test_it_refuses_when_the_invoices_own_columns_disagree():
    """base x units / per_carton must equal net, or we do not claim a count.

    This is the whole safety property. The unit count is PROPOSED by the shape of
    the Case/Bottle cell and PROVED against two money columns Paramount printed
    itself. Without the proof the cell's shape is just a convention we inferred
    from 21 invoices, and conventions change without telling you.
    """
    # right shape, wrong arithmetic (net says one carton, cell says two)
    assert units_on_line("6/700 ml", "2 / 0", D("331.74"), D("331.74")) is None
    # unreadable cells
    assert units_on_line("6/700 ml", "", D("331.74"), D("663.48")) is None
    assert units_on_line("MISC", "1,370", D("0.01"), D("13.70")) is None
    assert units_on_line("6/700 ml", "2 / 0", None, D("663.48")) is None
    assert units_on_line("6/700 ml", "0 / 0", D("331.74"), D("663.48")) is None


def test_a_single_bottle_line_is_unchanged_by_the_fix():
    """Most lines are '0 / 1'. Those must still be qty 1 — no silent restatement
    of prices that were already right."""
    for size, qty, base, net, want, _ in REAL_LINES:
        if qty.strip() == "0 / 1":
            assert units_on_line(size, qty, D(base.strip()), D(net.strip())) == 1


def test_every_stock_line_in_the_corpus_proves_out_if_the_corpus_is_here():
    """Optional: re-derive the identity from the real PDFs when they exist.

    data/invoice_corpus/ is gitignored, so this is a no-op in CI and a real sweep
    on a machine that has the invoices. It asserts nothing about how MANY lines
    it found — only that every line it DID find proves out. A count assertion
    would fail on whoever has a different slice of the corpus.
    """
    corpus = ROOT / "data" / "invoice_corpus" / "paramount"
    if not corpus.is_dir():
        return
    try:
        from modules.invoices import pdf_text
        from modules.invoices.parsers.paramount import COLS, _m
    except Exception:
        return
    for pdf in sorted(corpus.glob("*.pdf")):
        try:
            rows = pdf_text.word_rows(pdf.read_bytes())
        except Exception:
            continue                      # scanned / consolidated: the LLM path
        hi = next((i for i, r in enumerate(rows)
                   if {"Code", "Description", "Net"} <= {t for _, _, t in r}), None)
        if hi is None:
            continue
        for r in rows[hi + 1:]:
            c = pdf_text.bucket(r, COLS)
            if not re.fullmatch(r"\d{3,}", c["code"].strip()):
                continue
            if c["size"].strip().upper() == "MISC":
                continue
            base, net = _m(c["base"]), _m(c["net"])
            units = units_on_line(c["size"], c["qty"], base, net)
            if units is None:
                continue                  # refused — allowed, that is the fallback
            per = int(re.match(r"^(\d+)", c["size"].strip()).group(1))
            assert abs(base * units / per - net) <= D("0.02"), (
                f"{pdf.name}: {c['desc'].strip()[:30]} claimed {units} units")
