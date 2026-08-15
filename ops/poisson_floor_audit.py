"""The suspicious cluster: SKUs raised to exactly 1.0 by discreteness alone.

A Poisson/NegBinom 95th percentile of a very small lambda is 1 — you cannot have
a fractional event. So EVERY slow-moving spirit lands on "1 whole bottle"
regardless of how slowly it actually sells. That is a property of the
distribution, not evidence about the SKU.
"""
import csv, json
from collections import defaultdict

B = "/Users/Shared/ClaudeShared/par-build"
d = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))

# unit cost from the most recent stock count that carries it
cost = {}
import glob, os, re
for fn in sorted(glob.glob(f"{B}/data/stock_counts/*.csv")):
    for r in csv.DictReader(open(fn, encoding="utf-8-sig")):
        n = (r.get("ProductName") or "").strip()
        try:
            c = float(r.get("Cost") or 0)
        except ValueError:
            c = 0
        if n and c:
            cost[n] = c

cluster, capital_before, capital_after = [], 0.0, 0.0
for r in d["skus"]:
    live, rec = r.get("current_par"), r["rec_par"]
    sv = r.get("service") or {}
    if live is None or rec <= live + 1e-9:
        continue
    if sv.get("path") not in ("poisson", "negbinom"):
        continue
    dr = r["drivers"]
    dem = dr["pour_wk"] + dr["recipe_wk"] + dr["variance_wk"]
    if dem <= 0 or rec / dem <= 6:          # only the long-cover ones
        continue
    c = cost.get(r["product"], 0.0)
    cluster.append((rec - live, r["product"], live, rec, dem, rec / dem, c))
    capital_before += live * c
    capital_after += rec * c

cluster.sort(reverse=True)
print(f"SKUs raised by the discrete low-mover path to >6 weeks cover: {len(cluster)}\n")
print(f"{'SKU':38s}{'live':>6}{'new':>6}{'dem/wk':>8}{'weeks':>7}{'$/unit':>8}{'$ added':>9}")
for delta, name, live, rec, dem, cov, c in cluster:
    print(f"{name[:38]:38s}{live:>6}{rec:>6}{dem:>8.2f}{cov:>7.0f}{c:>8.0f}{delta*c:>9.0f}")

print(f"\ncapital held BEFORE on these SKUs: ${capital_before:,.0f}")
print(f"capital held AFTER:                ${capital_after:,.0f}")
print(f"ADDED by the raise:                ${capital_after-capital_before:,.0f}")
