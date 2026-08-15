#!/usr/bin/env python3
"""data/stock_catalogue.json — what the phone shows somebody counting.

One file, fetched once, small enough to work on a bad connection in a cool room.
For each item: what to call it, what unit to count it in, and whether that unit
can actually be converted.

THE UNCONVERTIBLE ITEMS ARE STILL LISTED, deliberately, and flagged. Hiding them
would mean the coriander silently never gets counted and its variance is blank
forever, which looks like "no problem here". Better to show it, take the count,
and hold it until somebody says how much a bunch weighs.

ORDER IS BY WHAT IT COSTS, not alphabetically. A stocktake that runs out of time
should have already counted the spirits.

Run: python3 scripts/build_stock_catalogue.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger import load_base_units, locations_ever_counted     # noqa: E402

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
CONTAINERS = ROOT / "data" / "container_sizes.csv"
COSTS = ROOT / "data" / "costs.csv"
OUT = ROOT / "data" / "stock_catalogue.json"

# Where things are counted. From the Lightspeed stock-count tabs the stocktake
# skill already walks, plus the HG line.
LOCATIONS = ["Bar & Kegroom", "Storeroom - Bar", "Pizza Shop", "HG Line", "Cool Room"]

# What a person says when counting this kind of thing.
UNIT_WORD = {"ml": "bottle", "g": "pack", "each": "each"}


def latest_costs() -> dict[str, tuple[Decimal, str]]:
    out: dict[str, tuple[Decimal, str]] = {}
    if not COSTS.exists():
        return out
    with COSTS.open() as f:
        for r in csv.DictReader(f):
            try:
                out[r["ingredient"]] = (Decimal(r["cost_per_unit"]), r["unit"])
            except Exception:
                continue
    return out


def item_names() -> dict[str, str]:
    """Names for EVERY item, not just the convertible ones — otherwise the
    phone shows a bare id for exactly the items that most need a human to
    recognise them."""
    book = json.loads((ROOT / "data" / "lightspeed_recipes_costed.json").read_text())
    out: dict[str, str] = {}
    for r in book["recipes"].values():
        for i in r.get("ingredients", []):
            if i.get("kind") == "id" and i.get("ref"):
                out.setdefault(i["ref"], i.get("name") or "")
    return out


def main() -> int:
    base_units = load_base_units()
    names_by_id = item_names()
    costs = latest_costs()
    seen_locs = locations_ever_counted()

    containers: dict[str, dict] = {}
    if CONTAINERS.exists():
        with CONTAINERS.open() as f:
            for r in csv.DictReader(f):
                containers[r["item_id"]] = r

    items = []
    for item, base in sorted(base_units.items()):
        c = containers.get(item)
        name = (c or {}).get("item_name") or names_by_id.get(item) or item
        cost, cunit = costs.get(item, (None, None))

        # Rough value of one container — only to sort the list, never a number
        # anybody sees as money.
        rank = Decimal(0)
        if cost is not None and c:
            try:
                rank = cost * Decimal(c["base_qty"])
            except Exception:
                rank = Decimal(0)

        items.append({
            "item_id": item,
            "name": name,
            "base_unit": base,
            "count_in": (c or {}).get("container") or UNIT_WORD.get(base, "each"),
            "per_container": str(c["base_qty"]) if c else None,
            "convertible": bool(c),
            "size_source": (c or {}).get("source"),
            "locations": sorted(seen_locs.get(item, set())) or None,
            "_rank": float(rank),
        })

    items.sort(key=lambda i: (-i["_rank"], i["name"]))
    for i in items:
        i.pop("_rank")

    doc = {
        "generated_at": datetime.now(timezone(timedelta(hours=10)))
                        .replace(microsecond=0).isoformat(),
        "locations": LOCATIONS,
        "item_count": len(items),
        "convertible": sum(1 for i in items if i["convertible"]),
        "items": items,
    }
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(items)} items, "
          f"{doc['convertible']} convertible, "
          f"{len(items) - doc['convertible']} listed but needing a size")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
