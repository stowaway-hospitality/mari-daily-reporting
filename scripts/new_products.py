#!/usr/bin/env python3
"""
List products that have entered the recipe database but a human/Claude has not
reviewed yet — the input to the daily new-product triage.

The deterministic pipeline (parsers -> cogs_list -> ingredients.json) decides a
product's name, cost and pack with NO judgement — it cannot tell that "Brown"
should be "Onion Brown", that a $900/kg olive oil is a decimal slip, or that a
cleaning chemical wandered into the food list. This surfaces exactly the products
that pipeline just admitted, so a reviewer (the daily Claude task) looks only at
what is new rather than re-reading the whole list every day.

    python3 scripts/new_products.py            # print new products as JSON
    python3 scripts/new_products.py --mark      # mark all current products reviewed
    python3 scripts/new_products.py --count     # just how many are new

Reviewed state lives in data/reviewed_products.json (a set of purchasable_ids).
Marking is append-only, so a product is reviewed once and never re-surfaces
unless it changes materially (its cost or pack moves — then it returns for a look).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / "data" / "ingredients.json"
STATE = ROOT / "data" / "reviewed_products.json"

# fields the reviewer needs — enough to judge name / cost / category / pack
_KEEP = ("id", "supplier", "description", "supplier_code", "pack_qty", "pack_unit",
         "cost_per_base_unit", "pack_cost_incl", "needs_pack_review",
         "pack_parsed_as", "source_invoice", "last_seen")


def _load_feed() -> list[dict]:
    if not FEED.exists():
        return []
    d = json.loads(FEED.read_text())
    return d["ingredients"] if isinstance(d, dict) else d


def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"reviewed": {}}


def _fingerprint(i: dict) -> str:
    """A product returns for review only if its NAME or UNIT changed (a re-parse) —
    NOT on price movement (that is the /pricing movers' job). So a quality review
    isn't re-triggered every time a supplier's price ticks."""
    return f"{i.get('description')}|{i.get('pack_unit')}"


def new_products() -> list[dict]:
    reviewed = _load_state().get("reviewed", {})
    out = []
    for i in _load_feed():
        seen = reviewed.get(i["id"])
        if seen is None or seen != _fingerprint(i):
            out.append({k: i.get(k) for k in _KEEP})
    return out


def mark_reviewed() -> int:
    st = _load_state()
    rev = st.setdefault("reviewed", {})
    feed = _load_feed()
    for i in feed:
        rev[i["id"]] = _fingerprint(i)
    st["updated"] = date.today().isoformat()
    STATE.write_text(json.dumps(st, indent=2))
    return len(feed)


def main(argv: list[str]) -> int:
    if "--mark" in argv:
        n = mark_reviewed()
        print(f"marked {n} products reviewed -> {STATE.relative_to(ROOT)}")
        return 0
    new = new_products()
    if "--count" in argv:
        print(len(new))
        return 0
    print(json.dumps({"generated": date.today().isoformat(),
                      "new_count": len(new), "new": new}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
