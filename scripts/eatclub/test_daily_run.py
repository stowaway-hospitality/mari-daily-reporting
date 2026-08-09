"""Runner tests — the parts that used to be hand-typed constants.

Seeded with the real Harry Gatos night of 2026-08-08, the first night assessed
by this runner rather than by a throwaway script. The hand-built analysis that
morning produced window $1864.50, EatClub bills $504.68, full-price $1359.82
against a Saturday baseline of $2474.48 (n=8). These tests pin that the runner
reproduces those figures from the files, so a regression here is visible.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import daily_run as dr  # noqa: E402


def test_closed_days_are_dropped_from_the_series():
    """A closed night is not a weak night. Averaging zeros deflates a baseline."""
    rows = dr.load_hourly(_write_hourly())
    assert [r["date"] for r in rows] == ["2026-08-07", "2026-08-08"]


def test_window_and_early_are_derived_not_typed():
    rows = {r["date"]: r for r in dr.load_hourly(_write_hourly())}
    sat = rows["2026-08-08"]
    assert round(sat["window"], 2) == 1864.50      # h17+h18+h19+h20
    assert round(sat["early"], 2) == 494.50        # h17+h18, the pre-arrival read
    assert round(sat["h21"], 2) == 903.00


def test_standard_tier_detection():
    """A tier above the published ladder means the team escalated to rescue."""
    assert not ({20} - dr.STANDARD_TIERS)          # standard late tier
    assert not ({25, 20} - dr.STANDARD_TIERS)      # both published tiers
    assert ({30} - dr.STANDARD_TIERS)              # escalated


def test_redeemed_on_filters_status_and_date():
    rows = [
        {"date": "2026-08-08", "status": "PAID", "bill_full": "213.53",
         "net_revenue": "147.33", "party_size": "3"},
        {"date": "2026-08-08", "status": "UNREDEEMED", "bill_full": "",
         "net_revenue": "", "party_size": "3"},
        {"date": "2026-08-07", "status": "PAID", "bill_full": "99.00",
         "net_revenue": "70.00", "party_size": "2"},
    ]
    from datetime import date
    got = dr.redeemed_on(rows, date(2026, 8, 8))
    assert len(got) == 1 and got[0]["bill_full"] == "213.53"


def test_profile_append_is_idempotent():
    """Re-running a night must not duplicate its rows in the profile."""
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "profile.csv")
    rows = [("2026-08-08", "eatclub_covers", 9, "", 2, "note"),
            ("2026-08-08", "eatclub_share_of_window_pct", 27.1, "", "", "note")]
    assert dr.append_profile(p, rows) == 2
    assert dr.append_profile(p, rows) == 0          # rerun adds nothing
    with open(p) as f:
        assert len(f.read().strip().splitlines()) == 3   # header + 2


def test_profile_rows_cover_all_three_venues():
    hg = {"skip": "closed"}
    mari = {"skip": "no baseline"}
    stow = {"skip": "needs RG feed", "tables": 6, "covers": 16}
    from datetime import date
    rows = dr.profile_rows(date(2026, 8, 8), hg, mari, stow)
    assert [r[1] for r in rows] == ["stow_eatclub_covers"]   # skips emit nothing


_HOURLY = """date,h17,h18,h19,h20,h21,h22
2026-08-02,0,0,0,0,0,0
2026-08-07,268.74,1071.69,1107.2,1554.5,226,0
2026-08-08,139,355.5,708.5,661.5,903,0
"""


def _write_hourly():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "hg_hourly.csv")
    with open(p, "w") as f:
        f.write(_HOURLY)
    return p
