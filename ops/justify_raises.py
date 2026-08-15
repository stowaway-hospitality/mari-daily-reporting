"""For every big raise: what SET the number, and is it defensible?

The question being answered: if we were not running out of this SKU before, why
is the new par so much bigger?
"""
import json

B = "/Users/Shared/ClaudeShared/par-build"

for venue_file, label in (("stowaway", "STOWAWAY"), ("harry_gatos", "HARRY GATOS")):
    d = json.load(open(f"{B}/data/par_recommendations_{venue_file}.json"))
    rows = []
    for r in d["skus"]:
        live, rec = r.get("current_par"), r["rec_par"]
        if live is None or rec <= live + 1e-9:
            continue
        dr, sv = r["drivers"], (r.get("service") or {})
        dem = dr["pour_wk"] + dr["recipe_wk"] + dr["variance_wk"]
        cover_before = (live / dem) if dem > 0 else float("inf")
        cover_after = (rec / dem) if dem > 0 else float("inf")

        # what set it?
        burst = sv.get("burst_floor") or 0
        why = []
        if sv.get("burst_floored"):
            why.append(f"BURST FLOOR {burst}")
        if "spike_floored" in (r.get("flags") or []):
            why.append(f"sanity floor {r.get('sanity_floor')}")
        if (r.get("override") or {}).get("type"):
            why.append(f"override {r['override']['type']}")
        if not why:
            why.append(f"service calc ({sv.get('service_class')}, {sv.get('path')})")
        rows.append((rec - live, r["product"], live, rec, dem,
                     cover_before, cover_after, dr["variance_wk"],
                     r.get("seasonal_index"), "; ".join(why)))

    rows.sort(reverse=True)
    print(f"\n{'='*100}\n{label} — biggest raises, decomposed\n{'='*100}")
    print(f"{'SKU':34s}{'live':>6}{'new':>7}{'dem/wk':>8}{'cover→':>9}{'shrink':>8}{'seas':>6}  what set it")
    for delta, name, live, rec, dem, cb, ca, shr, seas, why in rows[:18]:
        cbs = "inf" if cb == float("inf") else f"{cb:.1f}"
        cas = "inf" if ca == float("inf") else f"{ca:.1f}"
        print(f"{name[:34]:34s}{live:>6}{rec:>7}{dem:>8.2f}{cbs+'→'+cas:>9}{shr:>8.2f}{(seas or 0):>6.2f}  {why}")

    # the honest flag: raises that give a lot of cover on a WEEKLY order cycle
    print(f"\n  Raises leaving > 6 weeks of cover (weekly ordering — is that excessive?):")
    n = 0
    for delta, name, live, rec, dem, cb, ca, shr, seas, why in rows:
        if dem > 0 and ca > 6:
            print(f"    {name[:36]:36s} {live} -> {rec}   demand {dem:.2f}/wk = {ca:.0f} weeks cover   [{why}]")
            n += 1
    if not n:
        print("    (none)")
