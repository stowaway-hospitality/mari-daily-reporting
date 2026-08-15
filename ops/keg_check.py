import json
B = "/Users/Shared/ClaudeShared/par-build"
d = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))
print(f"{'KEG SKU':34s}{'live':>6}{'rec':>7}{'dem/wk':>8}{'burst':>7}{'sanity':>8}{'path':>10}  what set it")
for r in d["skus"]:
    if "[Keg]" not in r["product"]:
        continue
    sv = r.get("service") or {}
    dr = r["drivers"]
    dem = dr["pour_wk"] + dr["recipe_wk"] + dr["variance_wk"]
    if dem <= 0 and not r.get("current_par"):
        continue
    why = []
    if sv.get("burst_floored"):
        why.append("BURST")
    if "spike_floored" in (r.get("flags") or []):
        why.append("SANITY")
    if (r.get("override") or {}).get("type"):
        why.append("override")
    if not why:
        why.append("service calc")
    print(f"{r['product'][:34]:34s}{str(r.get('current_par')):>6}{r['rec_par']:>7}{dem:>8.2f}"
          f"{str(sv.get('burst_floor')):>7}{str(r.get('sanity_floor')):>8}{str(sv.get('path')):>10}  {'+'.join(why)}")
