import csv, glob, json, os, re
from datetime import date

B = "/Users/Shared/ClaudeShared/par-build"
CANS = ["Coke Can", "Coke Zero Can", "Sprite Can"]

# ── 1. every stock count, per can ───────────────────────────────────────────
def d(fn):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fn))
    return m.group(1) if m else "?"

files = sorted(glob.glob(f"{B}/data/stock_counts/*.csv"), key=d)
print("Per-count variance (Counted - Expected). Negative = left without a sale.\n")
print(f"{'count date':13}" + "".join(f"{c[:14]:>16}" for c in CANS))
prev = None
for fn in files:
    row = {}
    for r in csv.DictReader(open(fn, encoding="utf-8-sig")):
        n = (r.get("ProductName") or "").strip()
        if n in CANS:
            try:
                row[n] = (float(r.get("Variance") or 0), float(r.get("Qty") or 0))
            except ValueError:
                pass
    if not row:
        continue
    cells = ""
    for c in CANS:
        if c in row:
            v, q = row[c]
            cells += f"{v:>10.1f}/{q:<5.0f}"
        else:
            cells += f"{'-':>16}"
    print(f"{d(fn):13}" + cells)

# ── 2. do any deals bundle a CAN? ───────────────────────────────────────────
ls = json.load(open(f"{B}/data/lightspeed_recipes_costed.json"))["recipes"]
print("\nDeals/recipes bundling a CAN:")
found = 0
for prod, r in ls.items():
    for i in (r.get("ingredients") or []):
        if not isinstance(i, dict):
            continue
        desc = str(i.get("desc") or "")
        if re.search(r"\bcan\b", desc, re.I) and re.search(r"coke|sprite|solo|sunkist|cola", desc, re.I):
            print(f"   {prod[:44]:44s} <- {desc} x{i.get('qty')} ({i.get('unit')})")
            found += 1
print("   (none)" if not found else f"   {found} found")

# ── 3. total recorded can sales, all venues ────────────────────────────────
rows = list(csv.DictReader(open(f"{B}/data/products_weekly.csv")))
weeks = sorted(set(r["week_ending"] for r in rows))[-13:]
print("\nRecorded can sales per week, by venue (last 13wk):")
for c in CANS:
    per = {}
    for r in rows:
        if r["week_ending"] in weeks and r["product_name"] == c:
            per[r["venue"]] = per.get(r["venue"], 0.0) + float(r["qty"] or 0)
    tot = sum(per.values()) / len(weeks)
    detail = ", ".join(f"{v}={q/len(weeks):.1f}" for v, q in sorted(per.items()))
    print(f"   {c:16s} total {tot:5.1f}/wk   ({detail})")
