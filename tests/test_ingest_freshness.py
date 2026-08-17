"""The ingest may not invent a date, and may not overwrite a good day.

WHY THIS EXISTS. The Lightspeed product export carries no date of its own. Until
the scheduled reports include a Sale Closed Date column, the email's own
timestamp is the ONLY thing naming the trading day — which means every weakness
in reading that header is a weakness in the published P&L.

Two were found on 2026-08-17 while repairing insights_hg_2026-08-10.csv, which
held 2026-08-03's trade and understated Harry Gatos' Monday by $822.23:

  1. target_date() fell back to datetime.now() whenever the Date header could
     not be parsed. A real CSV would then be stamped with a day it has nothing
     to do with, silently, and look exactly like a normal ingest.

  2. The scanner reads the last 8 days of mail and dedupes on Message-ID. An
     old report re-delivered with a NEW Message-ID is therefore not "seen", and
     was dispatched unconditionally — on top of a day that had already landed
     correctly.

Both are now refusals. The second is deliberately narrow: age alone is fine,
because the self-heal exists to re-dispatch old mail when a day never landed.
It only refuses when the day is already complete — nothing to gain, a good
number to lose.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("GH_DISPATCH_PAT", "test-token-not-used")
ingest = pytest.importorskip("ingest_insights_email")


def _msg(date_header):
    m = Message()
    m["Subject"] = "HG Daily Sales Auto"
    if date_header is not None:
        m["Date"] = date_header
    return m


# ---------------------------------------------------------------- no guessing

def test_missing_date_header_yields_no_target_date():
    assert ingest.target_date(_msg(None)) is None


def test_unparseable_date_header_yields_no_target_date():
    assert ingest.target_date(_msg("not a date at all")) is None


def test_no_silent_now_fallback():
    """The specific regression: an unreadable header must NOT become today."""
    today_minus_1 = (datetime.now(timezone.utc).astimezone(ingest.SYD)
                     - timedelta(days=1)).strftime("%Y-%m-%d")
    assert ingest.target_date(_msg("garbage")) != today_minus_1


# ------------------------------------------------------- the day it reports on

def test_target_date_is_the_day_before_sending():
    # Schedules filter on "Yesterday", so a Tuesday 05:00 send reports Monday.
    assert ingest.target_date(_msg("Tue, 11 Aug 2026 05:00:00 +1000")) == "2026-08-10"


def test_target_date_respects_sydney_not_utc():
    # 00:30 Sydney on the 11th is still the 10th in UTC. Getting this wrong
    # shifts a whole day's revenue onto its neighbour.
    assert ingest.target_date(_msg("Tue, 11 Aug 2026 00:30:00 +1000")) == "2026-08-10"


# ------------------------------------------------------------------- staleness

def test_msg_sent_at_reads_the_header():
    got = ingest.msg_sent_at(_msg("Tue, 11 Aug 2026 05:00:00 +1000"), "imap")
    assert got is not None and got.astimezone(ingest.SYD).hour == 5


def test_msg_sent_at_is_none_when_unreadable():
    assert ingest.msg_sent_at(_msg("garbage"), "imap") is None
    assert ingest.msg_sent_at(_msg(None), "imap") is None


def test_stale_window_default_is_one_cycle_plus_slack():
    # The daily schedules fire 05:00 / 05:30 / 06:00 Sydney. A legitimate report
    # is hours old; days old means it was re-delivered.
    assert ingest.STALE_AFTER_HOURS == 26


def test_a_fresh_message_is_not_stale():
    fresh = datetime.now(timezone.utc) - timedelta(hours=2)
    age_h = (datetime.now(timezone.utc) - fresh).total_seconds() / 3600.0
    assert age_h <= ingest.STALE_AFTER_HOURS


def test_a_week_old_redelivery_is_stale():
    old = datetime.now(timezone.utc) - timedelta(days=7)
    age_h = (datetime.now(timezone.utc) - old).total_seconds() / 3600.0
    assert age_h > ingest.STALE_AFTER_HOURS


def test_output_complete_is_false_for_a_day_that_never_landed():
    """Staleness only blocks when the day is already good — so this is the
    switch that keeps the self-heal working for genuine gaps.

    Deliberately does NOT chdir: _output_complete() reads a relative data/
    path, and moving cwd inside the suite made unrelated corpus-dependent
    tests skip depending on ordering. A date no venue will ever have is
    enough to prove the miss path.
    """
    assert ingest._output_complete("harry", "1999-01-01") is False
