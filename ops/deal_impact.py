import json
B = "/Users/Shared/ClaudeShared/par-build"
before = {r["product"]: r for r in json.load(open("/Users/Shared/ClaudeShared/before_stow.json"))["skus"]}
after = json.load(open(f"{B}/data/par_recommendations_stowaway.json"))

SOFT = ["Coke 1.25L", "Coke Zero 1.25L", "Sprite 1.25L", "Solo 1.25L",
        "Sunkist 1.25L", "Coke Can", "Coke Zero Can", "Sprite Can"]
MLGD = ["Rooster Rojo Blanco Tequila [Bottle]", "Bombay Dry [Bottle]",
        "Stone & Wood [Keg]", "Version Two Sparkling - Bottle", "Aperol [Bottle]",
        "Guinness [Keg]", "Campari [Bottle]"]

def row(name, r_before, r_after):
    pb = r_before["drivers"]["pour_wk"] if r_before else 0
    pa = r_after["drivers"]["pour_wk"]
    rb = r_before["rec_par"] if r_before else None
    ra = r_after["rec_par"]
    live = r_after.get("current_par")
    return f"{name[:32]:32s}{str(live):>7}{pb:>9.2f}{pa:>9.2f}   {rb} -> {ra}"

amap = {r["product"]: r for r in after["skus"]}
print("SOFT DRINKS (expect increases)")
print(f"{'SKU':32s}{'live':>7}{'pour b':>9}{'pour a':>9}   rec")
for s in SOFT:
    if s in amap:
        print(" ", row(s, before.get(s), amap[s]))

print("\nml/g SKUs (MUST be unchanged - proves no double-count)")
bad = []
for s in MLGD:
    if s not in amap:
        continue
    pb = before[s]["drivers"]["pour_wk"]; pa = amap[s]["drivers"]["pour_wk"]
    rb = before[s]["rec_par"]; ra = amap[s]["rec_par"]
    ok = abs(pa - pb) < 1e-6 and rb == ra
    if not ok:
        bad.append(s)
    print(f"  {'OK ' if ok else 'MOVED'} {s[:34]:34s} pour {pb:.3f} -> {pa:.3f}   rec {rb} -> {ra}")
print("\nDOUBLE-COUNT CHECK:", "PASS" if not bad else f"FAIL {bad}")
