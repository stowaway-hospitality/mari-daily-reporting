#!/usr/bin/env python3
"""Book `receive` movements into the stock ledger from the invoice corpus.

Stock IN is the one side of inventory this business already owns outright: 604
parsed invoices, cent-accurate, with pack sizes. This turns them into ledger
rows — but only where BOTH questions have a provable answer:

    WHICH ITEM?   supplier + supplier_code -> lightspeed:<id>, via
                  data/product_map.csv, which resolve.py builds ONLY from real
                  invoice lines matched to real export rows. Never a name
                  guess: "ALEHOUSE CRISP KEG" and "ALEHOUSE PREMIUM KEG" are
                  $27.50 apart and the sensible guess is backwards.

    HOW MUCH?     qty x pack_qty, converted to the item's canonical base unit.
                  kg->g, L->ml, ea->each. A 'box' or a 'tray' has no provable
                  size and REFUSES.

Anything failing either test is NOT booked, and is counted in the refusal
report. That is the point: a ledger that silently skips a third of deliveries
reports stock that lasts longer than it should, which is the flattering
direction and the dangerous one.

Receive rows are DERIVED — rebuilt wholesale from the invoices each run
(rewrite_year), so they stay reproducible. Counts and waste are never rebuilt:
a human typed those once and they exist nowhere else.

Run: python3 scripts/build_receive_movements.py [--write]
     (dry by default — prints the coverage report without touching the ledger)
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger import (Movement, UnprovableUnit, load_base_units,   # noqa: E402
                    rewrite_year, to_base)

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
INVOICES = ROOT / "data" / "invoices"
PRODUCT_MAP = ROOT / "data" / "product_map.csv"

VENUE_KEY = {"stowaway": "stow", "harry_gatos": "hg", "marilynas": "mari"}


def load_map() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    if not PRODUCT_MAP.exists():
        return out
    with PRODUCT_MAP.open() as f:
        for r in csv.DictReader(f):
            code = (r.get("supplier_code") or "").strip()
            pid = (r.get("product_id") or "").strip()
            if code and pid:
                out[((r.get("supplier") or "").strip().lower(), code)] = f"lightspeed:{pid}"
    return out


def dec(x) -> Decimal | None:
    try:
        return Decimal(str(x).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None


def main() -> int:
    pmap = load_map()
    base_units = load_base_units()

    booked: list[Movement] = []
    refused: Counter = Counter()
    refused_value: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    by_supplier_unmapped: Counter = Counter()
    lines = 0

    for path in sorted(INVOICES.glob("*.json")):
        doc = json.loads(path.read_text())
        inv = doc.get("invoice", {})
        supplier = (inv.get("supplier_key") or "").strip().lower()
        day = (inv.get("invoice_date") or "").strip()
        venue = VENUE_KEY.get(inv.get("venue") or "", inv.get("venue") or "")
        ref = f"invoice:{inv.get('supplier_key')}:{inv.get('invoice_ref')}"
        credit = bool(inv.get("is_credit_note"))

        for L in inv.get("lines", []):
            if L.get("line_class") != "stock":
                continue
            lines += 1
            value = dec(L.get("line_total_incl")) or Decimal(0)

            item = pmap.get((supplier, (L.get("supplier_code") or "").strip()))
            if not item:
                refused["no item identity (supplier code not in product_map.csv)"] += 1
                refused_value["no item identity (supplier code not in product_map.csv)"] += value
                by_supplier_unmapped[supplier] += 1
                continue

            qty, pack_qty = dec(L.get("qty")), dec(L.get("pack_qty"))
            if qty is None or pack_qty is None:
                refused["missing qty or pack size"] += 1
                refused_value["missing qty or pack size"] += value
                continue

            try:
                per_pack, base = to_base(pack_qty, L.get("pack_unit") or "")
            except UnprovableUnit:
                key = f"unprovable pack unit {L.get('pack_unit')!r}"
                refused[key] += 1
                refused_value[key] += value
                continue

            want = base_units.get(item)
            if want is None:
                refused["item has no canonical base unit (unused by any recipe, or conflicted)"] += 1
                refused_value["item has no canonical base unit (unused by any recipe, or conflicted)"] += value
                continue
            if want != base:
                # The recipe book consumes this item in one dimension and the
                # invoice delivers another — grams against millilitres, or a
                # count against a weight. One of the two is wrong about what
                # this item IS, and booking it would be wrong forever.
                key = f"unit dimension clash (recipes use {want}, invoice delivers {base})"
                refused[key] += 1
                refused_value[key] += value
                continue

            total = qty * per_pack
            booked.append(Movement(
                ts=day, venue=venue, item_id=item,
                qty_base=str(total), base_unit=base,
                direction="out" if credit else "in",
                reason="receive", source_ref=ref, actor="invoice-pipeline",
                note=(L.get("description") or "")[:60],
            ))

    print(f"invoice stock lines: {lines:,}")
    print(f"  booked as receive movements: {len(booked):,} "
          f"({len(booked)/lines*100:.1f}%)")
    print(f"  refused: {sum(refused.values()):,}\n")
    print("  why refused                                                    lines      $ incl")
    for why, n in refused.most_common():
        print(f"    {why[:58]:58} {n:6,}  {refused_value[why]:10,.2f}")

    if by_supplier_unmapped:
        print("\n  unmapped supplier codes, by supplier:")
        for s, n in by_supplier_unmapped.most_common(8):
            print(f"    {s:22} {n:5,}")

    if "--write" in sys.argv:
        years = {m.ts[:4] for m in booked}
        total = 0
        for y in sorted(years):
            total += rewrite_year(y, [m for m in booked if m.ts[:4] == y])
        print(f"\nwrote {total:,} receive movement(s) across {len(years)} year file(s)")
    else:
        print("\n(dry run — pass --write to book these into data/ledger/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
