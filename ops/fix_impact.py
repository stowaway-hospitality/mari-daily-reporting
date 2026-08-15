import csv, glob, json
B = "/Users/Shared/ClaudeShared/par-build"
before = {r["product"]: r for r in json.load(open("/Users/Shared/ClaudeShared/before_fix.json"))["skus"]}
after = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))

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

moved, cap = [], 0.0
for r in after["skus"]:
    b = before.get(r["product"])
    if not b or b["rec_par"] == r["rec_par"]:
        continue
    live = r.get("current_par")
    dem = sum(r["drivers"][k] for k in ("pour_wk", "recipe_wk", "variance_wk"))
    c = cost.get(r["product"], 0.0)
    cap += (r["rec_par"] - b["rec_par"]) * c
    moved.append((b["rec_par"] - r["rec_par"], r["product"], live,
                  b["rec_par"], r["rec_par"], dem, c))

moved.sort(reverse=True)
print(f"SKUs whose rec changed after the fix: {len(moved)}\n")
print(f"{'SKU':38s}{'live':>6}{'was':>7}{'now':>7}{'dem/wk':>8}{'cover':>7}")
for _, name, live, was, now, dem, c in moved[:30]:
    cov = (now / dem) if dem > 0 else float('inf')
    cs = "inf" if cov == float('inf') else f"{cov:.0f}"
    print(f"{name[:38]:38s}{str(live):>6}{was:>7}{now:>7}{dem:>8.2f}{cs:>7}")
print(f"\ncapital change from the fix: ${cap:,.0f}")

# is anything now BELOW its live par that we already uploaded?
downs = [(r["product"], r.get("current_par"), r["rec_par"]) for r in after["skus"]
         if r.get("current_par") is not None and r["rec_par"] < r["current_par"] - 1e-9
         and before.get(r["product"], {}).get("rec_par", 0) >= r.get("current_par", 0)]
print(f"\nSKUs we RAISED live that the fix now wants lower again: {len(downs)}")
for n, live, rec in sorted(downs, key=lambda x: -(x[1] - x[2]))[:30]:
    print(f"   {n[:40]:40s} live {live:>5} -> should be {rec}")
