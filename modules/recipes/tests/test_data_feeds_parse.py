"""
Every JSON the dashboard fetches must actually parse.

data/ IS the API contract (ARCHITECTURE.md): the dashboard fetches these files
and draws. A malformed one is not a caught error, it is a blank panel or a
silently skipped day, and nothing in the suite was looking.

    data/mari_daily_2026-07-04.json
        "delivery": {total_dollars: 226.68,"delivery_pct": 10.5},
                     ^ unquoted key — invalid JSON

Hand-backfilled on 2026-07-09 for a 2026-07-04 trading day, and unreadable ever
since. Marilyna's delivery cost for that day was simply absent from anything that
loaded it, and every reader that wrapped json.load in a try/except — which is
most of them, reasonably — skipped the whole day without saying so.

This is the cheapest guard in the repo and it would have caught it the day it
landed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"

FEEDS = sorted(p for p in DATA.rglob("*.json") if "_backups" not in p.parts)


@pytest.mark.skipif(not FEEDS, reason="no data/ feeds on a clean checkout")
@pytest.mark.parametrize("path", FEEDS, ids=lambda p: p.name)
def test_feed_is_valid_json(path):
    try:
        json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        pytest.fail(f"{path.relative_to(ROOT)} is not valid JSON: {e}")


def test_there_is_something_to_check():
    """A parametrised test over an empty list passes and proves nothing."""
    assert len(FEEDS) > 50, f"only {len(FEEDS)} json feeds found — is data/ present?"
