#!/usr/bin/env python3
"""
A batch cannot yield more than it contains.

This is the proof standard data/recipe_line_unit_fixes.yaml already demands of
itself -- "the proof must be arithmetic, not judgement" -- applied to every prep
in the book. It is what turned two suspicions into facts on 2026-08-16:

    Jalapeno Tequila [1L]      7,000 ml tequila + 950 g jalapenos  ->  yield 7,500 ml
    Cooked Beef Brisket [1Kg]  10,000 g brisket + aromatics        ->  yield 6,000 g

Seven litres of tequila cannot leave the jar as one. The "[1L]" and "[1Kg]" in
those names are pack labels, and reading them as yields cost brisket 6x and
jalapeno tequila 7.5x. prep_yields.yaml had the right numbers all along, and
resolve_yield now prefers them (see build_recipe_feeds).

PACK COUNTS. The scrape records "2 ml" of a [4L] sauce meaning TWO 4-LITRE PACKS.
Summing the labelled quantity would say a batch contains 3 ml and yields 11 L, a
3,666x nonsense that buries the real findings. So where a line carries both a
per-unit rate and a line cost, the quantity is recovered as cost / rate -- what
the converter actually charged for -- and the label is ignored.

TWO LEGITIMATE ESCAPES, and each one must be visible in the recipe or its basis:
  * uncosted water -- pizza dough's 62% hydration, super juice's dilution, a
    broth. The yield exceeds the costed contents because water went in free.
  * cook loss -- the yield is LESS than the contents. Normal, and the reason the
    low end of this report is a question rather than an error.

    python3 scripts/audit_batch_yields.py            # human-readable
    python3 scripts/audit_batch_yields.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_recipe_feeds import (  # noqa: E402
    _prep_yield_estimates, resolve_yield,
)

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"

# A basis or name that explains a yield exceeding its costed contents.
_DILUTED = re.compile(r"water|hydrat|dilut|super ?juice|syrup|brine|broth|stock|"
                      r"soda|tea|infus", re.I)

# Above this, "the batch makes more than went into it" is not roundable away.
_OVER = 1.05
# Below this, the cook loss is large enough to want a weighing behind it.
_UNDER = 0.45


def contents(recipe: dict) -> float:
    """Base units in the batch, with pack-count lines recovered from their cost.

    A line saying "2 ml" of a [4L] pack is two packs; its labelled quantity is
    meaningless but `eff_cost / our_cost` gives the quantity the converter
    actually priced, which is the real one.
    """
    total = 0.0
    for ln in (recipe.get("ingredients") or []):
        unit = (ln.get("unit") or "").lower()
        try:
            qty = float(ln.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        try:
            rate, eff = float(ln.get("our_cost")), float(ln.get("eff_cost"))
            if rate > 0:
                implied = eff / rate
                # Believe the recovered quantity only when it disagrees with the
                # label by more than rounding -- otherwise the label was fine.
                if qty <= 0 or abs(implied - qty) / max(qty, 1e-9) > 0.01:
                    total += implied
                    continue
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        if unit in ("g", "ml"):
            total += qty
        elif unit in ("kg", "l", "lt", "litre"):
            total += qty * 1000
    return total


def audit() -> dict:
    book = json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    est = _prep_yield_estimates()
    over, under, unknown = [], [], []

    for name, r in book.items():
        if not r.get("is_prep"):
            continue
        yq, yu = resolve_yield(name)
        if yq is None:
            unknown.append(name)
            continue
        if yu == "each":
            continue                      # a count is not comparable to a mass
        c = contents(r)
        if c <= 0:
            continue
        ratio = float(yq) / c
        basis = (est.get(name, {}) or {}).get("basis") or ""
        row = {"prep": name, "yield_qty": float(yq), "yield_unit": yu,
               "contents": round(c, 1), "ratio": round(ratio, 3),
               "basis": basis[:200]}
        if ratio > _OVER and not (_DILUTED.search(basis) or _DILUTED.search(name)):
            over.append(row)
        elif ratio < _UNDER:
            under.append(row)

    over.sort(key=lambda r: -r["ratio"])
    under.sort(key=lambda r: r["ratio"])
    return {"makes_more_than_it_contains": over,
            "large_cook_loss": under,
            "no_yield_at_all": sorted(unknown)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = audit()
    if a.json:
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0

    print("=== a batch that makes MORE than went into it ===")
    print("    (no water or dilution in its basis to explain it)")
    for r in out["makes_more_than_it_contains"]:
        print(f"  {r['ratio']:7.2f}x  {r['prep'][:40]:42s} "
              f"yield {r['yield_qty']:>10,.0f} {r['yield_unit']:3s} "
              f"vs contents {r['contents']:>10,.0f}")
    print(f"  -> {len(out['makes_more_than_it_contains'])} to explain\n")

    print("=== a cook loss over 55% — wants a weighing behind it ===")
    for r in out["large_cook_loss"]:
        print(f"  {r['ratio']:7.2f}x  {r['prep'][:40]:42s} "
              f"yield {r['yield_qty']:>10,.0f} {r['yield_unit']:3s} "
              f"vs contents {r['contents']:>10,.0f}")
    print(f"  -> {len(out['large_cook_loss'])} to check\n")

    print(f"=== {len(out['no_yield_at_all'])} preps with no yield at all ===")
    print(f"    {', '.join(out['no_yield_at_all'][:12])}")
    print("    (each one is a dish that cannot be costed by the book)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
