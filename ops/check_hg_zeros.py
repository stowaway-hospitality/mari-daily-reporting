import json
B = "/Users/Shared/ClaudeShared/par-build"
d = json.load(open(f"{B}/data/par_recommendations_harry_gatos.json"))
print("HG [HG]-suffixed SKUs:")
for r in d["skus"]:
    if "[HG]" in r["product"]:
        ov = r.get("override") or {}
        print("   {:42s} live {:>5} -> rec {:>5}   override={} protect={}".format(
            r["product"][:42], str(r.get("current_par")), r["rec_par"],
            ov.get("type"), ov.get("protect")))
