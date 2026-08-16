#!/usr/bin/env python3
"""Always-on monitor + AUTO-REMEDIATION (runs in GitHub Actions, every few hours).

Mode: "fix the safe, reversible things silently; only tell a human what a human
must handle." So it:
  1. Re-runs a transiently-failed pipeline job ONCE (safe allow-list only, capped
     by run_attempt so it never loops) — silent.
  2. Re-triggers the sales ingest if yesterday's sales data is behind — silent.
  3. ESCALATES (notify) only when: a safe retry didn't help, a non-safe workflow
     failed, sales are badly behind (upstream), the STOW export is narrowed, or the
     health snapshot itself is stale (Mac/publish chain down).
Every action is logged to the run output + the job summary — that's the audit log.

Inert until a channel (ALERT_EMAIL / ALERT_WEBHOOK) is set; API actions need
GH_TOKEN (the repo PAT). Never raises fatally.
"""
import os
import sys
import json
import glob
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.notify import notify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("GITHUB_REPOSITORY", "stowaway-hospitality/mari-daily-reporting")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
WINDOW_MIN = int(os.environ.get("ALERT_WINDOW_MIN", "200"))
HEALTH_URL = "https://app.stowawaybar.com/data/system_health.json"
INGEST_WF = "ingest_insights_email.yml"
SELF_WF = "health_alert.yml"
# workflows that are idempotent data refreshes — safe to auto-re-run
SAFE_RERUN = {"daily_pull.yml", "ingest_insights_email.yml", "sph_from_email.yml",
              "hourly_pull.yml", "roster_pull.yml", "rebuild_wages.yml",
              "deploy_dashboard.yml"}


def log(msg):
    print(msg, flush=True)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        try:
            open(sp, "a").write(msg + "\n")
        except Exception:
            pass


def gh(path, method="GET", body=None):
    if not TOKEN:
        return None, 0
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        raw = r.read()
        return (json.loads(raw) if raw else {}), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:
        print(f"gh {method} {path} error: {e}", file=sys.stderr)
        return None, 0


def failed_runs():
    d, _ = gh("actions/runs?status=failure&per_page=30")
    if not d:
        return []
    since = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MIN)
    out = []
    for r in d.get("workflow_runs", []):
        if r.get("conclusion") != "failure":
            continue
        ts = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        if ts >= since and os.path.basename(r.get("path", "")) != SELF_WF:
            out.append(r)
    return out


def rerun(run_id):
    _, st = gh(f"actions/runs/{run_id}/rerun-failed-jobs", method="POST")
    return st in (201, 204)


def dispatch(workflow_file, ref="main"):
    _, st = gh(f"actions/workflows/{workflow_file}/dispatches", method="POST", body={"ref": ref})
    return st in (201, 204)


def stow_sales_age_days():
    latest = None
    for p in glob.glob(os.path.join(ROOT, "data", "insights_stow_*.csv")):
        try:
            d = datetime.strptime(os.path.basename(p)[len("insights_stow_"):][:10], "%Y-%m-%d").date()
            latest = d if latest is None or d > latest else latest
        except Exception:
            pass
    return None if latest is None else (datetime.now().date() - latest).days


def integrity_narrowed():
    try:
        d = json.load(open(os.path.join(ROOT, "data", "pull_integrity.json")))
        day = max(d.get("days", {}))
        return [v for v, f in d["days"][day].items() if f.get("narrowed")]
    except Exception:
        return []


def health_stale_hours():
    try:
        h = json.loads(urllib.request.urlopen(HEALTH_URL, timeout=30).read())
        return (datetime.now(timezone.utc) - datetime.fromisoformat(h["generated"])).total_seconds() / 3600
    except Exception:
        return None


def main():
    fixes, escalate = [], []

    # 1. transiently-failed jobs -> retry once (safe list), else escalate
    for r in failed_runs():
        wf = os.path.basename(r.get("path", ""))
        name, url, attempt = r.get("name", wf), r.get("html_url", ""), r.get("run_attempt", 1)
        if wf in SAFE_RERUN and attempt < 2:
            if rerun(r["id"]):
                fixes.append(f"auto-retried {name}  {url}")
            else:
                escalate.append(f"couldn't auto-retry {name} — needs a look  {url}")
        elif wf in SAFE_RERUN:
            escalate.append(f"{name} failed again after an auto-retry — needs a look  {url}")
        else:
            escalate.append(f"{name} failed and isn't safe to auto-retry — needs a look  {url}")

    # 2. sales data behind -> re-trigger the ingest (silent); badly behind -> escalate
    age = stow_sales_age_days()
    if age is not None:
        if age >= 2.5:
            dispatch(INGEST_WF)
            escalate.append(f"Sales data ~{age}d behind — re-triggered the ingest but it isn't landing; "
                            f"likely upstream (Lightspeed schedule / email). Needs a look.")
        elif age >= 1.5:
            if dispatch(INGEST_WF):
                fixes.append(f"sales data ~{age}d stale — re-dispatched the sales ingest")

    # 3. gated / human-only escalations
    nar = integrity_narrowed()
    if nar:
        escalate.append(f"STOW export NARROWED ({', '.join(nar)}) — Harry Gatos revenue at risk. "
                        f"The Lightspeed report must be put back to the full site. Do NOT fix in Lightspeed blindly.")
    sh = health_stale_hours()
    if sh is not None and sh > 30:
        escalate.append(f"Health snapshot is ~{sh:.0f}h stale — the office Mac or the publish chain may be down.")

    for f in fixes:
        log("FIXED (silent): " + f)
    if escalate:
        body = ("Auto-fixed this run:\n" + ("\n".join("- " + f for f in fixes) if fixes else "- (none)")
                + "\n\nNeeds a human:\n" + "\n".join("- " + e for e in escalate)
                + "\n\nSee TROUBLESHOOTING.md, or ask Claude to investigate.")
        notify("pipeline needs a human", body)
        # Write the FULL text to the run summary, not just a count. With no push
        # channel configured (by design — the health panel is the channel), the
        # job summary is where a human looking at a red workflow reads what
        # actually went wrong, instead of scrolling raw logs for it.
        log(f"ESCALATED {len(escalate)} item(s)\n\n{body}")
    if not fixes and not escalate:
        log("all clear — nothing failed, sales current, health ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
