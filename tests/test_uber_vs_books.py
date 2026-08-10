"""The feeds must agree with the accounts, or say so.

WHY: every other Uber guard is internal. The per-day identity is the portal's own
arithmetic; the weekly cross-check is the portal against itself; the Direct
reconciliation is the portal's deliveries against the portal's invoices. Not one
of them can see an entire CHANNEL going missing, because a missing channel leaves
no trace in any feed — the feeds are simply, quietly, smaller.

That happened. xero_pull.py splits Mari's third-party delivery into
mari_uber_fees (all of it) and mari_uber_only (the UberEats account); the
difference is "DoorDash + Uber Direct". pnl.js replaced that whole difference
with the Uber Direct feed as soon as it covered a window, so DoorDash — A$545.95
in May 2026, A$624.47 in June — stopped being a cost at all. It reads ~0 from
July because DoorDash stopped, so the bug fell silent by itself. Only Xero
disagreed, and nothing compared against Xero.

Understating cost flatters the margin, which CLAUDE.md calls the dangerous
direction. This check is the one that looks outside the portal.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

import scripts.health_monitor as hm

EATS_HDR = ("date,shop,sales_inc_gst,payout_inc_gst,commission_inc_gst,"
            "offers_inc_gst,refund_inc_gst,source,orders\n")


def _closed_month():
    first = dt.date.today().replace(day=1)
    return (first - dt.timedelta(days=1)).replace(day=1)


def _fixture(tmp_path, monkeypatch, commission, direct, books, start_day=1):
    """A month that is CLOSED and fully covered by the daily feed."""
    m = _closed_month()
    nxt = (m.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    ndays = (nxt - m).days
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    per = commission / ndays
    rows = "".join(
        f"{(m + dt.timedelta(days=i)).isoformat()},mari,100.00,50.00,{per:.2f},0.00,0.00,ubereats_portal,1\n"
        for i in range(start_day - 1, ndays))
    (data / "uber_daily.csv").write_text(EATS_HDR + rows)
    (data / "uber_direct_daily.csv").write_text(
        "date,shop,fee_inc_gst,source\n"
        f"{m.isoformat()},mari,{direct:.2f},uber_direct_portal\n")
    (data / "xero_overheads_monthly.csv").write_text(
        "month,mari_uber_fees,mari_uber_only\n"
        f"{m:%Y-%m},{books:.2f},0.00\n")
    monkeypatch.setattr(hm, "ROOT", tmp_path)
    return m


def test_feeds_matching_the_books_reads_ok(tmp_path, monkeypatch):
    # 11000 inc GST -> 10000 ex, books 10000
    _fixture(tmp_path, monkeypatch, commission=10890.0, direct=110.0, books=10000.0)
    r = hm._uber_vs_books()
    assert r["status"] == "ok", r


def test_a_whole_missing_channel_is_caught(tmp_path, monkeypatch):
    """The DoorDash case: feeds are internally perfect and A$624 too small."""
    _fixture(tmp_path, monkeypatch, commission=10890.0, direct=110.0, books=10700.0)
    r = hm._uber_vs_books()
    assert r["status"] == "warn", r
    assert "short" in r["detail"], r["detail"]
    assert "flatters the margin" in r["action"]


def test_double_counting_is_caught_too(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, commission=10890.0, direct=110.0, books=9000.0)
    r = hm._uber_vs_books()
    assert r["status"] == "warn"
    assert "over" in r["detail"]
    assert "twice" in r["action"]


def test_small_noise_does_not_raise_a_permanent_warn(tmp_path, monkeypatch):
    """A monitor that is always amber is furniture. 3% / A$100 is the floor."""
    _fixture(tmp_path, monkeypatch, commission=10890.0, direct=110.0, books=10050.0)
    assert hm._uber_vs_books()["status"] == "ok"


def test_a_month_the_daily_feed_does_not_fully_cover_is_skipped(tmp_path, monkeypatch):
    """Before the daily feed, fees are weekly totals cut across month boundaries
    by straight sevenths — ±10% by construction. Reconciling that would warn
    forever and teach everyone to ignore the panel."""
    _fixture(tmp_path, monkeypatch, commission=10890.0, direct=110.0, books=99999.0,
             start_day=10)
    r = hm._uber_vs_books()
    assert r["status"] == "ok"
    assert "daily-covered yet" in r["detail"], r["detail"]


def test_missing_inputs_never_crash_the_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "ROOT", tmp_path)
    assert hm._uber_vs_books()["status"] == "unknown"
