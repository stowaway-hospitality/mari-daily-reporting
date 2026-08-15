"""What I uploaded today vs what the FIXED model says. Correct my own error.

The model's `current_par` comes from the 2026-08-09 scrape, which predates
today's uploads — so it cannot see what I set. Compare against the actual
upload payloads instead.
"""
import csv, glob, json

B = "/Users/Shared/ClaudeShared/par-build"

cost = {}
for fn in sorted(glob.glob(f"{B}/data/stock_counts/*.csv")):
    for r in csv.DictReader(open(fn, encoding="utf-8-sig")):
        n = (r.get("ProductName") or "").strip()
        try:
            c = float(r.get("Cost") or 0)
        except ValueError:
            c = 0
        if n and c:
            cost[n] = c

for label, venue_file in (("stow", "stowaway"), ("hg", "harry_gatos")):
    uploaded = {}
    for fn in (f"{B}/data/_upload_{label}.json", f"{B}/data/_downgrade_{label}.json"):
        try:
            for name, val in json.load(open(fn)):
                uploaded[name] = val
        except FileNotFoundError:
            pass

    recs = {r["product"]: r for r in
            json.load(open(f"{B}/data/par_recommendations_{venue_file}.json"))["skus"]}

    fix, cap = [], 0.0
    for name, live_now in uploaded.items():
        r = recs.get(name)
        if not r:
            continue
        new = r["rec_par"]
        if new >= live_now - 1e-9:
            continue                      # what I set is still fine
        dem = sum(r["drivers"][k] for k in ("pour_wk", "recipe_wk", "variance_wk"))
        c = cost.get(name, 0.0)
        cap += (live_now - new) * c
        fix.append((live_now - new, name, live_now, new, dem, c))

    fix.sort(reverse=True)
    with open(f"{B}/data/_correction_{label}.json", "w") as fh:
        json.dump([[f[1], f[3]] for f in fix], fh, ensure_ascii=False)

    print(f"\n{label}: {len(fix)} SKUs I over-set today, correcting down "
          f"(${cap:,.0f} of stock released)")
    print(f"   {'SKU':40s}{'I set':>8}{'should be':>11}{'dem/wk':>9}")
    for _, name, live_now, new, dem, c in fix:
        print(f"   {name[:40]:40s}{live_now:>8}{new:>11}{dem:>9.2f}")
