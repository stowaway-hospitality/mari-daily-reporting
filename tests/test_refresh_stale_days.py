"""A published day must reflect the EatClub give-away that exists for it.

EatClub tables ring the FULL bill on the POS, so a day's revenue is overstated
until the give-away (offer discount + EatClub's 11% commission) comes off.
daily_aggregator.py subtracts it from data/eatclub_<prefix>_<date>.json -- but
only at the moment it runs. A day aggregated BEFORE that fact arrived keeps the
full amount forever, because nothing ever went back for it.

Nothing did, because the only self-heal we had asks a different question: "did
the day's SALES land?". They had. A day missing only its give-away looks
complete from every angle.

Found 2026-08-17: ten venue-days carrying $698.52 ex-GST that EatClub kept and
never paid us, e.g. hg 2026-07-24 short $268.56, mari 2026-08-07 short $105.48.

The sweep compares CONTENT, not file mtimes -- git sets mtimes to checkout time,
so an mtime rule would pass forever in CI while being wrong on every machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "scripts" / "refresh_stale_days.py"
sys.path.insert(0, str(ROOT / "scripts"))


def _mkday(data: Path, prefix: str, day: str, *, deducted_ex, covers,
           giveaway_inc=None, fact_covers=None, revenue_ex=1000.0):
    (data / f"{prefix}_daily_{day}.json").write_text(json.dumps({
        "sales": {"revenue_ex_gst": revenue_ex,
                  "eatclub_giveaway_ex_gst": deducted_ex,
                  "eatclub_covers": covers}}))
    if giveaway_inc is not None:
        (data / f"eatclub_{prefix}_{day}.json").write_text(json.dumps({
            "date": day, "giveaway_inc": giveaway_inc,
            "covers": fact_covers if fact_covers is not None else covers}))


def _sweep(tmp_path: Path):
    """Run the sweep against a synthetic data/ dir and return (rc, stdout)."""
    env = dict(os.environ, REPO_ROOT=str(tmp_path))
    r = subprocess.run([sys.executable, str(SWEEP)], env=env,
                       capture_output=True, text=True)
    return r.returncode, r.stdout


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_a_day_that_already_deducted_is_not_stale(data, tmp_path):
    _mkday(data, "stow", "2026-08-02", deducted_ex=62.44, covers=6,
           giveaway_inc=68.68)
    rc, out = _sweep(tmp_path)
    assert rc == 0, out


def test_a_day_missing_its_giveaway_is_caught(data, tmp_path):
    """The exact shape of stow 2026-08-02: record says 0, fact says $68.68."""
    _mkday(data, "stow", "2026-08-02", deducted_ex=0.0, covers=0,
           giveaway_inc=68.68, fact_covers=6)
    rc, out = _sweep(tmp_path)
    assert rc == 1
    assert "2026-08-02" in out and "PUBLISHED WITHOUT THEIR EATCLUB GIVE-AWAY" in out


def test_a_partial_deduction_is_caught(data, tmp_path):
    """mari 2026-08-07 had deducted $29.92 of $135.40 — a revised give-away."""
    _mkday(data, "mari", "2026-08-07", deducted_ex=29.92, covers=2,
           giveaway_inc=148.94, fact_covers=9)
    rc, out = _sweep(tmp_path)
    assert rc == 1 and "2026-08-07" in out


def test_a_record_predating_the_field_is_caught(data, tmp_path):
    """None is not zero: a record written before eatclub_giveaway_ex_gst
    existed cannot be said to have deducted anything."""
    _mkday(data, "stow", "2026-07-11", deducted_ex=None, covers=None,
           giveaway_inc=50.0, fact_covers=4)
    rc, out = _sweep(tmp_path)
    assert rc == 1 and "2026-07-11" in out


def test_covers_alone_can_mark_a_day_stale(data, tmp_path):
    """Same dollars, more covers — the fact was revised and the record did not
    follow. Covers feed the per-cover metrics, so this still matters."""
    _mkday(data, "mari", "2026-08-01", deducted_ex=32.93, covers=2,
           giveaway_inc=36.22, fact_covers=4)
    rc, out = _sweep(tmp_path)
    assert rc == 1 and "2026-08-01" in out


def test_no_eatclub_fact_means_nothing_is_owed(data, tmp_path):
    """Most days have no EatClub at all. They must never be flagged."""
    _mkday(data, "hg", "2026-08-05", deducted_ex=0.0, covers=0)
    rc, out = _sweep(tmp_path)
    assert rc == 0, out


def test_sub_cent_rounding_is_not_staleness(data, tmp_path):
    """giveaway_inc / 1.1 does not round-trip exactly through float."""
    _mkday(data, "stow", "2026-08-02", deducted_ex=68.68 / 1.1 + 0.004, covers=6,
           giveaway_inc=68.68)
    rc, out = _sweep(tmp_path)
    assert rc == 0, out


def test_the_live_repo_has_no_stale_days():
    """The sweep runs in daily_pull with --fix, so main should stay clean.

    If this fails, a day was published without its give-away and the sweep did
    not repair it — look at the Daily Pull log before trusting that day.
    """
    r = subprocess.run([sys.executable, str(SWEEP), "--quiet"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
