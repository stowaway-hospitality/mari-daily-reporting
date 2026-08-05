#!/usr/bin/env python3
"""Combine the per-night EatClub JSONs into per-venue rollups the EatClub
dashboard fetches and renders live.

For each venue reads data/eatclub_<key>_<date>.json and writes
data/eatclub_nights_<key>.json = {venue, launch, cogs_pct, latest, nights[]}.
Also writes data/eatclub_nights.json (= Harry Gatos) for backward compatibility.

Each nightly file: {date, venue, tables, covers, menu_inc, net_inc, giveaway_inc,
discount_inc, commission_inc}. giveaway_inc = discount_inc + commission_inc = the
channel cost; net_inc = menu_inc - giveaway_inc. All values inc-GST.

cogs_pct is the venue's FOOD COGS, charged on the full menu volume (EatClub is a
food offer; the discount comes off the price, not the plate). HG = 27.6% (Zak,
13 Jul actual). Stow/Mari = None until set - the dashboard then shows redemptions
and revenue but marks contribution "pending" rather than display a guessed cost.

Run: python3 scripts/build_eatclub_nights.py   (idempotent; rebuilds every file)
"""
import glob, json, os, re

ROOT = os.environ.get("EATCLUB_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.environ.get("EATCLUB_OUT", DATA)

# key -> (display name, food-COGS % charged on full menu volume; None = not set)
VENUES = {
    "hg":   ("Harry Gatos",            27.6),
    "stow": ("Stowaway Bar",           None),
    "mari": ("Marilyna's Famous Pizza", None),
}


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
    by_date = {n["date"]: n for n in nights}   # dedupe by date, last wins
    return [by_date[k] for k in sorted(by_date)]


def _rollup(key):
    name, cogs = VENUES[key]
    nights = _nights(key)
    return {
        "venue": name,
        "launch": nights[0]["date"] if nights else None,
        "cogs_pct": cogs,
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
              f"({out['launch']} -> {out['latest']}), cogs={out['cogs_pct']}, {t} tables, {c} covers")
    # backward-compat single file = Harry Gatos
    with open(os.path.join(OUT, "eatclub_nights.json"), "w") as f:
        json.dump(_rollup("hg"), f, separators=(",", ":"))


if __name__ == "__main__":
    main()
