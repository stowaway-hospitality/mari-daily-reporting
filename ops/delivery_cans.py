import csv, re
from collections import defaultdict
B = "/Users/Shared/ClaudeShared/par-build"
rows = list(csv.DictReader(open(f"{B}/data/products_weekly.csv")))
weeks = sorted(set(r["week_ending"] for r in rows))[-13:]

SOFT = re.compile(r"coke|sprite|solo|sunkist|cola|fanta|lift|pepsi|zero|"
                  r"lemonade|ginger|water|juice|soft", re.I)

agg = defaultdict(lambda: [0.0, 0.0, ""])
for r in rows:
    if r["week_ending"] not in weeks:
        continue
    n = r["product_name"]
    if not SOFT.search(n):
        continue
    a = agg[(r["venue"], n)]
    a[0] += float(r["qty"] or 0)
    a[1] += float(r["sales_ex_gst"] or 0)
    a[2] = r["reporting_group"]

print("EVERY soft-drink-ish POS line, all venues (last 13wk)\n")
print(f"{'venue':6}{'qty/wk':>9}{'$13wk':>8}  {'reporting group':30}  product")
for (v, n), (q, rev, rg) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
    if q <= 0:
        continue
    print(f"{v:6}{q/len(weeks):>9.2f}{rev:>8.0f}  {str(rg)[:30]:30}  {n[:44]}")

print("\n--- lines whose name ends in ' D' (delivery-coded) ---")
for (v, n), (q, rev, rg) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
    if re.search(r"\bD$", n) and q > 0:
        print(f"   {v:5} {q/len(weeks):>7.2f}/wk  {n}")
