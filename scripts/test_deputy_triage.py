#!/usr/bin/env python3
"""Guard the Deputy triage classifier — the part that decides approve vs park.

The safety property: anything uncertain must PARK, and no wage-affecting write is
ever proposed for a case a human should own (Zak's splits, Olly, runaways,
non-FOH overstays, unrostered overstays). A false-park is fine; a wrong approve is
not.
    python3 scripts/test_deputy_triage.py     # exit 0 = logic sound
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

def ts_at(day, sh, sm, eh, em, roster_eh=None, roster_em=0):
    """Build a timesheet dict for a shift on `day` (a date)."""
    def u(h, m, d=day):
        return int(datetime(d.year, d.month, d.day, h, m, tzinfo=dt.SYD).timestamp())
    r = {"Id": 1, "Employee": 6, "OperationalUnit": 6, "IsLeave": False,
         "IsInProgress": 0, "Discarded": 0, "TimeApproved": False,
         "StartTime": u(sh, sm), "EndTime": u(eh, em), "Mealbreak": 1800,
         "_DPMetaData": {}}
    if roster_eh is not None:
        r["RosterObject"] = {"EndTime": u(roster_eh, roster_em)}
    return r

PAST = datetime.now(dt.SYD).date() - timedelta(days=2)
TODAY = datetime.now(dt.SYD).date()

# leave record
r = ts_at(PAST, 9, 0, 16, 36); r["IsLeave"] = True
check("leave -> skip", dt.decide(r)[0] == dt.SKIP)

# in-progress today vs past
r = ts_at(TODAY, 10, 0, 0, 0); r["IsInProgress"] = 1
check("in-progress today -> skip", dt.decide(r)[0] == dt.SKIP)
r = ts_at(PAST, 10, 0, 0, 0); r["IsInProgress"] = 1
check("in-progress past day -> park", dt.decide(r)[0] == dt.PARK)

# runaway / overnight
r = ts_at(PAST, 10, 0, 10, 0); r["EndTime"] = r["StartTime"] + int(15.5*3600)
check("runaway >14h -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 14, 0, 11, 0, roster_eh=22)  # end next-day 11am
r["EndTime"] = int(datetime(PAST.year, PAST.month, PAST.day, 11, 0, tzinfo=dt.SYD).timestamp()) + 86400
check("overnight -> park", dt.decide(r)[0] == dt.PARK)

# Zak's own
r = ts_at(PAST, 10, 0, 16, 30, roster_eh=16); r["Employee"] = dt.ZAK
check("Zak -> park (manual splits)", dt.decide(r)[0] == dt.PARK)

# salaried overstay -> approve
r = ts_at(PAST, 10, 0, 17, 0, roster_eh=16); r["Employee"] = 1  # Kris
check("salaried overstay -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 10, 0, 20, 0, roster_eh=16); r["Employee"] = dt.MARSSHEEL
check("Marssheel late end -> approve", dt.decide(r)[0] == dt.APPROVE)

# casual FOH overstay within / over cap
r = ts_at(PAST, 10, 0, 17, 0, roster_eh=16); r["Employee"] = 60; r["OperationalUnit"] = 6
check("casual FOH +1.0h -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 10, 0, 18, 0, roster_eh=16); r["Employee"] = 60; r["OperationalUnit"] = 6
check("casual FOH +2.0h -> park", dt.decide(r)[0] == dt.PARK)

# casual non-FOH overstay -> park regardless
r = ts_at(PAST, 10, 0, 16, 30, roster_eh=16); r["Employee"] = 60; r["OperationalUnit"] = 8  # Stow Kitchen
check("casual kitchen overstay -> park", dt.decide(r)[0] == dt.PARK)

# per-employee exceptions
r = ts_at(PAST, 10, 0, 18, 0, roster_eh=16); r["Employee"] = dt.OLLY; r["OperationalUnit"] = 6
check("Olly overstay -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 10, 0, 16, 30, roster_eh=16); r["Employee"] = dt.RHYS; r["OperationalUnit"] = 8
check("Rhys overstay (non-runaway) -> approve", dt.decide(r)[0] == dt.APPROVE)

# break compliance
r = ts_at(PAST, 9, 0, 16, 30, roster_eh=16); r["Employee"] = 60; r["Mealbreak"] = 0  # 7.5h no break
check("casual 7.5h no break -> park", dt.decide(r)[0] == dt.PARK)
r = ts_at(PAST, 11, 0, 16, 0, roster_eh=16); r["Employee"] = 60; r["Mealbreak"] = 0  # 5h no break, on roster
check("casual 5h no break, clean -> approve", dt.decide(r)[0] == dt.APPROVE)

# clean vs mealbreak grid
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16); r["Employee"] = 60; r["Mealbreak"] = 1800
check("clean within roster -> approve", dt.decide(r)[0] == dt.APPROVE)
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16); r["Employee"] = 60; r["Mealbreak"] = 20*60  # 20m off-grid
check("off-grid mealbreak -> park (never auto-adjust a break)", dt.decide(r)[0] == dt.PARK)
# the real Deputy break value that crashed the first run must park, never guess/crash
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16); r["Employee"] = 60; r["Mealbreak"] = "2026-07-27T00:30:00"
check("datetime-style break value -> park (no crash, no guess)", dt.decide(r)[0] == dt.PARK)
# no break on a short shift is fine — full pay, nothing deducted
r = ts_at(PAST, 11, 0, 15, 0, roster_eh=15); r["Employee"] = 60; r["Mealbreak"] = 0  # 4h no break
check("no break, short shift -> approve (full pay)", dt.decide(r)[0] == dt.APPROVE)

# unrostered overstay -> park (cannot verify cap)
r = ts_at(PAST, 10, 0, 17, 0); r["Employee"] = 60; r["OperationalUnit"] = 6  # no RosterObject
check("no roster -> park", dt.decide(r)[0] == dt.PARK)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
