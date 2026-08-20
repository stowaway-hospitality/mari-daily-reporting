"""What a package function actually made, worked out from the tab.

THE PROBLEM THIS SOLVES
-----------------------
Stowaway sells package functions: a guest buys an $80 wristband, the drinks are
poured onto a separate bar tab, and that tab is comped to $0.00 at the end of
the night. Three things follow, all of them verified against the data in this
repo:

  * `$80 Razzle Dazzle` and `$60 Soiree` have NO costed recipe. On 2026-08-08
    the day's product mix carries `$80 Razzle Dazzle, 44.0, $3520.00 inc,
    cost 0.0, lightspeed`. The package books at 100% GP.
  * the drinks ring as ordinary Tap Beer / Cocktails / Wine and are
    indistinguishable from normal trade.
  * so a function's real gross profit exists NOWHERE in the business. The only
    time it had been worked out, it took a full manual transaction sweep.

`gross_profit()` below is that sweep, made repeatable: the ticket revenue on one
side, the tab's own line items costed against the book on the other.

WHAT IS PURE AND WHY THAT MATTERS
---------------------------------
This module reads no file, opens no socket and knows nothing about the cost
book's format. The book arrives as a `costs` callable -- `product name -> cost
per unit sold ex-GST, or None`. The pipeline hands it one backed by
`scripts/cogs_blend`, which is the reader the P&L already uses; a test hands it
a frozen dict of the costs a particular night was actually worked out on.

That is not tidiness. The cost book carries NO effective date -- `cogs_blend
._load_book_costs` says so in its own docstring -- so it answers "what does this
cost today", never "what did it cost in August". Measured on this repo on
2026-08-20, twelve days after the fixture night: Rooster Rojo Blanco Tequila
[House] has moved $1.9641 -> $2.6065 and Lychee Martini $3.9140 -> $5.0328.
A function's GP is therefore only reproducible against a DATED book, and the
only way to have one is to pass it in. See `modules/functions/feed.md`.

THE GST RULE, WHICH IS NOT NEGOTIABLE
-------------------------------------
Menu prices are inc-GST. Costs are ex-GST. ex = inc / 1.1. GP% = (revenue ex -
COGS ex) / revenue ex. Every figure here is `Decimal` and every published money
figure is an integer of cents. There is no float in the arithmetic.

WHAT IS SOFT, AND WHY IT IS SEPARABLE
-------------------------------------
Two things in the answer are estimates rather than measurements, and both are
reported as their own line rather than folded into one number:

  * THE MIXER. House spirits are costed in Lightspeed as the nip only -- the
    recipe holds the spirit and nothing else. A White Light Pure Vodka [House]
    is $1.5208 and the lemonade in it is not in that figure. So a per-pour
    mixer blend is ADDED BY ASSUMPTION, and `gp_pct_ex_mixer` is the same sum
    with it taken back out. On the two nights in the fixture it is worth 6.3
    and 7.7 points of GP, which is far too much to leave as a footnote.
  * UNCOSTED LINES. A product with no recipe is not a product that is free.
    Treating it as zero understates COGS and flatters GP, silently and in the
    direction that makes a decision look better than it is. Every unresolved
    line is counted, named and returned, so the caller can say out loud that
    the cost is a lower bound.

Food gets the same treatment for the same reason: kitchen items are uncosted
repo-wide and `data/cost_book_flags.json` warns that food recipes price plated
weight at raw purchase rate. A function with a food component therefore gets a
BEVERAGE-ONLY GP, with the food revenue taken OFF the top line rather than
credited against a cost nobody has.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Sequence

__all__ = [
    "Line", "FunctionNight", "MixerAssumption",
    "HOUSE_SUFFIX", "is_house_pour", "HOUSE_MIXER_BLEND", "MIXER_COMPONENTS",
    "BEVERAGE_BENCHMARK_GP_PCT", "GST_DIVISOR",
    "gross_profit",
]

# ---------------------------------------------------------------- constants

GST_DIVISOR = Decimal("1.1")

#: The venue's beverage run rate, the thing a function DISPLACES. A package
#: function does not add trade to an empty room -- it takes a Saturday night the
#: bar would have traded anyway and swaps high-margin ordinary trade for
#: lower-margin package trade. "It made a profit" and "it was worth doing" are
#: different questions, and this constant is what turns the first into the
#: second.
BEVERAGE_BENCHMARK_GP_PCT = Decimal("76.4")

#: The suffix Lightspeed Back Office puts on a house pour. Checked against the
#: real product list, not assumed: `data/bo_exports/stowaway_products.csv` has
#: exactly EIGHT products carrying it -- Appleton Rum, Bacardi Blanca, Bombay
#: Dry, Buffalo Trace Bourbon, Monkey Shoulder Scotch, Rooster Rojo Blanco
#: Tequila, Sailor Jerry, White Light Pure Vodka -- all at $11.00, all in
#: CategoryNames "STOW FAST,SPIRITS". Jack Daniels ($12.00) and Baileys Irish
#: Cream ($11.00) sit in "SPIRITS" WITHOUT the suffix and are therefore not
#: house pours by this rule. That distinction decides a real number; see the
#: note on `MixerAssumption.also`.
HOUSE_SUFFIX = "[House]"

#: The three mixers a house pour is served with, ex-GST per glass, and the
#: blended figure applied per pour.
#:
#: STATED AS AN ASSUMPTION, BECAUSE IT IS ONE. Nobody has measured the mix. The
#: blend is not a mean of the three below (that would be $0.9146) -- it is the
#: figure the original hand sweep used, and it happens to equal the pink
#: lemonade line exactly. That is worth knowing before anyone quotes it as
#: measured. `Tonic Glass` has no costed recipe at all, so tonic is not even in
#: the range.
MIXER_COMPONENTS = {
    "Lemonade Glass": Decimal("0.8032"),
    "Pink Lemonade Glass": Decimal("0.9483"),
    "Ginger Beer Glass": Decimal("0.9922"),
}
HOUSE_MIXER_BLEND = Decimal("0.9483")


def is_house_pour(product: str) -> bool:
    """Is this POS product a house spirit -- a nip, costed without its mixer?"""
    return (product or "").strip().endswith(HOUSE_SUFFIX)


# ---------------------------------------------------------------- the inputs

@dataclass(frozen=True)
class Line:
    """One line off the comped tab: what was poured, how many, what the menu
    would have charged for it inc-GST (the whole line, not the unit)."""
    product: str
    qty: int
    menu_value_inc: Decimal

    def __post_init__(self):
        if not isinstance(self.menu_value_inc, Decimal):
            raise TypeError(
                f"{self.product}: menu_value_inc must be Decimal, not "
                f"{type(self.menu_value_inc).__name__}. Money is Decimal in "
                f"this repo -- a float here is a rounding error nobody sees.")


@dataclass(frozen=True)
class MixerAssumption:
    """The per-pour mixer cost, and which pours it is applied to.

    `also` exists because the two hand-verified nights disagree with the
    `[House]` rule by exactly one pour, and inventing a rule to close the gap
    would be worse than naming it:

        Function B, 8 Aug 2026 -- [House] lines are White Light 55, Sailor
        Jerry 24, Appleton 4, Rooster Rojo 1 = 84 pours. The hand sweep's
        mixer figure is $79.66, and 79.66 / 0.9483 = 84.00. Agreed.

        Function A, same night -- [House] lines are White Light 63, Bacardi
        Blanca 49, Rooster Rojo 7 = 119 pours. The hand sweep's mixer figure
        is $113.80, and 113.80 / 0.9483 = 120.00. One pour more than the rule
        finds.

    The only single-unit spirit line on that tab is one Jack Daniels, which is
    a nip-only recipe ($2.2633) served long exactly like a house pour but which
    does not carry the suffix. So the sweep almost certainly gave it a mixer.
    "Almost certainly" is not a rule, so the DEFAULT here stays the suffix, the
    fixture pins what the sweep actually did, and the difference is one line in
    a test rather than a fudge inside the maths. It is worth $0.95 and 0.05
    points of GP.
    """
    per_pour_ex: Decimal = HOUSE_MIXER_BLEND
    also: frozenset = frozenset()

    def applies(self, product: str) -> bool:
        return is_house_pour(product) or product in self.also


@dataclass(frozen=True)
class FunctionNight:
    """One function, as the tab and the brief between them describe it."""
    name: str
    date: str                                   # ISO, the night it ran
    heads: int                                  # who actually came
    package_price_inc: Decimal                  # per head, inc-GST
    lines: Sequence[Line]                       # the comped tab
    package_hours: Decimal | None = None        # the package's stated duration
    food_revenue_inc: Decimal = Decimal("0")    # of the ticket, the food part
    booked_guests: int | None = None            # what the brief said
    tickets_sold: int | None = None             # if it differs from heads
    pos_refs: str = ""                          # how the tab was found
    venue: str = "stowaway"


# ---------------------------------------------------------------- rounding

_CENT = Decimal("0.01")
_PCT = Decimal("0.01")


def _cents(d: Decimal) -> int:
    """Dollars -> whole cents, half-up. Published money is an integer of cents
    so that no consumer of the feed can re-round it a second, different way."""
    return int((d.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())


def _ex(inc: Decimal) -> Decimal:
    return inc / GST_DIVISOR


def _pct(num: Decimal, den: Decimal):
    if den == 0:
        return None
    return (num / den * 100).quantize(_PCT, rounding=ROUND_HALF_UP)


def _ratio(num: Decimal, den: Decimal):
    if den == 0:
        return None
    return (num / den).quantize(_PCT, rounding=ROUND_HALF_UP)


def _int_round(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _tenth(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- the caveats

def _caveats(mixer_est_ex: Decimal, gp_pct, gp_pct_ex_mixer,
             food_revenue_inc: Decimal, uncosted) -> list:
    """Everything that qualifies the figure, as machine codes with prose.

    The order is deliberate: softest assumption first, then the two structural
    facts. `dashboard/functions/functions.js` refuses to print a GP percentage
    at all unless this list is non-empty, so an empty return here is not a
    tidier report -- it is a report with no number on it.
    """
    out = []
    if mixer_est_ex > 0 and gp_pct is not None and gp_pct_ex_mixer is not None:
        pts = (gp_pct_ex_mixer - gp_pct).quantize(_PCT, rounding=ROUND_HALF_UP)
        out.append({
            "code": "mixer_estimated",
            "gp_pct_ex_mixer": float(gp_pct_ex_mixer),
            "gp_pct_points": float(pts),
            "note": "the mixer cost is an estimate, not a repo figure -- house "
                    "spirits are costed as the nip only, so a per-pour mixer "
                    "blend is added by assumption",
            "effect": f"strip it out and GP rises {_tenth(pts)} points, "
                      f"to {_tenth(gp_pct_ex_mixer)}%",
        })
    if uncosted:
        n = sum(u["qty"] for u in uncosted)
        names = ", ".join(u["product"] for u in uncosted)
        out.append({
            "code": "uncosted_lines",
            "uncosted_drinks": n,
            "uncosted_products": [u["product"] for u in uncosted],
            "note": f"{len(uncosted)} product(s) on the tab have no costed "
                    f"recipe and are carrying no cost at all in the figure "
                    f"above -- {names}",
            "effect": f"{n} drink(s) were poured for nothing, so the COGS is a "
                      f"lower bound and the GP an upper one",
        })
    if food_revenue_inc > 0:
        out.append({
            "code": "food_cogs_unknown",
            "food_revenue_inc_cents": _cents(food_revenue_inc),
            "note": "food COGS is unknown -- kitchen items are uncosted, so "
                    "this is a beverage-only GP, not a blended one",
            "effect": "food revenue is excluded from the GP above rather than "
                      "being credited against a cost nobody has",
        })
    out.append({
        "code": "package_sku_uncosted",
        "note": "the package SKUs have no costed recipe and book at 100% GP in "
                "the P&L, which is why functions look free until someone works "
                "one out by hand",
        "effect": "this figure does not appear anywhere in the P&L",
    })
    return out


# ---------------------------------------------------------------- the sum

def gross_profit(night: FunctionNight,
                 costs: Callable[[str], object],
                 mixer: MixerAssumption = None,
                 benchmark_gp_pct: Decimal = BEVERAGE_BENCHMARK_GP_PCT,
                 cost_book_as_of: str = None) -> dict:
    """The gross-profit report for one function.

    `costs(product)` returns our cost per unit sold, ex-GST, or None when the
    book has no recipe for it. None is NOT zero and is never treated as zero.

    Returns a plain dict in the shape `dashboard/functions/functions.js`
    renders -- money as integer cents, percentages as floats to two places,
    `caveats` as a list of `{code, note, effect, ...}`.
    """
    mixer = mixer if mixer is not None else MixerAssumption()

    # ---- revenue. The ticket, not the tab. The tab was comped to $0.00, so
    # its menu value is what was GIVEN AWAY, not what was earned.
    revenue_inc = night.package_price_inc * night.heads
    food_inc = night.food_revenue_inc or Decimal("0")
    if food_inc > revenue_inc:
        raise ValueError(f"{night.name}: food revenue ${food_inc} exceeds the "
                         f"ticket revenue ${revenue_inc}")
    bev_inc = revenue_inc - food_inc

    # ---- the tab
    drinks = 0
    menu_value_inc = Decimal("0")
    drinks_cogs_ex = Decimal("0")
    mixer_pours = 0
    house_pours = 0
    uncosted = []
    priced = []

    for ln in night.lines:
        drinks += ln.qty
        menu_value_inc += ln.menu_value_inc
        if is_house_pour(ln.product):
            house_pours += ln.qty
        if mixer.applies(ln.product):
            mixer_pours += ln.qty
        unit = costs(ln.product)
        if unit is None:
            # NOT zero. Named, counted, returned.
            uncosted.append({"product": ln.product, "qty": ln.qty,
                             "menu_value_inc_cents": _cents(ln.menu_value_inc)})
            continue
        unit = Decimal(str(unit))
        line_cost = unit * ln.qty
        drinks_cogs_ex += line_cost
        priced.append({"product": ln.product, "qty": ln.qty,
                       "unit_cost_ex": str(unit),
                       "line_cost_ex_cents": _cents(line_cost)})

    mixer_est_ex = mixer.per_pour_ex * mixer_pours

    # Each published component is rounded ONCE, then the total is their sum.
    # Rounding the sum instead would put the total a cent away from the two
    # figures printed beside it, which is the kind of discrepancy that gets a
    # whole report disbelieved.
    cogs_ex_cents = _cents(drinks_cogs_ex)
    mixer_est_ex_cents = _cents(mixer_est_ex)
    total_cogs_ex_cents = cogs_ex_cents + mixer_est_ex_cents

    revenue_ex_cents = _cents(_ex(revenue_inc))
    bev_revenue_ex_cents = _cents(_ex(bev_inc))
    gross_profit_ex_cents = bev_revenue_ex_cents - total_cogs_ex_cents

    bev_ex = Decimal(bev_revenue_ex_cents)
    gp_pct = _pct(Decimal(gross_profit_ex_cents), bev_ex)
    gp_pct_ex_mixer = (_pct(bev_ex - Decimal(cogs_ex_cents), bev_ex)
                       if mixer_est_ex_cents else None)

    hours = night.package_hours
    heads = night.heads
    menu_cents = _cents(menu_value_inc)

    benchmark_gp_ex_cents = _int_round(bev_ex * benchmark_gp_pct / 100)
    margin_foregone = benchmark_gp_ex_cents - gross_profit_ex_cents

    return {
        # --- what the night was
        "name": night.name,
        "date": night.date,
        "venue": night.venue,
        "actual_heads": heads,
        "booked_guests": night.booked_guests,
        "tickets_sold": night.tickets_sold if night.tickets_sold is not None else heads,
        "package_price_inc_cents": _cents(night.package_price_inc),
        "package_hours": float(hours) if hours is not None else None,
        "pos_refs": night.pos_refs,

        # --- the top line
        "revenue_inc_cents": _cents(revenue_inc),
        "revenue_ex_cents": revenue_ex_cents,
        "food_revenue_inc_cents": _cents(food_inc) if food_inc > 0 else None,
        "bev_revenue_inc_cents": _cents(bev_inc),
        "bev_revenue_ex_cents": bev_revenue_ex_cents,

        # --- the tab
        "drinks_poured": drinks,
        "menu_value_inc_cents": menu_cents,
        "menu_value_inc_cents_per_head": (_int_round(Decimal(menu_cents) / Decimal(heads))
                                          if heads else None),
        "house_pours": house_pours,
        "mixer_pours": mixer_pours,
        "drinks_per_head": (float(_ratio(Decimal(drinks), Decimal(heads)))
                            if heads else None),
        "drinks_per_hour": (float(_ratio(Decimal(drinks), Decimal(str(hours))))
                            if hours else None),

        # --- the cost
        "cogs_ex_cents": cogs_ex_cents,
        "mixer_est_ex_cents": mixer_est_ex_cents,
        # Four places, as a string. Rounded to whole cents this would be
# "95c a pour", which is not the figure the sum used.
        "mixer_per_pour_ex": str(mixer.per_pour_ex),
        "total_cogs_ex_cents": total_cogs_ex_cents,
        "cogs_ex_cents_per_head": (_int_round(Decimal(total_cogs_ex_cents) / Decimal(heads))
                                   if heads else None),
        "uncosted_lines": uncosted,
        "uncosted_drinks": sum(u["qty"] for u in uncosted),
        "uncosted_menu_value_inc_cents": sum(u["menu_value_inc_cents"] for u in uncosted),
        "priced_lines": priced,
        "cost_book_as_of": cost_book_as_of,

        # --- the answer
        "gross_profit_ex_cents": gross_profit_ex_cents,
        "gp_pct": float(gp_pct) if gp_pct is not None else None,
        "gp_pct_ex_mixer": float(gp_pct_ex_mixer) if gp_pct_ex_mixer is not None else None,
        "gp_basis": "beverage",

        # --- was it worth doing
        "benchmark_gp_pct": float(benchmark_gp_pct),
        "margin_foregone_ex_cents": margin_foregone,
        "out_earn_ratio": (float(_ratio(benchmark_gp_pct, gp_pct)) if gp_pct else None),

        # --- and what qualifies all of it
        "caveats": _caveats(mixer_est_ex, gp_pct, gp_pct_ex_mixer, food_inc, uncosted),
    }
