#!/usr/bin/env python3
"""Record every time a REPORTED number changes after the fact, and how settled it is.

WHY THIS EXISTS
---------------
Zak, 2026-08-10: "this week we have a massive jump to the reported numbers for
marilynas ... if my reported numbers are going to be changing, i need to know
what's happened."

He is right and the cause is ordinary: Xero bills arrive late, so a month that
looked closed keeps growing for weeks. Marilyna's July, as reported on each
weekly Xero pull:

    22 Jul   overheads $3,593   uber $2,189
    27 Jul             $4,667        $6,487
    03 Aug             $5,136        $8,529
    10 Aug             $5,663        $8,529      <- +$2,070 (+58%) and +$6,340 (+290%)

Nothing was wrong on any of those days. But July profit fell by ~$8,400 after the
month ended and NOTHING on screen said so. A number that moves silently is worse
than one that is wrong, because you cannot argue with what you cannot see.

WHAT IT DOES
------------
Append-only ledger at data/restatements.json. Each run compares the live feeds to
the last value recorded for that (venue, period, metric) and appends a revision
when it moved. No git history needed at runtime — the ledger IS the history, which
is why it survives a shallow CI checkout. `--backfill-from-git` seeds it once from
the commits that already exist.

    python3 scripts/build_restatements.py                  # append today's revisions
    python3 scripts/build_restatements.py --backfill-from-git
    python3 scripts/build_restatements.py --print

MATURITY, and why it is a judgement not a fact
----------------------------------------------
  provisional  the period is not closed yet, or its costs moved >= 2% last pull
  settling     closed, and the last pull moved it < 2%
  final        closed, and the last TWO pulls did not move it at all

"Final" is the only one that means "safe to bank". It is deliberately hard to
earn: two consecutive quiet pulls, because one quiet week is just a week with no
invoices in it.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "restatements.json"

OVERHEADS = DATA / "xero_overheads_monthly.csv"
COGS = DATA / "xero_cogs_weekly.csv"

# metric -> (venue, human label). Only the ones that actually move a reported
# number; the group-level columns are carried for stow/hg/mari via their own.
OH_METRICS = {
    "mari_overheads": ("mari", "Overheads"),
    "stow_overheads": ("stow", "Overheads"),
    "hg_overheads":   ("hg",   "Overheads"),
    "mari_uber_fees": ("mari", "Uber fees"),
    "meu_fees":       ("group", "me&u + Doshii fees"),
    "group_overheads_ex_rent": ("group", "Group overheads"),
}

MOVED = Decimal("0.005")        # cents. below this it did not move.
SETTLING_PCT = Decimal("2")     # under 2% on the last pull = settling, not provisional


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:                                        # noqa: BLE001
        return Decimal(0)


def _load() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text())
        except Exception:                                    # noqa: BLE001
            pass
    return {"generated": None, "series": {}}


def _observe(led: dict, key: str, venue: str, period: str, metric: str,
             label: str, value: Decimal, observed: str) -> bool:
    """Append a revision if the value moved. -> True when something was recorded."""
    s = led["series"].setdefault(key, {
        "venue": venue, "period": period, "metric": metric, "label": label,
        "revisions": [],
    })
    revs = s["revisions"]
    if revs and abs(_dec(revs[-1]["value"]) - value) < MOVED:
        return False
    revs.append({"observed": observed, "value": float(round(value, 2))})
    return True


def _seen(led: dict, key: str, observed: str) -> None:
    """Record that we looked and it was unchanged. Without this an append-only
    ledger cannot tell 'moved last week' from 'has not moved since May' — the last
    entry looks equally recent in both cases, and every closed month would read
    provisional forever."""
    s = led["series"].get(key)
    if s and (s.get("last_seen") or "") < observed:
        s["last_seen"] = observed


def _period_closed(period: str, today: date) -> bool:
    """A month is closed once we are past its last day; a week once past its Sunday."""
    try:
        if len(period) == 7:                                  # YYYY-MM
            y, m = int(period[:4]), int(period[5:7])
            return (today.year, today.month) > (y, m)
        return date.fromisoformat(period) < today             # week ending
    except Exception:                                         # noqa: BLE001
        return False


QUIET_DAYS_FINAL = 14      # two weekly pulls with nothing new coded to the period


def _days(a: str, b: str) -> int:
    try:
        return (date.fromisoformat(a) - date.fromisoformat(b)).days
    except Exception:                                        # noqa: BLE001
        return 0


def _maturity(s: dict, today: date) -> str:
    """How safe this number is to bank.

    Measured off the QUIET PERIOD — how long since it last moved — not off
    consecutive revisions, because the ledger only appends when something
    changes. `last_seen` is what makes 'nothing new for a fortnight' sayable.
    """
    revs = s.get("revisions") or []
    if not revs:
        return "provisional"
    if not _period_closed(s.get("period", ""), today):
        return "provisional"
    # Days since it last MOVED, measured from today — not from last_seen. A period
    # that has aged out of the feed (April is no longer in the Xero window) stops
    # being confirmed, and measuring from last_seen froze it at 0 and left every
    # long-closed month reading "provisional" forever.
    quiet = _days(today.isoformat(), revs[-1]["observed"])
    if quiet >= QUIET_DAYS_FINAL:
        return "final"
    if len(revs) < 2:
        return "settling"
    last, prev = _dec(revs[-1]["value"]), _dec(revs[-2]["value"])
    pct = (abs(last - prev) / prev * 100) if prev else Decimal(100)
    return "settling" if pct < SETTLING_PCT else "provisional"


def _rows_from_overheads(text: str):
    for r in csv.DictReader(text.splitlines()):
        month = (r.get("month") or "").strip()
        if not month:
            continue
        for col, (venue, label) in OH_METRICS.items():
            if col in r and (r.get(col) or "") != "":
                yield f"{venue}|{month}|{col}", venue, month, col, label, _dec(r[col])


def _rows_from_cogs(text: str):
    for r in csv.DictReader(text.splitlines()):
        wk, venue = (r.get("week_ending") or "").strip(), (r.get("venue") or "").strip()
        if not (wk and venue):
            continue
        yield (f"{venue}|{wk}|actual_cogs", venue, wk, "actual_cogs",
               "COGS (Xero purchases)", _dec(r.get("actual_cogs_ex_gst")))


def _git_revisions(rel: str):
    """(commit date, file text) oldest-first for a tracked file, or [] if shallow."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "log", "--reverse",
                              "--format=%H %ad", "--date=short", "--", rel],
                             capture_output=True, text=True, timeout=60)
        rows = []
        for line in (out.stdout or "").splitlines():
            sha, _, day = line.partition(" ")
            if not sha:
                continue
            blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{sha}:{rel}"],
                                  capture_output=True, text=True, timeout=60)
            if blob.returncode == 0:
                rows.append((day.strip(), blob.stdout))
        return rows
    except Exception:                                        # noqa: BLE001
        return []


def main() -> int:
    args = sys.argv[1:]
    led = _load()
    today = date.today()
    n = 0

    if "--backfill-from-git" in args:
        for rel, parser in (("data/xero_overheads_monthly.csv", _rows_from_overheads),
                            ("data/xero_cogs_weekly.csv", _rows_from_cogs)):
            revs = _git_revisions(rel)
            print(f"  {rel}: {len(revs)} commit(s) in history")
            for day, text in revs:
                for key, venue, period, metric, label, val in parser(text):
                    n += _observe(led, key, venue, period, metric, label, val, day)

    # today's live values, always
    stamp = today.isoformat()
    if OVERHEADS.exists():
        for key, venue, period, metric, label, val in _rows_from_overheads(OVERHEADS.read_text()):
            n += _observe(led, key, venue, period, metric, label, val, stamp)
            _seen(led, key, stamp)
    if COGS.exists():
        for key, venue, period, metric, label, val in _rows_from_cogs(COGS.read_text()):
            n += _observe(led, key, venue, period, metric, label, val, stamp)
            _seen(led, key, stamp)

    # derived, recomputed every run so a rule change reaches old rows
    for s in led["series"].values():
        revs = s["revisions"]
        first, last = _dec(revs[0]["value"]), _dec(revs[-1]["value"])
        s["first"] = float(round(first, 2))
        s["latest"] = float(round(last, 2))
        s["delta"] = float(round(last - first, 2))
        s["pct"] = float(round((last - first) / first * 100, 1)) if first else None
        s["revision_count"] = len(revs)
        s["last_seen"] = s.get("last_seen") or revs[-1]["observed"]
        s["last_moved"] = revs[-1]["observed"]
        s["quiet_days"] = _days(today.isoformat(), revs[-1]["observed"])
        s["maturity"] = _maturity(s, today)

    led["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUT.write_text(json.dumps(led, indent=1, sort_keys=True))
    moved = [s for s in led["series"].values() if s["revision_count"] > 1]
    print(f"restatements.json: {len(led['series'])} series, {len(moved)} restated, "
          f"{n} revision(s) recorded this run")

    if "--print" in args:
        for s in sorted(moved, key=lambda x: -abs(x["delta"]))[:15]:
            print(f"  {s['venue']:5} {s['period']:10} {s['label']:22} "
                  f"${s['first']:>10,.2f} -> ${s['latest']:>10,.2f} "
                  f"({s['delta']:+,.2f}, {s['revision_count']} revisions, {s['maturity']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
