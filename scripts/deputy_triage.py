#!/usr/bin/env python3
"""Deputy timesheet triage — REPORT-ONLY (phase 1).

Reads unapproved Deputy timesheets and records what it WOULD auto-approve vs park
for a human, per the deputy-timesheet-approval rules. Writes NOTHING back — this
is the validation phase.

╔═ PAY-SAFETY RULE (do not weaken) ═════════════════════════════════════════════╗
║ A meal break that was NOT taken must be PAID — never silently deducted. A real ║
║ underpayment happened when a 30-min break was deducted from a shift the staff  ║
║ member worked straight through (clocked 1:00pm–10:30pm, no break, but recorded ║
║ as 9h instead of 9.5h). Therefore this tool NEVER edits a break and NEVER      ║
║ auto-approves a sheet whose break is anything other than plainly normal. Any   ║
║ break that is missing on a long shift, off the 15-min grid, or unreadable is   ║
║ PARKED for a human. A false-park costs 30 seconds; a wrong break costs wages.  ║
╚═══════════════════════════════════════════════════════════════════════════════╝

Auth: OAuth DEPUTY_TOKEN (read scope only for phase 1). Endpoint:
831d4015123255.au.deputy.com/api/v1/*
"""
from __future__ import annotations
import json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deputy_triage.json"
HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN", "")
SYD = timezone(timedelta(hours=10))

SALARIED = {3: "Zak", 1: "Kris", 5: "Nicola", 15: "Marssheel", 16: "Stephanie",
            41: "Bryony", 133: "Devon", 142: "Renan", 287: "Min", 297: "Pujan"}
ZAK, MARSSHEEL, RHYS, OLLY = 3, 15, 145, 284
FOH_OUS = {6, 13, 14, 15}
FOH_OVERSTAY_CAP_H = 1.5
RUNAWAY_H = 14.0
BREAK_MIN_SHIFT_H = 7.0

APPROVE, PARK, SKIP = "would_approve", "park_for_human", "skip"


def _req(method, path, body=None):
    url = HOST + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"OAuth {TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def fetch_unapproved(days=7):
    since = int(datetime.now(timezone.utc).timestamp()) - days * 86400
    body = {
        "search": {
            "s1": {"field": "StartTime", "type": "ge", "data": since},
            "s2": {"field": "TimeApproved", "type": "eq", "data": False},
            "s3": {"field": "Discarded", "type": "eq", "data": 0},
        },
        "join": ["EmployeeObject", "OperationalUnitObject", "RosterObject"],
        "max": 500,
    }
    return _req("POST", "/api/v1/resource/Timesheet/QUERY", body)


def mealbreak_min(ts):
    """Minutes of unpaid meal break, or None if it can't be read confidently.

    NEVER guess here — a wrong break is a wrong pay. Deputy's Mealbreak field is
    inconsistent (seconds int, 'H:M:S' duration, or a string carrying a datetime
    like '2026-07-27T00:30:00'). Only a plain small int/float or a clean short
    'H:M:S' duration is trusted; everything else returns None → parked."""
    mb = ts.get("Mealbreak")
    if mb in (None, "", 0, "0", "0:00:00"):
        return 0
    if isinstance(mb, bool):
        return None
    if isinstance(mb, (int, float)):
        return round(mb / 60) if mb > 60 else round(mb)  # seconds if big, else already minutes
    if isinstance(mb, str) and "T" not in mb and ":" in mb:
        parts = mb.split(":")
        try:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 12 and 0 <= m < 60:
                return h * 60 + m
        except Exception:
            return None
    return None


def decide(ts) -> tuple[str, str]:
    """(decision, reason) for one timesheet. Pure function; parks when unsure."""
    emp = ts.get("Employee")
    start = ts.get("StartTime") or 0
    end = ts.get("EndTime") or 0

    if ts.get("IsLeave"):
        return SKIP, "approved leave — auto-generated timesheet, no action"

    today = datetime.now(SYD).date()
    start_d = datetime.fromtimestamp(start, SYD).date() if start else today
    if ts.get("IsInProgress"):
        return (SKIP, "in progress on the current day — still on shift") if start_d >= today \
            else (PARK, "in progress on a past day — needs closing to roster (Kris)")

    dur_h = (end - start) / 3600 if end and start else 0
    end_d = datetime.fromtimestamp(end, SYD).date() if end else start_d
    if dur_h > RUNAWAY_H or end_d > start_d:
        return PARK, f"runaway/overnight ({dur_h:.1f}h) — forgotten clock-off (Kris)"

    if emp == ZAK:
        return PARK, "Zak's own timesheet — needs manual shift splits"

    ro = ts.get("RosterObject") or {}
    roster_end = ro.get("EndTime")
    overstay_h = (end - roster_end) / 3600 if roster_end else None

    if emp in SALARIED:
        return APPROVE, f"salaried ({SALARIED[emp]}) — overstay auto-approve rule"

    # ---- casuals ----
    ou = ts.get("OperationalUnit")
    mb = mealbreak_min(ts)   # None = unreadable, 0 = no break, else minutes

    # PAY-SAFETY gate — break must be plainly normal before we ever auto-approve.
    if mb is None:
        return PARK, "meal break unreadable — park (never guess a break = never mis-pay)"
    if dur_h >= BREAK_MIN_SHIFT_H and mb == 0:
        return PARK, f"casual {dur_h:.1f}h shift, no break recorded — pay in FULL, Kris confirms compliance"
    if mb % 15 != 0:
        return PARK, f"meal break {mb}m off the 15-min grid — park (breaks are never auto-adjusted)"

    if emp == RHYS:
        return APPROVE, "Rhys Taylor — overstays approved (non-runaway) per policy"
    if emp == OLLY and (overstay_h or 0) > 0:
        return PARK, "Olly — overstays always left for Kris per policy"
    if overstay_h is None:
        return PARK, "no linked roster — overstay unverifiable, park for a human"
    if overstay_h <= 0:
        return APPROVE, "clean — within rostered finish, break normal"
    if ou in FOH_OUS and overstay_h <= FOH_OVERSTAY_CAP_H:
        return APPROVE, f"casual FOH overstay {overstay_h:.2f}h ≤ {FOH_OVERSTAY_CAP_H}h, break normal"
    if ou in FOH_OUS:
        return PARK, f"casual FOH overstay {overstay_h:.2f}h > {FOH_OVERSTAY_CAP_H}h — Kris"
    return PARK, f"casual overstay {overstay_h:.2f}h in non-FOH area (ou{ou}) — Kris"


def main() -> int:
    if not TOKEN:
        print("DEPUTY_TOKEN not set", file=sys.stderr)
        return 2
    rows = fetch_unapproved(7)
    buckets = {APPROVE: [], PARK: [], SKIP: []}
    for ts in rows:
        code, reason = decide(ts)
        emp_info = ts.get("_DPMetaData", {}).get("EmployeeInfo", {})
        ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
        buckets[code].append({
            "timesheet_id": ts.get("Id"),
            "employee": emp_info.get("DisplayName", str(ts.get("Employee"))),
            "area": ou_info.get("OperationalUnitName", ""),
            "start": ts.get("StartTime"), "end": ts.get("EndTime"),
            "reason": reason,
        })
    rec = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": "report-only",
           "counts": {k: len(v) for k, v in buckets.items()},
           "would_approve": buckets[APPROVE], "parked": buckets[PARK], "skipped": buckets[SKIP]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, indent=2))

    c = rec["counts"]
    print(f"Deputy triage (REPORT-ONLY) — {len(rows)} unapproved in last 7 days")
    print(f"  would auto-approve : {c[APPROVE]}")
    print(f"  parked for a human : {c[PARK]}")
    print(f"  skipped (leave/now): {c[SKIP]}")
    for b in buckets[PARK]:
        print(f"    PARK  {b['employee']:22} {b['area']:14} — {b['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
