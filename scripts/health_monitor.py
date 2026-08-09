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


SELF = "Published app is current"


def _pages_drift(feeds=(("data/stow_daily_history.csv", "Stowaway daily history"),
                        ("data/hg_daily_history.csv", "Harry Gatos daily history"),
                        ("data/mari_daily_history.csv", "Marilyna's daily history"),
                        ("data/uber_daily.csv", "Uber fees"),
                        ("data/uber_direct_daily.csv", "Uber Direct fees")),
                 base="https://app.stowawaybar.com", computed=None):
    """Is the APP showing the numbers the repo actually has?

    The dashboard reads its feeds from GitHub Pages, not from main. Pages only
    republishes when a deploy runs, and deploy_dashboard.yml is triggered by
    PATHS. data/** was not among them, so for a long time a commit that touched
    only data/ was correct in git and invisible on screen — no error, no red,
    just yesterday's figures rendered with today's confidence.

    That is the failure mode this whole file exists to catch, aimed at the file
    itself: the health snapshot is ALSO a data/ file, so a check could flip to
    'down', publish, and never reach the panel meant to display it.

    The path fix means this should now read ok permanently. The check stays
    because it tests the OUTCOME (are the app's numbers current) rather than the
    mechanism (did a workflow fire), so it still holds if the paths are edited,
    if Pages fails to build, or if a future writer bypasses the trigger again.

    Two comparisons, because they fail differently:

      1. The newest DATE in each feed. Dates, not bytes — a cosmetic reformat is
         not an outage and an in-flight deploy must not cry wolf. Catches the
         app rendering older figures than the repo holds.
      2. The published snapshot's own verdict vs the one just computed. Feed
         dates alone would NOT have caught the 2026-08-09 case, where the drift
         was a health snapshot that had flipped status and never shipped. With
         alerting deliberately kept on-screen rather than emailed, a stale panel
         IS the outage: it reports "ok" with no way to know it is out of date.

    Self-reference is avoided by excluding this check from comparison (2), so it
    converges in one cycle instead of oscillating against its own output.
    Unreachable network (the office Mac offline) is 'unknown', never 'down'.
    """
    import urllib.request

    def _last_date(text):
        best = None
        for line in text.splitlines()[1:]:
            cell = line.split(",", 1)[0].strip()
            if len(cell) == 10 and cell[4] == "-" and cell[7] == "-":
                if best is None or cell > best:
                    best = cell
        return best

    behind = []
    try:
        for rel, label in feeds:
            local = ROOT / rel
            if not local.exists():
                continue
            mine = _last_date(local.read_text())
            if mine is None:
                continue
            with urllib.request.urlopen(f"{base}/{rel}", timeout=15) as r:
                theirs = _last_date(r.read().decode("utf-8", "replace"))
            if theirs is None or theirs < mine:
                behind.append(f"{label} (app {theirs or 'none'}, repo {mine})")

        if computed is not None:
            with urllib.request.urlopen(f"{base}/data/system_health.json", timeout=15) as r:
                live = json.loads(r.read().decode("utf-8", "replace"))
            def _flagged(rows):
                return {c.get("name") for c in rows
                        if c.get("status") in ("warn", "down")
                        and c.get("name") != SELF}
            was, now = _flagged(live.get("checks", [])), _flagged(computed)
            if was != now:
                missing = sorted(now - was) or sorted(was - now)
                behind.append(f"health panel itself (showing {live.get('overall', '?')}, "
                              f"differs on {missing[0]})")
    except Exception as e:
        return {"status": "unknown", "detail": f"could not reach the published app: {e}"}

    meaning = ("The app reads its numbers from the published copy, which updates only when a "
               "deploy runs — so the repo can be right while the screen is wrong.")
    if behind:
        return {"status": "warn",
                "detail": f"app is behind the repo on {len(behind)} feed(s): {behind[0]}",
                "meaning": meaning,
                "action": ("The figures on screen are older than the ones already recorded. Nothing is "
                           "lost and nothing is wrong in the data - it just has not been published. "
                           "Ask Claude to check the Pages deploy; a re-run of the deploy workflow "
                           "usually clears it."),
                "selfheal": "Clears on its own the next time any deploy runs."}
    return {"status": "ok", "detail": f"app matches the repo on all {len(feeds)} key feeds"}


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
        return {"status": "ok", "detail": f"exact split, balances on all {len(rows)} rows ({age}d old)"}
    except Exception as e:
        return {"status": "unknown", "detail": f"Uber feed unreadable: {e}"}


def _uber_direct(rel="data/uber_direct_daily.csv"):
    """Uber DIRECT ingest — Mari's own online orders delivered by Uber's fleet,
    read daily from direct.uber.com by the uber-eats-daily-fees task.

    WHY it needs watching: it used to have no schedule of its own — it moved only
    when an invoice email fired the uber_direct_dispatch workflow via Pipedream.
    Pipedream's free tier ran out 2026-07-24; sales ingestion and the auth worker
    were moved off it and this feed was missed, so it sat dead for 22 days with
    ZERO workflow runs and nothing went red anywhere. pnl.js degrades safely
    (uberDirectActual reports covered=false and the caller estimates), so no
    number was wrong — the cost was simply never captured.

    Since 2026-08-09 the daily uber-eats-daily-fees task reads the fees straight
    from direct.uber.com instead, so this check now watches that task. The portal
    figures reconciled to the cent against all six email-sourced days.
    """
    age = _csv_last_date_age_days(rel, "date")
    if age is None:
        return {"status": "unknown", "detail": "no Uber Direct feed yet"}
    meaning = "Uber Direct is Mari's own online delivery, read daily from direct.uber.com — separate from Uber Eats."
    if age >= 21:
        return {"status": "down", "detail": f"Uber Direct ingest silent {age}d",
                "meaning": meaning,
                "action": ("No Uber Direct fee has been recorded for three weeks, so those delivery costs "
                           "are being estimated instead of counted. The daily Uber pull reads them from "
                           "direct.uber.com - ask Claude to run it, and if it reports a login screen, Zak "
                           "needs to sign in to direct.uber.com once."),
                "selfheal": "Resumes on its own once the daily Uber pull can reach the portal."}
        # (fees still estimate cleanly, so this is a data-capture outage, not a wrong number)
    if age >= 7:
        return {"status": "warn", "detail": f"no Uber Direct fee for {age}d",
                "meaning": meaning,
                "action": f"No Uber Direct invoice has landed for {age} days - fine if Mari genuinely had no Direct orders, worth a look if she did.",
                "selfheal": "Clears when the next Direct delivery is recorded."}
    return {"status": "ok", "detail": f"last Direct fee {age}d ago"}


def _uber_direct_reconciled(feed_rel="data/uber_direct_daily.csv",
                            stmt_rel="data/uber_direct_statements.csv"):
    """Does the Direct feed still agree with Uber's own invoices?

    WHY on the panel and not only in CI: the deliveries list at direct.uber.com
    paginates at 50 rows, and a truncated read returns FEWER deliveries — rows
    that are correctly formatted, correctly dated, sorted, Mari-only, and too
    small. Every structural guard passes. Only Uber's invoice disagrees.

    That is not hypothetical: the first June-July read on 2026-08-10 came back
    capped at 50 rows and had 2026-06-05 at 27.94 against an invoice of 40.60.
    Re-read in weekly windows it was 40.60. The same read also recovered 16 days
    (A$607.67) that the feed had never held at all.

    Compared on TOTALS over the settled window, never day by day: Uber's invoice
    date is not the delivery date, and the feed is deliberately delivery-dated so
    it lines up with the sales it belongs to.
    """
    import csv as _csv
    from datetime import date as _date, timedelta as _td
    from decimal import Decimal as _D
    fp, sp = ROOT / feed_rel, ROOT / stmt_rel
    if not fp.exists() or not sp.exists():
        return {"status": "unknown", "detail": "no Uber Direct statements to reconcile against"}
    try:
        with fp.open() as fh:
            feed = {r["date"]: _D(r["fee_inc_gst"]) for r in _csv.DictReader(fh)}
        with sp.open() as fh:
            stmt = {r["statement_date"]: _D(r["amount_inc_gst"]) for r in _csv.DictReader(fh)}
        if not feed or not stmt:
            return {"status": "unknown", "detail": "Uber Direct reconciliation inputs empty"}
        SETTLE, ACK = 3, _D("-50.00")
        cutoff = (_date.fromisoformat(max(stmt)) - _td(days=SETTLE)).isoformat()
        floor = min(min(stmt), min(feed))
        f_tot = sum((v for d, v in feed.items() if floor <= d <= cutoff), _D("0"))
        s_tot = sum((v for d, v in stmt.items() if floor <= d <= cutoff), _D("0"))
        resid = s_tot - f_tot
        drift = resid - ACK
        if drift != 0:
            return {"status": "warn",
                    "detail": f"Direct feed vs Uber invoices out by A${drift} ({floor}..{cutoff})",
                    "meaning": ("Uber Direct fees are read from the deliveries list; Uber's invoices are "
                                "an independent record of the same money."),
                    "action": ("The two no longer agree. A feed SHORT of the invoices is almost always a "
                               "truncated deliveries page - the list caps at 50 rows per page. Ask Claude "
                               "to re-read the affected range in weekly windows, checking each window "
                               "returns fewer than 50 rows."),
                    "selfheal": "No - the affected days need re-reading from the portal."}
        return {"status": "ok",
                "detail": f"matches Uber invoices to the cent through {cutoff}"}
    except Exception as e:
        return {"status": "unknown", "detail": f"Uber Direct reconciliation unreadable: {e}"}


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

    ud = _uber_direct()
    _ud_check = {"name": "Uber Direct ingest", "detail": ud.get("detail"),
                 "age": None, "unit": "", "status": ud.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if ud.get(_k):
            _ud_check[_k] = ud[_k]
    checks.append(_ud_check)

    udr = _uber_direct_reconciled()
    _udr_check = {"name": "Uber Direct reconciled", "detail": udr.get("detail"),
                  "age": None, "unit": "", "status": udr.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if udr.get(_k):
            _udr_check[_k] = udr[_k]
    checks.append(_udr_check)

    pd = _pages_drift(computed=checks)
    _pd_check = {"name": SELF, "detail": pd.get("detail"),
                 "age": None, "unit": "", "status": pd.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if pd.get(_k):
            _pd_check[_k] = pd[_k]
    checks.append(_pd_check)

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
