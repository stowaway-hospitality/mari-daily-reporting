"""Upload list: RAISES ONLY (+ the explicit HG zeros Zak asked for).

Zak: "make sure we won't run out of everything. once that's done, please upload
the pars." Raising a par cannot cause a stockout; cutting one can. So the model's
46 Stowaway / 10 HG decreases are deliberately NOT uploaded — they stay in the
workbook for review. The only reductions here are the four [HG] SKUs Zak
explicitly zeroed (shared Stowaway stock) and Kaiju (discontinued).

CREATING a par where none exists is a bigger act than raising one — it starts
ordering something nobody was ordering. So new pars are gated: a real stock
item, meaningful demand, and never a POS pour-line (a "[60ml]" is a serve, not
a thing you hold) or a shared-stock line that belongs to the other venue.
"""
import json, re

B = "/Users/Shared/ClaudeShared/par-build"

EXPLICIT_ZEROS = {
    "Hyoketsu Lemon [HG]", "Trutta Streamside Shiraz - Bottle [HG]",
    "Two Tonne Riesling - Bottle [HG]", "Kaiju Hazy Pale [HG]",
}
# a serve size, not a stock unit
POUR_LINE = re.compile(r"\[(30|45|60|90|120|150|250)ml\]|\bschooner\b|\bpint\b|"
                       r"- regular$|- large$|- glass$", re.I)
NEW_PAR_MIN_DEMAND = 0.25          # /wk — below this, a new par is noise

for venue_file, label in (("stowaway", "stow"), ("harry_gatos", "hg")):
    d = json.load(open(f"{B}/data/par_recommendations_{venue_file}.json"))
    up, new_par, zeros, skipped = [], [], [], []
    for r in d["skus"]:
        name, rec, live = r["product"], r["rec_par"], r.get("current_par")
        dr = r["drivers"]
        demand = dr["pour_wk"] + dr["recipe_wk"] + dr["variance_wk"]

        if name in EXPLICIT_ZEROS:
            if live:
                zeros.append([name, 0.0])
            continue
        if rec <= 0:
            continue

        if live is None:
            if POUR_LINE.search(name):
                skipped.append((name, rec, demand, "POS pour-line, not a stock item"))
                continue
            if label == "hg" and "[HG]" not in name:
                skipped.append((name, rec, demand,
                                "shared stock — the par belongs at Stowaway"))
                continue
            if demand < NEW_PAR_MIN_DEMAND:
                skipped.append((name, rec, demand, "demand below new-par threshold"))
                continue
            new_par.append([name, rec])
        elif rec > live + 1e-9:
            up.append([name, rec])

    payload = up + new_par + zeros
    with open(f"{B}/data/_upload_{label}.json", "w") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print(f"\n{label}: {len(up)} raises + {len(new_par)} new pars + {len(zeros)} zeros "
          f"= {len(payload)} changes   ({len(skipped)} new-par candidates skipped)")
    if new_par:
        print("   new pars being created:")
        for n, v in new_par:
            print(f"      {n[:48]:48s} -> {v}")
    if skipped:
        print("   skipped (reported, not uploaded):")
        for n, v, dem, why in sorted(skipped, key=lambda s: -s[2])[:10]:
            print(f"      {n[:40]:40s} rec {v:<5} dem {dem:.2f}/wk  — {why}")
