#!/usr/bin/env python3
"""Populate data/ingredient_map.csv from bridges a human already confirmed.

ARCHITECTURE.md Decision 1 defines the Purchasable -> Ingredient map, and
core/domain.load_ingredient_map() has read it since July — but the file sat
empty (header only) while six other files grew to patch around its absence.
This generator populates it from the ONE source that is already a human-
confirmed identity assertion: data/product_map.csv, the supplier-code ->
Lightspeed-ProductID bridge built and adjudicated invoice-by-invoice in
session. No fuzzy matching, no name similarity, no new judgment.

  purchasable_id  = purchasable_id(supplier, supplier_code)   # slugs "ILG"->"ilg"
  ingredient_id   = lightspeed:<product_id>                   # the anchor
  confirmed_by    = product_map (<confidence>)
  note            = product_name

THE FENCE (same doctrine as the ILG re-parse): merging a supplier series into
a lightspeed series changes which observation is most recent, so it can move
a live rate. A merge may leave a rate alone or RAISE it freely; one that
would LOWER a consumed rate is held out to data/_identity_review/ for Zak —
the flattering direction always needs a human. Rows whose two series never
both exist are effect-free by construction.

Deterministic: same inputs -> byte-identical output (CI-diffable).
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.domain import purchasable_id, canonical_purchasable, load_cost_observations  # noqa: E402

OUT = ROOT / "data" / "ingredient_map.csv"
HELD = ROOT / "data" / "_identity_review" / "held_lowering_merges.csv"


def main() -> int:
    # 1. candidate rows from the confirmed bridge
    cand: dict[str, dict] = {}          # purchasable -> row
    conflicts: dict[str, set] = defaultdict(set)
    with (ROOT / "data" / "product_map.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sup, code, pid = r["supplier"], r["supplier_code"], r["product_id"]
            if not (sup and code and pid):
                continue
            p = canonical_purchasable(purchasable_id(sup, code))
            ing = f"lightspeed:{pid.strip()}"
            if p in cand and cand[p]["ingredient_id"] != ing:
                conflicts[p].add(cand[p]["ingredient_id"]); conflicts[p].add(ing)
                continue
            cand[p] = {"purchasable_id": p, "ingredient_id": ing,
                       "confirmed_by": f"product_map ({r.get('confidence','')})".strip(),
                       "note": (r.get("product_name") or "").strip()}
    for p in conflicts:
        cand.pop(p, None)

    # Zak-confirmed conflict resolutions (batched identity review 2026-08-16):
    # one product = one ingredient, anchored on the live STOCK ITEM record —
    # never a [House] POS pour product, which draws FROM the stock item.
    # These rows are explicit human confirmations, so they pass the PRICE fence
    # by authority — but never silently (movements print below), and they do
    # NOT pass the UNIT guard: identity is Zak's call, unit physics is not.
    res_path = HELD.parent / "conflict_resolutions.csv"
    resolutions: dict[str, dict] = {}
    if res_path.exists():
        with res_path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                resolutions[r["purchasable_id"]] = r
        for r in resolutions.values():
            seen = set()
            while r["ingredient_id"] in resolutions and r["ingredient_id"] not in seen:
                seen.add(r["ingredient_id"])
                r["ingredient_id"] = resolutions[r["ingredient_id"]]["ingredient_id"]
        for pu, r in resolutions.items():
            conflicts.pop(pu, None)

    # 2. the fence: measure the live-rate effect of each merge
    obs = load_cost_observations(purchasable_to_ingredient={})   # UNMERGED view
    latest: dict[tuple, object] = {}
    for o in obs:
        k = (canonical_purchasable(o.ingredient), o.venue)
        if k not in latest or o.observed_on >= latest[k].observed_on:
            latest[k] = o

    def live(ing: str, venue) -> object | None:
        return latest.get((ing, venue))

    ship, held = [], []
    venues = sorted({v for (_, v) in latest.keys() if v})
    for pu, r in sorted(resolutions.items()):
        row = {"purchasable_id": pu, "ingredient_id": r["ingredient_id"],
               "confirmed_by": r["confirmed_by"], "note": r["note"]}
        a = latest.get((pu, None)) or next((latest[k] for k in latest if k[0] == pu), None)
        b = latest.get((r["ingredient_id"], None)) or next(
            (latest[k] for k in latest if k[0] == r["ingredient_id"]), None)
        # Zak's confirmation settles IDENTITY, not units. Merging a bottle-unit
        # series into a per-ml series builds the mixed-unit series this whole
        # book fights (defect class 4.1) — the Aperol test caught exactly that.
        # Unit-clashing resolutions hold (identity stays recorded, auto-applies
        # once the declared-conversion layer lands); same-unit ones ship loud.
        if a and b and a.unit != b.unit:
            held.append((row, f"unit clash {a.unit} vs {b.unit} (zak-confirmed identity, needs conversion)"))
            continue
        ship.append((row, None))
        if a and b and abs(float(a.cost_per_unit) - float(b.cost_per_unit)) > 1e-9:
            print(f"  rate movement (zak-confirmed): {pu} {a.cost_per_unit}/{a.unit}"
                  f" joins {r['ingredient_id']} {b.cost_per_unit}/{b.unit}")
    for p, row in sorted(cand.items()):
        ing = row["ingredient_id"]
        lowering = None
        for v in venues + [None]:
            a, b = live(p, v), live(ing, v)
            if not (a and b):
                continue                     # only one series -> merge is a no-op or pure gain
            if a.unit != b.unit:
                lowering = f"unit clash {a.unit} vs {b.unit}"
                break
            # post-merge winner is the more recent observation; does any
            # consumer's current rate DROP?
            recent, other = (a, b) if a.observed_on >= b.observed_on else (b, a)
            if float(recent.cost_per_unit) < float(other.cost_per_unit) * 0.999:
                lowering = (f"{v or 'any'}: {other.cost_per_unit}/{other.unit} "
                            f"-> {recent.cost_per_unit}/{recent.unit}")
                break
        (held if lowering else ship).append((row, lowering))

    # 3a. flatten the WHOLE map: a bridge row (e.g. ILG's pricebook spelling
    # ilg:405-095-7) can point at a stub PID that a resolution just absorbed.
    # CostSeries translation is one-hop, so every ingredient_id must be final.
    final = {row["purchasable_id"]: row["ingredient_id"] for row, _ in ship}
    for row, _ in ship:
        seen = set()
        while row["ingredient_id"] in final and row["ingredient_id"] not in seen:
            seen.add(row["ingredient_id"])
            row["ingredient_id"] = final[row["ingredient_id"]]

    # 3. write
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["purchasable_id", "ingredient_id",
                                          "confirmed_by", "note"])
        w.writeheader()
        for row, _ in ship:
            w.writerow(row)
    HELD.parent.mkdir(exist_ok=True)
    with HELD.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["purchasable_id", "ingredient_id", "note",
                    "would_lower", "held_on"])
        for row, why in held:
            w.writerow([row["purchasable_id"], row["ingredient_id"],
                        row["note"], why, date.today().isoformat()])

    # conflicted purchasables (one supplier code, two+ PIDs — seed-era stubs,
    # or real [Bottle]/[House] splits) go to review with the evidence attached:
    # deciding "same ingredient?" is a chef/admin call, per the /alias route.
    names = {}
    with (ROOT / "data" / "product_map.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            names[f"lightspeed:{r['product_id'].strip()}"] = (
                (r.get("product_name") or "").strip(), (r.get("venue") or "").strip())
    cpath = HELD.parent / "conflicted_purchasables.csv"
    with cpath.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["purchasable_id", "candidate_ingredient", "product_name", "venue"])
        for pu, ings in sorted(conflicts.items()):
            for ing in sorted(ings):
                nm, vn = names.get(ing, ("?", "?"))
                w.writerow([pu, ing, nm, vn])
    # WORKLIST.md regenerates with the data it summarises — a hand-written
    # copy went stale within a day of existing (said 14 held / 28 conflicts
    # while the CSVs said 10 / 0). Derived doc, derived with the data.
    wl = ["# Identity review — the standing queue for Zak", "",
          f"Regenerated by `scripts/build_ingredient_map.py` — map carries "
          f"{len(ship)} confirmed rows.",
          "Answer in any Cowork chat; a session applies answers and re-runs "
          "the generator.", "",
          f"## 1. Held merges — {len(held)}", "",
          "The fence: a merge may raise a cost freely, never quietly lower "
          "one. Unit clashes need a declared conversion "
          "(`data/declared_conversions.yaml`); zak-confirmed identities "
          "auto-apply once their conversion lands.", "",
          "| purchasable | -> ingredient | note | why held |", "|---|---|---|---|"]
    for row, why in held:
        wl.append(f"| `{row['purchasable_id']}` | `{row['ingredient_id']}` "
                  f"| {row['note']} | {why} |")
    wl += ["", f"## 2. Conflicted purchasables — {len(conflicts)}", ""]
    if conflicts:
        wl += ["| purchasable | candidates |", "|---|---|"]
        for pu, ings in sorted(conflicts.items()):
            wl.append(f"| `{pu}` | {', '.join(sorted(ings))} |")
    else:
        wl.append("None — all resolved via `conflict_resolutions.csv`.")
    wl += ["", "## 3. Pack sizes still unconfirmed", "",
           "Live on the flags tab (`/recipes/#flags`) and the builder's "
           "confirm-pack prompt — that stays the canonical queue, per Zak's "
           "'keep the flags on the module' rule.", ""]
    (HELD.parent / "WORKLIST.md").write_text("\n".join(wl), encoding="utf-8")

    print(f"shipped {len(ship)} rows -> {OUT.name}; held {len(held)} lowering "
          f"merges -> {HELD.name}; {len(conflicts)} conflicted purchasables -> {cpath.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
