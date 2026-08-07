#!/usr/bin/env python3
"""Branch-proof, content-accurate health-snapshot publisher.

Two independent properties, both proof against the shared working tree being
parked on any branch (the Aug 2026 bug where a recipes feature branch froze the
live panel):

  * ACCURATE CONTENT — builds the snapshot from a main-pinned clone
    (~/.stowaway-ops/repo, fetch+reset each run) so the data-freshness checks
    reflect what's actually deployed on main; the machine-local runtime signals
    (poller log, xero token, heartbeats, weekly-pull log) are symlinked in from
    the live mounted tree so those stay real.
  * CORRECT DESTINATION — PUTs the snapshot to main via the GitHub Contents API,
    never a push on the mounted tree's current branch.

Runs hourly from launchd (com.stowaway.healthpublish) from a standalone path.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

REPO = "zakstowaway/mari-daily-reporting"
MOUNTED = os.path.expanduser("~/Documents/STOW/Sales Reports/Daily Reporting")
OPS = os.path.expanduser("~/.stowaway-ops")
CLONE = os.path.join(OPS, "repo")
FILE = "data/system_health.json"
HEARTBEAT_H = 6
PAT = open(os.path.join(MOUNTED, ".secrets", "github_pat_v2.txt")).read().strip()
REMOTE = f"https://x-access-token:{PAT}@github.com/{REPO}.git"
RUNTIME_LINKS = {
    "invoice_poller.log": "invoice_poller.log",
    "xero_pull_launchd.log": "xero_pull_launchd.log",
    ".secrets/xero_token_cache.json": ".secrets/xero_token_cache.json",
    "data/heartbeats": "data/heartbeats",
}


def sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True)


def ensure_clone():
    if not os.path.isdir(os.path.join(CLONE, ".git")):
        os.makedirs(OPS, exist_ok=True)
        sh("git", "clone", "--quiet", "--depth", "20", "--branch", "main", REMOTE, CLONE)
    else:
        sh("git", "remote", "set-url", "origin", REMOTE, cwd=CLONE)
        sh("git", "fetch", "--quiet", "--depth", "20", "origin", "main", cwd=CLONE)
        sh("git", "reset", "--quiet", "--hard", "origin/main", cwd=CLONE)
    for dst, src in RUNTIME_LINKS.items():
        d = os.path.join(CLONE, dst)
        s = os.path.join(MOUNTED, src)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.islink(d):
            if os.readlink(d) == s:
                continue
            os.remove(d)
        elif os.path.exists(d):
            shutil.rmtree(d) if os.path.isdir(d) else os.remove(d)
        try:
            os.symlink(s, d)
        except Exception:
            pass


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {PAT}", "Accept": "application/vnd.github+json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return json.loads(r.read() or "{}"), r.status
    except urllib.error.HTTPError as e:
        return e.read().decode(errors="replace"), e.code


def sig(d):
    return [(c.get("name"), c.get("status"), c.get("detail")) for c in d.get("checks", [])]


def main():
    ensure_clone()
    sys.path.insert(0, CLONE)
    import scripts.health_monitor as hm
    snap = hm.build()
    cur, st = api("GET", f"contents/{FILE}?ref=main")
    old, sha = None, None
    if st == 200 and isinstance(cur, dict):
        sha = cur.get("sha")
        try:
            old = json.loads(base64.b64decode(cur["content"]))
        except Exception:
            old = None
    reason = ""
    if old is None:
        reason = "first publish"
    elif sig(old) != sig(snap):
        reason = "status changed"
    else:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(old["generated"])).total_seconds() / 3600
            if age > HEARTBEAT_H:
                reason = f"heartbeat {age:.1f}h"
        except Exception:
            reason = "unreadable timestamp"
    if not reason:
        print("no publish needed")
        return 0
    body = {"message": f"chore: publish health snapshot ({reason})",
            "content": base64.b64encode(json.dumps(snap, indent=2).encode()).decode(),
            "branch": "main"}
    if sha:
        body["sha"] = sha
    res, st = api("PUT", f"contents/{FILE}", body)
    print(f"published to main: {reason} | HTTP {st}" + ("" if st in (200, 201) else f" | {str(res)[:200]}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
