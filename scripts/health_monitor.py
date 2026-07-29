#!/usr/bin/env python3
"""
System health monitor — the dead-man's switch for the unattended automation.

The pipeline runs jobs nobody watches: the invoice poller (every 30 min), the
Xero-approvals poller (every 2 min), the daily token refresh, the weekly pull.
If one dies — a token lapses, a script throws, launchd stops it — there is no
alarm; you find out when invoices quietly stop flowing. This writes a health
snapshot the app shows, so "it's been quiet" and "it's dead" stop looking the
same.

    python3 scripts/health_monitor.py     # writes data/system_health.json

Each check is a freshness test against a real signal (a log that updates every
cycle, a heartbeat, the token file's mtime). Status is ok / warn / down / unknown.

This runs ON the machine that runs the jobs (piggy-backed on the invoice poller),
so the snapshot's OWN age is the poller's dead-man's switch: if the file itself
goes stale, the poller that writes it has stopped — and the app flags that.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "system_health.json"
NOW = time.time()
_RANK = {"ok": 0, "unknown": 1, "warn": 2, "down": 3}


def _log_age_min(rel: str):
    p = ROOT / rel
    return (NOW - p.stat().st_mtime) / 60 if p.exists() else None


def _heartbeat_age_min(job: str):
    p = ROOT / "data" / "heartbeats" / f"{job}.json"
    if not p.exists():
        return None
    try:
        dt = datetime.fromisoformat(json.loads(p.read_text())["at"])
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except Exception:
        return None


def _status(age, warn_min, down_min):
    if age is None:
        return "unknown"
    if age > down_min:
        return "down"
    if age > warn_min:
        return "warn"
    return "ok"


def _oldest_queue_days():
    """Age (days) of the oldest invoice still sitting unapproved, or None."""
    p = ROOT / "dashboard" / "invoices" / "queue.json"
    if not p.exists():
        return None
    try:
        q = json.loads(p.read_text())
        rows = q if isinstance(q, list) else q.get("invoices", [])
        dates = []
        for r in rows:
            d = r.get("date")
            if d:
                try:
                    dates.append(datetime.fromisoformat(d[:10]))
                except Exception:
                    pass
        if not dates:
            return 0.0
        return (datetime.now() - min(dates)).days
    except Exception:
        return None


def build() -> dict:
    checks = []

    def add(name, detail, age, warn, down, unit="min"):
        checks.append({"name": name, "detail": detail,
                       "age": round(age, 1) if age is not None else None,
                       "unit": unit, "status": _status(age, warn, down)})

    # invoice poller — its log updates every 30-min cycle (work or not)
    add("Invoice poller", "reads the accounts@ inbox every 30 min",
        _log_age_min("invoice_poller.log"), 90, 180)
    # xero approvals — heartbeat every 2-min cycle
    add("Xero approvals poller", "posts approved bills every 2 min",
        _heartbeat_age_min("xero_approvals"), 15, 60)
    # xero token — refreshed daily (08:00)
    tok = _log_age_min(".secrets/xero_token_cache.json")
    add("Xero token", "auto-refreshes daily",
        (tok / 60) if tok is not None else None, 26, 48, unit="hr")
    # weekly xero pull
    wk = _log_age_min("xero_pull_launchd.log")
    add("Weekly Xero pull", "runs once a week",
        (wk / 60 / 24) if wk is not None else None, 8.5, 10, unit="day")

    # invoice queue — an invoice sitting unapproved too long. This is a WORKLOAD
    # signal (a bill needs a human), not an automation failure, so it caps at
    # "warn": a couple of chronically-parked old bills must never masquerade as
    # "the pipeline is down" — that's how alert fatigue starts.
    oq = _oldest_queue_days()
    checks.append({"name": "Invoice queue", "detail": "oldest bill awaiting approval",
                   "age": oq, "unit": "day", "advisory": True,
                   "status": ("unknown" if oq is None else "warn" if oq > 7 else "ok")})

    # overall reflects AUTOMATION health — the jobs that must keep running. An
    # advisory (workload) check can raise a warn but never a down on its own.
    core = [c for c in checks if not c.get("advisory")]
    overall = max((c["status"] for c in core), key=lambda s: _RANK[s]) if core else "unknown"
    if any(c["status"] == "warn" for c in checks) and _RANK[overall] < _RANK["warn"]:
        overall = "warn"
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall": overall, "checks": checks}


def main() -> int:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2))
    print(f"system health: {data['overall'].upper()}")
    for c in data["checks"]:
        a = f"{c['age']}{c['unit']}" if c["age"] is not None else "—"
        print(f"  {c['status']:8} {c['name']:24} {a:>10}  ({c['detail']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
