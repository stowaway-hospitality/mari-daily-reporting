#!/usr/bin/env python3
"""
What does this change do to the ingredient feed?

    python3 scripts/feed_diff.py [ref]        # ref defaults to HEAD

Rebuilds data/ingredients.json at `ref` and again from the working tree, and
reports every ingredient that APPEARED, VANISHED, or CHANGED PRICE — sorted by
how far the price moved. Exits 1 if anything moved more than --tol (default 1%),
so it can gate a change as well as inform one.

WHY THIS EXISTS
---------------
On 2026-08-15 a name repair — cosmetic, well tested, all 1900 tests green — also
deleted two products from the picker. Fresh Fruit Team sells the same herb as a
single bunch and as a MARKET bunch and calls both "Herb Chives"; the repair made
the names match, and a downstream collapse whose tiebreak prefers the CHEAPER row
dropped the dearer pack. HCMB $15.40 became HCBCH $2.42 (6x) and HCDRMB $7.70
became HCB $2.64 (3x), on two packs Zak had personally confirmed.

Nothing caught it. Every test passed, because the tests asserted the things the
change was ABOUT. It was found by rebuilding the feed at the previous commit and
diffing the two — and that only happened because someone thought to. This makes
it a command instead of a good habit.

The same run also shows the direction of a move, which matters more than the
size: a cost that comes out too HIGH makes a dish look unprofitable and someone
goes and looks at it, while one that comes out too LOW flatters GP and nothing
ever asks. VANISHED and CHEAPER are the lines to read first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEED = "data/ingredients.json"
BUILD = "modules/recipes/pipeline/build_ingredients.py"


def _load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    d = json.loads(path.read_text())
    items = d if isinstance(d, list) else d.get("ingredients", d.get("items", []))
    return {i["id"]: i for i in items if isinstance(i, dict) and i.get("id")}


def _build_at(ref: str, tmp: Path) -> dict[str, dict]:
    """Materialise `ref` into tmp and build the feed with THAT revision's code.

    git archive rather than a clone: it is the tree at that ref, including the
    committed data/cogs_list.csv the feed derives from, without touching the
    working tree or leaving worktree state behind on a repo other jobs share.
    """
    tar = subprocess.run(["git", "archive", ref], cwd=ROOT,
                         capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=tar, check=True)
    r = subprocess.run([sys.executable, BUILD], cwd=tmp, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ! build at {ref} failed:\n{r.stderr[-800:]}", file=sys.stderr)
        return {}
    return _load(tmp / FEED)


def _cost(it: dict):
    try:
        return Decimal(str(it.get("cost_per_base_unit")))
    except (InvalidOperation, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", nargs="?", default="HEAD", help="revision to compare against")
    ap.add_argument("--tol", type=float, default=0.01,
                    help="fractional price move that counts as a change (default 0.01)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        before = _build_at(args.ref, Path(td))
    if not before:
        print(f"nothing to compare: no feed built at {args.ref}")
        return 1

    r = subprocess.run([sys.executable, BUILD], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"! build of the working tree failed:\n{r.stderr[-800:]}", file=sys.stderr)
        return 1
    after = _load(ROOT / FEED)

    gone = sorted(set(before) - set(after))
    new = sorted(set(after) - set(before))
    moved = []
    for k in sorted(set(before) & set(after)):
        b, a = _cost(before[k]), _cost(after[k])
        if b is None or a is None or b == a:
            continue
        if b == 0:
            moved.append((float("inf"), k, b, a))
            continue
        rel = abs(a - b) / b
        if rel >= Decimal(str(args.tol)):
            moved.append((float(a / b), k, b, a))

    print(f"feed at {args.ref}: {len(before)} ingredients   ->   working tree: {len(after)}")

    if gone:
        print(f"\nVANISHED ({len(gone)}) — read these first; a product that leaves the")
        print("picker is usually a merge that discarded the dearer of two packs:")
        for k in gone:
            print(f"   - {k:<34} {str(before[k].get('description'))[:38]:<38} "
                  f"{before[k].get('cost_per_base_unit')}/{before[k].get('pack_unit')}")
    if new:
        print(f"\nAPPEARED ({len(new)}):")
        for k in new:
            print(f"   + {k:<34} {str(after[k].get('description'))[:38]:<38} "
                  f"{after[k].get('cost_per_base_unit')}/{after[k].get('pack_unit')}")
    if moved:
        print(f"\nPRICE MOVED ({len(moved)}) — cheaper first, because a cost that comes")
        print("out too LOW flatters GP and nothing else will ask about it:")
        for ratio, k, b, a in sorted(moved):
            arrow = "CHEAPER" if a < b else "dearer "
            print(f"   {arrow} x{ratio:<7.3f} {k:<34} {b} -> {a}")

    if not (gone or new or moved):
        print("\nno ingredient appeared, vanished or changed price.")
        return 0
    print(f"\n{len(gone)} vanished, {len(new)} appeared, {len(moved)} moved price.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
