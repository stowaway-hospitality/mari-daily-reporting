#!/usr/bin/env python3
"""
Clean the scraped Lightspeed Produce recipe names IN PLACE, keyed to the
AUTHORITATIVE product export (data/bo_exports/*.csv).

WHY THIS EXISTS
---------------
The Produce table renders each row as a [2-letter avatar badge][name], plus me&u
"quick codes" (initials + optional PLU/price) prefixed to some products. The
get_page_text scrape fused those onto the product name, giving keys like:

    NeNegroni                 (avatar "Ne" + Negroni)
    CM400 Conejos Margarita   (code "CM" + real name "400 Conejos Margarita")
    TB818 Tequila Blanco      (code "TB" + real name "818 Tequila Blanco")
    HR$5 House Red            (code "HR" + real name "$5 House Red")
    ZC.Coke Zero Can          (code "ZC." + Coke Zero Can)

This matters beyond display: a recipe keyed "ChChimichurri" no longer matches the
clean ingredient "Chimichurri" used in a parent recipe, so the SUB-RECIPE cost link
silently breaks. Cleaning the keys reconnects those and fixes the costing.

HOW (no guessing)
-----------------
The real name is always a SUFFIX of the scraped key; the stripped prefix is a short
code containing no lowercase letter (an avatar like "Ne" is the sole exception, and
it echoes the next two chars). So we take the longest AUTHORITATIVE export
ProductName that is a suffix of the key, provided the removed prefix is code-shaped.
Every result is therefore a real product name from the export, not a guess — which
is what caught "400 Conejos" (brand, keep 400) and "$5 House Red" (price is in the
name) where an earlier hand map was wrong.

    python3 scripts/clean_recipe_names.py            # preview full before/after
    python3 scripts/clean_recipe_names.py --apply    # rewrite the two data files
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "data" / "lightspeed_recipes.json",
         ROOT / "data" / "lightspeed_recipes_raw.json"]
EXPORTS = [ROOT / "data" / "bo_exports" / "stowaway_products.csv",
           ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"]

_DOUBLE = re.compile(r"^([A-Z][a-z])([A-Z][a-z])")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def tidy(s: str) -> str:
    """Cosmetic clean of a display name: trim, drop stray leading/trailing POS
    dots, collapse repeated spaces. Keeps real punctuation ($ , & - [] .)."""
    s = s.strip()
    s = s.strip(".").strip()          # POS docket dots, not real punctuation
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def load_authoritative() -> set[str]:
    names = set()
    for p in EXPORTS:
        if p.exists():
            for r in csv.DictReader(p.open(encoding="utf-8-sig")):
                n = (r.get("ProductName") or "").strip()
                if n:
                    names.add(n)
    return names


def build_cleaner(authoritative: set[str]):
    # index authoritative names by their normalised form, longest first for suffix match
    by_norm = {}
    for n in authoritative:
        by_norm.setdefault(norm(n), n)
    norms_sorted = sorted(by_norm, key=len, reverse=True)

    def clean(key: str) -> str:
        nk = norm(key)
        # already a real product name (exact, normalised) -> keep the scrape text tidied
        if nk in by_norm:
            return tidy(key)
        # doubled avatar: "NeNegroni" -> "Negroni" (prefix echoes next pair)
        m = _DOUBLE.match(key)
        if m and m.group(1).lower() == m.group(2).lower() and norm(key[2:]) in by_norm:
            return tidy(key[2:])
        # strip a leading me&u CODE (no lowercase letters): keep the LONGEST suffix
        # of the key that is itself a real product name. Return the KEY's own suffix
        # text (clean), not the export string (which carries docket dots).
        for i in range(1, len(key)):
            suffix = key[i:]
            if norm(suffix) in by_norm and not any(c.islower() for c in key[:i]):
                return tidy(suffix)
        return tidy(key)                        # already clean / not a sold product

    return clean


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    authoritative = load_authoritative()
    clean = build_cleaner(authoritative)

    primary = json.loads(FILES[0].read_text())
    mapping = {k: clean(k) for k in primary}
    changed = {k: v for k, v in mapping.items() if k != v}

    # collisions: two source keys -> one clean name (true duplicate products)
    seen, collisions = {}, {}
    for old, new in mapping.items():
        if new in seen and seen[new] != old:
            collisions.setdefault(new, [seen[new]]).append(old)
        seen[new] = old

    # anything still not matching an authoritative name?
    auth_norm = {norm(n) for n in authoritative}
    unresolved = [v for v in mapping.values() if norm(v) not in auth_norm]

    print(f"{len(changed)} names cleaned of {len(primary)} recipes")
    for old in sorted(changed):
        print(f"  {old!r:40} -> {changed[old]!r}")
    print(f"\n{len(collisions)} duplicate products merged:")
    for new, olds in sorted(collisions.items()):
        print(f"  {olds} -> {new!r}")
    print(f"\n{len(unresolved)} names not in the product export "
          f"(cocktails/preps not sold as products — expected):")
    for v in sorted(set(unresolved)):
        print(f"  {v!r}")

    if args.apply:
        for fp in FILES:
            if not fp.exists():
                continue
            data = json.loads(fp.read_text())
            out: dict = {}
            for k, body in data.items():
                nk = clean(k)
                if nk in out:                   # keep the richer ingredient list
                    if len(body.get("ingredients", [])) > len(out[nk].get("ingredients", [])):
                        out[nk] = body
                else:
                    out[nk] = body
            fp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
            print(f"  wrote {fp.relative_to(ROOT)}: {len(data)} -> {len(out)} keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
