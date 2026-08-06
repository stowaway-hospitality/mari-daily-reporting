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

ROOT = Path(__file__).resolve().parents[3]
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


def test_builder_recipes_resolve_their_sub_recipes():
    """
    cost_on was called WITHOUT `recipes`, so it resolved every sub-recipe against
    an empty list: "sub-recipe 'Sugar Syrup' has no version in force". 9 of the 21
    Stowaway builder recipes — every one drawing on a syrup, jam or batch — died
    that way and fell back to Lightspeed's cost.

    Assert every builder recipe costs, so a batch-using dish can never silently
    drop out again.
    """
    from datetime import date

    from daily_aggregator import _load_our_costs
    from modules.recipes.cost import load_recipes

    on = date(2026, 8, 4)
    for venue_key, venue_file in (("stowaway", "stowaway"),
                                  ("marilynas", "marilynas"),
                                  ("harry", "harry_gatos")):
        defined = {r.product for r in load_recipes(venue_file)}
        costed = set(_load_our_costs(venue_key, on))
        assert defined - costed == set(), (
            f"{venue_key}: these builder recipes failed to cost: {sorted(defined - costed)}"
        )


def test_a_zero_cost_is_never_published_as_a_cost():
    """
    ZERO IS NOT A PRICE, IT IS THE ABSENCE OF ONE.

    10 products reached the P&L at exactly $0.00 — Whispering Angel, Veuve
    Clicquot, Laphroaig, a Caipirinha. None of them is free. Each is a real pour
    whose product carries no cost anywhere: Back Office holds CostPriceIncTax
    0.0000 and no invoice has bridged to it.

    Publishing that 0 does not report "unknown". It reports a $40 glass of rosé
    at 100% gross profit, silently, in the flattering direction. Falling through
    to Lightspeed's own figure is wrong too, but it is visibly sourced
    (cost_source='lightspeed') and the audit is already shouting about it.
    """
    out = _load_book_costs("stowaway")
    assert all(v > 0 for v in out.values()), \
        [k for k, v in out.items() if v <= 0]


def test_products_with_no_price_anywhere_are_absent_not_free():
    """The specific products that were reading as 100% GP."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    free = {n for n, r in book.items()
            if not r.get("is_prep") and (r.get("our_cost") or 0) == 0
            and (r.get("sell_incl") or 0) > 0}
    assert free, "fixture sanity: some products still have no cost source at all"
    offered = set(_load_book_costs("stowaway")) | set(_load_book_costs("harry_gatos"))
    assert not (free & offered), sorted(free & offered)


def test_a_wine_called_blend_is_not_a_batch():
    """
    The prep classifier reads the recipe NAME for "blend"/"batch"/"mix"/"[2Kg]".
    "Sigurd GSM Red Blend" is a wine. It matched on "Blend", was filed as a
    batch, and a prep is excluded from the P&L's cost book — so $2,417 of wine
    revenue was costed off Lightspeed instead of our own $4.62 a glass.
    "Yuzushu [60ml]" and "Kunizakari Umeshu [60ml]" matched on their SERVE size.

    Back Office settles it: all 64 real preps carry a blank ReportingGroup and a
    $0 price, so a product filed under a menu category at a menu price is
    something a customer orders, whatever word is in its name.
    """
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    for name in ("Sigurd GSM Red Blend - Regular", "Sigurd GSM Red Blend - Large",
                 "Sigurd White Blend - Regular", "Yuzushu [60ml]",
                 "Kunizakari Umeshu [60ml]"):
        r = book.get(name)
        assert r, f"fixture: {name} should be in the book"
        assert not r["is_prep"], f"{name} is sold, not a batch"
        assert r["gp_pct"] is not None, f"{name} should report a GP"
    assert set(book) & set(_load_book_costs("stowaway")) >= {"Sigurd GSM Red Blend - Regular"}


def test_a_real_batch_is_still_a_batch():
    """The guard the rule must not break. Mint Yoghurt [Batch] and Tandoori
    Chicken [2Kg] DO carry a Back Office group, but their prices are $1 and $2
    placeholders and both are used as sub-recipes — costing a sale against a
    $10.78 tray is where the -1085% GP figures came from.

    And "Stow Vermouth Blend [Bottle]" must stay a prep while "Stow Vermouth
    Blend [30ml]" becomes a product: the two collapse to one key once the
    bracket is normalised away, so the category lookup has to be exact.
    """
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    for name in ("Mint Yoghurt [Batch]", "Tandoori Chicken [2Kg]",
                 "Stow Vermouth Blend [Bottle]", "Pizza Sauce [Recipe]"):
        r = book.get(name)
        assert r, f"fixture: {name} should be in the book"
        assert r["is_prep"], f"{name} is a batch and must not report a GP"
    assert "Stow Vermouth Blend [Bottle]" not in _load_book_costs("stowaway")
