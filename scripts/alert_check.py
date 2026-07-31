#!/usr/bin/env python3
"""Runs in GitHub Actions a few times a day and raises a LOUD alert on two signals:
  1) any pipeline workflow that FAILED in the trailing window, and
  2) the live system-health snapshot reporting overall == "down".
Either one -> a short alert via scripts/notify.py. Inert/no-op until a channel
(ALERT_EMAIL or ALERT_WEBHOOK) is configured. Health-down is throttled to one
slot a day to avoid nagging; pipeline failures alert whenever they're fresh.
"""
import os
import sys
import json
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.notify import notify

REPO = os.environ.get("GITHUB_REPOSITORY", "zakstowaway/mari-daily-reporting")
WINDOW_MIN = int(os.environ.get("ALERT_WINDOW_MIN", "200"))
HEALTH_URL = "https://app.stowawaybar.com/data/system_health.json"


def failed_runs():
    """Recently-failed workflow runs (naturally de-duped by the trailing window)."""
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        return []
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/actions/runs?status=failure&per_page=30",
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        since = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MIN)
        out = []
        for r in data.get("workflow_runs", []):
            if r.get("conclusion") != "failure":
                continue
            ts = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            if ts >= since:
                out.append(f"- {r['name']} — {r['created_at'][:16].replace('T', ' ')} UTC\n  {r['html_url']}")
        return out
    except Exception as e:
        print(f"failed_runs check error: {e}", file=sys.stderr)
        return []


def health_down():
    """Live system-health snapshot reporting overall == down, with the fixes."""
    try:
        h = json.loads(urllib.request.urlopen(HEALTH_URL, timeout=30).read())
        if h.get("overall") == "down":
            bad = [c for c in h.get("checks", []) if c.get("status") == "down"]
            lines = [f"- {c['name']}: {c.get('action') or c.get('detail', '')}" for c in bad]
            return "System health is DOWN:\n" + "\n".join(lines)
    except Exception as e:
        print(f"health check error: {e}", file=sys.stderr)
    return None


def main():
    msgs = []
    fr = failed_runs()
    if fr:
        msgs.append("Pipeline job(s) failed:\n" + "\n".join(fr))
    # throttle the health-down nag to one daily slot (06:00 AEST ~ 20:00 UTC),
    # unless forced for a manual test
    if datetime.now(timezone.utc).hour == 20 or os.environ.get("ALERT_FORCE_HEALTH") == "1":
        hd = health_down()
        if hd:
            msgs.append(hd)
    if not msgs:
        print("all clear — no fresh failures, health not down")
        return 0
    body = "\n\n".join(msgs) + "\n\nWhat to do: see TROUBLESHOOTING.md, or ask Claude to investigate."
    notify("pipeline needs attention", body)
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
