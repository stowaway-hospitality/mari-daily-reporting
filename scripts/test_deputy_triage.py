#!/usr/bin/env python3
"""Guard the Deputy triage classifier. Safety property: anything uncertain PARKS,
and no wage-affecting case a human should own is ever auto-approved. Break values
use the REAL Deputy format (datetime string, time-part = duration) and are
cross-checked against paid hours — learned from 5,910 timesheets.
    python3 scripts/test_deputy_triage.py    # exit 0 = logic sound
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import deputy_triage as dt

fails = []
def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)

def mb_str(minutes):
    return f"2026-01-06T{minutes//60:02d}:{minutes%60:02d}:00+10:00"

def ts_at(day, sh, sm, eh, em, roster_eh=None, break_min=30, set_total=True, total=None):
    def u(h, m):
        return int(datetime(day.year, day.month, day.day, h, m, tzinfo=dt.SYD).timestamp())
    start, end = u(sh, sm), u(eh, em)
    r = {"Id": 1, "Employee": 6, "OperationalUnit": 6, "IsLeave": False,
         "IsInProgress": 0, "Discarded": 0, "TimeApproved": False,
         "StartTime": start, "EndTime": end, "Mealbreak": mb_str(break_min),
         "_DPMetaData": {}}
    if total is not None:
        r["TotalTime"] = total
    elif set_total and end > start:
        r["TotalTime"] = round((end - start) / 3600 - break_min / 60, 4)
    if roster_eh is not None:
        r["RosterObject"] = {"EndTime": u(roster_eh, 0)}
    return r

PAST = datetime.now(dt.SYD).date() - timedelta(days=2)
TODAY = datetime.now(dt.SYD).date()

# ---- decode learned from history ----
check("decode 30-min break string", dt.mealbreak_min({"Mealbreak": "2025-08-22T00:30:00+10:00"}) == 30)
check("decode no-break (T00:00:00)", dt.mealbreak_min({"Mealbreak": "2025-08-22T00:00:00+10:00"}) == 0)
check("decode 60-min break", dt.mealbreak_min({"Mealbreak": "2025-08-22T01:00:00+11:00"}) == 60)

# ---- gating branches (return before break logic) ----
r = ts_at(PAST, 9, 0, 16, 36); r["IsLeave"] = True
check("leave -> skip", dt.decide(r)[0] == dt.SKIP)
r = ts_at(TODAY, 10, 0, 0, 0, set_total=False); r["IsInProgress"] = 1
check("in-progress today -> skip", dt.decide(r)[0] == dt.SKIP)
r = ts_at(PAST, 10, 0, 0, 0, set_total=False); r["IsInProgress"] = 1
check("in-progress past -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 10, 0, set_total=False); r["EndTime"] = r["StartTime"] + int(15.5*3600)
check("runaway >14h -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 16, 30, roster_eh=16); r["Employee"] = dt.ZAK
check("Zak -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 17, 0, roster_eh=16); r["Employee"] = 1
check("salaried overstay -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 10, 0, 20, 0, roster_eh=16); r["Employee"] = dt.MARSSHEEL
check("Marssheel late end -> approve", dt.decide(r)[0] == dt.APPROVE)

# ---- break handling (the pay-safety core) ----
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=30); r["Employee"] = 60
check("clean 30m break within roster -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 11, 0, 15, 0, roster_eh=15, break_min=0); r["Employee"] = 60  # 4h no break
check("no break short shift -> approve (full pay)", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 9, 0, 16, 30, roster_eh=16, break_min=0); r["Employee"] = 60  # 7.5h no break
check("7.5h no break -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=20); r["Employee"] = 60  # off grid
check("off-grid break -> park (never auto-adjust)", dt.decide(r)[0] == dt.PARK)
# decode says 30m break but paid hours imply 0 break -> anomaly -> park
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=30, total=6.0); r["Employee"] = 60
check("break decode vs paid disagree -> park", dt.decide(r)[0] == dt.PARK)

# ---- overstay handling ----
r = ts_at(PAST, 10, 0, 17, 0, roster_eh=16, break_min=30); r["Employee"] = 60; r["OperationalUnit"] = 6
check("casual FOH +1.0h -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 10, 0, 18, 0, roster_eh=16, break_min=30); r["Employee"] = 60; r["OperationalUnit"] = 6
check("casual FOH +2.0h -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 16, 30, roster_eh=16, break_min=30); r["Employee"] = 60; r["OperationalUnit"] = 8
check("casual kitchen overstay -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 18, 0, roster_eh=16, break_min=30); r["Employee"] = dt.OLLY; r["OperationalUnit"] = 6
check("Olly overstay -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 16, 30, roster_eh=16, break_min=30); r["Employee"] = dt.RHYS; r["OperationalUnit"] = 8
check("Rhys overstay -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 10, 0, 17, 0, break_min=30); r["Employee"] = 60; r["OperationalUnit"] = 6  # no roster
check("no roster -> park", dt.decide(r)[0] == dt.PARK)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
