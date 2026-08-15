#!/usr/bin/env python3
"""
Repair Fresh Fruit Team lines whose SKU cell swallowed the neighbouring column.

    python3 scripts/repair_fft_swallowed_codes.py            # review
    python3 scripts/repair_fft_swallowed_codes.py --apply    # rewrite data/invoices

THE DEFECT
----------
FFT's UNIT column does not always start right of the 143pt boundary, so the PDF
bucketer files the unit word under "sku". The parser was fixed for this on
2026-08-14 (modules/invoices/parsers/fresh_fruit_team.py::_split_sku, "an FFT
code is a single alphanumeric token") and 41 stored invoices were backfilled by
hand the same day. Five codes were missed, and they are still in data/invoices:

    BRL Box   CELE Each   PQEA Each   KITCSKG Kilogram   cauli-ea Each

WHY IT MATTERS, restating the parser's own note: supplier_code is the product
IDENTITY. "BRL" and "BRL Box" are the same broccolini but become two cost-book
entries — the price history splits, the picker shows the item twice, and
build_ingredients' "fullest description across the spellings of one identity"
consolidation cannot see across them. It is also how the feed ends up carrying
fragments like "Brl" and "Cele" as if they were product names, which is what
test_no_suspect_names_reach_the_picker catches.

THE REPAIR IS A COLUMN SHIFT, and it is deterministic. A healthy line and a bled
line from the same code sit side by side in the corpus:

    healthy   supplier_code "BRL"       description "Broccolini"   raw_uom "Box"
    bled      supplier_code "BRL Box"   description "BRL Box"      raw_uom "Broccolini"

so: the code is the first whitespace token; the real product name is sitting in
raw_uom; and the tail the code swallowed is the real unit. Nothing is inferred —
every field is already on the line, one column to the left of where it belongs.

WHAT IS DELIBERATELY NOT TOUCHED
--------------------------------
No money. qty, unit_price_incl and line_total_incl are never written, and the
script asserts every invoice's line-total sum is byte-identical before and
after. If a repair would change a total the invoice is skipped and reported —
a name is not worth a cent.

A line is only repaired when raw_uom actually holds something that looks like a
product name (letters, not a bare unit word). Where it does not, the code is
still split — that alone reunites the identity — and the description is left
exactly as it is rather than guessed at.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVOICES = ROOT / "data" / "invoices"

# a bled cell is "<token> <tail>"; a clean FFT code is one token
BLED = re.compile(r"^(\S+)\s+(.+)$")
# 2026-08-14, second pass: upstream's backfill split the CODE ("BRL Box" -> "BRL")
# but left the DESCRIPTION holding the bled cell and the product name stranded in
# raw_uom. So the repair below matches on the description, not the code.
# unit words FFT prints in that column — if raw_uom is one of these it is a real
# unit and NOT a displaced product name
UNIT_WORDS = {"each", "box", "bunch", "kilogram", "kg", "punnet", "tray", "bag",
              "dozen", "packet", "pack", "case", "ea", "market", "herb"}


def _d(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


def _total(lines) -> Decimal:
    t = Decimal(0)
    for ln in lines:
        v = _d(ln.get("line_total_incl"))
        if v is not None:
            t += v
    return t


def repair_lines(lines) -> int:
    """Repair in place. -> number of lines changed.

    Two shapes, because the backfill of 2026-08-14 fixed half of it:

      A. code still bled   code "BRL Box"  desc "BRL Box"  uom "Broccolini"
      B. code repaired     code "BRL"      desc "BRL Box"  uom "Broccolini"

    Both are the same column shift and both end at the same place:
                           code "BRL"      desc "Broccolini"  uom "Box"
    """
    n = 0
    for ln in lines:
        code = str(ln.get("supplier_code") or "").strip()
        desc = str(ln.get("description") or "").strip()
        uom = str(ln.get("raw_uom") or "").strip()

        m = BLED.match(code)
        head, tail = (m.group(1), m.group(2)) if m else (code, "")

        # the description echoes the bled cell: "<code> <unitword>"
        dm = BLED.match(desc)
        desc_is_bled = bool(dm and dm.group(1) == head
                            and dm.group(2).strip().lower() in UNIT_WORDS)
        if not (m or desc_is_bled):
            continue

        # the real product name is only in raw_uom when raw_uom is NOT a unit word
        name_displaced = bool(uom) and uom.lower() not in UNIT_WORDS
        if not name_displaced:
            # nothing to recover; still reunite the identity if the code bled
            if m:
                ln["supplier_code"] = head
                if not uom:
                    ln["raw_uom"] = tail
                n += 1
            continue

        ln["supplier_code"] = head
        ln["description"] = uom
        ln["raw_uom"] = (dm.group(2) if desc_is_bled else tail) or uom
        n += 1
    return n


def purge_orphan_cogs_rows(apply: bool) -> int:
    """PHASE 2 — drop cost-book rows keyed by a code no invoice carries any more.

    Repairing the invoices is only half of it. data/cogs_list.csv MERGES, it does
    not replace, so every line that was stored under a bled code keeps its row
    forever: 359 of them on 2026-08-14, sitting beside the clean-coded rows for
    the very same invoice lines. canonical_purchasable() maps "BRL Box" onto
    "BRL", so the two collide, and main was red on both consequences —
    test_the_real_cost_book_holds_each_invoice_line_once saw one invoice line
    counted twice, and test_no_suspect_names_reach_the_picker saw "Brl" and
    "Cele" offered to a chef as product names.

    TWO CONDITIONS, both required, both asserted rather than assumed:
      1. the bled code appears in NO stored invoice (so nothing still feeds it), and
      2. a row with the CLEAN code exists on the SAME invoice (so dropping it
         removes a duplicate, never an observation).
    Measured before writing: 359/359 satisfied both, and 0 bled codes were still
    live. A row failing either test is kept and reported.
    """
    import csv as _csv
    cogs = ROOT / "data" / "cogs_list.csv"
    if not cogs.exists():
        return 0
    live_codes = set()
    for f in INVOICES.glob("*fresh_fruit_team*.json"):
        inv = (json.loads(f.read_text(encoding="utf-8-sig")).get("invoice") or {})
        for ln in (inv.get("lines") or []):
            live_codes.add(str(ln.get("supplier_code") or "").strip())

    rows = list(_csv.DictReader(cogs.open(encoding="utf-8-sig")))
    fields = list(rows[0].keys()) if rows else []
    by_inv: dict = {}
    for r in rows:
        if (r.get("supplier") or "") == "Fresh Fruit Team":
            by_inv.setdefault(r.get("source_invoice"), set()).add(
                (r.get("supplier_code") or "").strip())

    keep, dropped, refused = [], 0, []
    for r in rows:
        code = (r.get("supplier_code") or "").strip()
        if (r.get("supplier") or "") != "Fresh Fruit Team" or " " not in code:
            keep.append(r)
            continue
        if code in live_codes:
            refused.append((code, r.get("source_invoice"), "still live in an invoice"))
            keep.append(r)
            continue
        if code.split()[0] not in by_inv.get(r.get("source_invoice"), set()):
            refused.append((code, r.get("source_invoice"), "no clean-code row on that invoice"))
            keep.append(r)
            continue
        dropped += 1

    print(f"\nphase 2: {dropped} orphan cost-book row(s) keyed by a dead bled "
          f"code{' — REMOVED' if apply else ' (review only)'}")
    for c, i, why in refused[:10]:
        print(f"  KEPT {c} on {i}: {why}")
    if apply and dropped:
        with cogs.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(keep)
    return dropped


def refresh_cogs_descriptions(apply: bool) -> int:
    """PHASE 3 — pull the repaired product NAME through into the cost book.

    build_cogs_list re-derives money (DERIVED = cost_per_unit_incl_gst, pack_qty,
    pack_unit, cost_per_base_unit) and nothing else, on purpose: a human's
    judgement must not be overwritten by a re-parse. invoice_description is
    neither, so a name written before the parser was fixed sits there forever.

    That is why the picker still offered "Brl" and "Cele" after phases 1 and 2 had
    repaired every invoice and dropped every orphan: the rows under the CLEAN code
    still carried the bled label. A description is a label, not money, so this
    copies it across where the two disagree, matched on (supplier_code,
    source_invoice) — never inventing one, only taking what the invoice now says.
    """
    import csv as _csv
    cogs = ROOT / "data" / "cogs_list.csv"
    if not cogs.exists():
        return 0
    want: dict = {}
    for f in INVOICES.glob("*fresh_fruit_team*.json"):
        doc = json.loads(f.read_text(encoding="utf-8-sig"))
        inv = doc.get("invoice") or {}
        num = str(inv.get("invoice_ref") or "").strip()
        for ln in (inv.get("lines") or []):
            code = str(ln.get("supplier_code") or "").strip()
            desc = str(ln.get("description") or "").strip()
            if code and desc and desc != code:
                want[(code, num)] = desc

    rows = list(_csv.DictReader(cogs.open(encoding="utf-8-sig")))
    fields = list(rows[0].keys()) if rows else []
    n = 0
    for r in rows:
        if (r.get("supplier") or "") != "Fresh Fruit Team":
            continue
        key = ((r.get("supplier_code") or "").strip(), (r.get("source_invoice") or "").strip())
        new = want.get(key)
        old = (r.get("invoice_description") or "").strip()
        if not new or new == old:
            continue
        # only replace a label that is a code echo, never a real name
        head = key[0]
        if not (old == head or old.startswith(head + " ")):
            continue
        r["invoice_description"] = new
        if (r.get("lightspeed_product") or "").strip() in ("", old):
            r["lightspeed_product"] = new
        n += 1

    print(f"phase 3: {n} stale cost-book label(s) refreshed from the repaired "
          f"invoice{' — WRITTEN' if apply else ' (review only)'}")
    if apply and n:
        with cogs.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    changed_files, changed_lines, skipped = 0, 0, []
    for f in sorted(INVOICES.glob("*fresh_fruit_team*.json")):
        doc = json.loads(f.read_text(encoding="utf-8-sig"))
        inv = doc.get("invoice") or {}
        lines = inv.get("lines") or []
        before_total = _total(lines)
        before = json.dumps(lines, sort_keys=True)
        n = repair_lines(lines)
        if not n:
            continue
        if _total(lines) != before_total:
            skipped.append((f.name, "TOTAL MOVED — not written"))
            doc["invoice"]["lines"] = json.loads(before)
            continue
        changed_files += 1
        changed_lines += n
        print(f"  {f.name[:44]:<46} {n} line(s)")
        for ln in lines:
            pass
        if args.apply:
            f.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n{changed_lines} line(s) across {changed_files} invoice(s)"
          f"{' — WRITTEN' if args.apply else ' (review only; pass --apply)'}")
    for name, why in skipped:
        print(f"  SKIPPED {name}: {why}")
    purge_orphan_cogs_rows(args.apply)
    refresh_cogs_descriptions(args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
