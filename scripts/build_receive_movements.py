#!/usr/bin/env python3
"""Book `receive` movements into the stock ledger from the invoice corpus.

Stock IN is the one side of inventory this business already owns outright: 604
parsed invoices, cent-accurate, with pack sizes. This turns them into ledger
rows — but only where BOTH questions have a provable answer:

    WHICH ITEM?   `core.domain.purchasable_id(supplier, code)` — the natural key
                  of a thing you buy, given by the invoice, never invented. That
                  IS the repo's identity model: recipes already reference
                  ingredients as `foodlink:102689` and `fresh-fruit-team:AH20T`
                  alongside `lightspeed:<id>`.

                  Where data/product_map.csv resolves the code to a Lightspeed
                  product we prefer that id, because it unifies the same item
                  bought from two suppliers. Otherwise the supplier key stands
                  on its own. A line with NO supplier_code has no natural key
                  and is refused — falling back to the description is how
                  ALEHOUSE CRISP KEG becomes the wrong $27.50 keg.

    Pack size has THREE sources, in order, and the third one exists because Zak
    asked "are you absolutely sure we haven't recorded packs for this in our
    cogs book?" — and we had. data/costs.csv is built by
    modules/recipes/pipeline/build_costs.py, which converts pack prices into the
    unit a recipe uses and REFUSES rather than guessing when a pack can't be
    read. So every row in it is a pack that was already resolved, by the same
    standard this script applies, and 69 items were being refused here that the
    cost book had solved months ago.

        1. data/pack_overrides.yaml   a human wrote down what a pack holds
        2. the invoice's own parse    pack_qty + pack_unit off the line
        3. data/costs.csv             a per-g/ml/ea price DERIVED FROM THIS VERY
                                      INVOICE (matched on source_invoice), so
                                      line_total_ex / cost_per_unit is the base
                                      quantity exactly, not an estimate

    Matching on source_invoice is what makes (3) safe. A price from a different
    delivery would silently turn a price change into a quantity change; the
    observation and the line have to be the same event.

    HOW MUCH?     qty x pack size, converted to the item's base unit.
                  kg->g, L->ml, ea->each. A 'box', 'tray' or 'case' has no
                  provable size in the invoice — but data/pack_overrides.yaml
                  is exactly the declared-conversion-with-evidence table this
                  needs, so a human-confirmed pack (`by`, `on`, `by_email`)
                  resolves it. Anything still unprovable REFUSES.

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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import (Movement, UnprovableUnit, load_base_units,   # noqa: E402
                    qty_str, rewrite_year, to_base)
# Identity and declared pack sizes are INHERITED, never re-derived. purchasable_id
# normalises the unit word the PDF parse bleeds onto a code, and pack_overrides is
# the chef-confirmed table that resolves a 'box' into a real quantity.
from core.domain import purchasable_id                            # noqa: E402
from core.pack_overrides import load_pack_overrides               # noqa: E402

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


COSTS = ROOT / "data" / "costs.csv"
BASE_PRICED = {"g", "ml", "ea", "each"}


def load_cost_units() -> dict[tuple[str, str], tuple[Decimal, str]]:
    """(ingredient, source_invoice) -> (cost_per_unit, base unit).

    The cost book already resolved these packs, refusing the ones it could not
    read — the same standard applied here. Keyed by the invoice the observation
    came FROM, so a price is only ever used against the delivery that produced
    it; using a later price would turn a price rise into a phantom shortfall.
    """
    out: dict[tuple[str, str], tuple[Decimal, str]] = {}
    if not COSTS.exists():
        return out
    with COSTS.open() as f:
        for r in csv.DictReader(f):
            unit = (r.get("unit") or "").strip().lower()
            if unit not in BASE_PRICED:
                continue                  # priced per box/tray/bottle — no help
            cpu = dec(r.get("cost_per_unit"))
            if not cpu or cpu <= 0:
                continue
            out[(r["ingredient"], (r.get("source_invoice") or "").strip())] = (cpu, unit)
    return out


def collect_lines() -> list[dict]:
    """Every stock line, with identity resolved, before any unit decision."""
    pmap = load_map()
    out = []
    for path in sorted(INVOICES.glob("*.json")):
        doc = json.loads(path.read_text())
        inv = doc.get("invoice", {})
        supplier = (inv.get("supplier_key") or "").strip()
        for L in inv.get("lines", []):
            if L.get("line_class") != "stock":
                continue
            code = (L.get("supplier_code") or "").strip()
            try:
                pid = purchasable_id(supplier, code) if code else None
            except ValueError:
                pid = None
            # Prefer the Lightspeed id where the evidence table has it: one item
            # bought from two suppliers must not become two piles of stock.
            item = pmap.get((supplier.lower(), code)) or pid
            out.append({
                "supplier": supplier, "code": code, "item": item,
                "purchasable": pid, "line": L, "inv": inv,
            })
    return out


def derive_base_units(rows: list[dict], recipe_units: dict[str, str]) -> tuple[dict[str, str], dict[str, set]]:
    """item_id -> base unit, from recipes first and the invoice's own units second.

    The recipe book is the better witness: it says how the item is CONSUMED, and
    that is what a deduction will be denominated in. Where no recipe touches the
    item, the supplier's own stated unit is still evidence — a thing delivered
    only ever in kg is a gram item.

    An item delivered in two DIMENSIONS (kg one week, each the next) is refused,
    not averaged. That is the CTN-6-read-as-one-tin failure wearing a new hat.
    """
    seen: dict[str, set] = defaultdict(set)
    for r in rows:
        if not r["item"]:
            continue
        unit = (r["line"].get("pack_unit") or "").strip().lower()
        if unit in ("g", "kg"):
            seen[r["item"]].add("g")
        elif unit in ("ml", "l"):
            seen[r["item"]].add("ml")
        elif unit in ("ea", "each"):
            seen[r["item"]].add("each")

    out = dict(recipe_units)
    for item, dims in seen.items():
        if item in out:
            continue                      # a recipe already decided
        if len(dims) == 1:
            out[item] = next(iter(dims))
    conflicts = {i: d for i, d in seen.items() if len(d) > 1 and i not in recipe_units}
    return out, conflicts


def main() -> int:
    recipe_units = load_base_units()
    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")
    rows = collect_lines()
    base_units, unit_conflicts = derive_base_units(rows, recipe_units)

    booked: list[Movement] = []
    refused: Counter = Counter()
    refused_value: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    by_supplier_unmapped: Counter = Counter()
    override_used = 0
    from_cost = 0
    cost_units = load_cost_units()

    def refuse(why: str, value: Decimal) -> None:
        refused[why] += 1
        refused_value[why] += value

    for r in rows:
        L, inv, item = r["line"], r["inv"], r["item"]
        value = dec(L.get("line_total_incl")) or Decimal(0)

        if not item:
            refuse("no supplier code — no natural key, and the description is not one", value)
            by_supplier_unmapped[r["supplier"].lower()] += 1
            continue

        qty = dec(L.get("qty"))
        if qty is None:
            refuse("missing quantity", value)
            continue

        # Pack size: a human confirmation beats the parse, always. That is what
        # pack_overrides.yaml is for, and it is keyed by purchasable id.
        ov = overrides.get(r["purchasable"] or "") or overrides.get(item)
        if ov:
            pack_qty, pack_unit = ov
            override_used += 1
        else:
            pack_qty, pack_unit = dec(L.get("pack_qty")), (L.get("pack_unit") or "")
            if pack_qty is None:
                refuse("missing pack size", value)
                continue

        try:
            per_pack, base = to_base(pack_qty, pack_unit)
        except UnprovableUnit:
            per_pack = base = None

        want = base_units.get(item)

        # The cost book may already have resolved this exact delivery. Its price
        # is per base unit, so the ex-GST line total divided by it IS the base
        # quantity — no parsing, no guess.
        if base is None or (want and base != want):
            obs = cost_units.get((item, str(inv.get("invoice_ref") or "").strip()))
            if obs:
                cpu, cunit = obs
                cbase = {"ea": "each"}.get(cunit, cunit)
                line_ex = dec(L.get("line_total_ex"))
                if line_ex is None:
                    inc = dec(L.get("line_total_incl"))
                    # GST-free lines exist (basic food); the invoice says which.
                    line_ex = (inc / Decimal("1.1")
                               if inc is not None and L.get("tax_treatment") == "gst"
                               else inc)
                if line_ex and line_ex > 0:
                    from_cost += 1
                    booked.append(Movement(
                        ts=(inv.get("invoice_date") or "").strip(),
                        venue=VENUE_KEY.get(inv.get("venue") or "", inv.get("venue") or ""),
                        item_id=item, qty_base=qty_str(line_ex / cpu), base_unit=cbase,
                        direction="out" if inv.get("is_credit_note") else "in",
                        reason="receive",
                        source_ref=f"invoice:{inv.get('supplier_key')}:{inv.get('invoice_ref')}",
                        actor="invoice-pipeline",
                        note=(L.get("description") or "")[:60],
                    ))
                    continue

        if base is None:
            refuse(f"unprovable pack unit {str(pack_unit).lower()!r} "
                   f"(no confirmation, and the cost book has not priced it either)", value)
            continue

        if want is None:
            why = ("item delivered in two dimensions — refused, not averaged"
                   if item in unit_conflicts
                   else "no base unit derivable for this item")
            refuse(why, value)
            continue
        if want != base:
            refuse(f"unit dimension clash (item is {want}, this line delivers {base})", value)
            continue

        booked.append(Movement(
            ts=(inv.get("invoice_date") or "").strip(),
            venue=VENUE_KEY.get(inv.get("venue") or "", inv.get("venue") or ""),
            item_id=item, qty_base=qty_str(qty * per_pack), base_unit=base,
            direction="out" if inv.get("is_credit_note") else "in",
            reason="receive",
            source_ref=f"invoice:{inv.get('supplier_key')}:{inv.get('invoice_ref')}",
            actor="invoice-pipeline",
            note=(L.get("description") or "")[:60],
        ))

    n = len(rows)
    print(f"invoice stock lines: {n:,}")
    print(f"  booked as receive movements: {len(booked):,} ({len(booked)/n*100:.1f}%)")
    print(f"  pack size taken from a human confirmation: {override_used:,}")
    print(f"  quantity taken from the cost book (same invoice): {from_cost:,}")
    print(f"  refused: {sum(refused.values()):,}\n")
    if refused:
        print("  why refused                                                    lines      $ incl")
        for why, c in refused.most_common():
            print(f"    {why[:58]:58} {c:6,}  {refused_value[why]:10,.2f}")
    if by_supplier_unmapped:
        print("\n  lines with no supplier code, by supplier:")
        for s, c in by_supplier_unmapped.most_common(8):
            print(f"    {s:24} {c:5,}")
    if unit_conflicts:
        print(f"\n  {len(unit_conflicts)} item(s) delivered in more than one dimension (refused):")
        for i, d in list(unit_conflicts.items())[:6]:
            print(f"    {i:38} {sorted(d)}")

    items = {m.item_id for m in booked}
    print(f"\n  distinct items receivable: {len(items):,}")

    if "--write" in sys.argv:
        total = 0
        for y in sorted({m.ts[:4] for m in booked}):
            total += rewrite_year(y, [m for m in booked if m.ts[:4] == y])
        print(f"\nwrote {total:,} receive movement(s)")
    else:
        print("\n(dry run — pass --write to book these into data/ledger/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
