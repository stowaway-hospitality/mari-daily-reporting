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
    hm._pages_drift = lambda *a, **k: monkey_ages.get("pagesdrift", {"status": "ok", "detail": "clean"})
    # Added 2026-08-10. Both must be stubbed or this test stops being hermetic:
    # _uber_direct_reconciled reads two CSVs off disk, and _workflow_failures
    # calls the GitHub API and returns "unknown" wherever there is no token —
    # which silently dragged `overall` off "ok" and failed two cases here.
    hm._uber_direct_reconciled = lambda *a, **k: monkey_ages.get("uberdirectrecon", {"status": "ok", "detail": "clean"})
    hm._workflow_failures = lambda *a, **k: monkey_ages.get("jobs", {"status": "ok", "detail": "clean"})
    hm._uber_vs_books = lambda *a, **k: monkey_ages.get("books", {"status": "ok", "detail": "clean"})
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

# ---- Uber vs the books -----------------------------------------------------
# The only Uber check that looks OUTSIDE the portal. DoorDash was dropped from
# Mari's delivery cost for two months and every internal guard stayed green,
# because a missing channel leaves no trace in a feed - the feed is just smaller.
out = _build_with(dict(allfresh, books={"status": "warn", "detail": "2026-06 feeds short A$624"}), 0)
check("feeds short of the books -> overall warn", out["overall"] == "warn")
check("books check is reported", any(c.get("name") == "Uber vs the books" for c in out["checks"]))

# ---- Automation jobs (failed Actions runs) ---------------------------------
# The one class of alert_check escalation with nowhere on screen to land until
# 2026-08-10: everything else it raises maps to a check here, and snapshot
# staleness is caught client-side from the snapshot's own timestamp.
out = _build_with(dict(allfresh, jobs={"status": "down", "detail": "Daily Pull failing"}), 0)
check("failing critical job -> overall down", out["overall"] == "down")

out = _build_with(dict(allfresh, jobs={"status": "warn", "detail": "Alias suggest failing"}), 0)
check("failing minor job -> overall warn", out["overall"] == "warn")

# No token (CI, or anyone's laptop) must not muddy the headline — see the
# advisory note in health_monitor.build().
out = _build_with(dict(allfresh, jobs={"status": "unknown", "detail": "no GitHub token"}), 0)
check("cannot read jobs -> stays ok, not unknown", out["overall"] == "ok")
check("unreadable jobs check is still reported", any(c.get("name") == "Automation jobs" for c in out["checks"]))

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

# The app reads Pages, not main. deploy_dashboard.yml is path-triggered and
# data/** was missing from it, so a data-only commit was correct in git and
# never reached the screen. Confirmed live on 2026-08-09: Pages was published at
# 62e704c2 while main had moved on twice, INCLUDING an 08:41 health snapshot
# committed because a check had changed status. The panel that reports outages
# was itself unpublishable. Warn, not down: the numbers are safe, just unseen.
out = _build_with(dict(allfresh, pagesdrift={"status": "warn", "detail": "app behind on 2 feeds"}), 0)
check("app behind the repo -> overall warn", out["overall"] == "warn")
check("pages drift check is reported",
      any(c.get("name") == "Published app is current" for c in out["checks"]))

# Offline (office Mac with no network) must never masquerade as an outage.
out = _build_with(dict(allfresh, pagesdrift={"status": "unknown", "detail": "unreachable"}), 0)
check("unreachable app -> not down", out["overall"] != "down")


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
