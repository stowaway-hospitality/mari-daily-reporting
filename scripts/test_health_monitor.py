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
    # new pipeline checks — default healthy unless a test overrides via monkey_ages
    hm._insights_pull_age_days = lambda *a, **k: monkey_ages.get("insights_pull", 1)
    hm._csv_last_date_age_days = lambda rel, col: monkey_ages.get("xero_cogs", 4)
    hm._overheads_months_behind = lambda *a, **k: monkey_ages.get("overheads", 0)
    hm._pull_integrity = lambda *a, **k: monkey_ages.get("integrity", {"status": "ok", "detail": "clean"})
    hm._uber_feed = lambda *a, **k: monkey_ages.get("uber", {"status": "ok", "detail": "clean"})
    hm._uber_direct = lambda *a, **k: monkey_ages.get("uberdirect", {"status": "ok", "detail": "clean"})
    return hm.build()


# all core jobs fresh, but a bill has been parked 300 days
allfresh = {"invoice_poller.log": 10, "hb:xero_approvals": 2,
            ".secrets/xero_token_cache.json": 60 * 3, "xero_pull_launchd.log": 60 * 24 * 2}
out = _build_with(dict(allfresh), 300)
check("no invoice-queue check (approvals live in Dext)", not any(c.get("name") == "Invoice queue" for c in out["checks"]))
check("all fresh -> overall ok without the queue", out["overall"] == "ok")

# a genuinely dead poller -> overall down
dead = dict(allfresh); dead["invoice_poller.log"] = 400        # ~7h, past its 180min down line
out = _build_with(dead, 0)
check("dead invoice poller -> overall down", out["overall"] == "down")

# everything healthy -> ok
out = _build_with(dict(allfresh), 0)
check("all healthy -> overall ok", out["overall"] == "ok")

# ---- Uber fee feed ---------------------------------------------------------
# The reason this check exists: the fee split was wrong for four weeks, the drift
# WAS detected on 11 consecutive runs, and every one of them wrote it to a log
# file instead of raising here. A guard that only logs is not a guard.
out = _build_with(dict(allfresh, uber={"status": "down", "detail": "split does not balance"}), 0)
check("uber split not balancing -> overall down", out["overall"] == "down")

out = _build_with(dict(allfresh, uber={"status": "warn", "detail": "feed 5d behind"}), 0)
check("stale uber feed -> overall warn", out["overall"] == "warn")

out = _build_with(dict(allfresh), 0)
check("healthy uber feed is reported", any(c.get("name") == "Uber fee feed" for c in out["checks"]))

# Uber Direct has no schedule of its own — it only moves when an invoice email
# fires the dispatch hook, so a dead hook is silent. Found dead 22d on 2026-08-09.
out = _build_with(dict(allfresh, uberdirect={"status": "down", "detail": "silent 22d"}), 0)
check("dead uber direct ingest -> overall down", out["overall"] == "down")
check("uber direct check is reported", any(c.get("name") == "Uber Direct ingest" for c in out["checks"]))

# ---- folded-in watchdog checks -------------------------------------------
# Stow export narrowed is the six-figure-silent-loss case -> must go down
out = _build_with(dict(allfresh, integrity={"status": "down", "detail": "stow narrowed"}), 0)
check("stow export narrowed -> overall down", out["overall"] == "down")

# Mari filter drift is a warn, never a down
out = _build_with(dict(allfresh, integrity={"status": "warn", "detail": "mari drift"}), 0)
check("mari drift -> overall warn (not down)", out["overall"] == "warn")

# stale Xero COGS feed (15 days > 12 down line) -> down
out = _build_with(dict(allfresh, xero_cogs=15), 0)
check("stale xero cogs -> overall down", out["overall"] == "down")

# overheads 2 months behind (> 1.5 warn line) -> at least warn
out = _build_with(dict(allfresh, overheads=2), 0)
check("overheads 2mo behind -> warn+", out["overall"] in ("warn", "down"))

# a missed Daily Pull (3 days since newest export > 2.6 down) -> down
out = _build_with(dict(allfresh, insights_pull=3), 0)
check("missed daily pull -> overall down", out["overall"] == "down")

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
