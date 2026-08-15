#!/usr/bin/env python3
"""
The guard the money-only regression could not give us.

parser_regression.py scores parsers on whether the invoice RECONCILES to its
printed total. supplier_code plays no part in that arithmetic, so a parser can
sit at 100% while corrupting product IDENTITY. Fresh Fruit Team did: its SKU
cell was swallowing the UNIT word, so "CLKG" and "CLKG Kilogram" arrived as two
different products. The cost book carried the same carrot twice, its price
history split across the pair, and build_ingredients' "fullest description
across the spellings of this identity" consolidation could no longer see across
them — which is how the chef's picker ended up showing "Large" and "Ruby Red"
as if those were product names. 94 of FFT's 186 codes were affected, for months,
while the regression table read a clean 52/52.

A supplier code is an identifier. It does not contain whitespace. That one line
of reasoning is the whole test.

The corpus is real invoices and is gitignored, so these skip on a clean checkout
— same convention as test_paramount_case_bottle.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices import pdf_text                             # noqa: E402
from modules.invoices.domains import DOMAIN_KEY                   # noqa: E402
from modules.invoices.parsers import parse_pdf                    # noqa: E402
from modules.invoices.run import looks_like_statement             # noqa: E402

CORPUS = ROOT / "data" / "invoice_corpus"
KEY_DOMAIN = {v: k for k, v in DOMAIN_KEY.items()}

# Suppliers whose codes legitimately contain a space. This is an ALLOWLIST, not a
# switch: a supplier only lands here once someone has opened the invoice and
# confirmed the whitespace is the supplier's own, not a column bleed. Anything
# not listed must stay clean, so a NEW instance of the FFT bug still fails.
#
# nicholas_seafood — verified 2026-08-14 against INV00126066. Its "ITEM NO."
#   column really does hold human-readable codes rather than alphanumeric SKUs:
#   the row reads QTY 1.75 | "Barra FSO" | "Barramundi - Fillets Skin On", and
#   "Squid - LW" | "Squid Loligo Whole". The space is Nicholas's, and collapsing
#   it would MERGE distinct products instead of splitting one — the opposite of
#   the FFT failure. (One code, "- Baby", does look truncated; it is a naming
#   defect on a 7-invoice supplier, not an identity split, so it is noted here
#   rather than silently repaired.)
#
# xero — verified 2026-08-15 against Philter PHIN-56956 and PHIN-57196. Philter's
#   own item code is "XPA 200", printed as two tokens ("XPA" at x=31, "200" at
#   x=47) BOTH inside the Item column, with the description starting cleanly at
#   its own anchor (x=85). The space is Philter's. Worth recording HOW this was
#   found: the identity audit flagged the xero parser within minutes of it being
#   written, which is the check doing exactly its job — and the answer was to read
#   the invoice, not to widen the rule.
WHITESPACE_OK = {"nicholas_seafood", "xero"}


def _parsed_codes(key: str) -> set[str]:
    """Every supplier_code this supplier's parser emits across the corpus."""
    out: set[str] = set()
    dom = KEY_DOMAIN.get(key, "")
    for pf in sorted((CORPUS / key).glob("*.pdf")):
        raw = pf.read_bytes()
        if not pdf_text.has_text_layer(raw):
            continue
        if looks_like_statement(pdf_text.text(raw)):
            continue
        try:
            inv = parse_pdf(raw, dom)
        except Exception:
            continue
        if inv is None:
            continue
        for line in inv.lines:
            if line.supplier_code:
                out.add(line.supplier_code)
    return out


def _corpus_keys() -> list[str]:
    if not CORPUS.exists():
        return []
    return sorted(d.name for d in CORPUS.iterdir() if d.is_dir())


def test_no_parser_emits_a_supplier_code_containing_whitespace():
    keys = _corpus_keys()
    if not keys:
        pytest.skip("no corpus — run build_corpus.py")
    offenders: dict[str, list[str]] = {}
    for key in keys:
        if key in WHITESPACE_OK:
            continue
        dirty = sorted(c for c in _parsed_codes(key) if " " in c)
        if dirty:
            offenders[key] = dirty
    assert not offenders, (
        "supplier_code must be a bare identifier — whitespace means an adjacent "
        "column (usually the UOM) bled into the code cell, which splits one "
        f"product into two in the cost book: {offenders}"
    )


def test_fresh_fruit_team_specifically_stays_clean():
    # The supplier the bug was found on. Pinned separately so a regression here
    # names FFT directly instead of hiding in the all-suppliers assertion.
    if not (CORPUS / "fresh_fruit_team").is_dir():
        pytest.skip("no FFT corpus — run build_corpus.py")
    codes = _parsed_codes("fresh_fruit_team")
    assert codes, "FFT corpus present but no codes parsed — parser is broken"
    assert not [c for c in codes if " " in c]
    # The two spellings of the carrot must be ONE identity, not two.
    assert not ({"CLKG", "CLKG Kilogram"} & codes) or "CLKG Kilogram" not in codes
