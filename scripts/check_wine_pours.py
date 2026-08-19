#!/usr/bin/env python3
"""
Does a glass of wine cost what that fraction of its own bottle costs?

    python3 scripts/check_wine_pours.py            # report
    python3 scripts/check_wine_pours.py --strict   # exit 1 if it got worse
    python3 scripts/check_wine_pours.py --rebase   # pin the current count

THE CHECK. Zak, 2026-08-20: a Regular is 150 ml and a Large is 250 ml, out of a
750 ml bottle. So a Regular glass should cost a fifth of the bottle and a Large a
third — and the bottle cost is the one number in this area that is checked,
because it comes off an invoice.

The pour costs are NOT checked by anything, and the two places they live disagree
in both directions:

  * BACK OFFICE is unusable here. Tiziano Grasso Barolo carries $30.80 for the
    Regular Glass AND $30.80 for the Large — both exactly a fifth of the $154
    bottle, so the 250 ml pour is under-costed 1.67x. San Giorgio has the same
    shape. La Petite Mort files BOTH pours at the FULL $22.89 bottle cost, a 5x
    over-statement on a 150 ml glass. Nothing reads that column, which is the
    only reason it has not cost anything.

  * LIGHTSPEED PRODUCE, which the book actually costs off, is mostly RIGHT —
    and that is the finding worth having. Of 84 pours with a costed bottle, the
    great majority reproduce the 150/250 ml split to the cent. So the recipes are
    sound and this is a narrow check, not a rewrite.

WHAT IT CATCHES. The ones that do not reproduce it, which look like copy-paste
between siblings rather than a different pour size:

    Sigurd White Blend - Large     $7.70 against $5.43 expected — and $7.70 is
                                   exactly Sigurd GSM Red Blend's Large. Both
                                   Sigurd pours carry the OTHER Sigurd's cost.
    Year Wines Fiano - Large       $7.12 against $5.52
    Two Tonne Riesling - Large     $7.12 against $5.86
    Unico Zelo Terra Cotta - Large $7.30 against $5.97

A cluster of unrelated wines landing on ~$7.1-7.3 is not four coincidences about
pour size; it is one number that got copied around.

TOLERANCE. 10%, which is wide. A pour cost legitimately differs from a clean
fraction — wastage on the last glass out of a bottle, a by-the-glass wine bought
at a different case price than the bottle list. What it does not do is land on
another wine's number.

Ratcheted rather than failed, for the usual reason: the open ones each want a
person to look at the recipe, and a guard that ships red gets switched off.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from convert_lightspeed_recipes import (_BOTTLE_ML, _POUR_ML,  # noqa: E402
                                        _bo_wine_index)

BASELINE = ROOT / "baselines" / "wine_pours.json"
TOLERANCE = 0.10


def findings() -> list[dict]:
    book_p = ROOT / "data" / "lightspeed_recipes_costed.json"
    if not book_p.exists():
        return []
    book = json.loads(book_p.read_text())["recipes"]
    idx = _bo_wine_index()
    out: list[dict] = []
    for name in sorted(idx["pours"]):
        head, _, size = name.rpartition(" - ")
        frac = _POUR_ML.get(size.strip().lower())
        bottle = idx["bottles"].get(f"{head} - Bottle")
        r = book.get(name)
        if not frac or not bottle or bottle <= 0 or not r:
            continue
        try:
            have = float(r.get("our_cost") or 0)
        except (TypeError, ValueError):
            continue
        want = bottle * (frac / _BOTTLE_ML)
        if have <= 0 or want <= 0 or abs(have - want) <= TOLERANCE * want:
            continue
        out.append({"pour": name, "have": round(have, 4), "want": round(want, 4),
                    "ratio": round(have / want, 3), "bottle": round(bottle, 2),
                    "ml": frac})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--rebase", action="store_true")
    a = ap.parse_args()

    found = findings()
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {"open": len(found)}

    print(f"wine pours disagreeing with their own bottle by >{TOLERANCE:.0%}: "
          f"{len(found)} (baseline {base.get('open')})")
    for f in found:
        print(f"    {f['pour'][:44]:<46}${f['have']:>7.2f} vs ${f['want']:>7.2f} "
              f"({f['ratio']:.2f}x)  {f['ml']:g} ml of a ${f['bottle']:.2f} bottle")

    if a.rebase:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {"open": len(found),
             "pour_ml": {"regular": 150, "large": 250, "bottle": 750},
             "ruled_by": "Zak 2026-08-20",
             "note": "pours whose costed figure does not reproduce the declared "
                     "fraction of their own bottle; may fall, may not rise"},
            indent=1) + "\n")
        print(f"pinned at {len(found)}")
        return 0

    if a.strict and len(found) > base.get("open", len(found)):
        print(f"::error::{len(found) - base['open']} more wine pour(s) stopped "
              f"agreeing with their own bottle. A glass is a measured fraction "
              f"of something we bought — check the recipe before the price.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
