"""
The cost book's flags feed must be DERIVED, and it must never invent a number.

WHAT IT IS
----------
scripts/build_cost_book_flags.py writes data/cost_book_flags.json — one place
for everything the cost book still needs from a human, rendered at
the Flags tab of /recipes/. It replaces a set of HANDOFF_*.md files that nobody opened, in
which a $2,726-a-year lamb-yield question and a seed row an invoice had already
superseded were equally visible: not at all.

THE TWO WAYS A WORK QUEUE DIES, AND WHAT GUARDS THEM HERE
---------------------------------------------------------
1. IT GOES STALE. A hand-kept list still shows work that is finished, and after
   two or three of those the reader stops believing the rest. audit_book.py's
   own docstring calls that the worst failure a work queue has, having made the
   mistake three times in one session.

   So 43 of the 44 flags are computed from data/ on every build, and the biggest
   family — "sold, but no costed recipe" — comes from audit_book.coverage(),
   the SAME function the auditor uses to decide what is covered. Not a copy:
   the same object. A panel with its own, looser idea of coverage would send
   someone to write a recipe that already exists.

2. IT SHOWS AN UNKNOWN AS A ZERO. 38 of the 44 flags have no honest arithmetic
   behind a dollar figure. Publishing those as $0.00 would sort every one of
   them to the bottom of a queue ordered by money, and read as "free to ignore".
   So impact_per_year is null, never 0, and any figure that IS stated must carry
   the arithmetic that produced it in impact_basis.

THE NUMBERS THIS FILE HOLDS, MEASURED 2026-08-08
------------------------------------------------
    54 flags — 17 high, 35 medium, 2 low. Ten of them come from
    modules/recipes/book_reconcile.py, which checks the book against itself
    rather than against arithmetic (2 structure, 4 batch_yield, 4 price_conflict)
    and is tested in modules/recipes/tests/test_book_reconcile.py.

    44 flags — 12 high, 30 medium, 2 low
    $9,202/yr of measurable under-cost across 6 of them, all cook loss:
        Achiote Chicken [15Kg]      $2,991     Beef Roast     $501
        Lamb Roast                  $2,726     Pork Roast     $454
        Cooked Beef Brisket [1Kg]   $2,146     Chicken Roast  $384
    Lamb reproduces Zak's own figure to the dollar: $4.29 of lamb x 1,180
    serves in the 52 weeks to 2026-08-09, x (1/0.65 - 1).

    2 bad seeds, both landing on an exact whole pack count:
        Garlic Bread       $59.8100/ea seeded vs $1.4952 invoiced =  40.0x
        Pizza Box Inserts  $11.0550/ea seeded vs $0.1105 invoiced = 100.0x

    1 config gap: six Paramount lines for WHITE LIGHT VODKA ORIGINAL 20 L at
    $1,012.78, which no sanity bound in suppliers.yaml admits (the highest is
    per_keg $600).

WHAT THIS GUARDS
----------------
- the no-recipe list is exactly what audit_book.coverage() says, never a literal
- "Open Price" can never be costed, so it is exempt and never a flag
- no flag states $0 impact; every stated figure shows its working
- the cook-loss arithmetic is reproducible from the book and the sales API
- the same feed built twice is the same feed (ids stable, order stable)
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_book                                                  # noqa: E402
import build_cost_book_flags as flags                              # noqa: E402


@pytest.fixture(scope="module")
def feed():
    return flags.build()


def _by_cat(feed, cat):
    return [f for f in feed["flags"] if f["category"] == cat]


# --- derived, not listed ---------------------------------------------------

def test_the_no_recipe_list_is_the_auditors_own_coverage_call():
    """Not a copy of the rule — the same function object. Two definitions of
    "costed" is how a panel sends someone to build a recipe that exists."""
    assert flags.coverage is audit_book.coverage


def test_every_uncosted_dish_over_the_threshold_becomes_a_flag(feed):
    """Recompute the gaps independently and demand the same set. If someone
    ever hardcodes this list, the next recipe to land will not remove itself.

    The rolled-up long tail (subject_kind "product_group") is a different claim
    about the same coverage call and has its own test below — one product one
    flag still has to hold for everything above the threshold."""
    recipes = json.loads(flags.BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    _tot, _cov, gaps = audit_book.coverage(recipes)
    merged: dict = {}
    for (ven, nm, _g), rev in gaps.items():
        merged[(ven, nm)] = merged.get((ven, nm), 0.0) + rev
    expected = {nm for (ven, nm), rev in merged.items() if rev >= 500.0}
    got = {f["subject"] for f in _by_cat(feed, "no_recipe")
           if f.get("subject_kind") != "product_group"}
    exempted = {e["subject"] for e in feed["exempt"]}
    assert got == expected - exempted, sorted(got ^ (expected - exempted))
    assert got, "coverage() found no gaps at all — that is a broken join, not a clean book"


def test_the_uncosted_long_tail_is_rolled_up_and_not_dropped(feed):
    """299 of the 334 coverage gaps are under $500 each and $20,113 together —
    every kitchen add-on, every 'Add Prawns', Sticky Chicken Wings at $41.36.
    They were not on the panel at all. They are now one flag per reporting
    group, which is also the unit of work, with every member named in the
    evidence so nothing hides behind a total."""
    recipes = json.loads(flags.BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    _tot, _cov, gaps = audit_book.coverage(recipes)
    merged: dict = {}
    for (ven, nm, _g), rev in gaps.items():
        merged[(ven, nm)] = merged.get((ven, nm), 0.0) + rev
    tail_products = {nm for (_v, nm), rev in merged.items() if rev < 500.0}
    assert len(tail_products) > 200, len(tail_products)

    rollups = [f for f in _by_cat(feed, "no_recipe")
               if f.get("subject_kind") == "product_group"]
    assert rollups, "the entire uncosted long tail is missing from the panel"
    named = {e.split(" — $")[0] for f in rollups for e in f["evidence"]}

    # The add-on groups Zak names, and the $41 dish that was invisible.
    assert any("Add-ons - Kitchen" in f["subject"] for f in rollups), \
        [f["subject"] for f in rollups]
    assert "Sticky Chicken Wings" in named

    for f in rollups:
        # No rollup may be a bare total. If it cannot name at least three
        # products it is not a group, it is three flags pretending to be one.
        assert len(f["evidence"]) >= 3, f["id"]
        assert f["revenue_13wk"] >= 500.0, f["id"]
        # A rollup states revenue at stake, never an under-cost — how much of
        # an uncosted dish's cost is missing is what having no recipe means we
        # cannot say.
        assert f["impact_per_year"] is None, f["id"]
        assert f["derived"] is True

    # Every product in a rollup is genuinely below the individual threshold, so
    # nothing appears both as its own flag and inside a group.
    solo = {f["subject"] for f in _by_cat(feed, "no_recipe")
            if f.get("subject_kind") != "product_group"}
    assert not (named & solo), sorted(named & solo)


def test_the_list_is_long_enough_to_be_the_real_queue(feed):
    """A sanity floor. The known gaps at Stowaway and Harry Gatos alone —
    Arancini, Beef Cheek, Baked Camembert, Roast Turkey, Pie, Shredded Beef,
    Miso, Shoyu, Unlimited BBQ, Chicken Karaage, BBQ Meat Platter, Edamame —
    are twelve. A feed that suddenly held three would mean coverage() broke,
    not that the kitchen had a productive week."""
    subjects = {f["subject"] for f in _by_cat(feed, "no_recipe")}
    for known in ("Beef Cheek", "Baked Camembert", "Roast Turkey", "Pie",
                  "Shredded Beef", "Miso", "Shoyu", "Unlimited BBQ",
                  "Chicken Karaage", "BBQ Meat Platter", "Edamame"):
        assert known in subjects, known


# --- what can never be costed is exempt, and stays visible -----------------

def test_open_price_is_exempt_and_not_a_flag(feed):
    """An open-price key rings whatever the operator types. There is no product
    behind it, so no recipe can ever exist — but it is $2,194 of uncosted
    revenue at Harry Gatos and sorts near the top of any queue by revenue."""
    assert not [f for f in feed["flags"]
                if (f["subject"] or "").lower() in ("open price", "open food")]
    exempt = {e["subject"].lower() for e in feed["exempt"]}
    assert "open price" in exempt and "open food" in exempt


def test_an_exemption_states_why_it_is_permanent(feed):
    """"Not yet" and "never" are different claims. Only the second belongs."""
    assert feed["exempt"]
    for e in feed["exempt"]:
        assert e["reason"] and len(e["reason"]) > 40, e


# --- never invent a number -------------------------------------------------

def test_no_flag_claims_a_zero_dollar_impact(feed):
    """null and 0.00 are different claims. A flag showing $0 is one a human
    skips, and 38 of these have no honest figure at all."""
    bad = [f["id"] for f in feed["flags"] if f["impact_per_year"] == 0]
    assert not bad, bad


def test_every_stated_dollar_figure_shows_its_working(feed):
    naked = [f["id"] for f in feed["flags"]
             if f["impact_per_year"] and not f["impact_basis"]]
    assert not naked, naked


def test_the_headline_total_is_the_sum_of_the_measured_flags(feed):
    total = sum(f["impact_per_year"] or 0 for f in feed["flags"])
    assert abs(feed["known_impact_per_year"] - total) < 0.02


def test_an_uncosted_dish_reports_revenue_at_stake_not_an_under_cost(feed):
    """How much of an uncosted dish's cost is missing is exactly what having no
    recipe means we cannot say. Stating it as an impact would be the guess this
    whole feed refuses."""
    for f in _by_cat(feed, "no_recipe"):
        assert f["impact_per_year"] is None
        assert f["revenue_13wk"] > 0


# --- the cook-loss arithmetic ----------------------------------------------

def test_the_cook_loss_dollars_reproduce_from_the_book_and_the_sales_api(feed):
    """Lamb Roast, recomputed here from first principles rather than trusted:

        the protein line's own cost  x  serves in the last 52 weeks
                                     x  (1/0.65 - 1)

    It lands on $2,726, which is the figure Zak arrived at independently. The
    0.65 is an assumption and is applied to no cost anywhere — it exists to say
    whether the question is worth a chef's afternoon."""
    recipes = json.loads(flags.BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    sold, _window = flags.annual_units()
    line = next(l for l in recipes["Lamb Roast"]["ingredients"]
                if l["ref"] == "lightspeed:22888695")
    serves = sold[flags._nrm("Lamb Roast")]
    expect = float(line["eff_cost"]) * serves * ((1 / 0.65) - 1)

    got = next(f for f in feed["flags"] if f["id"] == "yield-lamb-roast")
    assert abs(got["impact_per_year"] - expect) < 0.02
    assert 2500 < got["impact_per_year"] < 3000, got["impact_per_year"]
    assert got["severity"] == "high"


def test_a_batch_yield_question_sums_every_dish_that_draws_on_it(feed):
    """Cooked Beef Brisket is not a dish; it is 10 kg of raw brisket spread over
    Beef Burrito and Beef Burrito D. The question's cost is the whole cost of
    not knowing the yield, not one burrito's share, so both consumers must be
    named in the basis."""
    got = next(f for f in feed["flags"] if f["id"] == "yield-cooked-beef-brisket")
    assert "Beef Burrito" in got["impact_basis"]
    assert "Beef Burrito D" in got["impact_basis"]
    assert got["subject_kind"] == "prep"


def test_the_assumed_yield_is_declared_in_the_feed(feed):
    """The one assumption in the file, stated where a reader meets the number."""
    assert feed["assumptions"]["cook_loss_yield"] == 0.65
    for f in _by_cat(feed, "cook_loss"):
        assert "ASSUMPTION" in f["impact_basis"]


# --- the derived families the audit already knows about --------------------

def test_the_bad_seeds_are_the_two_pack_misreads(feed):
    """Both harmless today — costs.csv carries the invoiced rate — and both a
    wrong fallback for the next recipe that reaches the product before an
    invoice does. Each lands on an exact whole pack count, which names the
    defect rather than just flagging it."""
    seeds = {f["subject"]: f for f in _by_cat(feed, "bad_seed")}
    assert set(seeds) == {"Garlic Bread", "Pizza Box Inserts"}, sorted(seeds)
    assert "pack of 40" in seeds["Garlic Bread"]["what_is_wrong"]
    assert "pack of 100" in seeds["Pizza Box Inserts"]["what_is_wrong"]


def test_the_config_gap_is_closed(feed):
    """This flag did its job and is now expected to be EMPTY.

    It used to name exactly one subject: WHITE LIGHT VODKA ORIGINAL 20000ml, at
    $1,012.78 - $1,013.84, described as "a standing purchase whose pack
    suppliers.yaml has no way to express, so it goes to review on every
    delivery". That was literally true — six of the twenty Paramount invoices in
    the corpus carried the line, reconciled to the cent, and were then failed by
    SANITY_BOUNDS because a 20 L drum was being bound-checked as a retail unit
    against per_unit's $500 ceiling. Paramount sat at 13/20 (65%) for that one
    reason.

    Closed 2026-08-09 by giving a bulk container its own basis
    (CostBasis.PER_BULK) and its own bounds, rather than by widening per_unit —
    see models.py and sanity_bounds in suppliers.yaml. Paramount went to 19/20.

    Asserting EMPTY rather than deleting the test is the point: the category is
    still computed, so if a new pack shape appears that suppliers.yaml cannot
    express, this fails and names it instead of quietly sending that supplier's
    deliveries to the LLM forever."""
    cfg = _by_cat(feed, "config")
    assert cfg == [], [f["subject"] for f in cfg]


def test_a_stock_item_held_twice_is_flagged_only_past_a_keying_error(feed):
    """audit_book reports twins from 1.35x, and the bottom four of the six it
    lists are two venues buying on separate ILG accounts a fortnight apart —
    real buying, not work. A flag needs a gap buying cannot explain."""
    twins = _by_cat(feed, "back_office")
    assert any("Angostura" in f["subject"] for f in twins)
    assert not any("Mancino" in f["subject"] or "Oscar" in f["subject"] for f in twins)


def test_a_declared_flag_re_reads_its_prices_from_disk(feed):
    """A decision note that quotes a price is a handoff file waiting to rot.
    The yaml names the ids; the builder looks up what they cost today."""
    hg = next(f for f in feed["flags"] if f["id"] == "decision-hg-bottle-prices")
    assert hg["derived"] is False
    assert any("ilg:122-2858" in e and "212.44" in e for e in hg["evidence"]), hg["evidence"]
    assert any("ilg:122-2867" in e and "184.94" in e for e in hg["evidence"]), hg["evidence"]


# --- the book disagreeing with itself --------------------------------------

def test_a_missing_component_is_priced_off_its_own_siblings(feed):
    """The one family here whose dollar figure needs NO assumption at all. Three
    burritos carry 55 g of Mexican cheese; the fourth carries none. What the
    missing line would cost is exactly what theirs cost — no yield, no waste, no
    portion guess — times what the dish sells."""
    f = next(x for x in _by_cat(feed, "structure") if "Cauliflower Burrito" in x["subject"])
    assert 100 < f["impact_per_year"] < 250, f["impact_per_year"]
    assert "0.8250" in f["impact_basis"]
    assert "No yield, waste or portion assumption enters it." in f["impact_basis"]
    assert len(f["evidence"]) == 4          # three carriers plus the coherence


def test_a_batch_that_holds_more_than_it_makes_states_no_dollar(feed):
    """Either "Cooked Beef Brisket [1Kg]" is a 10 kg batch or its brisket line is
    wrong, and the two readings move the per-kilo rate in opposite directions.
    Sizing it would mean picking one — the guess this feed refuses."""
    got = {f["subject"]: f for f in _by_cat(feed, "batch_yield")}
    assert set(got) == {"Cooked Beef Brisket [1Kg]", "Mango-Chilli Puree [1L]",
                        "Jalapeño Tequila [1L]", "Coconut-washed Rooster Blanco [1L]"}
    for f in got.values():
        assert f["impact_per_year"] is None
        assert f["question"]
    assert "11.45x" in got["Cooked Beef Brisket [1Kg]"]["what_is_wrong"]


def test_a_price_conflict_reports_a_spread_and_calls_it_one(feed):
    """A spread is not an under-cost: nobody knows yet which of the two prices is
    wrong. It goes in the evidence, in words, and impact_per_year stays null so
    the panel's headline — "$X a year of under-cost that has actually been
    measured" — stays a true sentence. The spread still sets the severity, so it
    sorts by money anyway."""
    pcs = {f["subject"]: f for f in _by_cat(feed, "price_conflict")}
    assert "Massenez Elderflower [5L]" in pcs, sorted(pcs)
    m = pcs["Massenez Elderflower [5L]"]
    assert m["impact_per_year"] is None
    assert m["severity"] == "high"
    assert "SPREAD, not a loss" in m["evidence"][0]
    assert "10.47x above" in m["what_is_wrong"]


def test_the_headline_still_counts_only_measured_under_cost(feed):
    """The regression that keeps the sentence honest: everything with a dollar
    figure is a cost the book is actually missing, not a spread or a revenue."""
    assert {f["category"] for f in feed["flags"] if f["impact_per_year"]} <= {
        "cook_loss", "structure"}


# --- the contract the panel reads ------------------------------------------

def test_every_flag_carries_the_whole_contract(feed):
    """A stable id, a severity, what it is about, one line of what is wrong, a
    dollar figure or an explicit null, and what closes it — for whom."""
    for f in feed["flags"]:
        for k in ("id", "category", "severity", "subject", "subject_kind",
                  "what_is_wrong", "why_it_matters", "action", "owner", "source"):
            assert f.get(k), f"{f.get('id')}: missing {k}"
        assert "impact_per_year" in f
        assert f["severity"] in ("high", "medium", "low")
        assert len(f["what_is_wrong"]) < 260, f["id"]


def test_ids_are_unique_and_reproducible(feed):
    ids = [f["id"] for f in feed["flags"]]
    assert len(set(ids)) == len(ids), sorted(i for i in ids if ids.count(i) > 1)
    again = flags.build()
    assert [f["id"] for f in again["flags"]] == ids
    assert again["known_impact_per_year"] == feed["known_impact_per_year"]


def test_every_category_the_flags_use_is_one_the_panel_draws(feed):
    """dashboard/_shared/flags_view.js draws sections from `categories`.
    A family missing from that list exists in the feed and reaches no screen."""
    known = {c["key"] for c in feed["categories"]}
    assert {f["category"] for f in feed["flags"]} <= known
