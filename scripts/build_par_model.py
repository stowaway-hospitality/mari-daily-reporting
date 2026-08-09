#!/usr/bin/env python3
"""
Par model v2 — build step.

Computes par recommendations for Stowaway (stow) + Harry Gatos (hg) off the live
feeds and writes:
    data/par_recommendations_stowaway.json
    data/par_recommendations_harry_gatos.json

COVERAGE GATE: if any Classic/Signature cocktail with recent sales does not
resolve to a recipe (open-price '$NN Custom Cocktail' excepted) the build exits
NONZERO and prints the offenders — fail toward review.

Run:  /opt/homebrew/bin/python3.12 scripts/build_par_model.py   (Actions: python 3.11)
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.par import model  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = {"stow": "par_recommendations_stowaway.json", "hg": "par_recommendations_harry_gatos.json"}


def _summary(recs):
    inc = dec = same = 0
    changes = []
    for sku, r in recs.items():
        cur = r["current_par"]
        if cur is None:
            if r["rec_par"] > 0:
                inc += 1
                changes.append((r["rec_par"] - 0.0, sku, 0.0, r["rec_par"]))
            continue
        d = round(r["rec_par"] - cur, 1)
        if d > 0:
            inc += 1
        elif d < 0:
            dec += 1
        else:
            same += 1
        if abs(d) > 1e-9:
            changes.append((d, sku, cur, r["rec_par"]))
    changes.sort(key=lambda x: -abs(x[0]))
    return inc, dec, same, changes


def build_venue(venue, rows):
    recs, meta = model.compute_venue(venue, DATA, rows=rows)
    inc, dec, same, changes = _summary(recs)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "venue": venue,
        "source": "products_weekly.csv + recipes + bo_exports + scrape + par_overrides.json",
        "weeks": meta["weeks"],
        "week_range": meta["week_range"],
        "summary": {
            "n_skus": len(recs),
            "increase": inc,
            "decrease": dec,
            "unchanged": same,
            "coverage_gaps": [n for n, _ in meta["coverage_gaps"]],
        },
        "skus": [recs[k] for k in sorted(recs)],
    }
    with open(os.path.join(DATA, OUT[venue]), "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return recs, meta, (inc, dec, same, changes)


def main():
    rows = model.load_weekly(DATA)
    gate_failed = False
    all_meta = {}
    for venue in ("stow", "hg"):
        recs, meta, (inc, dec, same, changes) = build_venue(venue, rows)
        all_meta[venue] = (recs, meta, (inc, dec, same, changes))
        print(f"\n===== {venue.upper()} =====")
        print(f"  weeks {meta['week_range'][0]}..{meta['week_range'][1]}  ({meta['weeks']} wk)")
        print(f"  SKUs: {len(recs)}   increase {inc} / decrease {dec} / unchanged {same}")
        print("  Top 10 changes vs current par:")
        for d, sku, cur, rec in changes[:10]:
            print(f"    {d:+7.1f}   {cur!s:>6} -> {rec:<6}  {sku}")
        gaps = meta["coverage_gaps"]
        if gaps:
            gate_failed = True
            print(f"  !! COVERAGE GATE FAIL — {len(gaps)} live cocktail(s) with no recipe:")
            for name, q in gaps:
                print(f"       {q:8.0f}  {name}")
        else:
            print("  Coverage gate: PASS (every recent Classic/Signature cocktail resolves)")

    _sanity(all_meta)

    if gate_failed:
        print("\nBUILD FAILED: coverage gate not satisfied.", file=sys.stderr)
        sys.exit(1)
    print("\nPar recommendations written:", ", ".join(OUT.values()))


def _sanity(all_meta):
    print("\n===== SANITY =====")
    stow = all_meta["stow"][0]
    rooster = "Rooster Rojo Blanco Tequila [Bottle]"
    r = stow.get(rooster)
    if r:
        ok = r["rec_par"] >= 40
        print(f"  Rooster rec_par={r['rec_par']} (>=40 via override: {'OK' if ok else 'FAIL'});"
              f" recipe_wk driver={r['drivers']['recipe_wk']}"
              f" ({'margarita reaches Rooster' if r['drivers']['recipe_wk'] > 0 else 'NO recipe consumption!'})")
    else:
        print(f"  !! {rooster} not found in recs")
    bombay = stow.get("Bombay Dry [Bottle]")
    if bombay:
        print(f"  Bombay Dry [Bottle] rec_par={bombay['rec_par']} "
              f"(pour_wk={bombay['drivers']['pour_wk']}, recipe_wk={bombay['drivers']['recipe_wk']}, "
              f"override={bombay['override']})")


if __name__ == "__main__":
    main()
