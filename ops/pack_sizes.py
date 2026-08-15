import csv, json
from collections import Counter
B = "/Users/Shared/ClaudeShared/par-build"

rows = list(csv.DictReader(open(f"{B}/data/bo_exports/stowaway_products.csv",
                                encoding="utf-8-sig")))
hdr = rows[0].keys()
# the pack columns sit near the end: ..., "Crates", 24, "UNIT"
packcols = [h for h in hdr if h and ("crate" in h.lower() or "pack" in h.lower()
                                     or "case" in h.lower() or "outer" in h.lower())]
print("candidate pack columns:", packcols or "(unnamed — positional)")

scr = json.load(open(f"{B}/data/_scrape_stow_20260809.json"))["nonzero_pars"]
recs = {r["product"]: r for r in
        json.load(open(f"{B}/data/par_recommendations_stowaway.json"))["skus"]}

# find, positionally, the numeric pack size that follows a text pack unit
vals = list(rows[0].values())
print("sample tail values:", vals[-8:])

n_with_pack, examples = 0, []
for r in rows:
    name = r.get("ProductName") or ""
    v = list(r.values())
    pack = None
    for i, x in enumerate(v):
        if str(x).strip().lower() in ("crates", "case", "carton", "pack", "outer"):
            try:
                pack = float(v[i + 1])
            except (IndexError, TypeError, ValueError):
                pack = None
            break
    if pack and pack > 1:
        n_with_pack += 1
        if name in scr:
            examples.append((name, pack, scr[name], recs.get(name, {}).get("rec_par")))

print(f"\nSKUs in catalog with a pack size >1: {n_with_pack}")
print(f"of which currently hold a live par: {len(examples)}\n")
print(f"{'SKU':34s}{'pack':>6}{'live par':>10}{'model rec':>11}{'live/pack':>11}")
for name, pack, live, rec in sorted(examples, key=lambda e: -e[2])[:22]:
    print(f"{name[:34]:34s}{pack:>6.0f}{live:>10}{str(rec):>11}{live/pack:>11.2f}")
