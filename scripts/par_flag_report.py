#!/usr/bin/env python3
"""
Par model v2 — override flag / diff report.

Compares current LIVE pars (the _scrape files, via the recommendation JSON's
current_par) against the model's rec_par and the override ledger, and writes:
    data/par_flags_stowaway.json
    data/par_flags_harry_gatos.json

It surfaces:
  * manual RAISES   — current par materially above rec_par and NOT explained by
                      an active hard override (someone padded the par by hand).
  * manual ZEROS    — model wants the SKU stocked (rec>0, real demand) but it has
                      no current par (currently 0 / not parred).
  * hard-protected  — every protect="hard" override and whether rec honoured it.

NEW manual raises not already in the ledger are appended to data/par_overrides.json
as protect="flag" (type "min", value=current, reason "detected manual raise") so
the raise is logged/audited. protect="flag" is advisory only — the model never
enforces it, so logging a raise does not freeze a future modelled reduction.

Run after build_par_model.py.
"""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REC = {"stow": "par_recommendations_stowaway.json", "hg": "par_recommendations_harry_gatos.json"}
FLAGS = {"stow": "par_flags_stowaway.json", "hg": "par_flags_harry_gatos.json"}


def _material(cur, rec):
    gap = cur - rec
    return gap >= max(1.0, 0.15 * cur)


def load_overrides_doc():
    with open(os.path.join(DATA, "par_overrides.json")) as fh:
        return json.load(fh)


def analyse(venue, doc_overrides):
    with open(os.path.join(DATA, REC[venue])) as fh:
        rec_doc = json.load(fh)
    skus = rec_doc["skus"]

    hard = {(o["venue"], o["product"]) for o in doc_overrides["overrides"]
            if o.get("protect") == "hard" and o.get("status", "active") == "active"}
    existing = {(o["venue"], o["product"]) for o in doc_overrides["overrides"]}
    hard_by_product = {o["product"]: o for o in doc_overrides["overrides"]
                       if o["venue"] == venue and o.get("protect") == "hard"
                       and o.get("status", "active") == "active"}

    manual_raises, manual_zeros, hard_honoured = [], [], []

    for r in skus:
        p = r["product"]
        cur, rec = r["current_par"], r["rec_par"]
        flags = r.get("flags", [])
        if cur is not None and cur > rec and _material(cur, rec) and (venue, p) not in hard:
            manual_raises.append({
                "product": p, "current_par": cur, "rec_par": rec,
                "gap": round(cur - rec, 1),
                "in_ledger": (venue, p) in existing,
            })
        if rec >= 0.5 and cur is None and (venue, p) not in hard \
                and "held_no_recent_demand" not in flags \
                and "no_recent_sales" not in flags:
            manual_zeros.append({
                "product": p, "rec_par": rec,
                "recipe_wk": r["drivers"]["recipe_wk"], "pour_wk": r["drivers"]["pour_wk"],
            })

    for p, ov in hard_by_product.items():
        r = next((x for x in skus if x["product"] == p), None)
        rec = r["rec_par"] if r else None
        t, v = ov.get("type"), ov.get("value")
        if rec is None:
            honoured = None
        elif t == "hold":
            honoured = abs(rec - float(v)) < 1e-9
        elif t == "min":
            honoured = rec >= float(v) - 1e-9
        elif t == "max":
            honoured = rec <= float(v) + 1e-9
        elif t == "zero":
            honoured = rec == 0
        else:
            honoured = None
        hard_honoured.append({"product": p, "type": t, "value": v,
                              "rec_par": rec, "honoured": honoured})

    return rec_doc, manual_raises, manual_zeros, hard_honoured


def main():
    doc = load_overrides_doc()
    today = date.today().isoformat()
    new_entries = []
    totals = {}

    for venue in ("stow", "hg"):
        rec_doc, raises, zeros, honoured = analyse(venue, doc)
        payload = {
            "schema_version": 1,
            "generated_at": rec_doc["generated_at"],
            "venue": venue,
            "manual_raises": raises,
            "manual_zeros": zeros,
            "hard_protected": honoured,
        }
        with open(os.path.join(DATA, FLAGS[venue]), "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")

        existing = {(o["venue"], o["product"]) for o in doc["overrides"]}
        logged = 0
        for mr in raises:
            key = (venue, mr["product"])
            if key in existing:
                continue
            doc["overrides"].append({
                "venue": venue, "product": mr["product"], "api_name": None,
                "type": "min", "value": mr["current_par"],
                "reason": "detected manual raise", "set_by": "par_flag_report.py",
                "set_on": today, "status": "active", "source": "par_flag_report",
                "protect": "flag",
            })
            existing.add(key)
            new_entries.append(mr["product"])
            logged += 1

        n_honoured = sum(1 for h in honoured if h["honoured"])
        totals[venue] = (len(raises), logged, len(zeros), len(honoured), n_honoured)
        print(f"\n===== {venue.upper()} flags =====")
        print(f"  manual raises: {len(raises)} (newly logged as protect=flag: {logged})")
        for mr in raises[:10]:
            print(f"    +{mr['gap']:.1f}  current {mr['current_par']} vs rec {mr['rec_par']}   {mr['product']}")
        print(f"  manual zeros (should be parred): {len(zeros)}")
        for mz in zeros[:10]:
            print(f"    rec {mz['rec_par']}  {mz['product']}")
        print(f"  hard-protected overrides: {len(honoured)} ({n_honoured} honoured)")
        not_hon = [h for h in honoured if h["honoured"] is False]
        for h in not_hon:
            print(f"    !! NOT honoured: {h['product']} type={h['type']} value={h['value']} rec={h['rec_par']}")

    if new_entries:
        with open(os.path.join(DATA, "par_overrides.json"), "w") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        print(f"\npar_overrides.json: appended {len(new_entries)} protect=flag entr(y/ies).")
    else:
        print("\npar_overrides.json: no new manual raises to log.")


if __name__ == "__main__":
    main()
