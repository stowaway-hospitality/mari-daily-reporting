"""Stockout-risk audit across both venues. Answers one question per SKU:
if we leave the live par alone, can we run out?"""
import csv, json, math, re
from collections import defaultdict

B = "/Users/Shared/ClaudeShared/par-build"

# pack sizes from the BO catalog (positional: a text pack unit then its count)
def pack_sizes(venue_file):
    out = {}
    for r in csv.DictReader(open(f"{B}/data/bo_exports/{venue_file}_products.csv",
                                 encoding="utf-8-sig")):
        v = list(r.values())
        name = r.get("ProductName") or ""
        for i, x in enumerate(v):
            if str(x).strip().lower() in ("crates", "case", "carton", "pack", "outer"):
                try:
                    p = float(v[i + 1])
                    if p > 1:
                        out[name] = p
                except (IndexError, TypeError, ValueError):
                    pass
                break
    return out

def audit(venue, venue_file):
    d = json.load(open(f"{B}/data/par_recommendations_{venue_file}.json"))
    packs = pack_sizes(venue_file)
    raises, holds, subpack, zero_demand, capped, no_par = [], [], [], [], [], []

    for r in d["skus"]:
        live = r.get("current_par")
        rec = r["rec_par"]
        dr = r["drivers"]
        demand = dr["pour_wk"] + dr["recipe_wk"] + dr["variance_wk"]
        ov = r.get("override") or {}
        flags = r.get("flags") or []
        pack = packs.get(r["product"])

        if ov.get("type") == "zero":
            continue
        if (r.get("shrinkage") or {}).get("capped"):
            capped.append((r["product"], live, rec, demand))
        if demand > 0.05 and (live is None or live == 0):
            no_par.append((r["product"], demand, rec))
        if live is not None and rec > live + 1e-9:
            raises.append((round(rec - live, 1), r["product"], live, rec, demand))
        elif live is not None and rec < live - 1e-9:
            holds.append((round(live - rec, 1), r["product"], live, rec, demand))
        if pack and rec > 0 and rec < pack:
            subpack.append((r["product"], pack, live, rec, demand))
        if demand <= 0.001 and live:
            zero_demand.append((r["product"], live))

    raises.sort(reverse=True); holds.sort(reverse=True)
    print(f"\n{'='*74}\n{venue.upper()}  —  {d['summary']['n_skus']} SKUs\n{'='*74}")
    print(f"RAISES (model wants MORE than the live par) — these are the stockout fixes: {len(raises)}")
    for delta, name, live, rec, dem in raises[:30]:
        print(f"   +{delta:<6} {str(live):>6} -> {rec:<6} demand {dem:>6.2f}/wk   {name[:40]}")
    if len(raises) > 30:
        print(f"   ... and {len(raises)-30} more")

    print(f"\nSKUs with real demand but NO live par (nothing is ordering these): {len(no_par)}")
    for name, dem, rec in sorted(no_par, key=lambda x: -x[1])[:12]:
        print(f"   demand {dem:>6.2f}/wk  rec {rec:<6}  {name[:44]}")

    print(f"\nPars below ONE PACK (you buy these by the case): {len(subpack)}")
    for name, pack, live, rec, dem in sorted(subpack, key=lambda x: -x[4])[:12]:
        print(f"   pack {pack:>3.0f}  live {str(live):>6} rec {rec:<6} demand {dem:>5.2f}/wk  {name[:36]}")

    print(f"\nShrinkage CAPPED (loss exceeded 50% of demand — investigate): {len(capped)}")
    for name, live, rec, dem in capped[:10]:
        print(f"   live {str(live):>6} rec {rec:<6} demand {dem:>5.2f}/wk  {name[:44]}")

    print(f"\nDECREASES held back (NOT uploading these): {len(holds)}")
    return raises, holds, no_par, subpack

for v, vf in (("stowaway", "stowaway"), ("harry gatos", "harry_gatos")):
    audit(v, vf)
