#!/usr/bin/env python3
"""
Repair CTN-N invoice lines whose unit price is PER PIECE while the qty counts cartons.

    python3 scripts/repair_ctn_unit_price.py            # review
    python3 scripts/repair_ctn_unit_price.py --apply    # rewrite data/invoices

THE DEFECT
----------
Foodlink prints a carton line two different ways and the parser believes the
unit-price column either way:

    SI4472410  CHEESE CAMEMBERT 125GM  qty 1  CTN-12  unit $45.6000  total $45.60
    SI4480678  CHEESE CAMEMBERT 125GM  qty 1  CTN-12  unit $ 3.8000  total $45.60

Both are one carton of twelve 125 g pieces for $45.60. On the first the unit
column holds the CARTON price; on the second it holds the PIECE price. The pack
is read as "1 box" in both cases, so the second row books a whole carton at
$3.80 — 12x UNDER, which is the flattering direction and therefore the one that
does not get noticed.

It sat undetected because check_pack_agreement needs at least three deliveries in
a group before a median means anything, and Foodlink bills this code by the kilo
as well. Only when the August invoices arrived did the "box" group reach three
and the outlier become visible.

THE LINE'S OWN ARITHMETIC SETTLES IT — no judgement, no catalogue, no guess:

    line_total == qty x unit_price          -> consistent, leave it alone
    line_total == qty x unit_price x N      -> the unit price is per PIECE

and for qty 1 the price of one pack is simply the line total. That is what is
written back. `line_total_incl` is never touched, so no invoice total moves, and
the repair is idempotent: run it twice and the second pass finds nothing.

Deliberately narrow. It fires only where raw_uom is exactly "CTN-<N>", only where
the second identity holds to within 2 cents, and never where the line is already
self-consistent. Two lines matched on the whole corpus.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVOICES = ROOT / "data" / "invoices"
CTN = re.compile(r"^CTN-(\d+)$", re.I)
TOL = Decimal("0.02")


def _d(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


def repair_lines(lines) -> list:
    """Repair in place. -> list of (code, description, old_unit, new_unit, n)."""
    out = []
    for ln in lines:
        m = CTN.match(str(ln.get("raw_uom") or "").strip())
        if not m:
            continue
        q, up, lt = _d(ln.get("qty")), _d(ln.get("unit_price_incl")), _d(ln.get("line_total_incl"))
        if None in (q, up, lt) or q <= 0 or up <= 0 or lt <= 0:
            continue
        if abs(lt - q * up) <= TOL:
            continue                       # already consistent
        n = Decimal(m.group(1))
        if abs(lt - q * up * n) > TOL:
            continue                       # not this defect — leave it for a human
        new = (lt / q).quantize(Decimal("0.0001"))
        out.append((ln.get("supplier_code"), str(ln.get("description"))[:38], str(up), str(new), str(n)))
        ln["unit_price_incl"] = str(new)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    total = 0
    for f in sorted(INVOICES.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        inv = doc.get("invoice") or {}
        lines = inv.get("lines") or []
        before = sum((_d(l.get("line_total_incl")) or Decimal(0)) for l in lines)
        got = repair_lines(lines)
        if not got:
            continue
        after = sum((_d(l.get("line_total_incl")) or Decimal(0)) for l in lines)
        if before != after:                       # cannot happen; asserted anyway
            print(f"  REFUSED {f.name}: invoice total would move")
            continue
        total += len(got)
        for code, desc, old, new, n in got:
            print(f"  {f.name[:40]:<42}{str(code):<9}{desc:<40} ${old} -> ${new}  (CTN-{n})")
        if args.apply:
            f.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{total} line(s){' — WRITTEN' if args.apply else ' (review only; pass --apply)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
