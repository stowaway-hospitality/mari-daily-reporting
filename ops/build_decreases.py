"""The DECREASES held back from the 2026-08-10 raises-only upload.

Zak has since confirmed "its fine to be below 1 case", which was the only open
objection to them, and asked for them to be uploaded.

Still excluded, deliberately:
  - anything a hard override protects (drum reserves, holds, zeros)
  - anything the model is only HOLDING because it cannot see the demand
    (`held_no_recent_demand` / `held_cross_venue_demand_only`) — cutting on
    absent evidence is exactly how you run out
"""
import json

B = "/Users/Shared/ClaudeShared/par-build"
HOLD_FLAGS = {"held_no_recent_demand", "held_cross_venue_demand_only"}

for venue_file, label in (("stowaway", "stow"), ("harry_gatos", "hg")):
    d = json.load(open(f"{B}/data/par_recommendations_{venue_file}.json"))
    cuts, skipped = [], []
    for r in d["skus"]:
        live, rec = r.get("current_par"), r["rec_par"]
        if live is None or rec >= live - 1e-9:
            continue
        flags = set(r.get("flags") or [])
        ov = r.get("override") or {}
        dr = r["drivers"]
        demand = dr["pour_wk"] + dr["recipe_wk"] + dr["variance_wk"]
        if flags & HOLD_FLAGS:
            skipped.append((r["product"], live, rec, demand, "model is holding — demand not observable"))
            continue
        if ov.get("type") in ("zero", "hold", "reserve", "min", "max"):
            skipped.append((r["product"], live, rec, demand, f"protected override ({ov.get('type')})"))
            continue
        if rec == 0 and demand > 0:
            # A zero par is never reordered again. On a SKU that still sells,
            # that is a DELISTING, not a trim — the bottle empties and nothing
            # replaces it. Zak's standing instruction is "make sure we won't run
            # out", so this needs him to say the word, not me to infer it.
            skipped.append((r["product"], live, rec, demand,
                            "would zero a SKU that still sells — delisting, needs sign-off"))
            continue
        cuts.append([r["product"], rec, live, round(live - rec, 1), demand])

    cuts.sort(key=lambda c: -c[3])
    with open(f"{B}/data/_downgrade_{label}.json", "w") as fh:
        json.dump([[c[0], c[1]] for c in cuts], fh, ensure_ascii=False)
    print(f"\n{label}: {len(cuts)} decreases to upload   ({len(skipped)} withheld)")
    print(f"   {'SKU':40s}{'live':>7}{'->':>4}{'new':>7}{'demand/wk':>11}   weeks cover after")
    for name, rec, live, delta, dem in cuts[:20]:
        cov = (rec / dem) if dem > 0 else float("inf")
        print(f"   {name[:40]:40s}{live:>7}{'->':>4}{rec:>7}{dem:>11.2f}   {cov:>5.1f}")
    if skipped:
        print("   withheld:")
        for name, live, rec, dem, why in skipped[:8]:
            print(f"      {name[:38]:38s} {live} -> {rec}  — {why}")
