"""The Uber Direct fee feed must keep flowing, and must stay a portal read.

WHY: Uber Direct is Mari's own online delivery, billed per order by Uber's
fleet. Its ingest used to be an invoice email parsed by Pipedream. Pipedream's
free tier ran out on 2026-07-24; sales ingestion and the auth worker were both
moved off it, and this one was missed. It then sat dead for 22 days — the
GitHub workflow logged ZERO runs — while nothing errored anywhere, because
pnl.js degrades safely: uberDirectActual reports covered=false and the caller
estimates. A feed can die completely without a single red light.

$327.44 of real fees were recovered from direct.uber.com and reconciled
EXACTLY, to the cent, against all six days that the old email path had already
captured — which is what proves the portal is a faithful substitute for the
invoice.

These assertions hold the shape. Staleness is watched by health_monitor's
"Uber Direct ingest" check, not here, because a genuinely quiet week has no
orders and must not fail CI.
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "uber_direct_daily.csv"
FIELDS = ["date", "shop", "fee_inc_gst", "source"]

STATEMENTS = ROOT / "data" / "uber_direct_statements.csv"
STMT_FIELDS = ["statement_date", "shop", "amount_inc_gst", "source"]

# Uber bills Direct daily, but the INVOICE date is not the DELIVERY date: a
# late-settling charge lands on a later invoice. Measured lag on 2026-08-10 was
# 3 days (the 2026-07-24 deliveries were billed 60.83 on the 24th and the
# remaining 7.95 on the 27th). So the two sources are only comparable once a
# day has had 3 days to settle.
SETTLE_LAG_DAYS = 3

# The one difference that does NOT close. Traced 2026-08-10:
#   deliveries  2026-07-01  11.74 + 25.00 + 11.79 = 48.53   (no invoice at all)
#   deliveries  2026-07-02  15.14                            (invoiced 13.67)
#   ------------------------------------------------------------------
#   incurred 63.67, charged 13.67  ->  exactly A$50.00 never billed
# It drains like a credit balance: all of 07-01, then 1.47 of 07-02, stopping
# dead on a round fifty. Every one of the other 31 invoice days matches the
# deliveries to the cent, and all 32 invoices are "Fully paid" — no invoice was
# hidden behind an unpaid status. So this is a A$50.00 credit Uber applied, not
# a capture error.
#
# The feed deliberately keeps the GROSS delivery-date figures: they are the cost
# of the service actually consumed on the day the sales happened, and carrying
# them overstates delivery cost by A$50 rather than understating it — the safe
# direction (CLAUDE.md: the errors that flatter you are the dangerous ones).
# Recording the gap here means a NEW discrepancy cannot hide inside it. If this
# number MOVES, something changed: investigate, do not retune the constant.
ACKNOWLEDGED_RESIDUAL = Decimal("-50.00")


def rows():
    with FEED.open() as fh:
        return list(csv.DictReader(fh))


def test_feed_exists_and_keeps_its_columns():
    assert FEED.exists(), "data/uber_direct_daily.csv is missing"
    with FEED.open() as fh:
        assert next(csv.reader(fh)) == FIELDS


def test_fees_are_positive_two_dp():
    bad = [(r["date"], r["fee_inc_gst"]) for r in rows()
           if Decimal(r["fee_inc_gst"]) <= 0
           or not (r["fee_inc_gst"].count(".") == 1 and len(r["fee_inc_gst"].split(".")[1]) == 2)]
    assert not bad, ("a row must be a real charged day at 2dp — a day with only cancelled "
                     f"orders carries no fee and gets NO row: {bad[:5]}")


def test_one_row_per_shop_per_day_sorted():
    keys = [(r["date"], r["shop"]) for r in rows()]
    assert not [k for k in keys if keys.count(k) > 1], "duplicate (date, shop)"
    assert keys == sorted(keys), "feed must stay sorted by date then shop"


def test_mari_only():
    """Uber Direct is a Mari product; pnl.js::uberDirectActual returns null for
    any other venue, so a stray row would be silently ignored, not surfaced."""
    bad = sorted({r["shop"] for r in rows()} - {"mari"})
    assert not bad, f"Uber Direct is Mari-only, found: {bad}"


def test_source_is_a_known_ingest_path():
    """uber_direct_email is the retired Pipedream path, kept for the rows it
    captured. New rows must come from the portal — if a third label appears,
    someone has added an ingest nobody has reviewed."""
    known = {"uber_direct_portal", "uber_direct_email"}
    bad = sorted({r["source"] for r in rows()} - known)
    assert not bad, f"unknown ingest source: {bad}"


def statements():
    with STATEMENTS.open() as fh:
        return list(csv.DictReader(fh))


def test_money_is_written_canonically():
    """No "-0.00", and nothing that is 2dp only by accident. See the twin test in
    test_uber_daily.py — the runbook claimed a test of this name existed for
    four weeks while it did not."""
    bad = [f"{r['date']} {r['fee_inc_gst']!r}" for r in rows()
           if r["fee_inc_gst"].startswith("-0.00")
           or not re.fullmatch(r"-?\d+\.\d{2}", r["fee_inc_gst"])]
    assert not bad, f"fee must be Decimal-formatted 2dp, never negative zero: {bad[:5]}"


def test_statement_feed_exists_and_keeps_its_shape():
    assert STATEMENTS.exists(), (
        "data/uber_direct_statements.csv is missing — it is the only INDEPENDENT "
        "check on the Direct feed. Without it, a truncated deliveries page (the "
        "list silently caps at 50 rows) understates fees and nothing notices.")
    with STATEMENTS.open() as fh:
        assert next(csv.reader(fh)) == STMT_FIELDS
    bad = [r["statement_date"] for r in statements()
           if not re.fullmatch(r"\d+\.\d{2}", r["amount_inc_gst"])
           or Decimal(r["amount_inc_gst"]) <= 0]
    assert not bad, f"statement amounts must be positive 2dp: {bad[:5]}"
    dates = [r["statement_date"] for r in statements()]
    assert dates == sorted(dates), "statements must stay sorted by date"
    assert len(dates) == len(set(dates)), "one invoice row per date"


def test_feed_reconciles_against_ubers_own_invoices():
    """The deliveries list and the billing invoices are two independent reads of
    the same money. Over any window that has fully settled they must agree.

    WHY this is the important test in this file: every OTHER assertion here is a
    shape check that a truncated read passes cleanly. The deliveries list
    paginates at 50 rows, and a page-one-only read simply returns fewer
    deliveries — correct-looking rows, correct 2dp, sorted, Mari-only, and too
    small. That is exactly what happened on the first pass of 2026-08-10: the
    June–July range came back with 50 rows and 2026-06-05 summed to 27.94
    against an invoice of 40.60. Only the invoice caught it.

    Compared on TOTALS, not day by day, because invoice date != delivery date.
    """
    stmt = {r["statement_date"]: Decimal(r["amount_inc_gst"]) for r in statements()}
    feed = {r["date"]: Decimal(r["fee_inc_gst"]) for r in rows()}
    if not stmt or not feed:
        return
    cutoff = (date.fromisoformat(max(stmt)) - timedelta(days=SETTLE_LAG_DAYS)).isoformat()
    floor = min(min(stmt), min(feed))
    f_tot = sum((v for d, v in feed.items() if floor <= d <= cutoff), Decimal("0"))
    s_tot = sum((v for d, v in stmt.items() if floor <= d <= cutoff), Decimal("0"))
    resid = s_tot - f_tot
    assert resid == ACKNOWLEDGED_RESIDUAL, (
        f"Uber Direct feed does not reconcile to Uber's invoices over {floor}..{cutoff}: "
        f"invoices A${s_tot}, feed A${f_tot}, difference A${resid} "
        f"(only A${ACKNOWLEDGED_RESIDUAL} is accounted for). "
        "A feed SHORT of the invoices is usually a truncated deliveries page — the list "
        "caps at 50 rows, so re-read it in weekly windows and confirm each returns "
        "fewer than 50 rows before summing.")


def test_no_recorded_delivery_day_is_missing_from_the_feed():
    """Every invoice inside the feed's covered span must have a delivery row on
    the SAME date. An invoice for a day the feed never recorded means a whole
    day of deliveries was skipped and those fees are absent from the P&L.

    A first draft of this test excused any invoice whose preceding 3 days were in
    the feed, to allow for settlement lag. That was too generous to be a guard:
    deleting 2026-07-04 (A$137.00) still passed it, because 07-01 to 07-03 were
    present. Late settlements are rare and identifiable, so they are named here
    instead — a listed exception you can read beats a rule that quietly forgives.
    """
    LATE = {
        # A$7.95 tail of the 2026-07-24 deliveries, invoiced on its own 3 days
        # later. The delivery-date row for 07-24 already carries this money.
        "2026-07-27",
    }
    feed_dates = {r["date"] for r in rows()}
    if not feed_dates:
        return
    first, last = min(feed_dates), max(feed_dates)
    orphans = [f"{r['statement_date']} (invoiced A${r['amount_inc_gst']})"
               for r in statements()
               if first <= r["statement_date"] <= last
               and r["statement_date"] not in feed_dates
               and r["statement_date"] not in LATE]
    assert not orphans, (
        f"Uber invoiced days the feed has no delivery row for: {orphans[:5]} — "
        "either a day was skipped entirely, or a late settlement needs adding to LATE "
        "with a note saying which delivery day it belongs to.")
