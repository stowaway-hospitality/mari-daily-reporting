import json, re
B = "/Users/Shared/ClaudeShared/par-build"
d = json.load(open(f"{B}/data/lightspeed_recipes_costed.json"))
recipes = d["recipes"]          # {product_name: {ingredients: [...], ...}}

PAT = re.compile(r"deal|combo|feast|banquet|party|wings", re.I)
DRINK = re.compile(r"coke|sprite|solo|sunkist|cola|1\.25|can\b|drink", re.I)

deals = {k: v for k, v in recipes.items() if PAT.search(k)}
print(f"deal/combo recipes: {len(deals)} of {len(recipes)}\n")

withdrink = 0
for name, r in sorted(deals.items()):
    ings = r.get("ingredients") or []
    dn = []
    for i in ings:
        if not isinstance(i, dict):
            continue
        desc = str(i.get("desc") or i.get("name") or i.get("product")
                   or i.get("subrecipe") or "")
        if DRINK.search(desc):
            dn.append(f"{desc}  x{i.get('qty')}")
    if dn:
        withdrink += 1
        print(f"  {name[:52]:52s}")
        for x in dn:
            print(f"        DRINK: {x}")
print(f"\ndeals containing a drink component: {withdrink}")

# also: what does a deal's ingredient list look like at all?
sample = next(iter(deals.items())) if deals else None
if sample:
    print(f"\nsample deal '{sample[0]}':")
    for i in (sample[1].get("ingredients") or [])[:12]:
        print("   ", json.dumps(i)[:150])
