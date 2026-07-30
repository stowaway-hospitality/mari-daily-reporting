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


def _insights_pull_age_days(prefix="insights_stow_"):
    """Days since the newest Stowaway daily export. Stow trades 7 days a week, so
    it is the cleanest 'did the Daily Pull run' signal. None if no files."""
    latest = None
    for pth in (ROOT / "data").glob(f"{prefix}*.csv"):
        try:
            d = datetime.strptime(pth.stem.replace(prefix, "")[:10], "%Y-%m-%d").date()
            if latest is None or d > latest:
                latest = d
        except Exception:
            pass
    return None if latest is None else (datetime.now().date() - latest).days


def _csv_last_date_age_days(rel, date_col):
    """Age in days of the newest value in `date_col` of a CSV, or None."""
    import csv
    pth = ROOT / rel
    if not pth.exists():
        return None
    latest = None
    try:
        with pth.open() as f:
            for row in csv.DictReader(f):
                v = (row.get(date_col) or "").strip()[:10]
                try:
                    d = datetime.strptime(v, "%Y-%m-%d").date()
                    if latest is None or d > latest:
                        latest = d
                except Exception:
                    pass
    except Exception:
        return None
    return None if latest is None else (datetime.now().date() - latest).days


def _overheads_months_behind(rel="data/xero_overheads_monthly.csv"):
    """How many months the monthly overheads feed is behind the current month.
    0 = current month present, 1 = only last month (fine, month not closed)."""
    import csv
    pth = ROOT / rel
    if not pth.exists():
        return None
    latest = None
    try:
        with pth.open() as f:
            for row in csv.DictReader(f):
                v = (row.get("month") or "").strip()[:7]
                try:
                    y, m = (int(x) for x in v.split("-"))
                    key = y * 12 + m
                    if latest is None or key > latest:
                        latest = key
                except Exception:
                    pass
    except Exception:
        return None
    if latest is None:
        return None
    now = datetime.now()
    return (now.year * 12 + now.month) - latest


def _pull_integrity(rel="data/pull_integrity.json"):
    """(status, detail) from the record the Daily Pull writes each run — the
    replacement for the verify-daily-pull-mari-hg scheduled task. `narrowed`
    (Stow export filtered, HG bleeding revenue) is the one serious enough to go
    'down'; the rest are transient/benign warns."""
    pth = ROOT / rel
    if not pth.exists():
        return ("unknown", "no pull-integrity record yet")
    try:
        rec = json.loads(pth.read_text())
        days = rec.get("days", {})
        if not days:
            return ("unknown", "no pull-integrity record yet")
        latest = max(days)  # YYYY-MM-DD sorts lexically
        venues = days[latest]
        narrowed = [v for v, f in venues.items() if f.get("narrowed")]
        siblings = [v for v, f in venues.items() if f.get("sibling_missing")]
        drift = [v for v, f in venues.items() if f.get("mari_drift")]
        is_mon = datetime.strptime(latest, "%Y-%m-%d").weekday() == 0
        hg_missing_mon = (is_mon and "harry" in venues
                          and venues["harry"].get("realloc_rows", 0) == 0)
        if narrowed:
            return ("down", f"STOW export narrowed {latest} — HG revenue at risk")
        if hg_missing_mon:
            return ("warn", f"HG reallocation missing on Monday {latest}")
        if drift:
            return ("warn", f"Mari filter drift {latest}")
        if siblings:
            return ("warn", f"sibling CSV missing {latest} ({','.join(siblings)})")
        return ("ok", f"Mari filter holding, HG reallocation intact ({latest})")
    except Exception as e:
        return ("unknown", f"integrity record unreadable: {e}")


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

    # ---- daily sales pipeline + Xero feeds --------------------------------
    # Folds the verify-daily-pull-mari-hg and xero-weekly-pull scheduled tasks
    # into the app: the dashboard now self-reports what those runs used to check.
    add("Daily sales pull", "aggregates yesterday's Lightspeed exports",
        _insights_pull_age_days(), 1.6, 2.6, unit="day")
    add("Xero COGS feed", "weekly actual COGS from Xero",
        _csv_last_date_age_days("data/xero_cogs_weekly.csv", "week_ending"), 8.5, 12, unit="day")
    add("Xero overheads feed", "monthly overheads from Xero",
        _overheads_months_behind(), 1.5, 2.5, unit="mo")
    _ig_status, _ig_detail = _pull_integrity()
    checks.append({"name": "Pull integrity", "detail": _ig_detail,
                   "age": None, "unit": "", "status": _ig_status})

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
