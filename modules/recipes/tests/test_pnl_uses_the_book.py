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

from cogs_blend import _load_book_costs  # noqa: E402


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
    import cogs_blend as cb
    monkeypatch.setattr(cb, "COSTED_BOOK", tmp_path / "nope.json")
    assert cb._load_book_costs("stowaway") == {}


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

    from cogs_blend import _load_our_costs
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


def test_importing_the_cost_sources_does_not_run_a_daily_pull(tmp_path, monkeypatch):
    """
    daily_aggregator.py is a SCRIPT — no main(), no __main__ guard — so importing
    it runs the whole daily pull: reads the exports, writes
    data/<venue>_daily_<date>.json and rewrites the history CSV.

    cogs_blend's docstring has warned about that since the module was created,
    and then both cost loaders were written into daily_aggregator anyway. A test
    that wanted to check the P&L used the cost book had no choice but to import
    it, so `pytest` published a day's record and rewrote a history file as a side
    effect — on CI, and on the Mac where the real exports live. It surfaced as
    data/ files turning up dirty after a test run and was read as noise.

    This asserts the loaders stay importable on their own.
    """
    import importlib
    import sys

    for mod in ("cogs_blend",):
        sys.modules.pop(mod, None)
    before = {p.name for p in (ROOT / "data").glob("*_daily_*")}
    cb = importlib.import_module("cogs_blend")
    after = {p.name for p in (ROOT / "data").glob("*_daily_*")}
    assert before == after, f"importing cogs_blend wrote {sorted(after - before)}"
    assert callable(cb._load_book_costs) and callable(cb._load_our_costs)
    assert callable(cb.blend_reported_cogs)


def test_a_category_word_is_not_part_of_a_products_identity():
    """
    Produce names the recipe "Bombay Dry Gin [House]"; the POS sells "Bombay Dry
    [House]". One word apart, same gin — and the exact-name lookup missed it,
    $2,827 of it in 13 weeks costed off Lightspeed while a $2.11 recipe sat in
    the book. Same for Baileys Irish Cream vs "...Liqueur", Bombay Sapphire vs
    "...Gin", 1800 Coconut vs "...Tequila", Monkey Shoulder Scotch vs
    "...Whisky".
    """
    from cogs_blend import book_cost

    out = _load_book_costs("stowaway")
    for pos_name in ("Bombay Dry [House]", "Bombay Sapphire",
                     "Baileys Irish Cream", "1800 Coconut",
                     "Monkey Shoulder Scotch [House]"):
        assert pos_name not in out, f"fixture: {pos_name} should not match exactly"
        assert book_cost(out, pos_name) > 0, pos_name


def test_the_normalised_form_also_settles_punctuation_and_word_order():
    """Same class of difference: "Lemon Lime Bitters" vs "Lemon, Lime & Bitters",
    "Coke 1.25L" vs "1.25L Coke"."""
    from cogs_blend import book_cost

    assert book_cost(_load_book_costs("harry_gatos"), "Lemon Lime Bitters") > 0
    assert book_cost(_load_book_costs("marilynas"), "Coke 1.25L") > 0


def test_one_word_is_never_enough_and_ambiguity_is_dropped():
    """"Ginger" must not find "Ginger Beer". And "Gin Martini [HG]" and "Vodka
    Martini [HG]" both reduce to the same key at $3.90 and $2.89 — a real
    collision on the real book, which is discarded rather than resolved to
    whichever happened to win."""
    from cogs_blend import _stripped_key, book_cost

    assert _stripped_key("Ginger") is None
    assert _stripped_key("Gin") is None
    out = _load_book_costs("harry_gatos")
    assert _stripped_key("Gin Martini [HG]") == _stripped_key("Vodka Martini [HG]")
    assert book_cost(out, "Something Martini [HG]") is None


def test_an_exact_name_still_wins():
    """The fallback must never displace a builder recipe or a recipe whose name
    already matches — those are the properly-sourced numbers."""
    from cogs_blend import book_cost

    out = _load_book_costs("stowaway")
    out["Bombay Dry [House]"] = 99.0
    assert book_cost(out, "Bombay Dry [House]") == 99.0


def test_a_dine_in_pizza_is_priced_at_its_own_menu_price():
    """
    norm() strips the bracket, so "Regular Margherita [Dine-in]" and "Regular
    Margherita" collapsed to one sell-price key and whichever the export listed
    first won. It was always the takeaway, so every one of the 77 dine-in pizzas
    carried its takeaway price — $14 against a real $21, six to eight dollars
    low. The cost was right; the GP was not, in the direction that makes a dish
    look worse than it is.

    Asserted against Back Office directly: a recipe whose name IS a product name
    must carry that product's price.
    """
    import csv

    exact = {}
    for f in ("stowaway_products.csv", "harry_gatos_products.csv"):
        p = ROOT / "data" / "bo_exports" / f
        if not p.exists():
            continue
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            try:
                v = float(r.get("SellPriceIncTax") or 0)
            except ValueError:
                v = 0.0
            if v > 0:
                exact.setdefault(r["ProductName"].strip(), set()).add(round(v, 2))
    exact = {k: next(iter(v)) for k, v in exact.items() if len(v) == 1}

    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    # RENAMED_TO deliberately overrides a discontinued product's price; that is
    # the one exception and it is declared in the converter.
    from convert_lightspeed_recipes import RENAMED_TO

    wrong = [(n, r["sell_incl"], exact[n]) for n, r in book.items()
             if not r.get("is_prep") and n in exact and r.get("sell_incl")
             and n not in RENAMED_TO and abs(exact[n] - r["sell_incl"]) > 0.01]
    assert not wrong, wrong[:5]
    assert book["Regular Margherita [Dine-in]"]["sell_incl"] == 21.0


def test_the_legacy_dine_in_pepperoni_is_named_what_the_pos_sells():
    """Every other dine-in pizza is "Regular X [Dine-in]" — twenty of them. This
    one lost its size prefix in Produce, so the P&L could not match it to the SKU
    Back Office sells ($253 a quarter, 114 serves since launch), and the sell
    lookup landed on the $2.00 "Pepperoni" add-on instead — which is where the
    SEVERE "real recipe priced below cost, sells $2.00 costs $2.11" came from.
    That was never a POS pricing error."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    assert "Pepperoni [Dine-in]" not in book
    r = book["Regular Pepperoni [Dine-in]"]
    assert r["sell_incl"] == 21.0 and r["our_cost"] > 0 and r["gp_pct"] > 0
