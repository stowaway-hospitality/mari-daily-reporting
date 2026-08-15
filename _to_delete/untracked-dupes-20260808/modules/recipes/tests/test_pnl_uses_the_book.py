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

    from core.domain import CostSeries, load_cost_observations
    from modules.recipes.cost import cost_on, load_recipes

    # Asserted against cost_on directly, on EVERY recipe including the batches.
    # _load_our_costs deliberately withholds batches now (a batch is not a serve
    # cost — see the next test), so going through it would stop testing the thing
    # this guards: that sub-recipes resolve at all.
    on = date(2026, 8, 4)
    costs = CostSeries(load_cost_observations())
    for venue_file in ("stowaway", "marilynas", "harry_gatos"):
        recipes = load_recipes(venue_file)
        for r in recipes:
            cost_on(r, costs, on, recipes=recipes)      # raises MissingCost if broken


def test_a_builder_batch_is_never_published_as_a_serve_cost():
    """
    The distinction is already in the recipe: a batch declares a yield ("Dragon
    Soda: 20,000 ml") and carries no sell price; a serve carries a sell price and
    no yield.

    Publishing the batch cost as a serve cost books it against every unit sold,
    and both of these names ARE real Back Office products: Dragon Soda would have
    cost $37.20 on a $9.00 drink and Mint Yoghurt $9.51 on a $1.00 side.
    _load_book_costs already refuses this for the scraped book; the builder
    recipes had no such guard, and they OVERRIDE the book. Neither had fired yet
    only because neither product sold in the last 13 weeks.
    """
    from datetime import date

    from cogs_blend import _load_our_costs
    from modules.recipes.cost import load_recipes

    on = date(2026, 8, 4)
    for venue_key, venue_file in (("stowaway", "stowaway"),
                                  ("marilynas", "marilynas"),
                                  ("harry", "harry_gatos")):
        served = _load_our_costs(venue_key, on)
        batches = {r.product for r in load_recipes(venue_file) if r.yield_qty}
        assert not (batches & set(served)), sorted(batches & set(served))
    assert "Dragon Soda" not in _load_our_costs("stowaway", on)
    assert "Mint Yoghurt" not in _load_our_costs("marilynas", on)


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


def test_harry_gatos_wine_costs_off_the_invoice_that_actually_bought_it():
    """
    Harry Gatos' own SKUs for Whispering Angel and Veuve Clicquot carry
    CostPriceIncTax 0.0000, so a $26 glass of rosé and a $32 glass of champagne
    reported 100% GP.

    The reason to read them as Stowaway's stock is not the name, it is the
    purchasing: of 449 non-seed supplier rows filed to harry_gatos, every one is
    food except two lines of White Light Vodka. Harry Gatos has no wine
    supplier. Those bottles were bought on a Stowaway invoice because there is
    no other invoice they could have come from.
    """
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    for name in ("Whispering Angel - Regular [HG]", "Whispering Angel - Large [HG]",
                 "Veuve Clicquot - Glass [HG]"):
        r = book[name]
        assert r["our_cost"] > 0, name
        assert 0 < r["gp_pct"] < 95, f"{name} {r['gp_pct']}"


def test_a_pour_with_no_twin_anywhere_stays_visible():
    """Milagro Reposado and Velho Berreiro Cachaça have no costed twin in the
    group, so there is nothing to point them at. They must keep reading $0 and
    keep showing up in the audit until an invoice arrives — inventing a cost for
    them would be the flattering direction."""
    from convert_lightspeed_recipes import INGREDIENT_ALIAS

    for absent in ("Milagro Reposado Tequila [Bottle]", "Velho Berreiro Cachaça [Bottle]"):
        assert absent not in INGREDIENT_ALIAS


def test_davys_old_fashioned_costs_off_its_batch():
    """Back Office sells it at $24 (Cocktails - Signature) and Produce had no
    recipe for it at all, so it was one of the products reporting 100% GP. Spec
    from Zak, 2026-08-06: a 594 ml batch, 75 ml a serve.

    Water is 150 ml of the batch and costs nothing, which is why the priced
    lines sum to less than the yield. A drop is the metric drop, 1/20 ml —
    reading the 30 drops of Xocolatl as dasher DASHES instead would put 24 ml of
    bitters in the batch, which is not a thing anyone does.
    """
    from datetime import date

    from core.domain import CostSeries, load_cost_observations
    from modules.recipes.cost import cost_on, load_recipes, recipe_as_of

    on = date(2026, 8, 4)
    recipes = load_recipes("stowaway")
    costs = CostSeries(load_cost_observations())

    batch = recipe_as_of(recipes, "Davy's Old Fashioned Batch", on)
    assert batch and batch.yield_qty == 594 and batch.yield_unit == "ml"
    assert 35 < float(cost_on(batch, costs, on, recipes=recipes)) < 45

    serve = recipe_as_of(recipes, "Davy's Old Fashioned", on)
    assert serve and serve.sell_incl_gst == 24
    c = float(cost_on(serve, costs, on, recipes=recipes))
    gp = (float(serve.sell_incl_gst) / 1.1 - c) / (float(serve.sell_incl_gst) / 1.1) * 100
    assert 4.0 < c < 6.0, c
    assert 70 < gp < 85, gp


def test_havana_is_aliased_AND_bridged_the_seed_was_impossible():
    """Zak: "havana club definitely has a price somewhere". It does, under a name
    one word shorter — the recipe says "Havana Club 3yr [700ml]", the priced
    product is "Havana 3yr [700ml]".

    This test asserted the OPPOSITE until 2026-08-06, and the reversal is the
    point of keeping it.

    The old reasoning: ILG bills $58.01 on a "6x700ML" uom. One bottle gives
    $82.87/L (twice the seed); six gives $9.67 a bottle (absurd). $58.01 over TWO
    bottles is $29.01 and the seed said $29.09 — eight cents apart, surely not a
    coincidence. So the invoice was ruled wrong and the seed right.

    It was a coincidence. ILG's own MAR 2026 price book lists 355-055-2 Havana
    Club 700ml 3yo. at $49.20 a bottle. $29.09 is 41% under what the supplier
    themselves publish — not a keen price, an impossibility. And the seed was
    never an observation: "Lightspeed recipe cost (median of 4)", i.e. Produce's
    own derived number, the exact input this project exists because it cannot
    trust.

    THE LESSON, which is why this docstring is longer than the assertions: two
    candidate readings were adjudicated against ONE reference, and that reference
    was the number under suspicion. When the tie-breaker is the thing being
    tied, there is no tie-breaker. A third independent source was sitting in
    data/invoice_corpus/ilg_pricebook.pdf the whole time.

    Bridged now. Every Havana pour doubled, which is the correct direction."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    pika = book["Pika Pika"]
    havana = [l for l in pika["ingredients"] if "Havana" in (l.get("name") or "")]
    assert havana and havana[0]["eff_cost"] > 0
    rate = float(havana[0]["our_cost"])
    # Not pinned to a snapshot: two ILG invoices are already in the series
    # ($0.082871 on 19 May, $0.083114 on 2 June) and the as-of lookup takes the
    # latest, so any exact figure here would go red the next time Havana is
    # delivered. What must hold is the INVARIANT — the bottle never costs less
    # than the supplier's own published price again.
    assert rate * 700 > 49.20, (
        f"${rate * 700:.2f}/bottle is under ILG's MAR 2026 book price of $49.20 — "
        f"the Produce seed ($29.09) is back")
    assert rate > 0.070, f"{rate} is not an invoice-derived rate"


def test_dried_shiitake_is_not_twenty_five_dollars_a_gram():
    """Produce holds it twice: "Shiitake Mushrooms Dried" at DefaultSize 1 g and
    $25.00 — i.e. $25 a gram — and "Mushroom Shiitake Dried [1kg]" at $31.25/kg.
    Jun Pacific invoice NB10486744 settles it: "Dried Shiitake Mushroom 1kg",
    $31.25."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    for name in ("Shiitake Tare", "Shiitake Bouillon"):
        lines = [l for l in book[name]["ingredients"] if "hiitake" in (l.get("name") or "")]
        assert lines, name
        for l in lines:
            rate = float(l["eff_cost"]) / float(l["qty"])
            assert 0 < rate < 0.05, f"{name}: ${rate:.4f}/g"
    # and Produce still believes 50 g of it costs $1,250
    tare = [l for l in book["Shiitake Tare"]["ingredients"] if "hiitake" in (l.get("name") or "")][0]
    assert float(tare["ls_cost"]) > 1000 and tare["eff_cost"] < 2


def test_a_countable_cost_can_be_multiplied_by_a_countable_quantity():
    """resolve_pack answers "one whole pack" as "can" — its basis word — while
    every recipe line says "ea". So 105 cost rows in "can" could never be matched
    to 274 recipe lines in "ea", and each mismatch fell through to Lightspeed's
    number or to $0. They are the same dimension at the same magnitude: one of
    the thing you bought.

    Lemon, Lime & Bitters was 2 ml of Angostura and nothing else — a $5.00 drink
    costing 20c. Zak: "lemon lime bitters is the same as lemonade, plus the
    bitters", and Back Office prices one Lemonade [mixer] at $0.8032."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    llb = book["Lemon, Lime & Bitters"]
    mixer = [l for l in llb["ingredients"] if "mixer" in (l.get("name") or "").lower()]
    assert mixer and abs(mixer[0]["eff_cost"] - 0.8032) < 0.001
    assert 70 < llb["gp_pct"] < 85, llb["gp_pct"]

    pink = book["Pink Lemonade Glass"]
    assert 70 < pink["gp_pct"] < 85, pink["gp_pct"]


def test_a_delivery_twin_costs_the_same_as_the_dish_it_copies():
    """A "X D" recipe is a copy of X and has no scrape line of its own, so there
    is no raw cost to judge whole-vs-fraction with — and the default is WHOLE,
    the expensive reading. Bang Bang Cauli D took "0.01" of a $9.90 bunch of
    chives as a whole bunch and cost $12.57 on a $16 dish, while the identical
    Bang Bang Cauli read the same line as 10c."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())["recipes"]
    a = book["Bang Bang Cauli"]["our_cost"]
    b = book["Bang Bang Cauli D"]["our_cost"]
    assert abs(a - b) < 0.01, f"${a} vs ${b}"
    assert a < 4, a

    # Any OTHER twin that diverges is a real finding, not a test failure — the
    # audit reports them (Bundaberg Ginger Beer is one: $0.86 against $3.22, a
    # known pack clash on ilg:460-4128). Assert only that the list stays short
    # enough to be read.
    pairs = [(n, n + " D") for n in book if n + " D" in book and not book[n].get("is_prep")]
    assert pairs, "fixture sanity: the book has delivery twins"
    off = [n for n, t in pairs
           if (book[n].get("our_cost") or 0) and (book[t].get("our_cost") or 0)
           and abs(book[n]["our_cost"] - book[t]["our_cost"])
           / max(book[n]["our_cost"], book[t]["our_cost"]) > 0.35]
    assert len(off) <= 3, off
