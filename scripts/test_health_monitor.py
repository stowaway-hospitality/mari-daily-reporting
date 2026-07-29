#!/usr/bin/env python3
"""
Guard the health monitor's judgement — the part that decides ok / warn / down.

The monitor is what tells you the automation died, so its logic must be exactly
right in two ways: a genuinely dead job must read 'down', and a chronic workload
nag (an old bill parked in the queue) must NEVER read 'down' — because a monitor
that cries wolf gets ignored, and then a real outage goes unseen.

    python3 scripts/test_health_monitor.py     # exit 0 = logic sound
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.health_monitor as hm

fails = []


def check(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# ---- _status thresholds ---------------------------------------------------
check("fresh -> ok", hm._status(5, 15, 60) == "ok")
check("past warn -> warn", hm._status(30, 15, 60) == "warn")
check("past down -> down", hm._status(90, 15, 60) == "down")
check("no signal -> unknown", hm._status(None, 15, 60) == "unknown")


# ---- overall reflects AUTOMATION, queue is advisory-only ------------------
def _build_with(monkey_ages, queue_days):
    hm._log_age_min = lambda rel: monkey_ages.get(rel)
    hm._heartbeat_age_min = lambda job: monkey_ages.get("hb:" + job)
    hm._oldest_queue_days = lambda: queue_days
    return hm.build()


# all core jobs fresh, but a bill has been parked 300 days
allfresh = {"invoice_poller.log": 10, "hb:xero_approvals": 2,
            ".secrets/xero_token_cache.json": 60 * 3, "xero_pull_launchd.log": 60 * 24 * 2}
out = _build_with(dict(allfresh), 300)
check("stuck old bill -> overall warn, never down", out["overall"] == "warn")
check("queue check is flagged advisory", any(c.get("advisory") for c in out["checks"]))

# a genuinely dead poller -> overall down
dead = dict(allfresh); dead["invoice_poller.log"] = 400        # ~7h, past its 180min down line
out = _build_with(dead, 0)
check("dead invoice poller -> overall down", out["overall"] == "down")

# everything healthy -> ok
out = _build_with(dict(allfresh), 0)
check("all healthy -> overall ok", out["overall"] == "ok")

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
