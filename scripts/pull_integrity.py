#!/usr/bin/env python3
"""Scan a Daily Pull aggregator log for integrity markers and upsert
data/pull_integrity.json — the structured signal the health monitor reads so
the app self-reports what the verify-daily-pull-mari-hg scheduled task used to
check by hand.

Zero coupling to the revenue math: it only reads stdout the aggregator already
prints (the `*** ...` warnings and the reallocation line), attributed to a venue
by the `=== <venue> ===` headers daily_pull.yml emits.

Usage (from daily_pull.yml, after the aggregate step):
    python scripts/pull_integrity.py --log /tmp/pull.log --date 2026-07-29
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "pull_integrity.json"
KEEP_DAYS = 10

_VENUE_HDR = re.compile(r"^===\s+(\w+)\s+===\s*$")
_REALLOC = re.compile(r"Pulled (\d+) reallocated rows")


def scan(log_text: str) -> dict:
    """Return {venue: flags} parsed from the aggregator stdout."""
    per: dict = {}
    cur = None
    for line in log_text.splitlines():
        m = _VENUE_HDR.match(line.strip())
        if m:
            cur = m.group(1)
            per.setdefault(cur, {"narrowed": False, "sibling_missing": False,
                                 "mari_drift": False, "realloc_rows": 0})
            continue
        if cur is None:
            continue
        f = per[cur]
        if "STOW EXPORT LOOKS NARROWED" in line:
            f["narrowed"] = True
        if "SIBLING CSV MISSING" in line:
            f["sibling_missing"] = True
        if "MARI FILTER DRIFT" in line:
            f["mari_drift"] = True
        r = _REALLOC.search(line)
        if r:
            f["realloc_rows"] = int(r.group(1))
    return per


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--date", required=True, help="target trading date YYYY-MM-DD")
    a = ap.parse_args()

    lp = Path(a.log)
    per = scan(lp.read_text(errors="replace")) if lp.exists() else {}

    rec = {}
    if OUT.exists():
        try:
            rec = json.loads(OUT.read_text())
        except Exception:
            rec = {}
    days = rec.get("days", {})
    day = days.setdefault(a.date, {})
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for venue, flags in per.items():
        flags["checked_at"] = stamp
        day[venue] = flags  # last run for this venue+date wins (idempotent)

    cutoff = date.today().toordinal() - KEEP_DAYS
    for d in list(days):
        try:
            if datetime.strptime(d, "%Y-%m-%d").date().toordinal() < cutoff:
                del days[d]
        except Exception:
            pass

    OUT.write_text(json.dumps({"updated": stamp, "days": days}, indent=2))
    if per:
        summary = ", ".join(
            f"{v}:{'FLAG' if (f['narrowed'] or f['sibling_missing'] or f['mari_drift']) else 'ok'}"
            for v, f in per.items())
    else:
        summary = "(no venue sections found in log)"
    print(f"pull integrity {a.date}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
