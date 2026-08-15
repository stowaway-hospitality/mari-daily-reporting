"""Zero Harry Gatos' pars for [HG]-suffixed SKUs that draw on Stowaway stock.

Zak, 2026-08-10: "hg shouldn't have it's own pars for these items. we will
discontinue kaiju as well". Re-run of the original edit after /tmp was cleared
by a reboot; provenance is Zak's instruction in that session, not reconstructed
guesswork.
"""
import json

P = "/Users/Shared/ClaudeShared/par-build/data/par_overrides.json"
d = json.load(open(P))

TODAY = "2026-08-10"
BY = "Zak 2026-08-10"

ZEROS = {
    "Hyoketsu Lemon [HG]":
        "Draws from Stowaway stock (Hyoketsu Lemon Can). HG holds no par for "
        "shared-stock [HG] items - consumption is attributed to the Stowaway par.",
    "Trutta Streamside Shiraz - Bottle [HG]":
        "Draws from Stowaway stock (Trutta Streamside Shiraz [Chilled] - Bottle). "
        "HG holds no par for shared-stock [HG] items.",
    "Two Tonne Riesling - Bottle [HG]":
        "Draws from Stowaway stock (Two Tonne Riesling - Bottle). HG holds no par "
        "for shared-stock [HG] items.",
    "Kaiju Hazy Pale [HG]":
        "DISCONTINUED (Zak, 2026-08-10). No Stowaway equivalent exists and none is "
        "to be created - the product is being wound down entirely.",
}

existing = {(o["venue"], o["product"]) for o in d["overrides"]}
added = 0
for product, reason in ZEROS.items():
    key = ("hg", product)
    if key in existing:
        for o in d["overrides"]:
            if (o["venue"], o["product"]) == key:
                o.update(type="zero", value=None, reason=reason, set_by=BY,
                         set_on=TODAY, status="active", protect="hard")
        continue
    d["overrides"].append({
        "venue": "hg", "product": product, "api_name": None,
        "type": "zero", "value": None, "reason": reason,
        "set_by": BY, "set_on": TODAY, "status": "active",
        "source": "shared-stock policy / discontinued", "protect": "hard",
    })
    added += 1

d["hg_shared_stock_policy"] = (
    "Harry Gatos holds NO par for any [HG]-suffixed SKU: those are HG menu lines "
    "drawing on Stowaway stock, so the par lives at Stowaway and HG's is zeroed "
    "(Zak, 2026-08-10). If a new [HG] line has no Stowaway par SKU, the build "
    "flags it - create the Stowaway SKU rather than reinstating an HG par."
)

json.dump(d, open(P, "w"), ensure_ascii=False, indent=2)
print("added", added, "zero overrides; total overrides:", len(d["overrides"]))
