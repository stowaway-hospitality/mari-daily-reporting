#!/usr/bin/env python3
"""Recompute any day whose EatClub give-away landed after the day was written.

HOW THIS BIT US. EatClub tables ring the FULL bill on the POS, so the day's
revenue is overstated until the give-away (offer discount + EatClub's 11%
commission) is subtracted. daily_aggregator.py does subtract it -- but only from
data/eatclub_<prefix>_<date>.json, and only at the moment it runs. If the day
was aggregated BEFORE that fact arrived, the deduction never happened, and
nothing in the pipeline ever went back for it.

Nothing did, because the only self-heal we had is in ingest_insights_email.py
and it asks a different question: "did the day's SALES land?". They had. A day
missing only its give-away looks complete from every angle -- the record exists,
the revenue is plausible, the export is present.

Found 2026-08-17 while reconciling Harry Gatos 10 Aug. Fourteen venue-days were
carrying revenue EatClub kept and never paid us, $1,102.89 ex in five weeks, and
twelve of them matched the missing give-away to the cent:

    stow 2026-07-22   -67.32     hg   2026-07-24  -268.56
    mari 2026-07-29   -60.53     mari 2026-08-02   -19.31
    stow 2026-08-02   -62.44     stow 2026-08-04   -55.37
    mari 2026-08-07  -105.48     ...

THE CHECK. Not file mtimes -- git sets those to checkout time, so an mtime
comparison is meaningless in CI and would silently pass forever. Compare the
CONTENT instead: the record stores what it deducted (eatclub_giveaway_ex_gst,
eatclub_covers); the fact file says what should have been deducted
(giveaway_inc, covers). If they disagree, the record predates the fact.

That also covers the reverse -- a give-away later revised down, or a record
written under a schema that had no EatClub field at all (its value is None,
which no fact can match).

Usage:
    python3 scripts/refresh_stale_days.py            # report; exit 1 if stale
    python3 scripts/refresh_stale_days.py --fix      # recompute them, exit 0
    python3 scripts/refresh_stale_days.py --quiet    # only speak up if stale
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"

PREFIX_VENUE = {"stow": "stowaway", "hg": "harry", "mari": "marilynas"}
DAILY_RE = re.compile(r"^(stow|hg|mari)_daily_(\d{4}-\d{2}-\d{2})\.json$")

# Cents. The record stores give-away ex-GST as giveaway_inc / 1.1, so a
# round-trip through float leaves sub-cent dust that is not staleness.
TOL = 0.01


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def stale_days() -> list[dict]:
    """Every published day whose record disagrees with its EatClub fact."""
    out = []
    for path in sorted(DATA.glob("*_daily_*.json")):
        m = DAILY_RE.match(path.name)
        if not m:
            continue
        prefix, day = m.group(1), m.group(2)
        fact_path = DATA / f"eatclub_{prefix}_{day}.json"
        if not fact_path.exists():
            continue                      # no EatClub that day; nothing to owe
        try:
            record = json.loads(path.read_text())["sales"]
            fact = json.loads(fact_path.read_text())
        except (json.JSONDecodeError, KeyError, OSError):
            continue

        want_ex = _num(fact.get("giveaway_inc")) / 1.1
        want_covers = int(_num(fact.get("covers")))
        got_raw = record.get("eatclub_giveaway_ex_gst")
        got_ex = _num(got_raw)
        got_covers = int(_num(record.get("eatclub_covers")))

        # `None` is its own signal: a record written before the field existed.
        # It cannot "match" a zero give-away by accident.
        missing_field = got_raw is None and want_ex > 0
        if missing_field or abs(want_ex - got_ex) > TOL or want_covers != got_covers:
            out.append({
                "prefix": prefix, "venue": PREFIX_VENUE[prefix], "date": day,
                "published_ex": _num(record.get("revenue_ex_gst")),
                "deducted_ex": got_ex, "should_deduct_ex": want_ex,
                "covers_now": got_covers, "covers_fact": want_covers,
            })
    return out


def recompute(venue: str, day: str) -> bool:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "daily_aggregator.py"),
                        "--venue", venue, day],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    FAILED {venue} {day}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="recompute the stale days instead of only reporting")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing when everything is current")
    args = ap.parse_args()

    stale = stale_days()

    if not stale:
        if not args.quiet:
            print("eatclub staleness sweep: every published day reflects its give-away")
        return 0

    owed = sum(d["should_deduct_ex"] - d["deducted_ex"] for d in stale)
    print(f"\n*** {len(stale)} DAY(S) PUBLISHED WITHOUT THEIR EATCLUB GIVE-AWAY")
    print("    EatClub rings the full bill on the POS. Until the give-away is")
    print("    subtracted, these days carry revenue that was never received.")
    print(f"    Overstated by ${owed:,.2f} ex-GST in total.\n")
    for d in sorted(stale, key=lambda x: (x["date"], x["prefix"])):
        print(f"    {d['prefix']:<5} {d['date']}  published ${d['published_ex']:>10,.2f} ex   "
              f"deducted ${d['deducted_ex']:>8,.2f} of ${d['should_deduct_ex']:>8,.2f}   "
              f"covers {d['covers_now']} -> {d['covers_fact']}")
    print()

    if not args.fix:
        print("    Run with --fix to recompute them.")
        return 1

    print(f"    recomputing {len(stale)} day(s)...")
    failed = 0
    for d in sorted(stale, key=lambda x: (x["date"], x["prefix"])):
        if recompute(d["venue"], d["date"]):
            print(f"    rebuilt {d['venue']:<10} {d['date']}")
        else:
            failed += 1

    left = stale_days()
    if left or failed:
        print(f"\n    STILL STALE after --fix: {len(left)} day(s), {failed} failure(s)")
        return 1
    print("\n    all clear — every day now reflects its give-away")
    return 0


if __name__ == "__main__":
    sys.exit(main())
