"""The Uber Eats fee feed must stay EXACT, and must fail loudly when it isn't.

WHY: for four weeks this feed recorded commission as a flat 33% of sales and
called the leftover "marketing". Mari's real blended fee rate was 25.2% — she
runs staff-delivery (16%) and pickup (6%) orders — so commission came out
~$1,310 too HIGH and marketing ~$1,172 too LOW across 2026-07-13..2026-08-08.
Only 43% of the discretionary marketing spend was being captured.

The estimate was wrong from the first day and the drift was detected on eleven
consecutive runs. Every one of them wrote a note into data/uber_pull.log, which
nobody reads, and the numbers stayed wrong. A guard that logs is not a guard.

So the invariants live here, where CI fails on them. commission_inc_gst is now
the portal's actual "Service fees" line and offers_inc_gst its actual
"Marketing" line, which makes the day's arithmetic closed and checkable:

    sales - commission - offers - refund == payout

That identity cannot hold for an estimate. If anyone reintroduces a modelled
rate, this test goes red on the first day it is wrong. Note the failure that
got through was the flattering kind (fees too low) — the direction CLAUDE.md
says is the dangerous one.
"""

from __future__ import annotations

import csv
import re
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "uber_daily.csv"
WEEKLY = ROOT / "data" / "uber_fees_weekly.csv"

FIELDS = ["date", "shop", "sales_inc_gst", "payout_inc_gst",
          "commission_inc_gst", "offers_inc_gst", "refund_inc_gst", "source",
          # Added 2026-08-10, additive-only. The portal's order count for the day.
          # It is the ONLY field that carries independent evidence of WHICH SHOP
          # was read: every other guard here is an internal-consistency test that
          # passes just as happily on the wrong shop's numbers. Blank on rows
          # captured before it existed.
          "orders"]
MONEY = FIELDS[2:7]

# Uber's published rates, inc GST: delivery-by-Uber 30%+GST = 33% is the ceiling;
# pickup 6% is the floor. A day outside that band means the feed is not reading
# the portal's Service fees line any more.
RATE_MAX = Decimal("0.3301")
RATE_MIN = Decimal("0.0500")

# Money is written by a formatter, so it has exactly one legal shape. Anything
# else means a value reached the file by a path the writer does not control.
CANON = re.compile(r"-?\d+\.\d{2}")


def rows():
    with FEED.open() as fh:
        return list(csv.DictReader(fh))


def test_feed_exists_and_keeps_its_columns():
    assert FEED.exists(), "data/uber_daily.csv is missing — the Uber fee feed is the only record of delivery cost"
    with FEED.open() as fh:
        header = next(csv.reader(fh))
    assert header == FIELDS, f"column set changed: {header} != {FIELDS}"


@pytest.mark.parametrize("field", MONEY)
def test_money_is_two_decimal_places(field):
    bad = [(r["date"], r["shop"], r[field]) for r in rows()
           if not (r[field].count(".") == 1 and len(r[field].split(".")[1]) == 2)]
    assert not bad, f"{field} must be 2dp: {bad[:5]}"


def test_money_is_written_canonically():
    """One legal shape per amount — and negative zero is not one of them.

    Seven rows carried refund_inc_gst = "-0.00", a negative zero left by the
    original float formatting. It survived every guard in this file: the 2dp
    test counts the digits after the dot, and the sign test asks `< 0`, which
    negative zero is not. So the feed contained an amount in a form the writer
    never intended and CI called it clean.

    Nothing was mis-stated — -0.00 equals 0.00 to every consumer — but a value
    nobody meant to write is a value nobody can reason about, and the next one
    of these may not be cosmetic. Pin the shape, not just the magnitude.
    """
    bad = [(r["date"], r["shop"], f, repr(r[f]))
           for r in rows() for f in MONEY
           if not CANON.fullmatch(r[f]) or r[f].startswith("-0.00")]
    assert not bad, f"non-canonical money (negative zero? padding? currency symbol?): {bad[:5]}"


def test_the_days_arithmetic_closes():
    """sales - commission - offers - refund == payout, to the cent, every row.

    This is the whole point of reading actuals: the portal's own numbers balance.
    A modelled commission cannot satisfy this, so this single assertion is what
    stops the 33% estimate (or any successor) creeping back in.
    """
    bad = []
    for r in rows():
        resid = (Decimal(r["sales_inc_gst"]) - Decimal(r["commission_inc_gst"])
                 - Decimal(r["offers_inc_gst"]) - Decimal(r["refund_inc_gst"])
                 - Decimal(r["payout_inc_gst"]))
        if resid != 0:
            bad.append(f"{r['date']} {r['shop']} off by {resid}")
    assert not bad, "day arithmetic does not close (feed is estimated, not actual):\n  " + "\n  ".join(bad[:10])


def test_no_negative_fees():
    bad = [f"{r['date']} {r['shop']}" for r in rows()
           if any(Decimal(r[f]) < 0 for f in ("commission_inc_gst", "offers_inc_gst", "refund_inc_gst"))]
    assert not bad, f"negative fee/refund — a sign flip or an over-attributed split: {bad[:5]}"


def test_fee_rate_stays_inside_ubers_published_bands():
    """Commission as a share of sales must sit between the pickup (6%) and
    delivery (33%) rates. Above 33% means marketing has leaked into commission;
    a whole feed pinned at exactly 33% is the estimate coming back."""
    bad = []
    for r in rows():
        sales = Decimal(r["sales_inc_gst"])
        if sales <= 0:
            continue
        rate = Decimal(r["commission_inc_gst"]) / sales
        if not (RATE_MIN <= rate <= RATE_MAX):
            bad.append(f"{r['date']} {r['shop']} {rate:.3f}")
    assert not bad, f"commission rate outside 5–33%: {bad[:8]}"


def test_zero_sales_days_are_explained_not_zeroed():
    """A zero-sales day with money going OUT is real: either Uber ads billed on a
    day the shop never traded, or a refund on an earlier order. An earlier rule
    zeroed both columns on any zero-sales day and threw away $129.91 of genuine
    ad spend across four weeks, so the negative payout must be attributed to
    marketing or refund — never left unexplained."""
    bad = []
    for r in rows():
        if Decimal(r["sales_inc_gst"]) != 0 or Decimal(r["payout_inc_gst"]) >= 0:
            continue
        if Decimal(r["offers_inc_gst"]) + Decimal(r["refund_inc_gst"]) == 0:
            bad.append(f"{r['date']} {r['shop']}")
    assert not bad, f"zero-sales day with a negative payout but nothing attributed: {bad}"


def test_one_row_per_shop_per_day_sorted():
    keys = [(r["date"], r["shop"]) for r in rows()]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate (date, shop): {dupes}"
    assert keys == sorted(keys), "feed must stay sorted by date then shop"


def test_daily_and_weekly_feeds_do_not_overlap():
    """The weekly feed (uber_fees_weekly) covers the era before the daily feed.
    If they ever overlap, pnl.js::uberActual would count those days twice."""
    if not WEEKLY.exists():
        pytest.skip("no weekly feed")
    with WEEKLY.open() as fh:
        weekly = [r for r in csv.DictReader(fh) if r.get("week_ending")]
    if not weekly:
        pytest.skip("weekly feed empty")
    for shop in {r["shop"] for r in rows()}:
        firsts = [r["date"] for r in rows() if r["shop"] == shop]
        if not firsts:
            continue
        first_daily = min(firsts)
        clash = [w["week_ending"] for w in weekly
                 if w.get("venue") == shop and w["week_ending"] >= first_daily]
        assert not clash, (f"{shop}: weekly weeks {clash[:3]} land on/after the daily feed's "
                           f"first day {first_daily} — those days would be double-counted")


def test_money_is_written_canonically():
    """No "-0.00", and nothing that is 2dp only by accident.

    WHY this exists: for four weeks the pull formatted money with float, and a
    float round of a small negative emits "-0.00". That string walks straight
    past both of the guards you would expect to stop it —
    test_money_is_two_decimal_places counts digits and sees a perfectly good
    2dp number, and test_no_negative_fees asks `< 0`, which is False for
    Decimal("-0.00"). Seven rows carried it for four weeks.

    Worse, the runbook already CLAIMED a test named
    test_money_is_written_canonically rejected it. No such test existed — the
    claim was the only thing standing between the bug and production, and a
    claim is not a guard. This is that test, written for real (2026-08-10).
    """
    bad = []
    for r in rows():
        for f in MONEY:
            v = r[f]
            if v.startswith("-0.00") or not re.fullmatch(r"-?\d+\.\d{2}", v):
                bad.append(f"{r['date']} {r['shop']} {f}={v!r}")
    assert not bad, ("money must be Decimal-formatted, 2dp, and never negative zero "
                     f"(a float path is back): {bad[:5]}")


def test_orders_is_a_whole_count_consistent_with_sales():
    """orders is the row's only independent handle on reality.

    A day with sales but no orders, or orders but no sales, means the row was
    assembled from two different reads — most likely two different SHOPS, which
    is the one failure mode no arithmetic guard in this file can see.
    """
    bad = []
    for r in rows():
        o = (r.get("orders") or "").strip()
        if o == "":
            continue                      # pre-2026-08-10 rows: not captured
        if not o.isdigit():
            bad.append(f"{r['date']} {r['shop']} orders={o!r} not a whole count"); continue
        n, sales = int(o), Decimal(r["sales_inc_gst"])
        if n == 0 and sales > 0:
            bad.append(f"{r['date']} {r['shop']} {sales} of sales on 0 orders")
        if n > 0 and sales <= 0:
            bad.append(f"{r['date']} {r['shop']} {n} orders but no sales")
    assert not bad, f"orders disagrees with sales: {bad[:5]}"
