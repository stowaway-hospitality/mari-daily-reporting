#!/usr/bin/env python3
"""How big is one of the things a person counts?

Nobody counts in millilitres. A stocktake says "three quarters of a bottle",
"0.8 of a keg", "0.035 of the 20L drum" — and to turn that into ml we have to
know how big one bottle is. This builds that table.

SOURCES, STRONGEST FIRST. Each row records which one it came from, because the
weakest of them is the one most likely to be wrong:

  1. pack_overrides.yaml   a human opened one and wrote down what was in it.
                           Beats everything, including a name that disagrees.
  2. the product name      "Havana 3yr [700ml]", "East Coast Pineapple Juice
                           [2L]". This is Lightspeed's own declaration and it is
                           usually right — but a name is edited by hand and does
                           not have to follow the bottle it describes. Flagged
                           `product_name` so a variance traced back to one of
                           these is a suspect, not a mystery.
  3. base unit is `each`   a pizza box is one pizza box. The conversion is 1 and
                           there is nothing to get wrong.

Anything else REFUSES. An item counted in bottles whose bottle size nobody has
stated cannot be converted, and guessing 700ml because most spirits are 700ml is
exactly the error that compounds on every count forever.

Run: python3 scripts/build_container_sizes.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import load_base_units                              # noqa: E402
from core.pack_overrides import load_pack_overrides             # noqa: E402

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
OUT = ROOT / "data" / "container_sizes.csv"

# "[700ml]", "[2L]", "[1Kg]", "500g". Deliberately anchored to a unit word —
# "Pizza Base Gluten Free 11in" must not read 11 as a size.
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|kg|g)\b", re.I)
TO_BASE = {"ml": (Decimal(1), "ml"), "l": (Decimal(1000), "ml"),
           "g": (Decimal(1), "g"), "kg": (Decimal(1000), "g")}


def item_names() -> dict[str, str]:
    book = json.loads(BOOK.read_text())["recipes"]
    out: dict[str, str] = {}
    for r in book.values():
        for i in r.get("ingredients", []):
            if i.get("kind") == "id" and (i.get("ref") or "").startswith("lightspeed:"):
                out.setdefault(i["ref"], i.get("name") or "")
    return out


def main() -> int:
    names = item_names()
    base_units = load_base_units()
    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")

    rows, refused = [], []
    for item, name in sorted(names.items()):
        base = base_units.get(item)

        ov = overrides.get(item)
        if ov:
            qty, unit = ov
            unit = {"ea": "each"}.get(unit, unit)
            rows.append({"item_id": item, "item_name": name, "container": "pack",
                         "base_qty": qty, "base_unit": unit,
                         "source": "pack_override", "evidence": "human-confirmed"})
            continue

        if base == "each":
            rows.append({"item_id": item, "item_name": name, "container": "each",
                         "base_qty": 1, "base_unit": "each",
                         "source": "unit_is_each", "evidence": "one is one"})
            continue

        m = SIZE.search(name)
        if m and base:
            factor, implied = TO_BASE[m.group(2).lower()]
            if implied == base:
                rows.append({"item_id": item, "item_name": name,
                             "container": "container",
                             "base_qty": Decimal(m.group(1)) * factor,
                             "base_unit": base, "source": "product_name",
                             "evidence": f"name states {m.group(1)}{m.group(2).lower()}"})
                continue
            refused.append((item, name, f"name says {m.group(2).lower()} but recipes use {base}"))
            continue

        refused.append((item, name, "no stated size" if base else "no base unit"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item_id", "item_name", "container",
                                          "base_qty", "base_unit", "source",
                                          "evidence"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} of {len(names)} items convertible")
    for s, n in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"    {s:16} {n:4}")
    print(f"\n{len(refused)} item(s) REFUSED — a count in containers cannot be converted:")
    for item, name, why in refused[:12]:
        print(f"    {item:26} {name[:36]:38} {why}")
    if len(refused) > 12:
        print(f"    ... and {len(refused) - 12} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
