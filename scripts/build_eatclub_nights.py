#!/usr/bin/env python3
"""Combine the per-night EatClub JSONs into per-venue rollups the EatClub
dashboard fetches and renders live.

For each venue reads data/eatclub_<key>_<date>.json and writes
data/eatclub_nights_<key>.json = {venue, launch, cogs_pct, cogs_basis, latest,
nights[]}. Also writes data/eatclub_nights.json (= Harry Gatos) for backward compat.

Each nightly file: {date, venue, tables, covers, menu_inc, net_inc, giveaway_inc,
discount_inc, commission_inc}. giveaway_inc = discount_inc + commission_inc = the
channel cost; net_inc = menu_inc - giveaway_inc. All values inc-GST.

cogs_pct is LINKED to the daily reporting: it is the venue's BLENDED food+bev
ACTUAL COGS (Xero purchases / revenue over the trailing 4 complete weeks) - the
same 'Actual COGS' figure shown on the daily dashboard - not a hardcoded rate. It
is charged on the full menu volume (EatClub is a discount off the price, not off
the plate). None only if there isn't enough Xero/revenue data yet.

Run: python3 scripts/build_eatclub_nights.py   (idempotent; rebuilds every file)
"""
import csv, datetime as dt, glob, json, os, re
from collections import defaultdict

ROOT = os.environ.get("EATCLUB_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.environ.get("EATCLUB_OUT", DATA)

# key -> display name. COGS is NOT held here: it is linked from the actual
# reporting (blended food+bev Xero actual), computed below.
VENUES = {"hg": "Harry Gatos", "stow": "Stowaway Bar", "mari": "Marilyna's Famous Pizza"}


def _week_end(iso):
    d = dt.date.fromisoformat(iso)
    return (d + dt.timedelta(days=(6 - d.weekday()) % 7)).isoformat()


def _blended_actual_cogs_pct(key, n_weeks=4):
    """Trailing blended food+bev actual COGS% = Xero purchases / revenue over the
    last n complete weeks - the SAME figure the daily dashboard's 'Actual COGS
    (Xero)' shows. None if there isn't enough data yet."""
    xf = os.path.join(DATA, "xero_cogs_weekly.csv")
    hf = os.path.join(DATA, f"{key}_daily_history.csv")
    if not (os.path.exists(xf) and os.path.exists(hf)):
        return None
    cogs = {}
    for r in csv.DictReader(open(xf)):
        if r.get("venue") == key:
            try:
                cogs[r["week_ending"]] = float(r["actual_cogs_ex_gst"] or 0)
            except (TypeError, ValueError):
                pass
    rev = defaultdict(float)
    for r in csv.DictReader(open(hf)):
        try:
            rev[_week_end(r["date"])] += float(r.get("revenue_ex_gst") or 0)
        except (TypeError, ValueError):
            pass
    weeks = sorted(w for w in cogs if rev.get(w, 0) > 0)
    last = weeks[-n_weeks:]
    tc = sum(cogs[w] for w in last)
    tr = sum(rev[w] for w in last)
    return round(tc / tr * 100, 1) if tr else None


def _nights(key):
    nights = []
    for path in sorted(glob.glob(os.path.join(DATA, f"eatclub_{key}_*.json")) +
                       glob.glob(os.path.join(DATA, f"eatclub_{key}_*.csv"))):
        if not re.search(rf"eatclub_{key}_(\d{{4}}-\d{{2}}-\d{{2}})\.(json|csv)$", os.path.basename(path)):
            continue
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("date"):
            continue
        disc = float(d.get("discount_inc") or 0)
        comm = float(d.get("commission_inc") or 0)
        nights.append({
            "date": d["date"],
            "tables": d.get("tables", 0),
            "covers": d.get("covers", 0),
            "menu_inc": round(float(d.get("menu_inc") or 0), 2),
            "net_inc": round(float(d.get("net_inc") or 0), 2),
            "discount_inc": round(disc, 2),
            "commission_inc": round(comm, 2),
            "giveaway_inc": round(float(d.get("giveaway_inc") or (disc + comm)), 2),
        })
    by_date = {n["date"]: n for n in nights}
    return [by_date[k] for k in sorted(by_date)]


def _rollup(key):
    nights = _nights(key)
    return {
        "venue": VENUES[key],
        "launch": nights[0]["date"] if nights else None,
        "cogs_pct": _blended_actual_cogs_pct(key),
        "cogs_basis": "blended food+bev actual (Xero, trailing 4 complete weeks)",
        "latest": nights[-1]["date"] if nights else None,
        "nights": nights,
    }


def main():
    for key in VENUES:
        out = _rollup(key)
        with open(os.path.join(OUT, f"eatclub_nights_{key}.json"), "w") as f:
            json.dump(out, f, separators=(",", ":"))
        t = sum(n["tables"] for n in out["nights"]); c = sum(n["covers"] for n in out["nights"])
        print(f"eatclub_nights_{key}.json: {len(out['nights'])} nights "
              f"({out['launch']} -> {out['latest']}), cogs={out['cogs_pct']}%, {t} tables, {c} covers")
    with open(os.path.join(OUT, "eatclub_nights.json"), "w") as f:
        json.dump(_rollup("hg"), f, separators=(",", ":"))


if __name__ == "__main__":
    main()
