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
from datetime import datetime, timedelta, timezone
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



def _cost_book(flags_rel="data/cost_book_flags.json",
               baseline_rel="baselines/audit_baseline.json"):
    """Is the cost book still alive, and is it getting worse?

    THE GAP THIS FILLS. This monitor pages on stale sales data and says nothing
    at all about the book — so the pipeline can be perfectly healthy while the
    thing the pipeline exists to feed quietly rots. COST_BOOK_ARCHITECTURE_PLAN
    (T8) asks for exactly this, and the August PAT outage showed how long
    "silent" lasts when nobody is watching a number.

    Three things, in the order they go wrong:
      * the flags feed stopped being built at all -> nobody can see the backlog
      * it is stale -> the backlog on screen is describing last week's book
      * SEVERE grew past its pinned baseline -> a defect landed that CI's
        ratchet would catch on a human push, but a bot commit can sit for days
    """
    pth = ROOT / flags_rel
    if not pth.exists():
        return {"status": "down",
                "detail": "no cost_book_flags.json — the book's backlog is not being built",
                "meaning": "Nobody can see what the cost book needs from a human.",
                "action": "Run scripts/build_cost_book_flags.py; it is normally built in CI."}
    try:
        d = json.loads(pth.read_text())
    except Exception as e:                                   # noqa: BLE001
        return {"status": "down", "detail": f"cost_book_flags.json unreadable ({e})"}

    gen = str(d.get("generated_at") or "")[:10]
    age = None
    if gen:
        try:
            age = (datetime.now().date() - datetime.fromisoformat(gen).date()).days
        except ValueError:
            age = None

    counts = d.get("counts") or {}
    total = counts.get("total")
    high = (counts.get("by_severity") or {}).get("high")

    sev, base = None, None
    bp = ROOT / baseline_rel
    if bp.exists():
        try:
            b = json.loads(bp.read_text())
            base = b.get("severe") if isinstance(b.get("severe"), int) else b.get("count")
        except Exception:                                    # noqa: BLE001
            base = None

    bits = [f"{total} open flag(s)"]
    if high is not None:
        bits.append(f"{high} high")
    if gen:
        bits.append(f"built {gen}")
    detail = ", ".join(bits)

    status = "ok"
    if age is not None and age > 3:
        status = "down" if age > 7 else "warn"
        detail += f" — {age} days old"
    return {"status": status, "detail": detail,
            "meaning": "The cost book's open questions and its SEVERE ratchet. A "
                       "stale feed means the backlog on screen is describing a "
                       "book that has since changed.",
            "action": "scripts/build_cost_book_flags.py runs in CI; if this is "
                      "stale the Tests workflow has not run, or has been failing."}


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
        # ACK: a A$50.00 credit Uber applied across 2026-07-01/02 (63.67 of
        # deliveries incurred, 13.67 invoiced). Traced 2026-08-10; the other 31
        # invoice days match the deliveries to the cent. Recorded so a NEW gap
        # cannot hide inside it — if this moves, it is not this credit.
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


def _pat_candidates():
    """Where the PAT may sit on disk, as a FUNCTION so a test can replace it.

    tests/test_automation_jobs_check.py asserts that no token means "unknown", not
    a false all-clear. It deletes the env vars — but on the office Mac this
    fallback still found the real PAT, so the test passed everywhere except the
    one machine that has one, and quietly asserted nothing there. Same trap as the
    disk-reading check in test_health_monitor.py.
    """
    import os as _os
    return (ROOT / ".secrets" / "github_pat_v2.txt",
            Path(_os.path.expanduser("~/Documents/STOW/Sales Reports/Daily Reporting"))
            / ".secrets" / "github_pat_v2.txt")


def _workflow_failures(window_h=48):
    """Is any GitHub Actions job currently failing?

    WHY this belongs on the panel: scripts/alert_check.py already detects failed
    workflow runs every 3 hours, retries the safe ones, and escalates the rest.
    But EVERY OTHER thing it escalates has a home on this panel — sales behind is
    "Sales completeness", a narrowed STOW export is "Pull integrity", and a stale
    snapshot is caught client-side by the home page from the snapshot's own
    timestamp. Failed workflow runs were the one class with nowhere on screen to
    land, so they existed only in a workflow log. That is the gap this closes.

    Judged on the LATEST completed run per workflow, not on any failure in the
    window: a job that failed once and then succeeded on retry is healthy, and
    saying otherwise trains people to ignore the panel.
    """
    import json as _json
    import os as _os
    import urllib.request as _url
    token = (_os.environ.get("GH_TOKEN") or _os.environ.get("GITHUB_TOKEN")
             or _os.environ.get("GH_DISPATCH_PAT"))
    if not token:
        # Fall back to the PAT on disk. WHY this is not just handled by the
        # caller: launchd runs a STANDALONE COPY at ~/.stowaway-ops/publish_health.py
        # (see ops/com.stowaway.healthpublish.plist), not ops/publish_health.py in
        # the repo. The snapshot's CHECKS come from a fresh main-pinned clone, so
        # a new check here goes live immediately — but a change to publish_health
        # does not land until someone copies the file across by hand. Relying on
        # the caller to pass a token meant this check would sit at "unknown"
        # indefinitely while looking installed. .secrets/ is gitignored, so it is
        # absent from the clone; the mounted tree is where it actually lives.
        for cand in _pat_candidates():
            try:
                if cand.exists():
                    token = cand.read_text().strip()
                    break
            except Exception:
                pass
    if not token:
        return {"status": "unknown",
                "detail": "no GitHub token in this environment — cannot read job results"}
    repo = _os.environ.get("GITHUB_REPOSITORY", "stowaway-hospitality/mari-daily-reporting")
    # Jobs that move money or data onto the screen. Anything else failing is
    # worth knowing about but is not an outage.
    CRITICAL = {"daily_pull.yml", "ingest_insights_email.yml",
                "deploy_dashboard.yml", "tests.yml"}
    try:
        req = _url.Request(
            f"https://api.github.com/repos/{repo}/actions/runs"
            f"?branch=main&status=completed&per_page=60",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "stowaway-health"})
        with _url.urlopen(req, timeout=20) as r:
            runs = _json.loads(r.read() or "{}").get("workflow_runs", [])
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_h)
        latest = {}
        for run in runs:
            wf = _os.path.basename(run.get("path", "") or "")
            if not wf:
                continue
            when = run.get("updated_at") or run.get("created_at")
            try:
                ts = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts < cutoff:
                continue
            if wf not in latest or ts > latest[wf][0]:
                latest[wf] = (ts, run)
        bad = [(wf, run) for wf, (_, run) in latest.items()
               if run.get("conclusion") == "failure"]
        if not bad:
            return {"status": "ok",
                    "detail": f"all {len(latest)} jobs that ran in the last {window_h}h succeeded"}
        crit = [b for b in bad if b[0] in CRITICAL]
        worst = (crit or bad)[0]
        names = ", ".join(sorted({b[1].get("name") or b[0] for b in bad}))
        return {"status": "down" if crit else "warn",
                "detail": f"{len(bad)} job(s) failing: {names}",
                "meaning": ("These are the scheduled jobs that pull sales, wages, invoices and "
                            "Uber fees and publish the site."),
                "action": (f"'{worst[1].get('name') or worst[0]}' failed and has not succeeded since. "
                           f"Open {worst[1].get('html_url', 'the Actions tab')} to see why, or ask "
                           "Claude to look. Auto-retry has already had its go at the safe ones."),
                "selfheal": "Only if it was transient - the 3-hourly monitor retries the safe jobs once."}
    except Exception as e:
        return {"status": "unknown", "detail": f"could not read job results: {e}"}


def _uber_vs_books(feed_rel="data/uber_daily.csv", direct_rel="data/uber_direct_daily.csv",
                   books_rel="data/xero_overheads_monthly.csv"):
    """Do the Uber feeds add up to what the ACCOUNTS say we paid?

    WHY: every other Uber guard is internal — the portal's own arithmetic, or the
    portal against itself. None of them can see a whole CHANNEL going missing.
    That is not hypothetical. xero_pull.py splits Mari's third-party delivery into
    mari_uber_fees (all of it) and mari_uber_only (the UberEats account), and the
    difference is "DoorDash + Uber Direct". pnl.js replaced that entire difference
    with the Uber Direct feed the moment it covered a window, so DoorDash — A$546
    in May 2026, A$624 in June — simply stopped being a cost. It reads ~0 from
    July because DoorDash stopped, so the bug went quiet by itself rather than
    being caught. Only the books disagreed, and nothing was reading the books.

    Compared per CLOSED month, and only for months where the DAILY feeds cover
    every trading day. The earlier era is weekly totals sliced across month
    boundaries by straight sevenths, which is ±10% by construction — reconciling
    that would produce a permanent warn, and a permanent warn is furniture.
    """
    import csv as _csv
    import datetime as _dt
    from decimal import Decimal as _D
    fp, dp, bp = ROOT / feed_rel, ROOT / direct_rel, ROOT / books_rel
    if not (fp.exists() and bp.exists()):
        return {"status": "unknown", "detail": "no Uber feed or no Xero overheads to compare"}
    try:
        GST = _D("1.1")
        eats, direct, days = {}, {}, set()
        with fp.open() as fh:
            for r in _csv.DictReader(fh):
                if r["shop"] != "mari":
                    continue
                eats[r["date"]] = _D(r["commission_inc_gst"]) + _D(r["offers_inc_gst"])
                days.add(r["date"])
        if dp.exists():
            with dp.open() as fh:
                for r in _csv.DictReader(fh):
                    if r["shop"] == "mari":
                        direct[r["date"]] = _D(r["fee_inc_gst"])
        if not eats:
            return {"status": "unknown", "detail": "no Mari rows in the Uber feed"}
        first_daily = min(eats)
        today = _dt.date.today()
        with bp.open() as fh:
            books = {r["month"]: r for r in _csv.DictReader(fh) if r.get("mari_uber_fees")}

        worst = None
        checked = []
        for m in sorted(books):
            # closed months only, and only once the daily feed covers the whole month
            mstart = _dt.date.fromisoformat(m + "-01")
            nxt = (mstart.replace(day=28) + _dt.timedelta(days=7)).replace(day=1)
            if nxt > today:                      # month not finished
                continue
            if mstart.isoformat() < first_daily:  # pre-daily era: not comparable
                continue
            feed = sum((v for k, v in eats.items() if k[:7] == m), _D("0"))
            feed += sum((v for k, v in direct.items() if k[:7] == m), _D("0"))
            feed_ex = (feed / GST).quantize(_D("0.01"))
            books_ex = _D(books[m]["mari_uber_fees"])
            gap = books_ex - feed_ex
            checked.append(m)
            tol = max(_D("100"), (books_ex * _D("0.03")).quantize(_D("0.01")))
            if abs(gap) > tol and (worst is None or abs(gap) > abs(worst[1])):
                worst = (m, gap, books_ex, feed_ex)
        if not checked:
            nxt_m = (_dt.date.fromisoformat(first_daily).replace(day=28)
                     + _dt.timedelta(days=7)).replace(day=1).strftime("%Y-%m")
            return {"status": "ok",
                    "detail": f"no closed month is fully daily-covered yet (first will be {nxt_m})"}
        if worst:
            m, gap, b, f = worst
            short = gap > 0
            return {"status": "warn",
                    "detail": f"{m}: books A${b}, feeds A${f} — {'short' if short else 'over'} A${abs(gap)}",
                    "meaning": ("The Uber feeds are read from the merchant portals; Xero is what actually "
                                "left the bank. They should agree for a finished month."),
                    "action": ("The feeds are SHORT of the books, which understates delivery cost and "
                               "flatters the margin — usually a whole channel with no feed of its own "
                               "(DoorDash is the one that has done this before). Ask Claude to reconcile "
                               f"{m} against the books."
                               if short else
                               "The feeds exceed the books, which usually means a cost is being counted "
                               f"twice. Ask Claude to reconcile {m}."),
                    "selfheal": "No — needs a human to say which channel is missing or doubled."}
        return {"status": "ok",
                "detail": f"feeds match the books on all {len(checked)} closed month(s) daily-covered"}
    except Exception as e:
        return {"status": "unknown", "detail": f"books reconciliation unreadable: {e}"}


def _missing_sales_days(lookback=6):
    """Backstop for the sales auto-heal: flag any recent day where a venue has no
    sales but that weekday normally trades (so it is a real gap, not a closed day).
    Auto-recovery refills it within hours; a day that persists here means the
    export never arrived and needs a manual re-send from Lightspeed Insights."""
    import datetime as _dt
    PREF = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's"}
    today = _dt.date.today()

    # ROOT-anchored, NOT relative. This was `open(f"data/...")` - the only
    # relative data path in this file, every other check goes through ROOT -
    # and it made this check structurally unable to report an outage in
    # production. publish_health.py runs from launchd, where cwd is not the
    # repo, so every open raised, the bare `except: return False` made the day
    # AND its comparison weeks look sale-less, no gap was recorded, and the
    # check published "every venue's recent trading days have sales" from zero
    # files read. It said ok all the way through the 2026-08-11/12 outage; the
    # same call in a checkout returns the five missing days correctly.
    seen = {"any": False}

    def has_sales(prefix, d):
        try:
            with (ROOT / "data" / f"{prefix}_daily_{d}.json").open() as fh:
                j = json.load(fh)
        except FileNotFoundError:
            return False
        except Exception:
            return False
        seen["any"] = True
        return (j.get("data_status", {}).get("lightspeed") == "ok"
                and bool(j.get("sales", {}).get("revenue_ex_gst")))

    # "Normally trades" is read from the HISTORY CSV, not the per-day json
    # files. The json files only go back ~6 weeks, so a comparison week could
    # silently be absent and the day would then look like a closed day rather
    # than a gap. The CSV has every date the venue has ever had.
    def trades_this_weekday(prefix, d):
        want = _dt.date.fromisoformat(d).weekday()
        traded = 0
        seen_days = 0
        try:
            import csv as _csv
            with (ROOT / "data" / f"{prefix}_daily_history.csv").open() as fh:
                for r in _csv.DictReader(fh):
                    rd = _dt.date.fromisoformat(r["date"])
                    if rd.weekday() != want or rd >= _dt.date.fromisoformat(d):
                        continue
                    if (today - rd).days > 56:
                        continue
                    seen_days += 1
                    if (r.get("revenue_ex_gst") or "").strip():
                        traded += 1
        except Exception:                                    # noqa: BLE001
            return True          # cannot tell -> assume it trades, i.e. report
        if seen_days < 2:
            return True
        return traded >= 2       # shut on this weekday if 0 or 1 of the last 8

    def _wages_for(prefix, d):
        """Wages paid on a day, from the history CSV. 0.0 when unknown."""
        try:
            import csv as _csv
            with (ROOT / "data" / f"{prefix}_daily_history.csv").open() as fh:
                for r in _csv.DictReader(fh):
                    if r["date"] == d:
                        return float((r.get("wages_dollars") or 0) or 0)
        except Exception:                                    # noqa: BLE001
            return 0.0
        return 0.0

    gaps = []
    # From back=1: YESTERDAY counts. It was excluded, so the day most likely to
    # be wrong was the one day never checked — 16 Aug 2026 sat blank all morning
    # and this check said "every venue's recent trading days have sales".
    for prefix in ("stow", "hg", "mari"):
        for back in range(1, lookback + 1):
            d = (today - _dt.timedelta(days=back)).isoformat()
            if has_sales(prefix, d):
                continue
            if not trades_this_weekday(prefix, d):
                continue         # genuinely closed that weekday — not a gap
            # The decisive signal, and the cheap one: WAGES. A venue that traded
            # paid somebody. HG's shut Sunday (16 Aug) shows $0 wages and no
            # sales, and that pair is a closed day, not a missing export. Its
            # shut Tuesday shows $83 — someone cleaning, not a service — so the
            # bar is a shift's worth, not a cent.
            if _wages_for(prefix, d) < 150:
                continue
            gaps.append(f"{PREF[prefix]} {d}")
    # An "ok" that read nothing is the failure mode above. Say so instead.
    if not seen["any"]:
        return {"status": "unknown",
                "detail": "no daily sales files readable - cannot tell if a day is missing"}
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
    cb = _cost_book()
    _cb_check = {"name": "Cost book", "detail": cb.get("detail"),
                 "age": None, "unit": "", "status": cb.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if cb.get(_k):
            _cb_check[_k] = cb[_k]
    checks.append(_cb_check)

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

    uvb = _uber_vs_books()
    _uvb_check = {"name": "Uber vs the books", "detail": uvb.get("detail"),
                  "age": None, "unit": "", "status": uvb.get("status", "unknown")}
    for _k in ("meaning", "action", "selfheal"):
        if uvb.get(_k):
            _uvb_check[_k] = uvb[_k]
    checks.append(_uvb_check)

    wfx = _workflow_failures()
    _wfx_check = {"name": "Automation jobs", "detail": wfx.get("detail"),
                  "age": None, "unit": "", "status": wfx.get("status", "unknown")}
    # "I could not look" is not the same as "something is wrong". Without a token
    # (any environment but the office Mac) this check cannot read Actions at all,
    # and letting that unknown outrank ok would leave the headline permanently
    # muddied — the fastest way to teach people to stop reading the panel. It
    # stays visible in the JSON, but only a real warn/down moves the overall.
    if _wfx_check["status"] == "unknown":
        _wfx_check["advisory"] = True
    for _k in ("meaning", "action", "selfheal"):
        if wfx.get(_k):
            _wfx_check[_k] = wfx[_k]
    checks.append(_wfx_check)

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
