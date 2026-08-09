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
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "uber_direct_daily.csv"
FIELDS = ["date", "shop", "fee_inc_gst", "source"]


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
