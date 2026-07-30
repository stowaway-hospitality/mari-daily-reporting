#!/usr/bin/env python3
"""Evaluate deputy_triage.decide() over a wide window of REAL timesheets — read
only, writes nothing to Deputy. Measures:
  * effectiveness — how much it would auto-approve vs park
  * SAFETY — of the sheets it would auto-approve, how many did a human NOT
    approve (those are the dangerous over-approvals; must be ~0 or explainable)
  * where it parks — generalised reason breakdown, and how often a human DID
    approve that same park case (candidates to safely relax, or correctly-caught
    human mistakes like the underpayments)
Output: data/deputy_triage_eval.json
Env: DEPUTY_TOKEN, EVAL_DAYS (default 365).
"""
from __future__ import annotations
import json, os, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import deputy_triage as T

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deputy_triage_eval.json"


def window(since, until, start=0):
    return T._req("POST", "/api/v1/resource/Timesheet/QUERY", {
        "search": {"s1": {"field": "StartTime", "type": "ge", "data": since},
                   "s2": {"field": "StartTime", "type": "lt", "data": until},
                   "s3": {"field": "Discarded", "type": "eq", "data": 0}},
        "join": ["EmployeeObject", "OperationalUnitObject", "RosterObject"],
        "max": 500, "start": start})


def gen(reason):
    return re.sub(r"[-+]?\d+\.?\d*", "N", reason)


def main():
    if not T.TOKEN:
        print("DEPUTY_TOKEN not set", file=sys.stderr); return 2
    days = int(os.environ.get("EVAL_DAYS", "365"))
    now = int(datetime.now(timezone.utc).timestamp())
    rows, t, win = [], now - days * 86400, 14 * 86400
    while t < now:
        u = min(t + win, now); start = 0
        while True:
            batch = window(t, u, start); rows.extend(batch)
            if len(batch) < 500:
                break
            start += 500
        t = u

    dec = Counter()
    park_reasons = Counter()
    approve_human = Counter()           # would-approve vs human TimeApproved
    park_human = Counter()              # (reason) -> human approved count
    over_approve_samples = []           # would-approve but human did NOT (danger)
    for ts in rows:
        code, reason = T.decide(ts)
        dec[code] += 1
        human = bool(ts.get("TimeApproved"))
        if code == T.APPROVE:
            approve_human["human_approved" if human else "human_NOT_approved"] += 1
            if not human and len(over_approve_samples) < 30:
                emp = ts.get("_DPMetaData", {}).get("EmployeeInfo", {}).get("DisplayName", str(ts.get("Employee")))
                over_approve_samples.append({"id": ts.get("Id"), "employee": emp, "reason": reason,
                                             "comment": (ts.get("EmployeeComment") or "")[:60]})
        elif code == T.PARK:
            g = gen(reason)
            park_reasons[g] += 1
            park_human[g + (" | human approved" if human else " | human parked too")] += 1

    n = len(rows)
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evaluated": n, "days": days,
        "decisions": dict(dec),
        "decision_pct": {k: round(100 * v / n, 1) for k, v in dec.items()} if n else {},
        "would_approve_vs_human": dict(approve_human),
        "OVER_APPROVE_count": approve_human.get("human_NOT_approved", 0),
        "over_approve_samples": over_approve_samples,
        "park_reasons": park_reasons.most_common(30),
        "park_vs_human": park_human.most_common(40),
    }
    OUT.write_text(json.dumps(summary, indent=2))
    print(f"evaluated {n} timesheets over {days} days")
    print(f"decisions: {dict(dec)}  ({summary['decision_pct']})")
    print(f"SAFETY — would-approve but human did NOT approve: {summary['OVER_APPROVE_count']}")
    print("top park reasons:")
    for r, c in park_reasons.most_common(15):
        print(f"  {c:5d}  {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
