"""Give-away tests, seeded with the real Stowaway 2026-07-14 redemptions.

That day: 3 PAID tables, 4 covers, menu $320.68 inc, net $214.21 inc ->
give-away $106.47 inc. This is the exact figure the aggregator subtracts, and
it moved Stowaway's GP from an overstated 78.2% to the true 77.1%.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import giveaway  # noqa: E402

JUL14 = [
    {"date": "2026-07-14", "venue": "Stowaway Bar", "party_size": "1",
     "offer_pct": "25", "bill_full": "81.97", "net_revenue": "56.95", "status": "PAID"},
    {"date": "2026-07-14", "venue": "Stowaway Bar", "party_size": "1",
     "offer_pct": "25", "bill_full": "59.17", "net_revenue": "37.86", "status": "PAID"},
    {"date": "2026-07-14", "venue": "Stowaway Bar", "party_size": "2",
     "offer_pct": "25", "bill_full": "179.54", "net_revenue": "119.40", "status": "PAID"},
]


def test_real_jul14():
    g = giveaway.day_giveaway(JUL14, "2026-07-14", "Stowaway Bar")
    assert g["tables"] == 3
    assert g["covers"] == 4
    assert g["menu_inc"] == 320.68
    assert g["net_inc"] == 214.21
    assert g["giveaway_inc"] == 106.47
    # discount = 25% of each bill; commission = the rest of the give-away
    assert g["discount_inc"] == 80.17
    assert g["commission_inc"] == 26.30


def test_unredeemed_and_offerless():
    rows = [
        # UNREDEEMED (no bill) -> ignored entirely
        {"party_size": "2", "offer_pct": "30", "bill_full": "", "net_revenue": "", "status": "UNREDEEMED"},
        # offerless (0%) PAID -> only the ~11% commission is given away
        {"party_size": "1", "offer_pct": "0", "bill_full": "45.34", "net_revenue": "40.36", "status": "PAID"},
    ]
    g = giveaway.day_giveaway(rows, "2026-07-17", "Stowaway Bar")
    assert g["tables"] == 1
    assert g["covers"] == 1
    assert g["giveaway_inc"] == 4.98
    assert g["discount_inc"] == 0.0
    assert g["commission_inc"] == 4.98


# Marilyna's real 2026-08-07 takeaway night. Three of the four settled as
# COMPLETED rather than PAID; a PAID-only filter counted just Dominique's table
# and understated the day's give-away by $115.31. Regression guard for that.
MARI_AUG07 = [
    {"date": "2026-08-07", "venue": "Marilynas Famous Pizza", "party_size": "1",
     "offer_pct": "30", "bill_full": "45.00", "net_revenue": "26.55", "status": "COMPLETED"},
    {"date": "2026-08-07", "venue": "Marilynas Famous Pizza", "party_size": "2",
     "offer_pct": "30", "bill_full": "80.25", "net_revenue": "47.34", "status": "PAID"},
    {"date": "2026-08-07", "venue": "Marilynas Famous Pizza", "party_size": "4",
     "offer_pct": "30", "bill_full": "96.00", "net_revenue": "56.64", "status": "COMPLETED"},
    {"date": "2026-08-07", "venue": "Marilynas Famous Pizza", "party_size": "2",
     "offer_pct": "30", "bill_full": "142.00", "net_revenue": "83.78", "status": "COMPLETED"},
]


def test_mari_completed_counts_as_redeemed():
    g = giveaway.day_giveaway(MARI_AUG07, "2026-08-07", "Marilynas Famous Pizza")
    assert g["tables"] == 4
    assert g["covers"] == 9
    assert g["menu_inc"] == 363.25
    assert g["net_inc"] == 214.31
    assert g["giveaway_inc"] == 148.94
    # the two components still reconcile to the whole
    assert round(g["discount_inc"] + g["commission_inc"], 2) == g["giveaway_inc"]


def test_paid_only_would_have_dropped_three_tables():
    """The exact bug: COMPLETED must not be treated as unredeemed."""
    only_paid = [r for r in MARI_AUG07 if r["status"] == "PAID"]
    assert giveaway.day_giveaway(only_paid, "2026-08-07", "Marilynas Famous Pizza")["tables"] == 1
    assert giveaway.day_giveaway(MARI_AUG07, "2026-08-07", "Marilynas Famous Pizza")["tables"] == 4


def test_unredeemed_still_excluded_for_takeaway():
    rows = [{"party_size": "2", "offer_pct": "25", "bill_full": "",
             "net_revenue": "", "status": "UNREDEEMED"}]
    g = giveaway.day_giveaway(rows, "2026-08-08", "Marilynas Famous Pizza")
    assert g["tables"] == 0
    assert g["giveaway_inc"] == 0.0


def test_unknown_status_raises_instead_of_being_dropped():
    """The bug class. A status we have not classified must stop the run."""
    rows = [{"party_size": "2", "offer_pct": "25", "bill_full": "88.00",
             "net_revenue": "58.08", "status": "SETTLED"}]
    try:
        giveaway.day_giveaway(rows, "2026-08-09", "Marilynas Famous Pizza")
    except giveaway.UnknownEatClubStatus as e:
        assert "SETTLED" in str(e)
    else:
        raise AssertionError("unknown status was silently swallowed")


def test_known_statuses_do_not_raise():
    for s in ("PAID", "COMPLETED", "UNREDEEMED", "PENDING", "", "  paid  "):
        giveaway.day_giveaway(
            [{"party_size": "1", "offer_pct": "25", "bill_full": "10.00",
              "net_revenue": "7.00", "status": s}], "2026-08-09", "Stowaway Bar")


def test_reconcile_passes_on_consistent_facts():
    facts = [giveaway.day_giveaway(MARI_AUG07, "2026-08-07", "Marilynas Famous Pizza")]
    got = giveaway.reconcile(facts, MARI_AUG07)
    assert got["tables"] == 4
    assert got["giveaway_inc"] == 148.94


def test_reconcile_catches_a_dropped_table():
    """Simulate the COMPLETED bug: facts built from PAID only, source has four."""
    only_paid = [r for r in MARI_AUG07 if r["status"] == "PAID"]
    facts = [giveaway.day_giveaway(only_paid, "2026-08-07", "Marilynas Famous Pizza")]
    try:
        giveaway.reconcile(facts, MARI_AUG07)
    except giveaway.GiveawayReconcileError as e:
        assert "1 tables" in str(e) and "4 redeemed" in str(e)
    else:
        raise AssertionError("reconcile missed a dropped table")


def test_reconcile_catches_a_redeemed_row_with_no_bill():
    """A redemption whose money never lands is the same silent drop."""
    rows = MARI_AUG07 + [
        {"date": "2026-08-07", "venue": "Marilynas Famous Pizza", "party_size": "2",
         "offer_pct": "30", "bill_full": "70.00", "net_revenue": "41.30", "status": "PAID"},
    ]
    facts = [giveaway.day_giveaway(MARI_AUG07, "2026-08-07", "Marilynas Famous Pizza")]
    try:
        giveaway.reconcile(facts, rows)
    except giveaway.GiveawayReconcileError:
        pass
    else:
        raise AssertionError("reconcile missed an unaccounted redeemed row")


def test_dollar_and_comma_cleaning():
    rows = [{"party_size": "2", "offer_pct": "30", "bill_full": "$1,090.00",
             "net_revenue": "$643.10", "status": "PAID"}]
    g = giveaway.day_giveaway(rows, "2026-07-18", "Stowaway Bar")
    assert g["menu_inc"] == 1090.00
    assert g["giveaway_inc"] == 446.90
