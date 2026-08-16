#!/usr/bin/env python3
"""
Phase 2a shadow run — cost every product BOTH ways and diff them, daily.

    OLD   data/lightspeed_recipes_costed.json -> r["our_cost"]
          what cogs_blend._load_book_costs publishes to the P&L today

    NEW   data/recipes/_staged/<venue>.yaml   -> modules.recipes.cost.cost_on
          the materialised book, costed by the one engine

The scrape stays attached and reversible until this has been boringly zero for a
week (COST_BOOK_ARCHITECTURE_PLAN.md, T2). A one-shot equivalence check answers
"were they equal at 4pm on a Tuesday"; a daily job answers "do they stay equal as
invoices land", which is the question that matters, because the two engines read
prices differently -- the costed book is a snapshot with no effective date, and
cost_on is as-of. They will drift the first time a price moves, and that drift is
a finding, not a failure.

ZERO-DIFF IS A TEST, NOT A HOPE. Stop on any diff you cannot attribute.

    python3 scripts/shadow_diff_recipes.py --venue marilynas
    python3 scripts/shadow_diff_recipes.py --venue marilynas --fail-over 0.01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.domain import CostSeries, load_cost_observations          # noqa: E402
from modules.recipes.cost import (CircularRecipe, MissingCost,      # noqa: E402
                                  cost_on, load_recipes, recipe_as_of)
from modules.recipes.pipeline.build_recipe_feeds import venue_of    # noqa: E402

DATA = ROOT / "data"
COSTED = DATA / "lightspeed_recipes_costed.json"
SHADOW = DATA / "_shadow"


def old_costs(venue: str) -> dict:
    """What the P&L publishes today. Deliberately reproduces _load_book_costs's
    two refusals -- a batch is not a sold product, and $0.00 is the absence of a
    price, not a price -- so the diff compares like with like instead of counting
    exclusions as differences."""
    book = json.loads(COSTED.read_text(encoding="utf-8-sig"))["recipes"]
    out = {}
    for name, r in book.items():
        if venue_of(name) != venue or r.get("is_prep"):
            continue
        try:
            c = float(r.get("our_cost"))
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[name] = c
    return out


def new_costs(venue: str, path: Path, on: date) -> tuple[dict, list]:
    recipes = load_recipes(venue, path=path)
    costs = CostSeries(load_cost_observations())
    out, refused = {}, []
    for product in sorted({r.product for r in recipes}):
        r = recipe_as_of(recipes, product, on)
        if r is None or r.yield_qty:
            continue                     # a batch is not a serve cost
        try:
            out[product] = float(cost_on(r, costs, on, recipes=recipes))
        except (MissingCost, CircularRecipe) as e:
            refused.append({"product": product, "error": str(e)})
    return out, refused


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", default="marilynas")
    ap.add_argument("--staged", default=None)
    ap.add_argument("--on", default=None, help="cost as of this date (default today)")
    ap.add_argument("--fail-over", type=float, default=None,
                    help="exit 1 if any product differs by more than this many dollars")
    a = ap.parse_args()

    on = date.fromisoformat(a.on) if a.on else date.today()
    staged = Path(a.staged) if a.staged else DATA / "recipes" / "_staged" / f"{a.venue}.yaml"

    old = old_costs(a.venue)
    new, refused = new_costs(a.venue, staged, on)

    rows = []
    for p in sorted(set(old) | set(new)):
        o, n = old.get(p), new.get(p)
        d = None if (o is None or n is None) else round(n - o, 6)
        if o is None or n is None or abs(d) > 0.000005:
            rows.append({"product": p, "old": o, "new": n, "delta": d,
                         "pct": (round(100 * d / o, 3) if (d is not None and o) else None)})

    both = [r for r in rows if r["old"] is not None and r["new"] is not None]
    report = {
        "generated": date.today().isoformat(),
        "costed_as_of": on.isoformat(),
        "venue": a.venue,
        "products_old": len(old),
        "products_new": len(new),
        "matched": len(set(old) & set(new)),
        "identical": len(set(old) & set(new)) - len(both),
        "differing": len(both),
        "only_old": [r["product"] for r in rows if r["new"] is None],
        "only_new": [r["product"] for r in rows if r["old"] is None],
        "refused": refused,
        "max_abs_delta": max((abs(r["delta"]) for r in both), default=0.0),
        "sum_abs_delta": round(sum(abs(r["delta"]) for r in both), 4),
        "diffs": sorted(both, key=lambda r: -abs(r["delta"]))[:60],
    }
    SHADOW.mkdir(parents=True, exist_ok=True)
    (SHADOW / f"{a.venue}_diff.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"shadow diff {a.venue} as of {on}: "
          f"old={len(old)} new={len(new)} matched={report['matched']} "
          f"identical={report['identical']} differing={report['differing']}")
    if report["only_old"]:
        print(f"  only in the OLD book ({len(report['only_old'])}): {report['only_old'][:8]}")
    if report["only_new"]:
        print(f"  only in the NEW book ({len(report['only_new'])}): {report['only_new'][:8]}")
    if refused:
        print(f"  REFUSED to cost {len(refused)}:")
        for r in refused[:8]:
            print(f"    {r['product']}: {r['error'][:120]}")
    if both:
        print(f"  max |delta| ${report['max_abs_delta']:.4f}  "
              f"sum |delta| ${report['sum_abs_delta']:.2f}")
        for r in report["diffs"][:15]:
            print(f"    {r['product'][:44]:44s} {r['old']:9.4f} -> {r['new']:9.4f}  "
                  f"{r['delta']:+.4f}")

    if a.fail_over is not None and (report["max_abs_delta"] > a.fail_over
                                    or refused or report["only_old"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
