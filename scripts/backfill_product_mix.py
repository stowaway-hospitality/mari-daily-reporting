#!/usr/bin/env python3
"""Backfill data/product_mix/ from the committed Insights exports.

The full daily product mix is a prerequisite for the stock ledger
(INVENTORY_ARCHITECTURE.md). Going forward daily_aggregator.py writes it every
morning; this rebuilds the history we already hold, so the ledger starts with a
real baseline instead of one day.

HOW IT WORKS, AND WHY THIS WAY: it shells out to daily_aggregator.py with
--mix-only, once per venue-day. That is deliberate and is the whole point of
the flag:

  * Attribution is INHERITED, not re-derived. The mix has to agree with the
    P&L about which venue a line belongs to — Mari's slice, HG food on the
    Stow till, the symmetric food reallocation. Re-implementing that here
    would create the second copy the repo already regrets once
    (scripts/eatclub/config.py).
  * Nothing else is touched. --mix-only stops before the daily record and the
    history CSV are written, so backfilling cannot restate a published P&L
    number or a wage figure.

Dates come from the committed data/insights_*.csv files, so this only ever
rebuilds days we hold the source for. Idempotent: re-running overwrites each
mix file with the same content.

Run:
    python3 scripts/backfill_product_mix.py                  # everything we hold
    python3 scripts/backfill_product_mix.py --from 2026-07-01
    python3 scripts/backfill_product_mix.py --venue stowaway
    python3 scripts/backfill_product_mix.py --dry-run
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
MIX_DIR = DATA / "product_mix"
AGG = ROOT / "scripts" / "daily_aggregator.py"

# Mari has no till — she is the 'm' slice of the Stow export, so a Stow CSV is
# what makes her day backfillable. HG has its own till.
VENUES = {
    "stowaway":   ("stow", ("stow",)),
    "harry":      ("hg",   ("hg",)),
    "marilynas":  ("mari", ("stow",)),
}

DATE_RE = re.compile(r"insights_(?:(stow|hg|mari)_)?(\d{4}-\d{2}-\d{2})\.csv$")


def available() -> dict[str, set[date]]:
    """Which dates we hold an Insights export for, per till prefix."""
    have: dict[str, set[date]] = {"stow": set(), "hg": set(), "mari": set()}
    for f in DATA.glob("insights_*.csv"):
        m = DATE_RE.search(f.name)
        if not m:
            continue
        prefix, iso = m.group(1), m.group(2)
        # Unprefixed legacy files are Marilyna's old filtered export. They are
        # a witness, not a source (see daily_aggregator.py) — she is rebuilt
        # off the Stow till, so they cannot make a day backfillable.
        if prefix is None:
            continue
        have[prefix].add(date.fromisoformat(iso))
    return have


def parse_args(argv: list[str]) -> tuple[date | None, date | None, list[str], bool]:
    d_from = d_to = None
    venues = list(VENUES)
    dry = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--from":
            d_from = date.fromisoformat(argv[i + 1]); i += 2; continue
        if a == "--to":
            d_to = date.fromisoformat(argv[i + 1]); i += 2; continue
        if a == "--venue":
            v = argv[i + 1]
            if v not in VENUES:
                raise SystemExit(f"unknown venue {v!r}; expected one of {sorted(VENUES)}")
            venues = [v]; i += 2; continue
        if a == "--dry-run":
            dry = True; i += 1; continue
        raise SystemExit(f"unrecognised argument {a!r}")
    return d_from, d_to, venues, dry


def main() -> int:
    d_from, d_to, venues, dry = parse_args(sys.argv[1:])
    have = available()

    jobs: list[tuple[str, date]] = []
    for venue in venues:
        _prefix, needs = VENUES[venue]
        days = set.intersection(*(have[p] for p in needs)) if needs else set()
        for day in sorted(days):
            if d_from and day < d_from:
                continue
            if d_to and day > d_to:
                continue
            jobs.append((venue, day))

    jobs.sort(key=lambda j: (j[1], j[0]))
    print(f"{len(jobs)} venue-day(s) to backfill "
          f"({len({d for _, d in jobs})} dates, venues: {', '.join(venues)})")
    if dry:
        for venue, day in jobs[:10]:
            print(f"  would run: {venue} {day.isoformat()}")
        if len(jobs) > 10:
            print(f"  ... and {len(jobs) - 10} more")
        return 0

    MIX_DIR.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "REPO_ROOT": str(ROOT)}
    written = failed = notied = skipped = 0
    problems: list[str] = []

    for n, (venue, day) in enumerate(jobs, 1):
        out = subprocess.run(
            [sys.executable, str(AGG), "--venue", venue, "--mix-only", day.isoformat()],
            cwd=ROOT, env=env, capture_output=True, text=True)
        tail = out.stdout.strip().splitlines()[-1:] or [""]
        if out.returncode != 0:
            failed += 1
            problems.append(f"{venue} {day}: exit {out.returncode} — {out.stderr.strip()[-200:]}")
        elif "NO PRODUCT MIX" in out.stdout:
            # The source is not a product export (HG's reporting-group report
            # under the product filename). No mix is the honest answer.
            skipped += 1
            problems.append(f"{venue} {day}: no product mix — source is not a product export")
        elif "DOES NOT TIE" in out.stdout:
            notied += 1
            written += 1
            problems.append(f"{venue} {day}: written but does NOT tie")
        elif "Product mix:" in out.stdout:
            written += 1
        if n % 25 == 0 or n == len(jobs):
            print(f"  {n}/{len(jobs)} — {written} written, {skipped} skipped, "
                  f"{notied} not tying, {failed} failed  [{tail[0].strip()}]")

    print(f"\nbackfill complete: {written} mix file(s) written, {skipped} skipped, "
          f"{notied} did not tie, {failed} failed")
    if problems:
        print("problems:")
        for p in problems[:30]:
            print(f"  {p}")
        if len(problems) > 30:
            print(f"  ... and {len(problems) - 30} more")

    # Fail toward review: a day that does not tie must not pass unnoticed into
    # a stock deduction. The files are still written and flagged.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
