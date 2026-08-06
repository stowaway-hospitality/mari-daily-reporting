#!/usr/bin/env python3
"""
Import a per-venue Lightspeed Produce scrape into data/lightspeed_recipes.json.

    python3 scripts/merge_venue_scrape.py hg_recipes.json --venue harry_gatos
    python3 scripts/merge_venue_scrape.py hg_recipes.json --venue harry_gatos --apply

WHY THIS EXISTS
---------------
Produce has no venue switcher: you switch venue in Back Office and click Launch
from there. The original scrape only ever ran in the Stowaway context, so for
months the pipeline believed Harry Gatos had no recipes at all — while 215 sat in
Produce untouched, and 95.7% of HG revenue reported no cost.

Re-scraping a venue is therefore a recurring job, and it needs to be repeatable
rather than a one-off paste. This is that step.

THE COLLISION PROBLEM
---------------------
data/lightspeed_recipes.json is keyed by PRODUCT NAME with no venue. 34 names
exist at both Stowaway and Harry Gatos with DIFFERENT recipes. A naive merge
overwrites: HG's Classic Margarita (2 lines) would replace Stowaway's (6 lines,
with lime, sugar, salt and garnish). That UNDER-costs, which flatters GP — the
direction this repo treats as dangerous.

So the rule is: NEVER overwrite an existing recipe. A colliding name is either
  * declared in data/recipe_venue_mirrors.yaml -> intentional, the source venue's
    recipe stands, and we say so; or
  * undeclared -> reported as a genuine per-venue difference that cannot be
    represented until recipes are venue-keyed. It is left alone, loudly.

Dry-run by default. Nothing is written without --apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "data" / "lightspeed_recipes.json"
MIRRORS = ROOT / "data" / "recipe_venue_mirrors.yaml"


def load_mirrors(venue: str) -> dict:
    """dish -> source venue, for this venue. {} if the file is absent."""
    if not MIRRORS.exists():
        return {}
    doc = yaml.safe_load(MIRRORS.read_text()) or {}
    return dict(doc.get(venue) or {})


def plan(scrape: dict, current: dict, mirrors: dict) -> tuple[list, list, list]:
    """-> (new, mirrored, undeclared_conflicts). Pure; the tests drive this."""
    new, mirrored, conflict = [], [], []
    for name in sorted(scrape):
        if name not in current:
            new.append(name)
        elif name in mirrors:
            mirrored.append(name)
        else:
            conflict.append(name)
    return new, mirrored, conflict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scrape", help="JSON from the Produce scrape: {name: {ingredients:[...]}}")
    ap.add_argument("--venue", required=True, help="venue key, e.g. harry_gatos")
    ap.add_argument("--apply", action="store_true", help="write the merge (default: dry run)")
    a = ap.parse_args()

    scrape = json.loads(Path(a.scrape).read_text())
    current = json.loads(RECIPES.read_text())
    mirrors = load_mirrors(a.venue)

    new, mirrored, conflict = plan(scrape, current, mirrors)

    print(f"scrape {len(scrape)} recipes for {a.venue}; book currently {len(current)}")
    print(f"  NEW, will import                     : {len(new)}")
    print(f"  mirrored (declared, source stands)   : {len(mirrored)}")
    for n in mirrored:
        print(f"      {n}  <- {mirrors[n]}")
    print(f"  UNDECLARED collisions (left alone)   : {len(conflict)}")
    for n in conflict:
        h, c = len(scrape[n].get("ingredients") or []), len(current[n].get("ingredients") or [])
        note = "same line count, check products" if h == c else f"{a.venue} {h} lines vs existing {c}"
        print(f"      {n}  ({note})")
    if conflict:
        print("\n  ^ these are real per-venue differences. They cannot be represented while")
        print("    recipes are keyed by name alone. Declare a mirror if the venue should use")
        print("    the existing recipe; otherwise they need a venue-keyed recipe store.")

    if not a.apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        return 0

    for n in new:
        current[n] = scrape[n]
    RECIPES.write_text(json.dumps(current, indent=1, ensure_ascii=False))
    print(f"\nwrote {RECIPES.relative_to(ROOT)}: +{len(new)} -> {len(current)} recipes")
    print("now rebuild: build_costs -> build_ingredients -> convert_lightspeed_recipes -> build_recipe_feeds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
