#!/usr/bin/env python3
"""
Two files hold a yield for the same batch and disagree. Which one is the book on?

    python3 scripts/check_yield_conflicts.py            # ranked by money at risk
    python3 scripts/check_yield_conflicts.py --strict   # exit 1 on a NEW conflict
    python3 scripts/check_yield_conflicts.py --rebase   # pin the current count

WHY THIS IS A WORKLIST AND NOT A RULE. data/recipe_yields.yaml is a HARVEST:
Produce's own "Expected yield" field, read by hand on 2026-08-09.
data/prep_yields.yaml is the yield the book actually costs off, and every entry
carries a written basis. They disagree on 12 batches, and the tempting move --
"Zak said Lightspeed is the source of truth, so import it" -- destroys evidence,
because three of those prep_yields entries exist SPECIFICALLY to say why
Produce's number is wrong:

    Cooked Beef Brisket   Produce's 10,500 g is the RAW joint, recorded as if it
                          were the yield. Reading it put $8.53 on every
                          Meatlovers.
    Achiote Chicken       the same fault, with a published 70-75% retention band
                          in the basis.
    Pizza Sauce           the basis says in capitals not to touch it. 9,338 g
                          describes the OLD 10 kg tomato-sauce recipe the scraped
                          copy still holds; 6,028 g describes the Kagome re-spec
                          of 2026-08-15 that lives in the builder book. Pairing
                          one recipe's yield with the other's batch cost invents
                          a $6.17/kg sauce that has never existed -- which was
                          tried once already and reverted.

So neither file outranks the other. Produce is authoritative for what Produce
holds; prep_yields is authoritative where somebody has written down why Produce
is wrong. The honest position is that these are twelve open questions, and the
defect was that nothing said so -- the two numbers sat in two files and no
report put them side by side.

RANKED BY MONEY, not alphabetically, because that is the order they are worth
answering in: the revenue of every product that draws on the batch, however
deep, since a yield error multiplies through every dish that uses it. Pizza
Sauce reaches 146 dishes and $1.06M; the bottom of the list reaches one drink.

A UNIT-CLASS conflict is called out separately. "13 each" against "4,376 g" is
not a disagreement about how much, it is a disagreement about what is being
counted, and no arithmetic reconciles the two -- somebody has to say whether the
batch is trays or grams.

The answer, when there is one, is a WEIGHING: put it in data/measured_yields.yaml
and it outranks both files everywhere, which is now true of the live cost book
as well as the builder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.declarations import PREP_YIELDS, RECIPE_YIELDS  # noqa: E402

BASELINE = ROOT / "baselines" / "yield_conflicts.json"
TOLERANCE = 0.05

_CLASS = {"g": "mass", "kg": "mass",
          "ml": "volume", "l": "volume", "lt": "volume", "litre": "volume",
          "ea": "count", "each": "count", "units": "count", "unit": "count",
          "pcs": "count", "pc": "count"}


def _base(qty, unit) -> tuple[float, str]:
    """(magnitude, base unit) so 1.5 kg and 1500 g stop looking like a conflict."""
    u = str(unit or "").strip().lower()
    try:
        q = float(qty)
    except (TypeError, ValueError):
        return 0.0, u
    if u == "kg":
        return q * 1000, "g"
    if u in ("l", "lt", "litre"):
        return q * 1000, "ml"
    if u in ("ea", "each", "units", "unit", "pcs", "pc"):
        return q, "each"
    return q, u


def _revenue_behind() -> dict:
    """batch -> revenue of every product that draws on it, at any depth.

    A yield error does not stay in the batch: it multiplies through every dish
    drawing on it, so the money at risk is the whole downstream cone.
    """
    book_p = ROOT / "data" / "lightspeed_recipes_costed.json"
    idx_p = ROOT / "dashboard" / "sales" / "products" / "index.json"
    if not book_p.exists() or not idx_p.exists():
        return {}
    book = json.loads(book_p.read_text())["recipes"]
    rev = {}
    for p in json.loads(idx_p.read_text(encoding="utf-8-sig")).get("products") or []:
        rev[(p.get("name") or "").strip().lower()] = float(
            p.get("lifetime_revenue_ex_gst") or 0)

    out: dict = {}
    for product, r in book.items():
        money = rev.get(product.strip().lower(), 0.0)
        if money <= 0:
            continue
        seen, frontier = set(), {product}
        while frontier:
            nxt = set()
            for n in frontier:
                for ln in (book.get(n, {}).get("ingredients") or []):
                    ref = ln.get("ref")
                    if ln.get("kind") == "subrecipe" and ref and ref not in seen:
                        seen.add(ref)
                        nxt.add(ref)
            frontier = nxt
        for batch in seen:
            out[batch] = out.get(batch, 0.0) + money
    return out


def conflicts() -> list[dict]:
    harvest = (RECIPE_YIELDS.load() or {}).get("yields") or {}
    working = PREP_YIELDS.load() or {}
    money = _revenue_behind()
    out: list[dict] = []
    for batch, v in harvest.items():
        w = working.get(batch)
        if not w:
            continue
        hq, hu = _base(v.get("yield"), v.get("unit"))
        wq, wu = _base(w.get("yield_qty"), w.get("yield_unit"))
        if hq <= 0 or wq <= 0:
            continue
        same_class = _CLASS.get(hu) == _CLASS.get(wu)
        if same_class and abs(hq - wq) <= TOLERANCE * max(hq, wq):
            continue
        out.append({
            "batch": batch,
            "produce": f"{hq:,.0f} {hu}",
            "working": f"{wq:,.0f} {wu}",
            "ratio": round(wq / hq, 3) if hq else None,
            "unit_class": None if same_class else f"{_CLASS.get(hu)} vs {_CLASS.get(wu)}",
            "revenue": round(money.get(batch, 0.0)),
            "basis": " ".join((w.get("basis") or "").split())[:90],
        })
    return sorted(out, key=lambda c: -c["revenue"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--rebase", action="store_true")
    a = ap.parse_args()

    found = conflicts()
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {"open": len(found)}

    print(f"batches whose two yield files disagree: {len(found)} "
          f"(baseline {base.get('open')})\n")
    print(f"{'batch':<34}{'Produce':>14}{'prep_yields':>14}{'ratio':>8}"
          f"{'revenue':>12}  note")
    for c in found:
        note = c["unit_class"] or ""
        print(f"{c['batch'][:33]:<34}{c['produce']:>14}{c['working']:>14}"
              f"{(c['ratio'] or 0):>7.2f}x${c['revenue']:>11,}  {note}")
    if found:
        print("\nThe answer to any of these is a WEIGHING in "
              "data/measured_yields.yaml, which outranks both files.")
        for c in found[:4]:
            print(f"\n  {c['batch']}\n     prep_yields basis: {c['basis'] or '(none)'}")

    if a.rebase:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {"open": len(found),
             "note": "batches where data/recipe_yields.yaml (a 2026-08-09 harvest "
                     "of Produce) and data/prep_yields.yaml (what the book costs "
                     "off) disagree by more than 5% or across unit classes. "
                     "Neither file outranks the other — see the script header. "
                     "May fall, may not rise."},
            indent=1) + "\n")
        print(f"\npinned at {len(found)}")
        return 0

    if a.strict and len(found) > base.get("open", len(found)):
        print(f"\n::error::{len(found) - base['open']} more batch(es) now have "
              f"two yields that disagree. One of the two files moved without the "
              f"other — find out which, before a dish is costed off the loser.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
