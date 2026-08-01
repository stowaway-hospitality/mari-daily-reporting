#!/usr/bin/env python3
"""Publish the system-health snapshot so the LIVE dashboard stays current.

The health monitor WRITES `data/system_health.json` on the Mac every poller cycle,
but the file only reaches the site when it's committed and a deploy runs. Nothing
was doing that — the file had been committed exactly once — so the live panel
froze and (correctly) flagged itself stale. This closes the loop: it commits +
pushes the snapshot when a check's status/detail changed, or when the published
copy is older than HEARTBEAT_H (so the panel's staleness line is never crossed in
normal operation). It rides the pipeline's normal deploys; it does not force one.

Run hourly from launchd (com.stowaway.healthpublish). Isolated from the invoice
poller on purpose, so a hiccup here can never affect invoice ingestion.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

REPO = os.path.expanduser("~/Documents/STOW/Sales Reports/Daily Reporting")
F = "data/system_health.json"
HEARTBEAT_H = 6


def sh(*a):
    return subprocess.run(a, cwd=REPO, capture_output=True, text=True)


def sig(d):
    return [(c.get("name"), c.get("status"), c.get("detail")) for c in d.get("checks", [])]


def main():
    try:
        cur = json.load(open(os.path.join(REPO, F)))
    except Exception as e:
        print("no local snapshot:", e)
        return 0
    head = sh("git", "show", "HEAD:" + F)
    reason = ""
    if head.returncode != 0:
        reason = "first publish"
    else:
        try:
            old = json.loads(head.stdout)
        except Exception:
            old = {}
        if sig(old) != sig(cur):
            reason = "status changed"
        else:
            try:
                age_h = (datetime.now(timezone.utc)
                         - datetime.fromisoformat(old["generated"])).total_seconds() / 3600
                if age_h > HEARTBEAT_H:
                    reason = f"heartbeat {age_h:.1f}h"
            except Exception:
                reason = "unreadable published timestamp"
    if not reason:
        print("no publish needed")
        return 0
    sh("git", "add", F)
    sh("git", "-c", "user.name=Stowaway Mac", "-c", "user.email=zak@stowawaybar.com",
       "commit", "-q", "-m", f"chore: publish health snapshot ({reason})", F)
    p = sh("git", "push", "-q")
    if p.returncode != 0:
        sh("git", "pull", "--rebase", "--autostash", "-q")
        p = sh("git", "push", "-q")
    print("published:", reason, "| push", "ok" if p.returncode == 0 else "FAILED: " + (p.stderr or "")[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
