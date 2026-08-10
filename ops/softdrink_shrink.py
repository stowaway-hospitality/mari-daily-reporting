import json
B = "/Users/Shared/ClaudeShared/par-build"
sh = json.load(open(f"{B}/data/par_shrinkage.json"))
recs = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))

# shrinkage file may be keyed by sku or a list
if isinstance(sh, dict) and "skus" in sh:
    smap = {r["product"]: r for r in sh["skus"]}
elif isinstance(sh, dict):
    smap = sh
else:
    smap = {r.get("product"): r for r in sh}

WANT = ["Coke Zero Can", "Coke Can", "Sprite Can", "Coke 1.25L",
        "Coke Zero 1.25L", "Solo 1.25L", "Sprite 1.25L", "Sunkist 1.25L",
        "San Pellegrino 500ml", "Bundaberg Ginger Beer [750ml]"]

print("{:26s}{:>7}{:>8}{:>9}{:>9}{:>8}  {}".format(
    "SKU", "live", "rec", "pour/wk", "shrink", "capped", "flags"))
for r in recs["skus"]:
    if r["product"] not in WANT:
        continue
    d = r["drivers"]
    s = r.get("shrinkage") or {}
    print("{:26s}{:>7}{:>8}{:>9.2f}{:>9.3f}{:>8}  {}".format(
        r["product"][:26], str(r.get("current_par")), r["rec_par"],
        d["pour_wk"], d["variance_wk"], str(s.get("capped")),
        ",".join(f for f in r.get("flags", []) if "shrink" in f or "held" in f)))

print("\nraw shrinkage records (what the counts actually measured):")
for w in WANT:
    rec = smap.get(w)
    if rec:
        print(f"  {w[:28]:28s} loss/wk={rec.get('loss_per_week')} "
              f"frac={rec.get('loss_fraction')} n={rec.get('n_periods')} capped={rec.get('capped')}")
