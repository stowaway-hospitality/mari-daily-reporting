#!/usr/bin/env python3
"""Deputy timesheet triage — REPORT-ONLY (phase 1).

Reads unapproved Deputy timesheets and records what it WOULD auto-approve vs park
for a human, per the deputy-timesheet-approval rules. Writes NOTHING back — this
is the validation phase.

╔═ PAY-SAFETY RULE (do not weaken) ═════════════════════════════════════════════╗
║ A meal break that was NOT taken must be PAID — never silently deducted. A real ║
║ underpayment happened when a 30-min break was deducted from a shift the staff  ║
║ member worked straight through (clocked 1:00pm–10:30pm, no break, but recorded ║
║ as 9h instead of 9.5h). Therefore this tool NEVER edits a break and NEVER      ║
║ auto-approves a sheet whose break is anything other than plainly normal. Any   ║
║ break that is missing on a long shift, off the 15-min grid, or unreadable is   ║
║ PARKED for a human. A false-park costs 30 seconds; a wrong break costs wages.  ║
╚═══════════════════════════════════════════════════════════════════════════════╝

Auth: OAuth DEPUTY_TOKEN (read scope only for phase 1). Endpoint:
831d4015123255.au.deputy.com/api/v1/*
"""
from __future__ import annotations
import json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deputy_triage.json"
HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN", "")
SYD = timezone(timedelta(hours=10))

SALARIED = {3: "Zak", 1: "Kris", 5: "Nicola", 15: "Marssheel", 16: "Stephanie",
            41: "Bryony", 133: "Devon", 142: "Renan", 287: "Min", 297: "Pujan"}
ZAK, MARSSHEEL, RHYS, OLLY = 3, 15, 145, 284
FOH_OUS = {6, 13, 14, 15}
FOH_OVERSTAY_CAP_H = 1.5
RUNAWAY_H = 14.0
BREAK_MIN_SHIFT_H = 7.0

APPROVE, PARK, SKIP = "would_approve", "park_for_human", "skip"


def _req(method, path, body=None, _tries=5):
    """Deputy request with exponential backoff on transient errors (503/429 are
    common when the API is busy). Read-only callers only."""
    url = HOST + path
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(_tries):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": f"OAuth {TOKEN}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < _tries - 1:
                time.sleep(min(30, 3 * (2 ** attempt)))   # 3,6,12,24s
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt < _tries - 1:
                time.sleep(min(30, 3 * (2 ** attempt)))
                continue
            raise
    raise last


def approve_sheet(tid):
    """Approve one timesheet: POST /api/v1/supervise/timesheet/approve. This is a
    PAYROLL WRITE — only ever called for the proven-safe APPROVE bucket. Returns
    (ok, detail). Does not retry a 4xx (a 403 = token lacks write scope)."""
    try:
        r = _req("POST", "/api/v1/supervise/timesheet/approve", {"intTimesheetId": tid})
        return True, r
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:160]
        except Exception:
            pass
        return False, f"HTTP {e.code} {body}"
    except Exception as e:
        return False, str(e)


def _round15_ts(u):
    return int(round(u / 900.0)) * 900 if u else u


def _round15_min(m):
    return int(round(m / 15.0)) * 15


def update_sheet(tid, ou, start=None, end=None, break_min=None, comment=None):
    """POST /api/v1/supervise/timesheet/update — PAYROLL WRITE (Rule 1/1B rounding).
    Requires intOpunitId. Returns (ok, detail). Updating resets TimeApproved, so
    the caller re-approves after."""
    body = {"intTimesheetId": tid, "intOpunitId": ou}
    if start is not None:
        body["intStartTimestamp"] = int(start)
    if end is not None:
        body["intEndTimestamp"] = int(end)
    if break_min is not None:
        body["intMealbreakMinute"] = int(break_min)
    if comment:
        body["strComment"] = comment
    try:
        r = _req("POST", "/api/v1/supervise/timesheet/update", body)
        return True, r
    except urllib.error.HTTPError as e:
        try:
            b = e.read().decode()[:160]
        except Exception:
            b = ""
        return False, f"HTTP {e.code} {b}"
    except Exception as e:
        return False, str(e)


def rounded_view(ts):
    """A copy of the timesheet with start/end rounded to 15 min and TotalTime
    reconciled so the break cross-check stays consistent."""
    start, end = ts.get("StartTime") or 0, ts.get("EndTime") or 0
    bmin = mealbreak_min(ts)
    if not (start and end) or bmin is None:
        return ts
    v = dict(ts)
    v["StartTime"] = _round15_ts(start)
    v["EndTime"] = _round15_ts(end)
    v["TotalTime"] = round((v["EndTime"] - v["StartTime"]) / 3600 - bmin / 60, 4)
    return v


def fetch_unapproved(days=7):
    since = int(datetime.now(timezone.utc).timestamp()) - days * 86400
    body = {
        "search": {
            "s1": {"field": "StartTime", "type": "ge", "data": since},
            "s2": {"field": "TimeApproved", "type": "eq", "data": False},
            "s3": {"field": "Discarded", "type": "eq", "data": 0},
        },
        "join": ["EmployeeObject", "OperationalUnitObject", "RosterObject"],
        "max": 500,
    }
    return _req("POST", "/api/v1/resource/Timesheet/QUERY", body)


def mealbreak_min(ts):
    """Unpaid meal-break minutes, or None if it can't be trusted (-> park).

    LEARNED from 5,910 timesheets over a year: Deputy stores Mealbreak as a
    datetime string whose TIME component after 'T' is the break DURATION —
    '2025-08-22T00:30:00+10:00' = 30 min, '...T00:00:00...' = no break. That decode
    equalled the pay-truth (gross shift minus TotalTime paid hours) on 5910/5910
    sheets = 100%. We compute the break BOTH ways and require them to agree; if they
    ever disagree the sheet is anomalous and returns None so a human handles it —
    never a wrong pay."""
    mb = ts.get("Mealbreak")
    dec = None
    if isinstance(mb, str) and "T" in mb:
        try:
            parts = mb.split("T")[1].split(":")
            dec = int(parts[0]) * 60 + int(parts[1])
        except Exception:
            dec = None

    start = ts.get("StartTime") or 0
    end = ts.get("EndTime") or 0
    total = ts.get("TotalTime")
    cross = None
    if end and start and total is not None:
        cross = round(((end - start) / 3600 - total) * 60)
        if -2 <= cross < 0:
            cross = 0

    if dec is not None and cross is not None:
        return dec if abs(dec - cross) <= 2 else None   # disagree -> park
    return dec if dec is not None else cross


def _norm(c):
    return re.sub(r"\s+", " ", (c or "").lower().replace("\u2019", "'").replace("`", "'")).strip()


def comment_no_break(comment) -> bool:
    """Staff declaring they worked through their break. Learned phrasings (a year
    of history): 'no break', 'no break taken', 'nb', 'no breaks', 'no meal break',
    "didn't take (a/my) break", 'break not taken', plus typos ('ni break taken',
    'no break talen'). This is the signal the old process mishandled."""
    c = _norm(comment)
    if not c:
        return False
    if "no break" in c or "no breaks" in c or "no meal" in c or "ni break" in c:
        return True
    if "break not taken" in c or "without break" in c or "worked through" in c or "straight through" in c:
        return True
    if re.search(r"did\s*n?[o']?t?\s+take\b.*break", c) or c.strip() == "nb":
        return True
    return False


def comment_declared_break_min(comment):
    """A break duration the staff member states, e.g. '30 min break taken' -> 30."""
    c = _norm(comment)
    if "break" not in c:
        return None
    m = re.search(r"(\d+)\s*(hour|hr|min|minute)s?\b", c)
    if not m:
        return None
    v = int(m.group(1))
    return v * 60 if m.group(2).startswith(("hour", "hr")) else v


_TIME = r"(\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)?)"


def _find_time(after_words, c):
    for w in after_words:
        m = re.search(w + r"(?:\s+(?:at|work|shift|my|for|the|on|to))*\s+" + _TIME, c)
        if m and re.search(r"\d", m.group(1)):
            return m.group(1).strip()
    return None


def parse_clock_comment(comment):
    """Extract a structured correction from a clock-time / area comment so the
    report shows the actual fix instead of a blank 'needs a human'. Learned from
    a year of real phrasings. Returns None if nothing clock/area-related, else a
    dict {start, finish, forgot_clock, area}. We PARSE and PRESENT only — never
    auto-apply (times like 'start 5' are am/pm-ambiguous and pay-sensitive)."""
    c = _norm(comment)
    if not c:
        return None
    start = _find_time([r"start(?:ed|ing)?", r"begin", r"commenced"], c)
    if not start:
        m = re.search(_TIME + r"\s*start", c)
        if m and re.search(r"\d", m.group(1)):
            start = m.group(1).strip()
    finish = _find_time([r"finish(?:ed)?", r"\bfin\b", r"end(?:ed)?", r"clock(?:ed)?\s*off"], c)
    if not finish:
        m = re.search(_TIME + r"\s*finish", c)
        if m and re.search(r"\d", m.group(1)):
            finish = m.group(1).strip()
    if not (start and finish):
        rng = re.search(_TIME + r"\s*(?:a|to|til|till|until|-|\u2013)\s*" + _TIME, c)
        if rng and re.search(r"\d", rng.group(1)) and re.search(r"\d", rng.group(2)):
            start = start or rng.group(1).strip()
            finish = finish or rng.group(2).strip()
    forgot = bool(re.search(r"forgot|did\s*n.?t\s*(?:clock|sign)|clock(?:ed)?\s*(?:on|in|out|off)|sign\s*in", c))
    area = None
    sp = re.findall(r"(\d{1,2}(?:[:.]\d{2})?)\s*(?:-|–|to)\s*(\d{1,2}(?:[:.]\d{2})?)\s*(stow|hg|harry|floor|bar|kitchen|pizza)", c)
    if sp:
        area = "split " + "; ".join(f"{a}-{b} {v}" for a, b, v in sp)
    else:
        m = re.search(r"from\s*" + _TIME + r"\s*(harry gatos|harry|hg|stow\w*|floor|bar|kitchen)", c)
        if m:
            area = f"from {m.group(1).strip()} {m.group(2)}"
        elif re.search(r"moved into the (?:bar|floor|kitchen)|worked at (?:hg|harry|stow)", c):
            area = re.search(r"(moved into the (?:bar|floor|kitchen)|worked at (?:hg|harry|stow\w*))", c).group(1)
    if start or finish or forgot or area:
        return {"start": start, "finish": finish, "forgot_clock": forgot, "area": area}
    return None


def comment_needs_human(comment) -> bool:
    """Clock-time corrections or area/venue splits the sheet needs edited by hand
    (e.g. 'started 5:30', 'forgot to sign in', '2-6 Stow / 6-10 HG')."""
    c = _norm(comment)
    if not c:
        return False
    if re.search(r"\bstart(ed|ing)?\b|\bfinish(ed)?\b|\bforgot\b|\bclock\b|sign\s*in|\d{1,2}[:.]\d{2}|\b\d{1,2}\s*(am|pm)\b", c):
        return True
    if re.search(r"\b(stow|hg|harry|floor|bar|kitchen|pizza)\b", c) and re.search(r"\d", c):
        return True
    return False


def decide(ts, correctable=False) -> tuple[str, str]:
    """(decision, reason) for one timesheet. Pure function; parks when unsure."""
    emp = ts.get("Employee")
    start = ts.get("StartTime") or 0
    end = ts.get("EndTime") or 0

    if ts.get("IsLeave"):
        return SKIP, "approved leave — auto-generated timesheet, no action"

    today = datetime.now(SYD).date()
    start_d = datetime.fromtimestamp(start, SYD).date() if start else today
    if ts.get("IsInProgress"):
        return (SKIP, "in progress on the current day — still on shift") if start_d >= today \
            else (PARK, "in progress on a past day — needs closing to roster (Kris)")

    dur_h = (end - start) / 3600 if end and start else 0
    end_d = datetime.fromtimestamp(end, SYD).date() if end else start_d
    if dur_h > RUNAWAY_H or end_d > start_d:
        return PARK, f"runaway/overnight ({dur_h:.1f}h) — forgotten clock-off (Kris)"

    if emp == ZAK:
        return PARK, "Zak's own timesheet — needs manual shift splits"

    # ---- comment signals (apply to everyone; this is the underpayment guard) ----
    mb = mealbreak_min(ts)
    comment = ts.get("EmployeeComment") or ""
    if comment_no_break(comment) and mb and mb > 0:
        return PARK, f"UNDERPAY RISK: comment says no break but {mb}m deducted — pay in FULL (Kris/Zak)"
    _dbm = comment_declared_break_min(comment)
    if _dbm is not None and mb is not None and abs(_dbm - mb) > 2:
        return PARK, f"comment states {_dbm}m break but {mb}m deducted — reconcile (Kris)"
    _pc = parse_clock_comment(comment)
    if _pc:
        _det = []
        if _pc["start"]: _det.append(f"start {_pc['start']}")
        if _pc["finish"]: _det.append(f"finish {_pc['finish']}")
        if _pc["forgot_clock"]: _det.append("forgot to clock")
        if _pc["area"]: _det.append(_pc["area"])
        return PARK, "clock/area correction — " + ", ".join(_det) + " — verify & set (Kris)"
    if comment_needs_human(comment):
        return PARK, "comment notes a clock-time / area correction — needs a human"

    ro = ts.get("RosterObject") or {}
    roster_end = ro.get("EndTime")
    overstay_h = (end - roster_end) / 3600 if roster_end else None

    if emp in SALARIED:
        return APPROVE, f"salaried ({SALARIED[emp]}) — overstay auto-approve rule"

    # ---- casuals ----
    ou = ts.get("OperationalUnit")   # mb (break) already computed above

    # PAY-SAFETY gate — break must be plainly normal before we ever auto-approve.
    if mb is None:
        return PARK, "meal break unreadable — park (never guess a break = never mis-pay)"
    if dur_h >= BREAK_MIN_SHIFT_H and mb == 0:
        return PARK, f"casual {dur_h:.1f}h shift, no break recorded — pay in FULL, Kris confirms compliance"
    if mb % 15 != 0 and not correctable:
        return PARK, f"meal break {mb}m off the 15-min grid — park (breaks are never auto-adjusted)"

    if emp == RHYS:
        return APPROVE, "Rhys Taylor — overstays approved (non-runaway) per policy"
    if emp == OLLY and (overstay_h or 0) > 0:
        return PARK, "Olly — overstays always left for Kris per policy"
    if overstay_h is None:
        return PARK, "no linked roster — overstay unverifiable, park for a human"
    if overstay_h <= 0:
        return APPROVE, "clean — within rostered finish, break normal"
    if ou in FOH_OUS and overstay_h <= FOH_OVERSTAY_CAP_H:
        return APPROVE, f"casual FOH overstay {overstay_h:.2f}h ≤ {FOH_OVERSTAY_CAP_H}h, break normal"
    if ou in FOH_OUS:
        return PARK, f"casual FOH overstay {overstay_h:.2f}h > {FOH_OVERSTAY_CAP_H}h — Kris"
    return PARK, f"casual overstay {overstay_h:.2f}h in non-FOH area (ou{ou}) — Kris"


def main() -> int:
    if not TOKEN:
        print("DEPUTY_TOKEN not set", file=sys.stderr)
        return 2
    rows = fetch_unapproved(7)
    live = os.environ.get("DEPUTY_APPROVE") == "1"
    buckets = {APPROVE: [], PARK: [], SKIP: []}
    for ts in rows:
        tsv = rounded_view(ts) if live else ts            # decide on rounded times when correcting
        code, reason = decide(tsv, correctable=live)
        emp_info = ts.get("_DPMetaData", {}).get("EmployeeInfo", {})
        ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
        buckets[code].append({
            "timesheet_id": ts.get("Id"),
            "employee": emp_info.get("DisplayName", str(ts.get("Employee"))),
            "area": ou_info.get("OperationalUnitName", ""),
            "start": ts.get("StartTime"), "end": ts.get("EndTime"),
            "reason": reason, "_ts": ts,
        })

    if live:
        done = corrected = 0
        for x in buckets[APPROVE]:
            ts = x["_ts"]
            rs, re_ = ts.get("StartTime") or 0, ts.get("EndTime") or 0
            bmin = mealbreak_min(ts)
            r_start, r_end = _round15_ts(rs), _round15_ts(re_)
            r_break = _round15_min(bmin) if bmin is not None else None
            ou = ts.get("OperationalUnit")
            note = []
            if ou and (r_start != rs or r_end != re_ or (r_break is not None and r_break != bmin)):
                cok, cdetail = update_sheet(
                    ts.get("Id"), ou,
                    start=r_start if r_start != rs else None,
                    end=r_end if r_end != re_ else None,
                    break_min=r_break if (r_break is not None and r_break != bmin) else None,
                    comment="Rounded start/end/break to nearest 15 min (auto)")
                if cok:
                    corrected += 1; note.append("rounded")
                else:
                    note.append(f"round FAILED {cdetail}")
                    print(f"  CORRECT FAILED — {x['employee']} #{ts.get('Id')}: {cdetail}")
            ok, detail = approve_sheet(ts.get("Id"))
            x["result"] = ("approved" + (" + " + ", ".join(note) if note else "")) if ok else f"APPROVE FAILED {detail}"
            if ok:
                done += 1
            else:
                print(f"  APPROVE FAILED — {x['employee']} #{ts.get('Id')}: {detail}")
        audit = ROOT / "data" / "deputy_approvals_log.json"
        try:
            logrows = json.loads(audit.read_text()) if audit.exists() else []
        except Exception:
            logrows = []
        logrows.append({"run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "approved": [{"id": x["timesheet_id"], "employee": x["employee"],
                                      "reason": x["reason"], "result": x.get("result")}
                                     for x in buckets[APPROVE]]})
        audit.write_text(json.dumps(logrows[-300:], indent=2))
        print(f"LIVE: {done}/{len(buckets[APPROVE])} approved ({corrected} rounded); "
              f"{len(buckets[PARK])} left for Kris")

    for _b in buckets.values():           # keep the raw ts out of the written report
        for _x in _b:
            _x.pop("_ts", None)

    rec = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": "live-approve" if live else "report-only",
           "counts": {k: len(v) for k, v in buckets.items()},
           "would_approve": buckets[APPROVE], "parked": buckets[PARK], "skipped": buckets[SKIP]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, indent=2))

    c = rec["counts"]
    print(f"Deputy timesheets ({'LIVE auto-approve' if live else 'report-only'}) — {len(rows)} unapproved in last 7 days")
    print(f"  would auto-approve : {c[APPROVE]}")
    print(f"  parked for a human : {c[PARK]}")
    print(f"  skipped (leave/now): {c[SKIP]}")
    for b in buckets[PARK]:
        print(f"    PARK  {b['employee']:22} {b['area']:14} — {b['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
