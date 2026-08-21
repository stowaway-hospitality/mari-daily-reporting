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

import re
import json
import yaml
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
    solo = {(f["venue"], f["subject"]) for f in _by_cat(feed, "no_recipe")
            if f.get("subject_kind") != "product_group"}
    assert not (named & solo), sorted(named & solo)   # same venue, both places


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
    """Recomputed from first principles rather than trusted:

        the protein line's own cost  x  serves in the last 52 weeks
                                     x  (1/assumed_yield - 1)

    LAMB IS NO LONGER THE SUBJECT. Zak weighed it on 2026-08-15 — "raw lamb 2.7kg
    was 2.3kg cooked" — so its question is answered and its flag is gone; the
    measurement is applied in data/cook_yields.yaml and guarded by
    test_cook_yields.py. The arithmetic this test exists for is unchanged, so it
    now runs on whichever subjects are still open.

    WORTH KNOWING WHILE READING THOSE FIGURES: the assumed 0.65 implied a 54%
    uplift on the raw joint. The one protein anybody has actually weighed came in
    at 85.2%, a 17% uplift. The assumption is doing what it was built to do — size
    the question — but it is sizing it HIGH, and the remaining dollar figures
    should be read as an upper bound rather than an estimate.
    """
    spec = yaml.safe_load(flags.DECLARED.read_text(encoding="utf-8-sig"))["cook_loss"]
    y = float(spec["assumed_yield"])
    recipes = json.loads(flags.BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    sold, _window = flags.annual_units()

    checked = 0
    for subj in spec["subjects"]:
        got = next((f for f in feed["flags"] if f["id"] == subj["id"]), None)
        if not got or not subj.get("ref") or not subj.get("in_recipe"):
            continue
        rec = recipes.get(subj["in_recipe"])
        if not rec:
            continue
        line = next((l for l in rec["ingredients"] if l["ref"] == subj["ref"]), None)
        if line is None:
            continue
        expect = float(line["eff_cost"]) * sold[flags._nrm(subj["in_recipe"])] * ((1 / y) - 1)
        assert abs(got["impact_per_year"] - expect) < 0.02, subj["id"]
        checked += 1
    assert checked, "no open cook-loss subject reproduced — the arithmetic is untested"

    # and the settled one is settled
    assert not any(f["id"] == "yield-lamb-roast" for f in feed["flags"])


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

def test_the_bad_seeds_are_gone(feed):
    """CLOSED 2026-08-14. The family is empty and this guards that it stays so.

    Both were recipe-bridge-seed rows that copied a Gulli PACK price into a
    per-each field: garlic bread $59.81 (the carton of 40) and pizza box inserts
    $11.055 (the box of 100), each landing on an exact whole pack count, which is
    what named the defect rather than merely flagging it.

    Neither was mispriced in the book — costs.csv already carried the invoiced
    rate — but a seed is what a new ProductID falls back to before its first
    invoice lands, so a $59.81 garlic bread was a loaded gun with the safety on.
    Corrected in data/cogs_list.csv to $1.4952 and $0.11055, the per-each figures
    the Gulli packs actually work out at.

    NOT fixed by re-running build_recipe_bridge.py, which is the obvious move and
    the wrong one: it now proposes bridging Herb Coriander to FFT's HCDRMB at
    $15.40 — the MARKET bunch — against a retail bunch at $1.30, and re-points
    the 6" tortilla at a different B&E code than the one already adjudicated.
    A regenerator is only safe where nothing downstream has been decided by hand.
    """
    seeds = {f["subject"]: f for f in _by_cat(feed, "bad_seed")}
    assert seeds == {}, sorted(seeds)


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
    # CLOSED 2026-08-09: the HG seed was 13x low; corrected from the ILG invoice,
    # so the pair no longer reads as a twin. Guards the fix, not the defect.
    assert not any("Angostura" in f["subject"] for f in twins)
    assert not any("Mancino" in f["subject"] or "Oscar" in f["subject"] for f in twins)


def test_a_declared_flag_re_reads_its_prices_from_disk(feed):
    """A decision note that quotes a price is a handoff file waiting to rot.
    The yaml names the ids; the builder looks up what they cost today."""
    hg = next(f for f in feed["flags"] if f["id"] == "decision-hg-bottle-prices")
    assert hg["derived"] is False
    # The invoice observation was $212.44/keg until the declared-conversion
    # layer (2026-08-16) restated keg series to base units: 212.44 / 49500 =
    # $0.0043/ml. Same observation, one unit system — pin the restated form.
    assert any("ilg:122-2858" in e and "/ml" in e for e in hg["evidence"]), hg["evidence"]
    assert any("ilg:122-2867" in e and "/ml" in e for e in hg["evidence"]), hg["evidence"]


# --- the book disagreeing with itself --------------------------------------

def test_a_missing_component_is_priced_off_its_own_siblings(feed):
    """The one family here whose dollar figure needs NO assumption at all. Three
    burritos carry 55 g of Mexican cheese; the fourth carries none. What the
    missing line would cost is exactly what theirs cost — no yield, no waste, no
    portion guess — times what the dish sells."""
    # Cauliflower Burrito used to be the example here. It is not a finding any
    # more — it carries vegan cheese at the same 55 g, so the substitution guard
    # drops it (see test_book_reconcile). Fish Burrito's lime is the real one and
    # prices the same way: what the carriers pay, times what the dish sells.
    f = next(x for x in _by_cat(feed, "structure") if "Fish Burrito" in x["subject"])
    assert f["impact_per_year"] > 0, f["impact_per_year"]
    assert "No yield, waste or portion assumption enters it." in f["impact_basis"]
    assert len(f["evidence"]) >= 3          # the carriers plus the coherence


def test_a_batch_that_holds_more_than_it_makes_states_no_dollar(feed):
    """Either "Cooked Beef Brisket [1Kg]" is a 10 kg batch or its brisket line is
    wrong, and the two readings move the per-kilo rate in opposite directions.
    Sizing it would mean picking one — the guess this feed refuses."""
    # All four are settled, so the OVERFLOW family is empty and this guards that
    # it stays settled. Three were the LABEL being wrong (real yields now read
    # from Lightspeed, data/recipe_yields.yaml); Mango-Chilli was a 10x-low
    # chilli rate.
    #
    # Keyed on the rule's own id prefix, not on the category. The category gained
    # a second rule on 2026-08-16 -- yield_overstated, the mirror -- and asserting
    # the whole category was empty would have made adding the missing half look
    # like a regression.
    got = {f["subject"]: f for f in _by_cat(feed, "batch_yield")
           if f["id"].startswith("batch-yield-")}
    assert got == {}, sorted(got)


def test_the_mirror_rule_exists_and_also_states_no_dollar(feed):
    """A batch that MAKES more than it contains is the other half, and the half
    that flatters: it makes the per-unit rate too LOW, so every dish drawing on
    it under-costs and nothing ever prompts a question. batch_overflow ran
    without its mirror from the day it was written until 2026-08-16.

    Same refusal to put a dollar on it, for the same reason: either the yield is
    too big or a quantity is too small, and picking one is the guess this feed
    exists not to make.
    """
    got = [f for f in _by_cat(feed, "batch_yield")
           if f["id"].startswith("yield-overstated-")]
    assert got, "the mirror rule produced nothing — has yield_overstated been wired in?"
    for f in got:
        assert f["impact_per_year"] is None, f"{f['subject']} put a dollar on a guess"
        assert "?" in f["question"], f"{f['subject']} must ask, not accuse"
        assert "uncosted water" in f["question"], (
            f"{f['subject']}: the question must offer the legitimate explanation "
            f"first — a dough is 62% hydration and a broth is mostly water")


def test_a_price_conflict_reports_a_spread_and_calls_it_one(feed):
    """A spread is not an under-cost, so it goes in the evidence, in words, and
    impact_per_year stays null — that keeps the panel's headline ("$X a year of
    under-cost that has actually been measured") a true sentence.

    WHAT CHANGED 2026-08-21. This test used to say "nobody knows yet which of
    the two prices is wrong". For a conflict whose OUR side is invoice-fed, that
    is no longer true and never really was: Zak — "the lightspeed scrape was just
    to give us a headstart on building our own costbook", and "this whole system
    is removing any link to back office". A scraped figure is not a rival price
    to a purchase, so those conflicts are settled, drop to LOW, and are owned by
    nobody; the gap survives only as a record of how far the old scrape drifted.

    The ones that still bite are where NEITHER side has an invoice — both numbers
    came off the same scrape, so adjudicating between them is theatre. Those stay
    high/medium and the action is to go and find a purchase.

    So severity is now a statement about EVIDENCE, not about size alone."""
    pcs = {f["subject"]: f for f in _by_cat(feed, "price_conflict")}
    # Massenez was ADJUDICATED 2026-08-14 (Back Office's DefaultSize carries an
    # extra zero — 50000 ml on a [5L] cask — so Lightspeed's $0.004833 divided by
    # 50 L of liqueur that does not exist, and our $0.0506 is right), so it is no
    # longer here. The CONTRACT this test exists for is not about Massenez, so it
    # is asserted against whatever is in the family rather than a named member.
    assert pcs, "the family is empty — this contract is untested, not satisfied"
    for name, m in pcs.items():
        # a spread is not an under-cost: nobody knows yet which side is wrong
        assert m["impact_per_year"] is None, name
        assert m["impact_basis"] is None, name
        # the ratio is stated in words, in the sentence a human reads
        assert re.search(r"\d+(\.\d+)?x (above|below)", m["what_is_wrong"]), (
            name, m["what_is_wrong"])
    # and where a spread was measurable it is called one, in the evidence
    spreads = [m for m in pcs.values()
               if any("SPREAD, not a loss" in e for e in m["evidence"])]
    assert spreads, sorted(pcs)
    # Severity now tracks whether a purchase settles it.
    for name, m in pcs.items():
        settled = "invoice already settles it" in m["owner"]
        if settled:
            assert m["severity"] == "low", (
                f"{name}: an invoice-fed rate beats the scrape, so this is not a "
                f"live question — it must not sit above LOW and compete for "
                f"attention with defects nobody has evidence for")
            assert "INVOICE" in m["what_is_wrong"], name
        else:
            assert m["severity"] in ("high", "medium"), name
            # the honest version of an unbacked conflict: it is a coverage gap
            assert "NEITHER side has an invoice" in m["what_is_wrong"], name
            assert "Dev" in m["owner"], (
                f"{name}: with no invoice on either side there is nothing for Zak "
                f"to adjudicate — somebody has to go and find one")


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


def test_a_garnish_is_not_a_pack_and_a_weight_is_not_a_count(feed):
    """The two failure modes that wear the same shape, kept apart.

    Both of these "would inflate" if you multiplied the quantity by the pack
    rate, and before 2026-08-21 the audit could not tell them apart:

        Bad Bitch Martini    1 g   of Edible Flower [Punnet]   -> ONE FLOWER
        Tonkotsu Broth     900 g   of Shallot [Bunch]          -> 900 GRAMS

    The first is a portion of a pack: swapping the unit to "punnet" charges the
    cocktail a whole $10.50 punnet and takes the drink from $4.10 to $14.60.
    The audit must say DO NOT SWAP and ask how many serves a punnet yields.

    The second is an honest weight whose pack weight nobody recorded. Asking
    "how many serves does a bunch give" is the wrong question and reading 900 as
    a count of bunches is the Rosemary Salted Fries failure again. It must take
    the weigh-the-pack path.

    The discriminator is COUNT_SWAP_MAX, not the arithmetic.
    """
    units = [f for f in _by_cat(feed, "feed_defect")
             if "in the wrong unit" in f["subject"]]
    assert units, "no unit defects at all — this contract is untested"

    portions = [f for f in units if "serves per pack" in (f["owner"] or "")]
    weighs = [f for f in units if "weigh one and record" in (f["owner"] or "")]
    assert portions, "the garnish class vanished"
    assert weighs, "the weigh-the-pack class vanished"

    for f in portions:
        assert "DO NOT" in f["action"], (
            f"{f['subject']}: a portion-of-a-pack flag MUST warn against the "
            f"naive unit swap — following it silently raises the dish")
        # every quantity behind a portion flag is a plausible COUNT
        for e in f["evidence"]:
            m = re.search(r":\s*([\d.]+)\s", e)
            if m:
                assert float(m.group(1)) <= 12, (
                    f"{f['subject']}: {m.group(1)} is a weight, not a count of "
                    f"packs — it belongs on the weigh-the-pack path")

    for f in weighs:
        assert "Weigh one" in f["action"], f["subject"]
        assert "serves" not in (f["question"] or ""), (
            f"{f['subject']}: asking how many serves a pack gives is the wrong "
            f"question for a line that genuinely is in grams")


def test_no_recipe_routes_bundles_and_bar_away_from_the_kitchen(feed):
    """A package is not a dish and a mocktail is not chef work.

    "$80 Razzle Dazzle" and "Blind Winos Ticket" will never have a recipe no
    matter who weighs what — their cost is the sum of their components. Sending
    them to the kitchen queue is how the queue stops being believed.
    """
    nr = [f for f in _by_cat(feed, "no_recipe") if f.get("no_recipe_class")]
    assert nr, "no_recipe flags carry no class — has the classifier been dropped?"

    bundles = [f for f in nr if f["no_recipe_class"] == "bundle"]
    assert bundles, "the bundle class is empty"
    for f in bundles:
        assert "Kitchen" not in f["owner"], (
            f"{f['subject']} is a package and cannot be weighed into existence")
        assert "PACKAGE" in f["action"]

    for f in nr:
        if f["no_recipe_class"] == "bar_build":
            assert f["owner"].startswith("Bar"), f["subject"]
        if f["no_recipe_class"] == "dish":
            # the residue is the real chef queue, and it must still say so
            assert "Kitchen" in f["owner"], f["subject"]
