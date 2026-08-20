"""The two nights of 8 August 2026, reproduced from raw line items.

WHY THESE TWO
-------------
They are the only two functions anybody has ever costed. Both were worked out
by hand off the POS, transaction by transaction, because nothing in this
platform could do it: `$80 Razzle Dazzle` has no costed recipe, so the P&L
books the whole $3,520 that day at 100% GP and a function looks free.

They are the regression fixture in the strict sense -- the module must arrive
at these figures from `product, qty, menu value` and a cost book, not from
stored totals. Every number below was hand-verified before this module existed,
so a change that cannot draw them is wrong and it is not the fixture that is
wrong.

THE CROSS-CHECK THAT IS NOT IN THIS FILE
----------------------------------------
`data/function_tabs/cost_book_2026-08-08.json` pins the unit costs the sweep
used. That is not a convenience -- the live book has no effective date and has
already moved underneath these nights. See `test_live_book_still_finds_these`
below, which runs the same tabs against the CURRENT book and reports the
disagreement instead of hiding it.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from modules.functions.gross_profit import (
    HOUSE_MIXER_BLEND, MIXER_COMPONENTS, FunctionNight, Line, MixerAssumption,
    gross_profit, is_house_pour)
from modules.functions.pipeline.build_functions_gp import dated_book, live_book, read_tab

ROOT = Path(__file__).resolve().parents[3]
TABS = ROOT / "data" / "function_tabs"

DAZZLE = TABS / "2026-08-08_dazzle_drinks.json"
HARRY = TABS / "2026-08-08_harry.json"


def report(tab_path, mixer=None):
    night = read_tab(tab_path)
    costs, as_of, pinned = dated_book(night.date)
    return gross_profit(night, costs, mixer=mixer or pinned, cost_book_as_of=as_of)


# ------------------------------------------------------ Function A: 25 heads

# $80 x 25 = $2,000 inc, all of it drinks. 239 drinks over the bar with a menu
# value of $3,599.00 -- so the tab gave away $1,599 more than the tickets took,
# and the function is still profitable. That is the whole reason the number is
# worth computing.
DAZZLE_EXPECTED = {
    "actual_heads": 25,
    "revenue_inc_cents": 200000,
    "revenue_ex_cents": 181818,          # 2000 / 1.1
    "food_revenue_inc_cents": None,
    "bev_revenue_inc_cents": 200000,
    "bev_revenue_ex_cents": 181818,
    "drinks_poured": 239,
    "menu_value_inc_cents": 359900,
    "menu_value_inc_cents_per_head": 14396,
    "house_pours": 119,                  # White Light 63 + Bacardi 49 + Rooster 7
    "mixer_pours": 120,                  # + the one Jack Daniels the sweep costed
    "cogs_ex_cents": 62548,
    "mixer_est_ex_cents": 11380,         # 120 x 0.9483
    "total_cogs_ex_cents": 73928,
    "cogs_ex_cents_per_head": 2957,
    "gross_profit_ex_cents": 107890,
    "gp_pct": 59.34,
    "gp_pct_ex_mixer": 65.6,
    "gp_basis": "beverage",
    "drinks_per_head": 9.56,
    "package_hours": 3.0,
    "drinks_per_hour": 79.67,
    "benchmark_gp_pct": 76.4,
    "margin_foregone_ex_cents": 31019,
    "out_earn_ratio": 1.29,
    "uncosted_drinks": 4,
    "uncosted_menu_value_inc_cents": 3500,
    "cost_book_as_of": "2026-08-08",
}

# $80 x 19 = $1,520 inc, of which $380 is food. So the GP is a BEVERAGE GP on
# $1,140 and not a GP on $1,520 -- the food revenue comes off the top line
# rather than being credited against a kitchen cost nobody has.
HARRY_EXPECTED = {
    "actual_heads": 19,
    "revenue_inc_cents": 152000,
    "revenue_ex_cents": 138182,          # 1520 / 1.1
    "food_revenue_inc_cents": 38000,
    "bev_revenue_inc_cents": 114000,
    "bev_revenue_ex_cents": 103636,      # 1140 / 1.1
    "drinks_poured": 149,
    "menu_value_inc_cents": 182050,
    "menu_value_inc_cents_per_head": 9582,
    "house_pours": 84,                   # 55 + 24 + 4 + 1
    "mixer_pours": 84,                   # and the [House] rule agrees, here
    "cogs_ex_cents": 33256,
    "mixer_est_ex_cents": 7966,          # 84 x 0.9483
    "total_cogs_ex_cents": 41222,
    "cogs_ex_cents_per_head": 2170,
    "gross_profit_ex_cents": 62414,
    "gp_pct": 60.22,
    "gp_pct_ex_mixer": 67.91,
    "gp_basis": "beverage",
    "drinks_per_head": 7.84,
    "package_hours": 3.0,
    "drinks_per_hour": 49.67,
    "benchmark_gp_pct": 76.4,
    "margin_foregone_ex_cents": 16764,
    "out_earn_ratio": 1.27,
    "uncosted_drinks": 0,
    "uncosted_menu_value_inc_cents": 0,
    "cost_book_as_of": "2026-08-08",
}


@pytest.mark.parametrize("tab,expected,label", [
    (DAZZLE, DAZZLE_EXPECTED, "Dazzle drinks"),
    (HARRY, HARRY_EXPECTED, "Harry"),
])
def test_the_night_reproduces_from_its_line_items(tab, expected, label):
    got = report(tab)
    assert got["name"] == label
    wrong = {k: (v, got.get(k)) for k, v in expected.items() if got.get(k) != v}
    assert not wrong, f"{label}: expected vs got -> {wrong}"


def test_the_sum_holds_together():
    """Revenue minus cost is the profit, and the parts add to the whole.

    Cheap, and it is the check that catches a component being rounded twice.
    """
    for tab in (DAZZLE, HARRY):
        r = report(tab)
        assert r["cogs_ex_cents"] + r["mixer_est_ex_cents"] == r["total_cogs_ex_cents"]
        assert r["bev_revenue_ex_cents"] - r["total_cogs_ex_cents"] == r["gross_profit_ex_cents"]
        assert sum(l["line_cost_ex_cents"] for l in r["priced_lines"]) == pytest.approx(
            r["cogs_ex_cents"], abs=len(r["priced_lines"]))   # per-line rounding only
        counted = (sum(l["qty"] for l in r["priced_lines"])
                   + sum(l["qty"] for l in r["uncosted_lines"]))
        assert counted == r["drinks_poured"]


def test_gst_comes_off_the_revenue_and_never_off_the_cost():
    """inc / 1.1 = ex, on the revenue only. Costs are already ex-GST."""
    r = report(HARRY)
    assert r["revenue_ex_cents"] == 138182                      # 152000 / 1.1
    assert r["bev_revenue_ex_cents"] == 103636                  # 114000 / 1.1
    # the ratio the whole report rests on
    assert Decimal(r["bev_revenue_inc_cents"]) / Decimal("1.1") == pytest.approx(
        Decimal(r["bev_revenue_ex_cents"]), abs=1)


def test_money_must_be_decimal():
    """A float here is a rounding error nobody ever sees. Refuse it at the door."""
    with pytest.raises(TypeError) as e:
        Line("Beetle Juice", 45, 990.00)
    assert "Decimal" in str(e.value)
    Line("Beetle Juice", 45, Decimal("990.00"))                 # and this is fine


# ------------------------------------------------- the mixer, held separately

def test_the_mixer_is_separable_and_worth_six_to_eight_points():
    """Report the GP with and without it, because it moves the answer a lot and
    it is an estimate, not a measurement."""
    d, h = report(DAZZLE), report(HARRY)
    assert d["gp_pct"] == 59.34 and d["gp_pct_ex_mixer"] == 65.6      # 6.26 points
    assert h["gp_pct"] == 60.22 and h["gp_pct_ex_mixer"] == 67.91     # 7.69 points
    for r, pts in ((d, 6.26), (h, 7.69)):
        cav = next(c for c in r["caveats"] if c["code"] == "mixer_estimated")
        assert cav["gp_pct_points"] == pts
        assert cav["gp_pct_ex_mixer"] == r["gp_pct_ex_mixer"]
        assert "estimate" in cav["note"]


def test_the_blend_is_an_assumption_and_says_so():
    """$0.9483 is not the mean of its three components -- it is the pink
    lemonade line exactly. Worth knowing before anyone quotes it as measured.
    Tonic is not even in the range: `Tonic Glass` has no costed recipe."""
    assert HOUSE_MIXER_BLEND == MIXER_COMPONENTS["Pink Lemonade Glass"]
    mean = sum(MIXER_COMPONENTS.values()) / len(MIXER_COMPONENTS)
    assert HOUSE_MIXER_BLEND != mean
    assert "Tonic Glass" not in MIXER_COMPONENTS


def test_the_house_rule_finds_119_and_the_sweep_costed_120():
    """THE ONE DISAGREEMENT WITH THE HAND FIGURES, kept visible.

    The `[House]` suffix is the rule, and it is checked against the real
    product list: `data/bo_exports/stowaway_products.csv` carries it on exactly
    eight products, all $11.00, all in "STOW FAST,SPIRITS". By that rule the
    Dazzle drinks tab has 119 mixer-bearing pours. The hand sweep costed 120 --
    $113.80 / $0.9483 = 120.00 exactly.

    One Jack Daniels is the only line that reconciles it: a nip-only recipe
    ($2.2633) served long just like a house pour, at $12.00, without the
    suffix. So the sweep almost certainly gave it a mixer.

    Rather than widen the rule to fit one pour, the night's cost book pins what
    the sweep DID and this test states the size of the gap: $0.95 and five
    hundredths of a point.
    """
    night = read_tab(DAZZLE)
    costs, _, pinned = dated_book(night.date)

    by_the_rule = gross_profit(night, costs, mixer=MixerAssumption())
    as_swept = gross_profit(night, costs, mixer=pinned)

    assert by_the_rule["mixer_pours"] == 119
    assert as_swept["mixer_pours"] == 120
    assert pinned.also == frozenset({"Jack Daniels"})
    assert not is_house_pour("Jack Daniels")

    assert by_the_rule["mixer_est_ex_cents"] == 11285          # 119 x 0.9483
    assert as_swept["mixer_est_ex_cents"] == 11380             # 120 x 0.9483
    assert as_swept["mixer_est_ex_cents"] - by_the_rule["mixer_est_ex_cents"] == 95
    assert by_the_rule["gp_pct"] == 59.39 and as_swept["gp_pct"] == 59.34

    # Function B does not discriminate: it has no non-house spirit line at all,
    # so both bases give 84 and the hand figure agrees with the rule.
    hn = read_tab(HARRY)
    hcosts, _, _ = dated_book(hn.date)
    assert gross_profit(hn, hcosts, mixer=MixerAssumption())["mixer_est_ex_cents"] == 7966


# ------------------------------------------- uncosted lines, named not zeroed

def test_uncosted_lines_are_named_and_counted_not_treated_as_free():
    """A product with no recipe is not a product that is free.

    Costing it at zero understates COGS and flatters GP, silently, in the
    direction that makes a decision look better than it is -- and leaves no
    trace at all, because a zero line looks exactly like a cheap one.
    """
    r = report(DAZZLE)
    named = {u["product"] for u in r["uncosted_lines"]}
    assert named == {"Corona", "Better Beer Tin", "Fresh Lime Soda"}
    assert r["uncosted_drinks"] == 4
    assert r["uncosted_menu_value_inc_cents"] == 3500

    cav = next(c for c in r["caveats"] if c["code"] == "uncosted_lines")
    assert set(cav["uncosted_products"]) == named
    assert cav["uncosted_drinks"] == 4
    assert "lower bound" in cav["effect"]

    # None of them contributed a cent of cost, which is exactly why they have
    # to be reported: the figure cannot show what is missing from it.
    assert all(u["product"] not in {p["product"] for p in r["priced_lines"]}
               for u in r["uncosted_lines"])

    # Harry's tab is fully costed, so no such caveat is emitted.
    assert not [c for c in report(HARRY)["caveats"] if c["code"] == "uncosted_lines"]


# -------------------------------------------------------- food, taken off top

def test_food_is_excluded_from_the_top_line_not_credited_against_a_cost():
    """Kitchen items are uncosted repo-wide, and `cost_book_flags` warns that
    food recipes price plated weight at raw purchase rate. So a function with a
    food component gets a beverage-only GP with the food revenue removed."""
    r = report(HARRY)
    assert r["revenue_inc_cents"] == 152000
    assert r["food_revenue_inc_cents"] == 38000
    assert r["bev_revenue_inc_cents"] == 152000 - 38000
    assert r["gp_basis"] == "beverage"
    cav = next(c for c in r["caveats"] if c["code"] == "food_cogs_unknown")
    assert cav["food_revenue_inc_cents"] == 38000
    assert "excluded" in cav["effect"]

    # And the GP is on the beverage half. On the whole $1,520 it would read
    # 70.2% and be meaningless.
    assert r["gp_pct"] == 60.22

    # No food, no caveat. The list states what is true of THIS night.
    assert not [c for c in report(DAZZLE)["caveats"] if c["code"] == "food_cogs_unknown"]


def test_food_cannot_exceed_the_ticket():
    night = read_tab(HARRY)
    costs, _, mixer = dated_book(night.date)
    bad = FunctionNight(**{**night.__dict__, "food_revenue_inc": Decimal("9999")})
    with pytest.raises(ValueError):
        gross_profit(bad, costs, mixer=mixer)


# ------------------------------------------------- was it worth doing at all

def test_the_displaced_trade_comparison():
    """A function does not fill an empty room. It takes a Saturday the bar
    would have traded anyway and swaps 76.4% beverage margin for package
    margin, so "it made a profit" is not the same question as "it was worth
    doing"."""
    d = report(DAZZLE)
    # $1,818.18 of beverage revenue at the run rate would have made $1,389.09.
    # It made $1,078.90. The gap is the cost of the booking.
    assert d["margin_foregone_ex_cents"] == 31019
    # ...and the function has to take 1.29x what the displaced trade would have
    # to break even on gross profit.
    assert d["out_earn_ratio"] == 1.29
    assert report(HARRY)["out_earn_ratio"] == 1.27


def test_a_percentage_never_travels_without_its_caveats():
    """`gpFigureHTML()` in dashboard/functions/functions.js draws a refusal
    instead of a number when the caveat list is empty. This end must therefore
    never produce a figure with an empty list -- and it cannot: the package SKU
    is uncosted on every function that exists."""
    for tab in (DAZZLE, HARRY):
        r = report(tab)
        assert r["gp_pct"] is not None
        assert r["caveats"], "a GP with no caveats renders as a refusal"
        assert r["caveats"][-1]["code"] == "package_sku_uncosted"
        assert all(c.get("note") for c in r["caveats"])


# ----------------------------------------- the live book, and what it says now

# Products on these two tabs that the CURRENT book cannot price. Documented
# rather than asserted exactly, because this list should only ever SHRINK: a
# name that starts failing is a regression, a name that stops failing is
# somebody's good day in the cost book and must not turn CI red.
KNOWN_UNRESOLVED_LIVE = {
    # No costed recipe anywhere, on any book. Genuinely uncosted.
    "Fresh Lime Soda",
    # NOT uncosted -- MIS-NAMED. The book holds "Bacardi Blanca White Rum
    # [House]" at $2.1379, which is the sweep's figure to the cent, but the POS
    # sells "Bacardi Blanca [House]" and `_stripped_key` cannot bridge them
    # because "white" is not a category word. This is a live P&L defect, not a
    # gap in this module: data/products_daily/2026.csv carries
    # `2026-08-08,stow,...,Bacardi Blanca [House],9.0,...,lightspeed` -- every
    # pour falling through to Lightspeed's figure while a costed recipe sits in
    # the book. The fix is one confirmed line in
    # data/product_recipe_aliases.yaml, which is cost-book's file and whose own
    # header says an entry lands only once a human names it. Reported, not
    # guessed at from here.
    "Bacardi Blanca [House]",
}


def test_live_book_still_finds_these():
    """Run the same two tabs against the CURRENT book and say what it can't
    price. Structural, not numeric: this must not go red the day someone
    recosts a recipe."""
    costs, as_of, _ = live_book("stowaway")
    assert as_of == "live"
    unresolved = set()
    for tab in (DAZZLE, HARRY):
        night = read_tab(tab)
        r = gross_profit(night, costs, cost_book_as_of=as_of)
        # whatever it cannot price, it NAMES -- it never silently zeroes
        assert {u["product"] for u in r["uncosted_lines"]} == {
            l.product for l in night.lines if costs(l.product) is None}
        unresolved |= {u["product"] for u in r["uncosted_lines"]}
    new = unresolved - KNOWN_UNRESOLVED_LIVE
    assert not new, (
        f"the live cost book has stopped pricing {sorted(new)}. Either a "
        f"recipe was renamed or one was deleted; either way the P&L is now "
        f"costing those pours off Lightspeed's figure too.")


def test_the_dated_book_exists_because_the_live_one_has_moved():
    """The reason `cost_book_2026-08-08.json` is in the repo at all.

    Printed, not asserted: these gaps are the cost book doing its job. What is
    asserted is only that the pinned book is CLOSED -- a product it does not
    price is reported uncosted rather than quietly filled in from today.
    """
    pinned, as_of, _ = dated_book("2026-08-08")
    live, _, _ = live_book("stowaway")
    assert as_of == "2026-08-08"
    moved = []
    for tab in (DAZZLE, HARRY):
        for l in read_tab(tab).lines:
            p, n = pinned(l.product), live(l.product)
            if p is not None and n is not None and Decimal(str(p)) != Decimal(str(n)):
                moved.append((l.product, str(p), str(n)))
    if moved:
        print("\ncost book has moved since 2026-08-08:")
        for name, was, now in sorted(moved):
            print(f"  {name:42} {was} -> {now}")
    # The pinned book prices nothing it was not given.
    assert pinned("Corona") is None and pinned("Better Beer Tin") is None
    assert live("Corona") is not None, (
        "Corona was uncosted on the night and is costed now -- which is why a "
        "function's GP is only reproducible against a dated book")
