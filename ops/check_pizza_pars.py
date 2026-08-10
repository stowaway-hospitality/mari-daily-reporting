import json, re
B = "/Users/Shared/ClaudeShared/par-build"
scr = json.load(open(f"{B}/data/_scrape_stow_20260809.json"))
nonzero = scr["nonzero_pars"]
bo = open(f"{B}/data/bo_exports/stowaway_products.csv", encoding="utf-8-sig").read().splitlines()

PIZZA = re.compile(r"pizza|mozzarella|pepperoni|dough|pizza flour|calabrese|"
                   r"bocconcini|prosciutto|ham socc|salami|base", re.I)

names = []
for line in bo[1:]:
    parts = line.split(",")
    if len(parts) > 2:
        nm = parts[2].strip('"')
        if PIZZA.search(nm):
            names.append(nm)

parred = [n for n in names if n in nonzero]
unparred = [n for n in names if n not in nonzero]
print(f"pizza-input SKUs in catalog: {len(names)}")
print(f"  WITH a live par: {len(parred)}")
for n in parred:
    print(f"     {nonzero[n]:>7}  {n}")
print(f"  with NO live par (ordered entirely outside the par system): {len(unparred)}")
for n in unparred[:18]:
    print(f"       -    {n}")
