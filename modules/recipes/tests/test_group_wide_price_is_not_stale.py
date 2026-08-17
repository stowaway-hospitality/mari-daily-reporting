"""A cost lookup with no venue preference must return the NEWEST price.

THE BUG
-------
`CostSeries.as_of` walked its venue buckets in dict order and returned the first
one with a hit, trying the untagged bucket first. So an ingredient whose every
real invoice arrives on a venue account answered from whatever stale untagged
seed happened to exist — forever, no matter how many invoices landed after it.

On 2026-08-16 that was 15 ingredients across 63 dishes. Broccolini was being
costed at a **2 January** price of $4.61 while the 12 August invoice on the
Harry Gatos account said $3.17. Most were stale LOW, which understates cost and
overstates GP — the direction CLAUDE.md names as the one nobody investigates,
because a number that flatters you never prompts a question.

WHY THE NEWEST IS THE RIGHT ANSWER
----------------------------------
T6 (Zak, 15 Aug 2026): "the whole group pays the same costs per ingredient."
Venue on a cost row is PROVENANCE — which account bought it — never a cost
dimension. So when no venue preference has been expressed, there is nothing to
prefer and the newest observation simply is the price.

WHAT DID NOT CHANGE
-------------------
An explicitly requested venue still wins outright when it has an observation on
or before the day. An invoice from the account that actually bought the thing is
the best evidence available and the documented rule stays.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.domain import CostObservation, CostSeries  # noqa: E402


def _obs(ing, day, cost, venue, unit="g"):
    return CostObservation(ingredient=ing, observed_on=day,
                           cost_per_unit=Decimal(str(cost)), unit=unit,
                           venue=venue, source_invoice="t")


ON = date(2026, 8, 16)


def test_a_stale_untagged_seed_does_not_outrank_a_recent_invoice():
    """The broccolini case, in miniature: a January seed with no venue against an
    August invoice on the Harry Gatos account."""
    cs = CostSeries([
        _obs("x", date(2026, 1, 2), "4.61", None),
        _obs("x", date(2026, 8, 12), "3.17", "harry_gatos"),
    ], purchasable_to_ingredient={})
    assert cs.as_of("x", ON).cost_per_unit == Decimal("3.17")


def test_an_explicit_venue_does_NOT_pin_a_stale_price():
    """T6 finished the job on 2026-08-17: venue is provenance, never a cost
    dimension, so the newest price wins even when a venue was named.

    Preferring the asked-for venue sounds like better evidence and is not — it
    meant Stowaway kept paying 20 July's price for an onion because the 1 August
    invoice happened to land on Marilyna's account, and that one ingredient
    froze 57 recipe lines in the migration."""
    cs = CostSeries([
        _obs("x", date(2026, 7, 1), "1.00", "stowaway"),
        _obs("x", date(2026, 8, 12), "9.99", "harry_gatos"),
    ], purchasable_to_ingredient={})
    assert cs.as_of("x", ON, venue="stowaway").cost_per_unit == Decimal("9.99")


def test_but_the_asked_for_venue_still_fixes_the_UNIT():
    """Venue-blind on PRICE, never on unit. Taking the newest row outright picked
    a $2.75-per-BUNCH observation for an ingredient Avocado Verde draws in CANS,
    and cost_on refused the dish — correctly, because that is the $11,400/serve
    class. Where the named venue has its own history, its unit is kept."""
    cs = CostSeries([
        _obs("x", date(2026, 7, 1), "1.00", "stowaway", unit="can"),
        _obs("x", date(2026, 8, 1), "5.00", "stowaway", unit="can"),
        _obs("x", date(2026, 8, 12), "2.75", "harry_gatos", unit="bunch"),
    ], purchasable_to_ingredient={})
    got = cs.as_of("x", ON, venue="stowaway")
    assert got.unit == "can" and got.cost_per_unit == Decimal("5.00")


def test_falling_back_from_a_venue_takes_the_newest_not_the_first_bucket():
    """A venue with no observation of its own falls back — and the fallback used
    to be decided by dict insertion order, which is not a fact about prices."""
    cs = CostSeries([
        _obs("x", date(2026, 2, 1), "5.00", "stowaway"),
        _obs("x", date(2026, 8, 12), "3.00", "harry_gatos"),
    ], purchasable_to_ingredient={})
    assert cs.as_of("x", ON, venue="marilynas").cost_per_unit == Decimal("3.00")


def test_as_of_is_still_as_of():
    """The whole design rests on this: asking what July cost must give July's
    answer, whenever you ask. A newest-wins rule must not leak future prices
    into a past day."""
    cs = CostSeries([
        _obs("x", date(2026, 7, 1), "1.00", "stowaway"),
        _obs("x", date(2026, 8, 12), "9.99", "harry_gatos"),
    ], purchasable_to_ingredient={})
    assert cs.as_of("x", date(2026, 7, 15)).cost_per_unit == Decimal("1.00")


def test_ties_break_deterministically():
    """Two observations on the same day in different buckets must always resolve
    the same way, or costs.csv stops reproducing byte-identically and the
    determinism gate starts failing at random."""
    obs = [_obs("x", date(2026, 8, 12), "2.00", "stowaway"),
           _obs("x", date(2026, 8, 12), "3.00", "harry_gatos")]
    a = CostSeries(obs, purchasable_to_ingredient={}).as_of("x", ON)
    b = CostSeries(list(reversed(obs)), purchasable_to_ingredient={}).as_of("x", ON)
    assert a.cost_per_unit == b.cost_per_unit


def test_a_missing_ingredient_still_refuses():
    """Refusal over invention, unchanged."""
    import pytest
    cs = CostSeries([], purchasable_to_ingredient={})
    with pytest.raises(LookupError):
        cs.as_of("nothing", ON)
