#!/usr/bin/env python3
"""
Par model v3 — build step.

Computes par recommendations for Stowaway (stow) + Harry Gatos (hg) off the live
feeds and writes:
    data/par_recommendations_stowaway.json
    data/par_recommendations_harry_gatos.json
    data/par_shrinkage.json              (Stowaway; HG has no stock counts yet)

v3 adds, per SKU: a measured shrinkage channel from the Lightspeed stock counts,
an (R,S) service-level par over the REAL delivery-to-delivery exposure window,
a week-of-year seasonal index, and a shadow bookings uplift that is recorded but
not applied. See modules/par/model.py for the why of each.

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
from modules.par import calendar as par_calendar  # noqa: E402
from modules.par import model  # noqa: E402
from modules.par import shrinkage as shrinkage_mod  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = {"stow": "par_recommendations_stowaway.json", "hg": "par_recommendations_harry_gatos.json"}
SHRINK_OUT = "par_shrinkage.json"


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
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    recs, meta = model.compute_venue(venue, DATA, rows=rows)
    inc, dec, same, changes = _summary(recs)

    # Shrinkage feed (Stowaway only today — HG has no stock counts).
    if meta["shrinkage"]:
        shrinkage_mod.write_json(
            os.path.join(DATA, SHRINK_OUT), venue, meta["shrinkage"],
            meta["shrinkage_summary"], generated_at)

    payload = {
        "schema_version": 2,
        "engine": meta["engine"],
        "generated_at": generated_at,
        "venue": venue,
        "source": "products_weekly.csv + recipes + bo_exports + scrape + "
                  "par_overrides.json + stock_counts + par_calendar.json",
        "weeks": meta["weeks"],
        "week_range": meta["week_range"],
        "order_sunday": meta["order_sunday"],
        "exposure": meta["exposure"],
        "target_week_of_year": meta["target_week_of_year"],
        "service_classes": meta["service_classes"],
        "bookings": {
            "live": meta["bookings_live"],
            "status": meta["bookings_status"],
            "note": "SHADOW MODE — bookings_uplift_shadow is recorded per SKU but "
                    "is NOT added to rec_par (modules/par/bookings.py BOOKINGS_LIVE=False)",
        },
        "summary": {
            "n_skus": len(recs),
            "increase": inc,
            "decrease": dec,
            "unchanged": same,
            "coverage_gaps": [n for n, _ in meta["coverage_gaps"]],
            # counted off the FLAGS, so these agree with what a human reading
            # the SKU list sees (a loss under MATERIAL_LOSS_WK is count rounding)
            "shrinkage_applied": sum(1 for r in recs.values()
                                     if "shrinkage_applied" in r["flags"]),
            "shrinkage_capped": sum(1 for r in recs.values()
                                    if "shrinkage_capped_investigate" in r["flags"]),
            "shrinkage_without_demand_mapping": sum(
                1 for r in recs.values()
                if "shrinkage_without_demand_mapping" in r["flags"]),
            "low_mover_poisson": sum(1 for r in recs.values()
                                     if r["service"].get("path") == "poisson"),
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
        exp = meta["exposure"]
        print(f"  order Sun {meta['order_sunday']} -> deliver {exp['delivery']}"
              f" -> next {exp['next_delivery']}"
              f"  ({exp['days']}d, {exp['day_units']} day-units,"
              f" {exp['exposure_ratio']:.2f}x normal)  [{exp['note']}]")
        print(f"  service classes: {meta['service_classes']}   "
              f"Poisson low-movers: "
              f"{sum(1 for r in recs.values() if r['service'].get('path') == 'poisson')}")
        nsh = sum(1 for r in recs.values() if "shrinkage_applied" in r["flags"])
        ncap = sum(1 for r in recs.values()
                   if "shrinkage_capped_investigate" in r["flags"])
        nun = sum(1 for r in recs.values()
                  if "shrinkage_without_demand_mapping" in r["flags"])
        print(f"  shrinkage: {nsh} SKUs with a material measured loss, "
              f"{ncap} capped (investigate), {nun} losing stock the till never rang up")
        print(f"  bookings (SHADOW, not applied): {meta['bookings_status']}")
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
        ov = r["override"] or {}
        reserve = float(ov.get("value") or 0) if ov.get("type") == "reserve" else 0.0
        ok = r["rec_par"] >= reserve
        print(f"  Rooster rec_par={r['rec_par']} (reserve {reserve} additive: "
              f"{'OK' if ok else 'FAIL'});"
              f" recipe_wk driver={r['drivers']['recipe_wk']}"
              f" ({'margarita reaches Rooster' if r['drivers']['recipe_wk'] > 0 else 'NO recipe consumption!'})"
              f" variance_wk={r['drivers']['variance_wk']}")
    else:
        print(f"  !! {rooster} not found in recs")
    bombay = stow.get("Bombay Dry [Bottle]")
    if bombay:
        print(f"  Bombay Dry [Bottle] rec_par={bombay['rec_par']} "
              f"(pour_wk={bombay['drivers']['pour_wk']}, recipe_wk={bombay['drivers']['recipe_wk']}, "
              f"variance_wk={bombay['drivers']['variance_wk']}, override={bombay['override']})")

    cal = par_calendar.load_calendar(DATA)
    xm = par_calendar.christmas_2026_exposure(cal)
    print(f"  CHRISTMAS 2026: order Sun 2026-12-20 -> deliver {xm['delivery']}"
          f" -> next delivery {xm['next_delivery']}: {xm['days']} days,"
          f" {xm['day_units']} day-units = {xm['exposure_ratio']:.2f}x a normal cycle."
          f"  See data/_par_review/christmas_2026.md")


if __name__ == "__main__":
    main()
