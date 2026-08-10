import json, subprocess
B = "/Users/Shared/ClaudeShared/par-build"

before = json.loads(subprocess.run(
    ["git", "-C", B, "show", "HEAD:data/par_recommendations_stowaway.json"],
    capture_output=True, text=True).stdout)
bmap = {r["product"]: r for r in before["skus"]}
after = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))

print("Stowaway SKUs that gained Marilyna's volume")
print("{:34s}{:>7}{:>9}{:>9}{:>9}".format("SKU", "live", "pour b", "pour a", "rec b->a"))
moved = []
for r in after["skus"]:
    b = bmap.get(r["product"])
    if not b:
        continue
    pb, pa = b["drivers"]["pour_wk"], r["drivers"]["pour_wk"]
    if abs(pa - pb) > 1e-6 or b["rec_par"] != r["rec_par"]:
        moved.append((abs(pa - pb), r["product"], r.get("current_par"), pb, pa, b["rec_par"], r["rec_par"]))
moved.sort(reverse=True)
for _, name, live, pb, pa, rb, ra in moved[:18]:
    print("{:34s}{:>7}{:>9.2f}{:>9.2f}   {} -> {}".format(name[:34], str(live), pb, pa, rb, ra))
print(f"\ntotal SKUs moved: {len(moved)}")

u = json.load(open(f"{B}/data/par_unattributed_marilynas.json"))
print("\nmari report keys:", list(u.keys()))
for k in u:
    if isinstance(u[k], list):
        print(f"  {k}: {len(u[k])}")
