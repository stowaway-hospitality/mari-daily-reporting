import csv, re
from collections import defaultdict
B = "/Users/Shared/ClaudeShared/par-build"
rows = list(csv.DictReader(open(f"{B}/data/products_weekly.csv")))
weeks = sorted(set(r["week_ending"] for r in rows))[-13:]

PAT = re.compile(r"coke|cola|sprite|solo|sunkist|fanta|lift|pepsi|soft ?drink|"
                 r"can\b|1\.25|600ml|deal|combo|meal|bundle|family|pizza \+|"
                 r"lemonade|ginger ale|drink", re.I)

agg = defaultdict(lambda: [0.0, 0.0, "", ""])
for r in rows:
    if r["week_ending"] not in weeks:
        continue
    n = r["product_name"]
    if not PAT.search(n):
        continue
    a = agg[(r["venue"], n)]
    a[0] += float(r["qty"] or 0)
    a[1] += float(r["sales_ex_gst"] or 0)
    a[2] = r["reporting_group"]

items = sorted(agg.items(), key=lambda kv: -kv[1][0])
print(f"{'venue':6}{'qty/wk':>9}{'$13wk':>9}  {'reporting group':26}  product")
for (v, n), (q, rev, rg, _) in items[:45]:
    if q <= 0:
        continue
    print(f"{v:6}{q/len(weeks):>9.1f}{rev:>9.0f}  {str(rg)[:26]:26}  {n[:46]}")

print("\n--- totals by venue for anything matching ---")
tot = defaultdict(float)
for (v, n), (q, rev, rg, _) in agg.items():
    tot[v] += q
for v, q in sorted(tot.items()):
    print(f"  {v}: {q/len(weeks):.1f} units/wk")
