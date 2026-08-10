"""How much par-relevant DRINK volume is hidden inside Lightspeed's own recipe
book (deals, combos, anything) that the par model never reads?"""
import csv, json, re, unicodedata
from collections import defaultdict

B = "/Users/Shared/ClaudeShared/par-build"

def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\s-\s*(bottle|regular|large|glass)\s*$", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

# par universe = Stowaway par SKUs
scr = json.load(open(f"{B}/data/_scrape_stow_20260809.json"))["nonzero_pars"]
par_by_norm = {norm(k): k for k in scr}

# weekly sales of every POS line, all venues
rows = list(csv.DictReader(open(f"{B}/data/products_weekly.csv")))
weeks = sorted(set(r["week_ending"] for r in rows))[-13:]
sold = defaultdict(float)
for r in rows:
    if r["week_ending"] in weeks:
        sold[r["product_name"]] += float(r["qty"] or 0)

ls = json.load(open(f"{B}/data/lightspeed_recipes_costed.json"))["recipes"]

# what the par model already models, so we only count what's MISSING
recs = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))
modelled = {r["product"]: r["drivers"]["pour_wk"] + r["drivers"]["recipe_wk"]
            for r in recs["skus"]}

extra = defaultdict(float)
contrib = defaultdict(list)
for prod, r in ls.items():
    qty_wk = sold.get(prod, 0.0) / len(weeks)
    if qty_wk <= 0:
        continue
    for i in (r.get("ingredients") or []):
        if not isinstance(i, dict):
            continue
        desc = str(i.get("desc") or i.get("name") or "")
        tgt = par_by_norm.get(norm(desc))
        if not tgt:
            continue
        try:
            per = float(i.get("qty") or 0)
        except (TypeError, ValueError):
            per = 0.0
        if per <= 0:
            continue
        extra[tgt] += qty_wk * per
        contrib[tgt].append((qty_wk * per, prod, per))

print("Par SKUs with volume hidden in Lightspeed's recipe book")
print("{:30s}{:>10}{:>11}{:>9}".format("par SKU", "modelled", "hidden/wk", "live"))
tot = 0.0
for sku, v in sorted(extra.items(), key=lambda kv: -kv[1]):
    if v < 0.05:
        continue
    tot += v
    print("{:30s}{:>10.2f}{:>11.2f}{:>9}".format(
        sku[:30], modelled.get(sku, 0.0), v, scr.get(sku)))
    for amt, prod, per in sorted(contrib[sku], reverse=True)[:3]:
        print(f"        {amt:6.2f}/wk  <- {prod[:40]} (x{per})")
print(f"\ntotal hidden units/wk across par SKUs: {tot:.1f}")
