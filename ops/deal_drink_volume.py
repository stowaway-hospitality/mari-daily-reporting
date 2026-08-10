"""Whole-unit drinks bundled inside deals: the volume that never rings separately."""
import csv, json, re
from collections import defaultdict

B = "/Users/Shared/ClaudeShared/par-build"
rows = list(csv.DictReader(open(f"{B}/data/products_weekly.csv")))
weeks = sorted(set(r["week_ending"] for r in rows))[-13:]
sold = defaultdict(float)
for r in rows:
    if r["week_ending"] in weeks:
        sold[r["product_name"]] += float(r["qty"] or 0)

ls = json.load(open(f"{B}/data/lightspeed_recipes_costed.json"))["recipes"]
scr = json.load(open(f"{B}/data/_scrape_stow_20260809.json"))["nonzero_pars"]
recs = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))
modelled = {r["product"]: round(r["drivers"]["pour_wk"] + r["drivers"]["recipe_wk"], 2)
            for r in recs["skus"]}

DRINK = re.compile(r"coke|sprite|solo|sunkist|cola|lemonade|1\.25", re.I)

extra = defaultdict(float)
detail = defaultdict(list)
for prod, r in ls.items():
    qw = sold.get(prod, 0.0) / len(weeks)
    if qw <= 0:
        continue
    for i in (r.get("ingredients") or []):
        if not isinstance(i, dict):
            continue
        desc = str(i.get("desc") or i.get("name") or "")
        unit = str(i.get("unit") or "").lower()
        if not DRINK.search(desc):
            continue
        if unit not in ("ea", "each", "", "unit"):
            continue          # ml/g handled by the normal recipe path
        try:
            per = float(i.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if per <= 0:
            continue
        extra[desc] += qw * per
        detail[desc].append((qw * per, prod, per))

print("Whole-unit drinks bundled in deals (never ring as their own sale)\n")
print("{:24s}{:>11}  {}".format("deal ingredient", "hidden/wk", "comes from"))
for desc, v in sorted(extra.items(), key=lambda kv: -kv[1]):
    print("{:24s}{:>11.2f}".format(desc[:24], v))
    for amt, prod, per in sorted(detail[desc], reverse=True):
        print(f"        {amt:5.2f}/wk  <- {prod[:44]}  (x{per:g} per deal)")

print("\nAgainst the matching par SKUs:")
for sku in ("Coke 1.25L", "Coke Zero 1.25L", "Sprite 1.25L", "Solo 1.25L", "Sunkist 1.25L"):
    print(f"   {sku:18s} modelled {modelled.get(sku, 0):>6}/wk   live par {scr.get(sku)}")
