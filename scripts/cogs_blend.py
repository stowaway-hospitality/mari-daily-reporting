#!/usr/bin/env python3
"""Reported ("estimated") COGS = our own recipe cost where we have a recipe,
Lightspeed's cost elsewhere.

Kept as its own tiny, import-cheap module so it is unit-testable without importing
daily_aggregator (which runs a full pull on import). See COGS_ARCHITECTURE.md: the
dashboard's estimated COGS is what we ACTUALLY used (recipe x invoice cost), not
Lightspeed's stale Average-Cost figure; Xero purchases stay the separate ACTUAL
COGS feed. Products without a recipe keep LS's cost as a visible per-product
fallback, and recipe_coverage_pct reports how much of revenue is on a real recipe.

BOTH COST SOURCES LIVE HERE, and that is the point of the module.

daily_aggregator.py is a SCRIPT: it has no main() and no __main__ guard, so
importing it runs the entire daily pull — reads the exports, writes
data/<venue>_daily_<date>.json and rewrites the history CSV. The line above has
said so since the module was created, and then the two loaders were written into
daily_aggregator anyway. A test that wanted to check the P&L used the cost book
had no choice but to import it, so `pytest` published a day's record and rewrote
a history file as a side effect — on CI, and on the Mac where the real exports
live. It showed up as data/ files appearing dirty after a test run and was read
as noise for weeks.

So: anything the P&L needs to COMPUTE a cost belongs in this module, where it can
be imported for the price of an import. daily_aggregator imports it back.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


COSTED_BOOK = Path(__file__).resolve().parents[1] / "data" / "lightspeed_recipes_costed.json"
_BOOK_VENUE = {"stowaway": "stow", "harry": "hg", "harry_gatos": "hg", "marilynas": "mari"}


def _load_book_costs(venue_key):
    """
    POS product name -> our cost per serve, from the 648-recipe costed book.

    WHY THIS EXISTS
    ---------------
    _load_our_costs below reads data/recipes/*.yaml — 35 hand-authored recipes
    whose names do not match POS product names. So every published day carried
    `recipe_coverage_pct: 0.0`: the blend blended nothing and COGS fell through to
    Lightspeed's figure for 100% of revenue, while the 648 invoice-fed recipes this
    project exists to produce sat unused. Wiring the book in lifts coverage to ~49%
    of revenue (91.5% at Marilyna's, 45% at Stowaway).

    AS-OF CAVEAT, stated plainly: the book is a CURRENT snapshot — it carries no
    effective date, so it cannot answer "what did this cost in June?". It is
    therefore used as a present-day cost, which is right for the daily 6am pull and
    approximate for a historical backfill. The builder recipes (properly as-of via
    CostSeries) still WIN wherever both price the same product, so this only ever
    fills a gap that was previously Lightspeed's number. Making the book itself
    effective-dated is the clean follow-up; until then this is strictly better than
    the stale Average-Cost figure it replaces, and never overrides a dated source.

    NOT filtered by venue, deliberately: the book carries no venue field, product
    names are unique across the three venues, and the caller has ALREADY split rows
    by venue — so an unfiltered lookup can only match this venue's own products.
    The venue key is still validated, so an unknown venue returns {} rather than
    silently costing against a map it was never meant to use.

    Never raises: this runs unattended at 6am and a recipe problem must not take
    the pull down.
    """
    if venue_key not in _BOOK_VENUE:
        return {}
    try:
        book = json.loads(COSTED_BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    except Exception as e:                                   # noqa: BLE001
        print(f"  costed book unavailable ({e}) — falling back")
        return {}
    out = {}
    for name, r in book.items():
        if r.get("is_prep"):
            continue                                          # a batch is not a sold product
        try:
            c = float(r.get("our_cost"))
        except (TypeError, ValueError):
            continue
        # ZERO IS NOT A PRICE, IT IS THE ABSENCE OF ONE.
        #
        # 10 products reach this point at exactly $0.00 — Whispering Angel, Veuve
        # Clicquot, Laphroaig, a Caipirinha. None of them is free. Each is a real
        # pour whose product carries no cost anywhere: Lightspeed's Back Office
        # has CostPriceIncTax 0.0000 for it and no invoice has bridged to it yet.
        #
        # Publishing that 0 to the P&L does not report "we don't know". It reports
        # a $40 glass of rosé at 100% gross profit, and it does it silently, in the
        # flattering direction. Falling through to Lightspeed's own figure is the
        # honest answer — it is wrong too, but it is visibly sourced
        # (cost_source='lightspeed') and the audit is already shouting about it.
        if c > 0:
            out[name] = c

    # A SPIRIT'S CATEGORY WORD IS NOT PART OF ITS IDENTITY.
    #
    # Produce names the recipe "Bombay Dry Gin [House]"; the POS sells "Bombay
    # Dry [House]". One word apart, same gin, and the exact-name lookup below
    # missed it — $2,827 of it in 13 weeks, costed off Lightspeed's figure while
    # a $2.11 recipe sat in the book. Same for "Baileys Irish Cream" vs
    # "...Liqueur", "Bombay Sapphire" vs "...Gin", "1800 Coconut" vs
    # "...Tequila", "Monkey Shoulder Scotch" vs "...Whisky".
    #
    # So each recipe also answers to a normalised form of its name: lowercase
    # alphanumeric words, sorted, with the category words dropped. That also
    # settles punctuation and word order, which is the same class of difference
    # — "Lemon Lime Bitters" vs "Lemon, Lime & Bitters", "Coke 1.25L" vs
    # "1.25L Coke", "Flor De Cana" vs the export's mangled "Flor De Ca|a".
    #
    # Two guards keep it from becoming a fuzzy match:
    #   * at least two words must remain, so "Ginger" cannot become "Ginger Beer"
    #   * the form must identify exactly ONE cost, or it is dropped entirely.
    #     It earns its keep on the real book: "Gin Martini [HG]" and "Vodka
    #     Martini [HG]" both reduce to "hg martini" and are $3.90 and $2.89, so
    #     that key is discarded rather than resolved to whichever won.
    #
    # It is a fallback. An exact name always wins, and so does a builder recipe.
    stripped: dict = {}
    for name, c in list(out.items()):
        k = _stripped_key(name)
        if k:
            stripped.setdefault(k, set()).add(c)
    for k, costs in stripped.items():
        if len(costs) == 1 and k not in out:
            out[k] = next(iter(costs))
    return out


# Words that name a drink's CATEGORY rather than the drink. "Bombay Dry" and
# "Bombay Dry Gin" cannot be two different products behind the same bar. Kept
# short on purpose: every word added here is a distinction the P&L stops making.
_CATEGORY_WORDS = {"gin", "vodka", "whisky", "whiskey", "rum", "tequila",
                   "wine", "beer", "liqueur"}
_KEY_PREFIX = "\x00tok:"        # cannot collide with a real POS product name


def _stripped_key(name):
    """A comparable key for `name` with its category words removed, or None.

    None when fewer than two words survive — one word is not enough to identify
    a product, and matching on it is how "Ginger" would find "Ginger Beer"."""
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
             if w not in _CATEGORY_WORDS]
    if len(words) < 2:
        return None
    return _KEY_PREFIX + " ".join(sorted(words))


def book_cost(our_costs, product_name):
    """Our cost per serve for a POS product name, or None.

    Exact name first — that is a builder recipe or a recipe whose name already
    matches — then the category-word-stripped form. Never the other way round."""
    hit = our_costs.get(product_name)
    if hit is not None:
        return hit
    k = _stripped_key(product_name)
    return our_costs.get(k) if k else None


def _load_our_costs(venue_key, target):
    """
    product name -> our cost per serve on `target`, from our own recipes.

    Returns {} and carries on if there are no recipes yet, or if anything in
    the recipe module is unhappy. This runs unattended at 6am and its job is
    the daily numbers -- a recipe problem must not take the whole pull down.
    Falling back to Lightspeed's cost is a known, visible state
    (cost_source='lightspeed'), not a silent one.

    AS-OF, not current: costing 16 July uses 16 July's prices, whenever it runs.
    See ARCHITECTURE.md decision 2.
    """
    try:
        from core.domain import CostSeries, load_cost_observations
        from modules.recipes.cost import MissingCost, cost_on, load_recipes, recipe_as_of

        venue_file = {"stowaway": "stowaway", "harry": "harry_gatos",
                      "marilynas": "marilynas"}.get(venue_key, venue_key)
        recipes = load_recipes(venue_file)
        if not recipes:
            return {}
        costs = CostSeries(load_cost_observations())
        out = {}
        for product in {r.product for r in recipes}:
            r = recipe_as_of(recipes, product, target)
            if not r:
                continue
            if r.yield_qty and not getattr(r, "is_serve", False):
                # A BATCH IS NOT A SERVE, AND THIS DICT IS SERVE COSTS.
                #
                # "Has a yield" was the whole test until 2026-08-16, and it is
                # not quite the rule this comment describes. BBQ Wings is SOLD
                # and also drawn "1 ea" by 22 wings deals: it needs a yield to be
                # divisible and a serve cost to be sold, and on the yield alone
                # it got the yield and lost the cost. Same shape as three sold
                # products that briefly cost $0.00 the same afternoon.
                #
                # So the test is now the one the paragraph below already states:
                # a batch has a yield AND NO SELL PRICE. `is_serve` is set by
                # scripts/materialise_recipes.py on any record the scrape reports
                # as a sold product of this venue.
                #
                # The distinction is already in the recipe: a batch declares a
                # yield ("Dragon Soda: 20,000 ml") and carries no sell price; a
                # serve carries a sell price and no yield. Publishing the batch
                # cost here books it against every unit sold — Dragon Soda would
                # have cost $37.20 on a $9.00 drink and Mint Yoghurt $9.51 on a
                # $1.00 side, because both names ARE real Back Office products.
                #
                # _load_book_costs already refuses this for the scraped book
                # ("a batch is not a sold product"); the builder recipes had no
                # such guard, and they OVERRIDE the book. Neither had fired yet
                # — neither product sold in the last 13 weeks — which is the only
                # reason nobody had seen a 4x over-cost land in a day's COGS.
                continue
            try:
                # PASS `recipes`. Without it cost_on resolves sub-recipes against
                # an EMPTY list, so every batch-using dish died with "sub-recipe
                # 'Sugar Syrup' has no version in force" and silently fell back to
                # Lightspeed's cost — 9 of the 21 Stowaway recipes, i.e. every one
                # that draws on a syrup, jam or batch.
                out[product] = float(cost_on(r, costs, target, recipes=recipes))
            except MissingCost as e:
                # Refusing to cost one dish is correct; it must not stop the pull.
                print(f"  recipe cost skipped: {e}")
        if out:
            print(f"  our recipes cost {len(out)} product(s) on {target}")
        return out
    except Exception as e:                                  # noqa: BLE001
        print(f"  recipe costing unavailable ({e}) — using Lightspeed's cost")
        return {}


def blend_reported_cogs(product_breakdown, cogs_lightspeed, revenue_net):
    """(cogs, source, coverage_pct) for the reported estimated COGS.

    product_breakdown: rows with 'cost' (already qty x unit cost), 'rev' and
    'cost_source' in {'recipe','lightspeed'}. cogs_lightspeed: the all-LS total,
    used as the fallback. Fails toward review: an implausible blend (implied GP
    outside 0-100%, or negative cost) falls back to Lightspeed rather than ship a
    broken/flattering GP to the board.
    """
    cogs_recipe = sum((p.get("cost") or 0) for p in product_breakdown)

    # COVERAGE IS A SHARE OF REVENUE, so both halves must sit on the same base and
    # that base must not be allowed to shrink. A discount, void or refund row
    # carries NEGATIVE rev and never has a recipe, so it came off the denominator
    # only — which is how Marilyna's published 102.3% coverage, a share of a
    # shrinking base. Clamp the parts, then clamp the result: a percentage of
    # revenue cannot exceed 100 whatever the rows do.
    covered_rev = sum(max(p.get("rev") or 0, 0) for p in product_breakdown
                      if p.get("cost_source") == "recipe")
    prod_rev = sum(max(p.get("rev") or 0, 0) for p in product_breakdown)
    coverage = min(covered_rev / prod_rev * 100, 100.0) if prod_rev else 0.0

    ok = (bool(revenue_net) and cogs_recipe >= 0
          and 0.0 <= (revenue_net - cogs_recipe) / revenue_net <= 1.0)

    if not ok:
        # The blend was REFUSED and none of it is being published, so its coverage
        # is not a fact about the number on the screen. Returning it anyway told
        # the dashboard a recipe figure was in play when the COGS shown came
        # wholly from Lightspeed.
        return cogs_lightspeed, "lightspeed", 0.0

    if coverage <= 0:
        # Every row priced off Lightspeed. Summing per-product Lightspeed costs is
        # not a recipe blend, and calling it one claims a provenance the number
        # does not have — 8 committed day files say `recipe_blend` beside
        # `recipe_coverage_pct: 0.0` today.
        #
        # The NUMBER is deliberately unchanged (cogs_recipe is the same per-product
        # sum it always was); only the label stops overstating where it came from.
        return cogs_recipe, "lightspeed", 0.0

    return cogs_recipe, "recipe_blend", coverage
