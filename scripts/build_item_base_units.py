#!/usr/bin/env python3
"""Decide ONE canonical base unit per stock item, from evidence.

Every movement in the ledger is denominated in g, ml or each. Which one an item
uses is not a preference — it is a fact about the item, and the evidence is how
the recipe book already consumes it: 45 ml of Havana, 200 g of flour, 1 each of
a lime.

WHY IT MATTERS MORE HERE THAN IN COSTING. A wrong unit in a recipe is one wrong
dish. A wrong unit in inventory is wrong on every movement for that item,
forever, and it compounds — the CTN-6-read-as-one-tin class of error (6x), ILG
cases read as bottles (6x), Red Chilli (10x), Angostura (13x).

CONFLICTS ARE NOT RESOLVED, THEY ARE FLAGGED. If recipes consume an item in
both g and ml, that is two different things wearing one id, or a recipe with a
typo. Picking the more common one would bury it. Such items are written with
conflict=true and the ledger refuses to book them.

Run: python3 scripts/build_item_base_units.py
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
OUT = ROOT / "data" / "item_base_units.csv"

# Recipe units -> the base unit they imply. 'bunch' and 'tray' are absent on
# purpose: a bunch of coriander has no provable gram weight, and inventing one
# is wrong on every movement.
IMPLIES = {"g": "g", "kg": "g", "ml": "ml", "l": "ml", "ea": "each", "each": "each"}


def main() -> int:
    book = json.loads(BOOK.read_text())
    seen: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, str] = {}

    for recipe in book["recipes"].values():
        for ing in recipe.get("ingredients", []):
            if ing.get("kind") != "id":
                continue                        # subrecipe, resolved elsewhere
            ref = (ing.get("ref") or "").strip()
            if not ref or ":" not in ref:
                continue
            unit = (ing.get("unit") or "").strip().lower()
            seen[ref][unit] += 1
            names.setdefault(ref, ing.get("name") or "")

    rows, conflicts, unusable = [], 0, 0
    for item, units in sorted(seen.items()):
        bases = {IMPLIES[u] for u in units if u in IMPLIES}
        undeclared = sorted(u for u in units if u not in IMPLIES)

        if len(bases) == 1 and not undeclared:
            base, conflict, why = next(iter(bases)), "false", ""
        elif len(bases) == 1 and undeclared:
            # e.g. mostly grams, one 'bunch'. The base is known; the odd unit is
            # not convertible, so record both rather than silently dropping it.
            base, conflict = next(iter(bases)), "false"
            why = f"also seen in {', '.join(undeclared)} (no declared conversion)"
        elif len(bases) > 1:
            base, conflict = "", "true"
            why = f"consumed in {', '.join(sorted(bases))} — two things under one id, or a recipe typo"
            conflicts += 1
        else:
            base, conflict = "", "true"
            why = f"only ever consumed in {', '.join(undeclared)}, which has no declared conversion"
            unusable += 1

        rows.append({
            "item_id": item,
            "item_name": names.get(item, ""),
            "base_unit": base,
            "conflict": conflict,
            "recipe_units_seen": "|".join(f"{u}x{n}" for u, n in units.most_common()),
            "note": why,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    usable = sum(1 for r in rows if r["conflict"] == "false")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} item(s)")
    print(f"  usable base unit: {usable}")
    print(f"  conflicting units (REFUSED): {conflicts}")
    print(f"  no convertible unit (REFUSED): {unusable}")
    for r in rows:
        if r["conflict"] == "true":
            print(f"    {r['item_id']:26} {r['item_name'][:34]:36} {r['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
