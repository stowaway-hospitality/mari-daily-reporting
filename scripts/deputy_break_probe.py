#!/usr/bin/env python3
"""Learn Deputy's meal-break encoding from real history — READ ONLY, writes nothing
back to Deputy. Pulls a wide window of past timesheets and, for each, compares
candidate decodings of the Mealbreak field (and the Slots array) against the
GROUND TRUTH implied break = gross shift time − paid hours. Whatever decoding
matches ground truth across the most sheets is the one deputy_triage should trust.

Output: data/deputy_break_probe.json  (summary + samples for offline analysis)
Env: DEPUTY_TOKEN (read), PROBE_DAYS (default 365).
"""
from __future__ import annotations
import json, os, sys, urllib.request
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "deputy_break_probe.json"
HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN", "")


def _req(path, body):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Authorization": f"OAuth {TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def window(since, until, start=0):
    return _req("/api/v1/resource/Timesheet/QUERY", {
        "search": {"s1": {"field": "StartTime", "type": "ge", "data": since},
                   "s2": {"field": "StartTime", "type": "lt", "data": until},
                   "s3": {"field": "Discarded", "type": "eq", "data": 0}},
        "join": ["EmployeeObject", "OperationalUnitObject"],
        "max": 500, "start": start})


def decode_datetime_timepart(mb):
    """If Mealbreak is a string carrying a time like '...T00:30:00' or 'H:M:S',
    read the H:M:S as a DURATION -> minutes. Returns minutes or None."""
    if not isinstance(mb, str) or ":" not in mb:
        return None
    tpart = mb.split("T")[-1] if "T" in mb else mb
    bits = tpart.split(":")
    try:
        h, m = int(bits[0]), int(bits[1])
        return h * 60 + m
    except Exception:
        return None


def decode_numeric(mb):
    if isinstance(mb, bool) or not isinstance(mb, (int, float)):
        return None
    return round(mb / 60) if mb > 60 else round(mb)   # seconds if big else minutes


def main():
    if not TOKEN:
        print("DEPUTY_TOKEN not set", file=sys.stderr); return 2
    days = int(os.environ.get("PROBE_DAYS", "365"))
    now = int(datetime.now(timezone.utc).timestamp())
    rows = []
    t = now - days * 86400
    win = 14 * 86400
    while t < now:
        u = min(t + win, now)
        start = 0
        while True:
            batch = window(t, u, start)
            rows.extend(batch)
            if len(batch) < 500:
                break
            start += 500
        t = u
    print(f"pulled {len(rows)} timesheets over {days} days")

    mb_types = Counter()
    mb_value_samples = Counter()
    comments = Counter()
    break_kw_comments = Counter()
    no_break_but_deducted = []
    clock_split_comments = Counter()
    _NB_KW = ("no break", "nobreak", "no br", "didnt break", "didn't break",
              "did not break", "without break", "worked through", "through break",
              "straight through", "no lunch", "no meal")
    dt_match = dt_checked = 0
    num_match = num_checked = 0
    slots_present = 0
    mismatch_samples = []
    total_vs_gross = []  # to learn what TotalTime means

    for ts in rows:
        mb = ts.get("Mealbreak")
        mb_types[type(mb).__name__] += 1
        mb_value_samples[repr(mb)[:40]] += 1
        start_u = ts.get("StartTime") or 0
        end_u = ts.get("EndTime") or 0
        gross_h = (end_u - start_u) / 3600 if end_u and start_u else None
        total = ts.get("TotalTime")
        if ts.get("Slots"):
            slots_present += 1
        comment = (ts.get("EmployeeComment") or "").strip()
        if comment:
            cl = comment.lower()
            comments[cl[:70]] += 1
            if re.search(r"start|finish|clock|sign\s*in|forgot|\d{1,2}[:.]\d{2}|\b\d{1,2}\s*(?:am|pm)\b|\b(?:stow|hg|harry|floor|bar|kitchen|pizza)\b", cl):
                clock_split_comments[comment[:90]] += 1
            if any(k in cl for k in ("break", "lunch", "meal")) or "nb" == cl.strip():
                break_kw_comments[cl[:70]] += 1
            # underpayment signature: staff say no break yet a break was deducted
            dec_here = decode_datetime_timepart(mb)
            if any(k in cl for k in _NB_KW) and dec_here and dec_here > 0:
                emp = ts.get("_DPMetaData", {}).get("EmployeeInfo", {}).get("DisplayName", str(ts.get("Employee")))
                no_break_but_deducted.append({
                    "id": ts.get("Id"), "employee": emp, "start": ts.get("StartTime"),
                    "deducted_break_min": dec_here, "comment": comment[:120],
                    "approved": ts.get("TimeApproved")})
        if gross_h and total is not None and gross_h > 0:
            implied_break_min = round((gross_h - total) * 60)
            if -5 <= implied_break_min <= 240:  # sane break range
                dt = decode_datetime_timepart(mb)
                if dt is not None:
                    dt_checked += 1
                    if abs(dt - implied_break_min) <= 2:
                        dt_match += 1
                    elif len(mismatch_samples) < 25:
                        mismatch_samples.append({
                            "id": ts.get("Id"), "mb": repr(mb),
                            "decoded_min": dt, "implied_break_min": implied_break_min,
                            "gross_h": round(gross_h, 3), "total": total})
                nm = decode_numeric(mb)
                if nm is not None:
                    num_checked += 1
                    if abs(nm - implied_break_min) <= 2:
                        num_match += 1
                if len(total_vs_gross) < 60:
                    total_vs_gross.append({"gross_h": round(gross_h, 3), "total": total,
                                           "implied_break_min": implied_break_min})

    summary = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pulled": len(rows), "days": days,
        "mealbreak_field_types": dict(mb_types),
        "top_mealbreak_values": mb_value_samples.most_common(25),
        "datetime_timepart_decode": {"checked": dt_checked, "matched_ground_truth": dt_match,
                                     "match_pct": round(100 * dt_match / dt_checked, 1) if dt_checked else None},
        "numeric_decode": {"checked": num_checked, "matched_ground_truth": num_match,
                           "match_pct": round(100 * num_match / num_checked, 1) if num_checked else None},
        "slots_present_count": slots_present,
        "mismatch_samples": mismatch_samples,
        "top_comments": comments.most_common(25),
        "clock_split_comments": clock_split_comments.most_common(70),
        "break_keyword_comments": break_kw_comments.most_common(40),
        "no_break_comment_but_break_deducted": no_break_but_deducted,
        "no_break_underpayment_count": len(no_break_but_deducted),
        "total_vs_gross_samples": total_vs_gross,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2))
    print(f"mealbreak field types: {dict(mb_types)}")
    d = summary["datetime_timepart_decode"]; n = summary["numeric_decode"]
    print(f"datetime-timepart decode: {d['matched_ground_truth']}/{d['checked']} = {d['match_pct']}% match ground truth")
    print(f"numeric decode          : {n['matched_ground_truth']}/{n['checked']} = {n['match_pct']}% match ground truth")
    print(f"slots present on {slots_present} sheets")
    print(f"break-keyword comments: {sum(break_kw_comments.values())} across {len(break_kw_comments)} distinct phrasings")
    print(f"UNDERPAYMENT SIGNATURE (no-break comment + break still deducted): {len(no_break_but_deducted)} sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
