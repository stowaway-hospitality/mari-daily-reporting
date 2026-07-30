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

# ---- comment parser (learned phrasings) ----
check("comment_no_break 'no break'", dt.comment_no_break("no break"))
check("comment_no_break 'No break taken thanks'", dt.comment_no_break("No break taken thanks"))
check("comment_no_break 'nb'", dt.comment_no_break("nb"))
check("comment_no_break typo 'ni break taken'", dt.comment_no_break("ni break taken"))
check("comment_no_break NOT '30 min break taken'", not dt.comment_no_break("30 min break taken"))
check("comment_no_break NOT 'good shift'", not dt.comment_no_break("good shift"))
check("declared_break '30 min break taken' == 30", dt.comment_declared_break_min("30 min break taken") == 30)
check("needs_human 'Started 5:30'", dt.comment_needs_human("Started 5:30"))
check("needs_human NOT 'good shift'", not dt.comment_needs_human("good shift"))

# ---- THE underpayment guard: no-break comment but a break was deducted ----
r = ts_at(PAST, 13, 0, 22, 30, roster_eh=22, break_min=30); r["Employee"] = 60; r["OperationalUnit"] = 8
r["EmployeeComment"] = "No break taken thanks"
d, why = dt.decide(r)
check("no-break comment + 30m deducted -> park (underpay risk)", d == dt.PARK and "UNDERPAY" in why)

# no-break comment + no break deducted = consistent, short shift -> approve (full pay)
r = ts_at(PAST, 11, 0, 15, 0, roster_eh=15, break_min=0); r["Employee"] = 60
r["EmployeeComment"] = "no break"
check("no-break comment + 0 deducted, short -> approve", dt.decide(r)[0] == dt.APPROVE)

# comment states a break that doesn't match the deduction -> park to reconcile
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=0, total=6.0); r["Employee"] = 60
r["EmployeeComment"] = "30 minute break taken"
check("declared 30m but 0 deducted -> park reconcile", dt.decide(r)[0] == dt.PARK)

# clock-time correction comment -> needs a human even if otherwise clean
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=30); r["Employee"] = 60
r["EmployeeComment"] = "forgot to sign in, started 9:30"
check("clock-correction comment -> park", dt.decide(r)[0] == dt.PARK)

# ---- clock/area comment parser ----
pc = dt.parse_clock_comment("Started at 5:15 forgot to clock on")
check("parse start + forgot", pc and pc["start"] == "5:15" and pc["forgot_clock"])
pc = dt.parse_clock_comment("Start at 3pm and finish 10pm")
check("parse start + finish", pc and pc["start"] == "3pm" and pc["finish"] == "10pm")
pc = dt.parse_clock_comment("2-6 Stow\nNo break\n6-10 HG")
check("parse area split", pc and pc["area"] and "split" in pc["area"])
check("parse 'good shift' -> none", dt.parse_clock_comment("good shift") is None)

# decide surfaces the structured correction (still parks)
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=30); r["Employee"] = 60
r["EmployeeComment"] = "Started at 5:15 forgot to clock on"
d, why = dt.decide(r)
check("clock comment -> park with detail", d == dt.PARK and "clock/area correction" in why and "5:15" in why)

# ---- Rule 1 / 1B rounding (corrections) ----
def _u(h, m):
    return int(datetime(PAST.year, PAST.month, PAST.day, h, m, tzinfo=dt.SYD).timestamp())
check("round time 17:08 -> 17:15", dt._round15_ts(_u(17,8)) == _u(17,15))
check("round time 17:22 -> 17:15", dt._round15_ts(_u(17,22)) == _u(17,15))
check("round time 22:38 -> 22:45", dt._round15_ts(_u(22,38)) == _u(22,45))
check("round break 22 -> 15", dt._round15_min(22) == 15)
check("round break 38 -> 45", dt._round15_min(38) == 45)
check("round break 30 -> 30", dt._round15_min(30) == 30)

# an off-grid break parks by default, but is APPROVE-able when correcting (we round it)
r = ts_at(PAST, 10, 0, 16, 0, roster_eh=16, break_min=20); r["Employee"] = 60
check("off-grid break parks by default", dt.decide(r)[0] == dt.PARK)
check("off-grid break approves when correctable", dt.decide(r, correctable=True)[0] == dt.APPROVE)

# rounded_view rounds times and keeps the break cross-check consistent
r = ts_at(PAST, 10, 8, 16, 7, roster_eh=16, break_min=30); r["Employee"] = 60
rv = dt.rounded_view(r)
check("rounded_view rounds start", rv["StartTime"] == _u(10,15))
check("rounded_view rounds end", rv["EndTime"] == _u(16,0))
check("rounded_view keeps break readable", dt.mealbreak_min(rv) == 30)

# a no-break underpay sheet is NEVER made correctable/approved
r = ts_at(PAST, 13, 0, 22, 30, roster_eh=22, break_min=30); r["Employee"] = 60; r["OperationalUnit"] = 8
r["EmployeeComment"] = "No break taken thanks"
check("underpay risk still parks even when correctable", dt.decide(r, correctable=True)[0] == dt.PARK)

# ---- missed-switch detection (Kris/Stephanie) + roster close hint ----
def seg(sh, eh, ou):
    return {"StartTime": _u(sh, 0), "EndTime": _u(eh, 0), "OperationalUnit": ou, "ou_name": {9:"Admin",15:"HG Floor"}.get(ou, f"ou{ou}")}

# Stephanie clocked one Admin sheet 12:00-20:15, rostered Admin 12-17 then HG Floor 17-20:30
segs = [seg(12,17,9), seg(17,20,15)]
r = ts_at(PAST, 12, 0, 20, 15, break_min=30); r["Employee"] = dt.STEPHANIE; r["OperationalUnit"] = 9
check("missed_switch detected", dt.missed_switch(r, segs) is not None)
check("missed-switch parks (not salaried-approve)", dt.decide(r, rosters={(dt.STEPHANIE, PAST.isoformat()): segs})[0] == dt.PARK)
# without rosters, salaried still auto-approves (unchanged)
check("no rosters -> salaried approve unchanged", dt.decide(r)[0] == dt.APPROVE)
# a salaried sheet with a single rostered area is NOT a missed-switch
check("single-area roster -> no missed switch", dt.missed_switch(r, [seg(12,20,9)]) is None)

# forgotten clock-off gets a roster close hint in its reason
r2 = ts_at(PAST, 17, 0, 0, 0, set_total=False); r2["IsInProgress"] = 1; r2["Employee"] = 60
rc = {(60, PAST.isoformat()): [seg(17, 22, 6)]}
d, why = dt.decide(r2, rosters=rc)
check("forgot clock-off -> park with close hint", d == dt.PARK and "close to rostered" in why)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
