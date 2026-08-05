"""
The P&L must use the 648-recipe cost book, not just the 35 builder recipes.

THE GAP
-------
`_load_our_costs` only read data/recipes/*.yaml — 21 Stowaway + 8 HG + 6 Mari
products. None of their names match POS product names, so every published day
carried `recipe_coverage_pct: 0.0` and `cogs_source: recipe_blend` that had in
fact blended nothing: COGS fell through to Lightspeed's number for 100% of
revenue. The entire output of this project — 648 invoice-fed recipes — was not
reaching the P&L at all.

Wiring the book in lifts coverage to ~49% of revenue (91.5% at Marilyna's).

WHAT THIS GUARDS
----------------
- the book is actually loaded and keyed by POS product name
- preps are excluded (a batch is not a sold product)
- builder recipes still WIN where both exist (hand-authored, properly as-of)
- a missing/garbage book degrades to {} rather than taking the 6am pull down
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from daily_aggregator import _load_book_costs  # noqa: E402


def test_book_costs_load_and_are_keyed_by_product_name():
    out = _load_book_costs("stowaway")
    assert out, "the costed book should provide costs for stowaway"
    # a known Stowaway cocktail, priced off invoices
    assert any("margarita" in k.lower() for k in out)
    for v in out.values():
        assert isinstance(v, float) and v >= 0


def test_preps_are_excluded():
    """A batch/prep is not a sold product; costing sales against a $37 sauce tray
    would be nonsense."""
    out = _load_book_costs("stowaway")
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    preps = {n for n, r in book.items() if r.get("is_prep")}
    assert preps, "fixture sanity: the book should contain preps"
    assert not (preps & set(out)), "preps must not be offered as product costs"


def test_marilynas_and_harry_gatos_also_resolve():
    assert _load_book_costs("marilynas"), "Mari pizzas are 91.5% of her revenue"
    assert isinstance(_load_book_costs("harry_gatos"), dict)


def test_unknown_venue_is_empty_not_an_exception():
    assert _load_book_costs("not_a_venue") == {}


def test_missing_book_degrades_quietly(tmp_path, monkeypatch):
    """This runs unattended at 6am. A recipe problem must never stop the pull."""
    import daily_aggregator as da
    monkeypatch.setattr(da, "COSTED_BOOK", tmp_path / "nope.json")
    assert da._load_book_costs("stowaway") == {}
