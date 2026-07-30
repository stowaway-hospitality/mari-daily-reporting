#!/usr/bin/env python3
"""Deputy timesheet triage — REPORT-ONLY (phase 1).

Reads unapproved timesheets from Deputy and decides, per the deputy-timesheet-
approval rules, what it WOULD do — auto-approve / correct-then-approve / park for
a human. It writes NOTHING back to Deputy. Output is data/deputy_triage.json plus
a printed summary, so the decisions can be validated against what Kris/Zak would
actually do before any write path is switched on (phase 2).

Design rule: when a timesheet does not clearly fit a safe auto-approve category,
it is PARKED. False-parks cost a human 30 seconds; a wrong auto-approve costs real
wages. Conservative by construction.

Auth: OAuth DEPUTY_TOKEN (read scope is enough for phase 1 — same token the daily
pull already uses). Endpoint: 831d4015123255.au.deputy.com/api/v1/*
"""
from __future__ import annotations
import json, os, sys, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deputy_triage.json"
HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN", "")
SYD = timezone(timedelta(hours=10))  # AEST — date attribution only

# ---- roster / policy constants (from deputy-timesheet-approval skill) --------
SALARIED = {3: "Zak", 1: "Kris", 5: "Nicola", 15: "Marssheel", 16: "Stephanie",
            41: "Bryony", 133: "Devon", 142: "Renan", 287: "Min", 297: "Pujan"}
ZAK, MARSSHEEL, RHYS, OLLY = 3, 15, 145, 284
FOH_OUS = {6, 13, 14, 15}          # Stow Bar, Stow Floor, HG Bar, HG Floor
FOH_OVERSTAY_CAP_H = 1.5
RUNAWAY_H = 14.0                    # single shift longer than this = forgotten clock-off
BREAK_MIN_SHIFT_H = 7.0            # casual must break at/over this; below it, no break is fine

# decision codes
APPROVE, CORRECT, PARK, SKIP = "would_approve", "would_correct_then_approve", "park_for_human", "skip"


def _req(method, path, body=None):
    url = HOST + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"OAuth {TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def fetch_unapproved(days=7):
    now = int(datetime.now(timezone.utc).timestamp())
    since = now - days * 86400
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


def _mealbreak_min(ts):
    """Deputy stores Mealbreak as seconds or 'HH:MM:SS' depending on endpoint."""
    mb = ts.get("Mealbreak")
    if isinstance(mb, (int, float)):
        return round(mb / 60)
    if isinstance(mb, str) and ":" in mb:
        h, m, *rest = mb.split(":")
        return int(h) * 60 + int(m)
    return 0


def decide(ts) -> tuple[str, str]:
    """Return (decision_code, reason). Pure function of one timesheet record."""
    emp = ts.get("Employee")
    start = ts.get("StartTime") or 0
    end = ts.get("EndTime") or 0

    if ts.get("IsLeave"):
        return SKIP, "approved leave — auto-generated timesheet, no action"

    today = datetime.now(SYD).date()
    start_d = datetime.fromtimestamp(start, SYD).date() if start else today
    if ts.get("IsInProgress"):
        if start_d >= today:
            return SKIP, "in progress on the current day — still on shift"
        return PARK, "in progress on a past day — needs closing to roster (Kris)"

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
        who = SALARIED[emp]
        # runaway already parked above; every other salaried overstay auto-approves
        return APPROVE, f"salaried ({who}) — overstay auto-approve rule"

    # ---- casuals ----
    ou = ts.get("OperationalUnit")
    mb = _mealbreak_min(ts)

    if dur_h >= BREAK_MIN_SHIFT_H and mb == 0:
        return PARK, f"casual {dur_h:.1f}h shift with no meal break — Kris reviews"

    if emp == RHYS:
        return APPROVE, "Rhys Taylor — overstays approved (non-runaway) per policy"
    if emp == OLLY and (overstay_h or 0) > 0:
        return PARK, "Olly — overstays always left for Kris per policy"

    if overstay_h is None:
        # no linked roster → cannot confirm the overstay is within the 1.5h cap
        return PARK, "no linked roster — overstay unverifiable, park for a human"

    if overstay_h <= 0:
        if mb % 15 != 0 and mb > 0:
            return CORRECT, f"clean but mealbreak {mb}m not on a 15m grid — round then approve"
        return APPROVE, "clean — within rostered finish"

    if ou in FOH_OUS and overstay_h <= FOH_OVERSTAY_CAP_H:
        return APPROVE, f"casual FOH overstay {overstay_h:.2f}h ≤ {FOH_OVERSTAY_CAP_H}h"
    if ou in FOH_OUS:
        return PARK, f"casual FOH overstay {overstay_h:.2f}h > {FOH_OVERSTAY_CAP_H}h — Kris"
    return PARK, f"casual overstay {overstay_h:.2f}h in non-FOH area (ou{ou}) — Kris"


def main() -> int:
    if not TOKEN:
        print("DEPUTY_TOKEN not set", file=sys.stderr)
        return 2
    rows = fetch_unapproved(7)
    buckets = {APPROVE: [], CORRECT: [], PARK: [], SKIP: []}
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
           "would_approve": buckets[APPROVE], "would_correct": buckets[CORRECT],
           "parked": buckets[PARK], "skipped": buckets[SKIP]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, indent=2))

    c = rec["counts"]
    print(f"Deputy triage (REPORT-ONLY) — {len(rows)} unapproved in last 7 days")
    print(f"  would auto-approve : {c[APPROVE]}")
    print(f"  would correct+appr : {c[CORRECT]}")
    print(f"  parked for a human : {c[PARK]}")
    print(f"  skipped (leave/now): {c[SKIP]}")
    for b in buckets[PARK]:
        print(f"    PARK  {b['employee']:22} {b['area']:12} — {b['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
