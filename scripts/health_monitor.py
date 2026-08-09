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

# Plain-English guidance shown on the home page for any check that is not OK, so a
# non-technical manager knows what a red/amber row means and what to safely do.
# Additive only — the renderer ignores these fields when absent.
ADVICE = {
    "Invoice poller": {
        "meaning": "Reads the supplier-bills inbox on the office Mac every 30 minutes.",
        "action": "Check the office Mac is on and awake - it resumes on its own and no bills are lost. Still red an hour after it is awake? Tell Zak.",
        "selfheal": "Recovers by itself once the Mac is awake.",
    },
    "Xero approvals poller": {
        "meaning": "Posts approved supplier bills into Xero every couple of minutes (office Mac).",
        "action": "Same office-Mac job - check it is awake. Approvals just wait until it runs; nothing is lost.",
        "selfheal": "Recovers once the Mac is awake.",
    },
    "Xero token": {
        "meaning": "The Xero login the finance jobs use, refreshed automatically each day.",
        "action": "Expired - Zak needs to re-log-in to Xero on the Mac. Wages and COGS keep showing the last good data until then.",
        "selfheal": "No - needs Zak to re-authenticate Xero.",
    },
    "Weekly Xero pull": {
        "meaning": "Pulls payroll and overheads from Xero once a week.",
        "action": "Behind - the weekly Xero pull needs re-running (needs the Mac and Zak's Xero login). Rarely urgent.",
        "selfheal": "Catches up on the next weekly run.",
    },
    "Daily sales pull": {
        "meaning": "Yesterday's Lightspeed sales landing in the dashboard.",
        "action": "Usually the Stowaway sales email has not arrived yet. It retries every 20 min until ~10am. Still missing after 10am? Ask Claude to check the Stow export landed and re-run the ingest, or tell Zak. Numbers fill in once it arrives.",
        "selfheal": "Retries through the morning; often fixes itself before 10am.",
    },
    "Xero COGS feed": {
        "meaning": "Weekly actual cost-of-goods from Xero (drives the margin figures).",
        "action": "The weekly Xero pull is behind - see 'Weekly Xero pull'. Margins show the last good week meanwhile.",
        "selfheal": "Catches up with the weekly Xero pull.",
    },
    "Xero overheads feed": {
        "meaning": "Monthly overheads from Xero (rent, utilities and the like).",
        "action": "Run the Xero pull to refresh. Benign early in a new month before it is closed.",
        "selfheal": "Catches up with the monthly Xero pull.",
    },
}


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
        return {"status": "unknown", "detail": "no pull-integrity record yet"}
    try:
        rec = json.loads(pth.read_text())
        days = rec.get("days", {})
        if not days:
            return {"status": "unknown", "detail": "no pull-integrity record yet"}
        latest = max(days)  # YYYY-MM-DD sorts lexically
        venues = days[latest]
        narrowed = [v for v, f in venues.items() if f.get("narrowed")]
        siblings = [v for v, f in venues.items() if f.get("sibling_missing")]
        drift = [v for v, f in venues.items() if f.get("mari_drift")]
        is_mon = datetime.strptime(latest, "%Y-%m-%d").weekday() == 0
        hg_missing_mon = (is_mon and "harry" in venues
                          and venues["harry"].get("realloc_rows", 0) == 0)
        if narrowed:
            return {"status": "down", "detail": f"STOW export narrowed {latest} — HG revenue at risk",
                    "meaning": "The single Stowaway till feeds all three venues; this checks it still does.",
                    "action": (f"URGENT - the STOW export was narrowed on {latest} and Harry Gatos revenue is "
                               "being dropped. Do NOT change it blindly in Lightspeed. Tell Zak today or ask "
                               "Claude - the Stowaway export must be the FULL SITE report."),
                    "selfheal": "No - the Lightspeed report must be put back."}
        if hg_missing_mon:
            return {"status": "warn", "detail": f"HG reallocation missing on Monday {latest}",
                    "meaning": "Harry Gatos' Monday food is carved from the Stow till.",
                    "action": f"HG's Monday reallocation didn't run for {latest} - check the Stow export landed for that day.",
                    "selfheal": "Clears once the day re-aggregates."}
        if drift:
            return {"status": "warn", "detail": f"Mari filter drift {latest}",
                    "meaning": "Marilyna's sales are carved from the Stow till and cross-checked against her own Lightspeed report.",
                    "action": (f"Minor: on {latest} a few Marilyna's rows weren't in her Lightspeed report, so the "
                               "filter drifted slightly. The revenue is still attributed - this is a nudge to tidy the "
                               "'Mari Daily Sales Auto' report in Lightspeed when convenient, not an outage."),
                    "selfheal": "Clears on its own once a clean day aggregates; only needs a Lightspeed fix if it keeps recurring."}
        if siblings:
            return {"status": "warn", "detail": f"sibling CSV missing {latest} ({','.join(siblings)})",
                    "meaning": "A venue's cross-check export was missing for the day.",
                    "action": f"A cross-check CSV was missing for {latest} ({','.join(siblings)}) - usually transient.",
                    "selfheal": "Usually clears on the next pull."}
        return {"status": "ok", "detail": f"Mari filter holding, HG reallocation intact ({latest})"}
    except Exception as e:
        return {"status": "unknown", "detail": f"integrity record unreadable: {e}"}


def _uber_feed(rel="data/uber_daily.csv"):
    """Uber Eats fee feed — freshness AND correctness.

    WHY this is a health check and not just a CI test: for four weeks the feed
    modelled commission as a flat 33% of sales, which overstated Mari's
    commission by ~$1,310 and understated her discretionary marketing by
    ~$1,172. The drift WAS detected — eleven consecutive runs wrote it to
    data/uber_pull.log — but a log nobody opens is not an alert, so the numbers
    stayed wrong until a human happened to ask. Now it surfaces here.

    The feed is exact (portal Service fees + Marketing lines), so every day's
    arithmetic closes: sales - commission - offers - refund == payout. If that
    stops holding, the feed has gone back to estimating.
    """
    pth = ROOT / rel
    if not pth.exists():
        return {"status": "unknown", "detail": "no Uber fee feed yet"}
    try:
        import csv as _csv
        from decimal import Decimal as _D
        with pth.open() as fh:
            rows = list(_csv.DictReader(fh))
        if not rows:
            return {"status": "unknown", "detail": "Uber fee feed empty"}
        broken, hot = [], []
        for r in rows:
            resid = (_D(r["sales_inc_gst"]) - _D(r["commission_inc_gst"])
                     - _D(r["offers_inc_gst"]) - _D(r.get("refund_inc_gst", "0"))
                     - _D(r["payout_inc_gst"]))
            if resid != 0:
                broken.append(f"{r['date']} {r['shop']}")
            sales = _D(r["sales_inc_gst"])
            if sales > 0 and _D(r["commission_inc_gst"]) / sales > _D("0.3301"):
                hot.append(f"{r['date']} {r['shop']}")
        if broken:
            return {"status": "down",
                    "detail": f"Uber fee split does not balance ({len(broken)} days, e.g. {broken[0]})",
                    "meaning": "Uber Eats fees are split into commission (unavoidable) and marketing (Zak's discretionary spend).",
                    "action": ("The Uber feed has gone back to ESTIMATING instead of reading the portal's actual "
                               "Service fees and Marketing lines. Delivery cost and marketing spend are both wrong "
                               "until it is fixed. Ask Claude to re-run the Uber pull for the affected days."),
                    "selfheal": "No - the pull method needs fixing, then the days re-pulled."}
        if hot:
            return {"status": "warn",
                    "detail": f"Uber commission above the 33% ceiling ({hot[0]})",
                    "meaning": "Uber's highest published rate is 30% + GST = 33% of sales.",
                    "action": f"A day is billing above Uber's own ceiling ({hot[0]}) - marketing has probably leaked into the commission column.",
                    "selfheal": "No - re-pull the affected day."}
        age = _csv_last_date_age_days(rel, "date")
        if age is None:
            return {"status": "unknown", "detail": "Uber feed dates unreadable"}
        if age > 3:
            return {"status": "warn", "detail": f"Uber fee feed {age}d behind",
                    "meaning": "The daily Uber pull records delivery volume and fees that never touch the till.",
                    "action": (f"The Uber pull has not landed for {age} days - usually the merchant-portal login "
                               "expired. Ask Claude to run the Uber pull; if it reports a login screen, Zak needs "
                               "to sign in to merchants.ubereats.com once."),
                    "selfheal": "Resumes on its own if the session is still valid."}
        return {"status": "ok", "detail": f"exact split, balances on all {len(rows)} days ({age}d old)"}
    except Exception as e:
        return {"status": "unknown", "detail": f"Uber feed unreadable: {e}"}


def _missing_sales_days(lookback=6):
    """Backstop for the sales auto-heal: flag any recent day where a venue has no
    sales but that weekday normally trades (so it is a real gap, not a closed day).
    Auto-recovery refills it within hours; a day that persists here means the
    export never arrived and needs a manual re-send from Lightspeed Insights."""
    import datetime as _dt
    PREF = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's"}
    today = _dt.date.today()

    def has_sales(prefix, d):
        try:
            j = json.load(open(f"data/{prefix}_daily_{d}.json"))
            return (j.get("data_status", {}).get("lightspeed") == "ok"
                    and bool(j.get("sales", {}).get("revenue_ex_gst")))
        except Exception:
            return False

    gaps = []
    for prefix in ("stow", "hg", "mari"):
        for back in range(2, lookback + 1):
            d = (today - _dt.timedelta(days=back)).isoformat()
            if has_sales(prefix, d):
                continue
            wk1 = has_sales(prefix, (today - _dt.timedelta(days=back + 7)).isoformat())
            wk2 = has_sales(prefix, (today - _dt.timedelta(days=back + 14)).isoformat())
            if wk1 or wk2:
                gaps.append(f"{PREF[prefix]} {d}")
    if not gaps:
        return {"status": "ok", "detail": "every venue's recent trading days have sales"}
    return {"status": "warn",
            "detail": "sales missing: " + ", ".join(gaps),
            "meaning": "A venue has no sales on a day it normally trades - usually a Lightspeed export that did not ingest.",
            "action": ("Auto-recovery re-pulls these from the mailbox within a few hours. If a day stays listed, "
                       "the export never arrived - re-send that day from Lightspeed Insights."),
            "selfheal": "The sales ingest retries missing days automatically on its next run."}


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

    # (Invoice queue advisory removed 2026-08-01 — supplier-bill approvals run in
    # Dext, not the app, so an "old unapproved bill" here is noise, not a signal.)

    # ---- daily sales pipeline + Xero feeds --------------------------------
    # Folds the verify-daily-pull-mari-hg and xero-weekly-pull scheduled tasks
    # into the app: the dashboard now self-reports what those runs used to check.
    add("Daily sales pull", "aggregates yesterday's Lightspeed exports",
        _insights_pull_age_days(), 1.6, 2.6, unit="day")
    add("Xero COGS feed", "weekly actual COGS from Xero",
        _csv_last_date_age_days("data/xero_cogs_weekly.csv", "week_ending"), 8.5, 12, unit="day")
    add("Xero overheads feed", "monthly overheads from Xero",
        _overheads_months_behind(), 1.5, 2.5, unit="mo")
    ig = _pull_integrity()
    _ig_check = {"name": "Pull integrity", "detail": ig.get("detail"),
                 "age": None, "unit": "", "status": ig.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if ig.get(_k):
            _ig_check[_k] = ig[_k]
    checks.append(_ig_check)

    ms = _missing_sales_days()
    _ms_check = {"name": "Sales completeness", "detail": ms.get("detail"),
                 "age": None, "unit": "", "status": ms.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if ms.get(_k):
            _ms_check[_k] = ms[_k]
    checks.append(_ms_check)

    uf = _uber_feed()
    _uf_check = {"name": "Uber fee feed", "detail": uf.get("detail"),
                 "age": None, "unit": "", "status": uf.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if uf.get(_k):
            _uf_check[_k] = uf[_k]
    checks.append(_uf_check)

    # overall reflects AUTOMATION health — the jobs that must keep running. An
    # advisory (workload) check can raise a warn but never a down on its own.
    # attach plain-English guidance for the home-page panel
    for _c in checks:
        _a = ADVICE.get(_c["name"])
        if _a:
            _c["meaning"] = _a.get("meaning")
            if _a.get("action"):
                _c["action"] = _a["action"]
            if _a.get("selfheal"):
                _c["selfheal"] = _a["selfheal"]
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
