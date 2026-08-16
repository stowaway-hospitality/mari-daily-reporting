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
    print(f"shipped {len(ship)} rows -> {OUT.name}; held {len(held)} lowering "
          f"merges -> {HELD.name}; {len(conflicts)} conflicted purchasables -> {cpath.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
