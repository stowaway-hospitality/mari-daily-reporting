"""The missing-sales backstop: does it catch a real gap, and stay quiet on a closed day?

Written after 16 Aug 2026, when the check reported "every venue's recent trading
days have sales" while Stowaway's Saturday was blank. Two reasons, both fixed
here and both pinned by a test:

  1. it started at back=2, so YESTERDAY — the day most likely to be wrong — was
     the one day never examined;
  2. "does this weekday normally trade" was answered from the per-day json
     files, which only go back ~6 weeks, so a missing comparison week made a
     gap look like a closed day.

And the opposite error matters just as much: Harry Gatos is shut Tuesdays and
some Sundays, and crying wolf on every one of them is how a health panel gets
ignored. The decisive signal is wages — a venue that traded paid somebody.

Run: python3 scripts/test_missing_sales_check.py
"""
import csv
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import health_monitor as h  # noqa: E402

PASS, FAIL = [], []
TODAY = dt.date.today()
COLS = ["date", "revenue_ex_gst", "wages_dollars"]


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def build(tmp: Path, rows_by_venue: dict):
    """Write history CSVs + per-day json files into a fake repo root."""
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    for prefix, rows in rows_by_venue.items():
        with (tmp / "data" / f"{prefix}_daily_history.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        for r in rows:
            payload = {"data_status": {"lightspeed": "ok"},
                       "sales": {"revenue_ex_gst": float(r["revenue_ex_gst"])
                                 if r["revenue_ex_gst"] else None}}
            (tmp / "data" / f"{prefix}_daily_{r['date']}.json").write_text(json.dumps(payload))


def run(rows_by_venue):
    tmp = Path(tempfile.mkdtemp())
    build(tmp, rows_by_venue)
    old = h.ROOT
    h.ROOT = tmp
    try:
        return h._missing_sales_days(lookback=8)
    finally:
        h.ROOT = old


def series(days, revenue, wages, blank_on=()):
    """A venue trading every day for `days` days, blank on the given dates."""
    out = []
    for back in range(days, 0, -1):
        d = (TODAY - dt.timedelta(days=back)).isoformat()
        blank = d in blank_on
        out.append({"date": d,
                    "revenue_ex_gst": "" if blank else f"{revenue:.2f}",
                    "wages_dollars": f"{wages:.2f}"})
    return out


print("-- a REAL outage: yesterday blank, but staff were paid --")
yday = (TODAY - dt.timedelta(days=1)).isoformat()
r = run({"stow": series(40, 9000, 2500, blank_on={yday})})
check("flagged", r["status"] == "warn", str(r))
check("names the venue and the day", yday in r.get("detail", ""), str(r.get("detail")))
check("and it is YESTERDAY that was caught", "Stowaway" in r.get("detail", ""))

print("\n-- a CLOSED day: blank, and nobody was paid --")
r = run({"hg": [*series(40, 2500, 1800, blank_on={yday})][:-1]
              + [{"date": yday, "revenue_ex_gst": "", "wages_dollars": "0"}]})
check("not flagged", r["status"] == "ok", str(r.get("detail")))

print("\n-- a shut WEEKDAY: blank every week, small cleaning wage --")
tuesdays = {(TODAY - dt.timedelta(days=b)).isoformat()
            for b in range(0, 60)
            if (TODAY - dt.timedelta(days=b)).weekday() == 1}
rows = []
for back in range(40, 0, -1):
    d = (TODAY - dt.timedelta(days=back)).isoformat()
    shut = d in tuesdays
    rows.append({"date": d, "revenue_ex_gst": "" if shut else "2500.00",
                 "wages_dollars": "83.00" if shut else "1800.00"})
r = run({"hg": rows})
check("a venue shut every Tuesday is never flagged", r["status"] == "ok", str(r.get("detail")))

print("\n-- nothing readable at all is UNKNOWN, not ok --")
tmp = Path(tempfile.mkdtemp())
(tmp / "data").mkdir()
old = h.ROOT
h.ROOT = tmp
try:
    r = h._missing_sales_days(lookback=8)
finally:
    h.ROOT = old
check("reports unknown rather than a clean bill", r["status"] == "unknown", str(r))

print("\n" + "=" * 58)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)
