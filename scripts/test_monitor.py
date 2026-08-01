#!/usr/bin/env python3
"""Battle-test the monitor/auto-remediator (alert_check.py) routing + loop safety.

Proves it: re-runs a transiently-failed SAFE workflow once, NEVER re-runs an
already-retried run (no loop) or a non-safe workflow, escalates the un-fixable,
silently re-dispatches the ingest when sales are 1.5-2.5d late, escalates when
badly behind or narrowed. Standalone: python3 scripts/test_monitor.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.alert_check as A

res = []


def ok(l, c):
    res.append((l, c))


calls = {"rerun": [], "dispatch": [], "notify": []}
A.rerun = lambda rid: (calls["rerun"].append(rid) or True)
A.dispatch = lambda wf, ref="main": (calls["dispatch"].append(wf) or True)
A.notify = lambda s, b: calls["notify"].append(b)
A.integrity_narrowed = lambda: []
A.health_stale_hours = lambda: None


def reset():
    calls["rerun"].clear(); calls["dispatch"].clear(); calls["notify"].clear()


# mixed failures: safe attempt-1, safe already-retried, unsafe
A.failed_runs = lambda: [
    {"id": 1, "path": ".github/workflows/daily_pull.yml", "name": "Daily Pull", "run_attempt": 1, "html_url": "u1"},
    {"id": 2, "path": ".github/workflows/rebuild_wages.yml", "name": "Rebuild Wages", "run_attempt": 2, "html_url": "u2"},
    {"id": 3, "path": ".github/workflows/deputy_triage.yml", "name": "Deputy Auto-Approve", "run_attempt": 1, "html_url": "u3"},
]
A.stow_sales_age_days = lambda: 1.0
reset(); A.main()
ok("retried the attempt-1 safe run once", calls["rerun"] == [1])
ok("did NOT retry the attempt-2 run (no loop)", 2 not in calls["rerun"])
ok("did NOT retry the unsafe deputy run", 3 not in calls["rerun"])
ok("escalated the 2 un-fixable", calls["notify"] and calls["notify"][0].count("needs a look") == 2)

A.failed_runs = lambda: []
A.stow_sales_age_days = lambda: 2.0
reset(); A.main()
ok("sales 2d stale -> dispatched ingest", calls["dispatch"] == ["ingest_insights_email.yml"])
ok("sales 2d stale -> silent (no notify)", calls["notify"] == [])

A.stow_sales_age_days = lambda: 3.0
reset(); A.main()
ok("sales 3d -> dispatched", calls["dispatch"] == ["ingest_insights_email.yml"])
ok("sales 3d -> escalated (upstream)", len(calls["notify"]) == 1)

A.stow_sales_age_days = lambda: 1.0
A.integrity_narrowed = lambda: ["stowaway"]
reset(); A.main()
ok("narrowed -> escalated", bool(calls["notify"]) and "NARROWED" in calls["notify"][0])

fails = [l for l, c in res if not c]
for l, c in res:
    print(("PASS" if c else "FAIL"), l)
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
