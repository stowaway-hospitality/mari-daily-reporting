#!/usr/bin/env python3
"""
A container's price recorded as its per-unit rate.

    python3 scripts/check_pack_as_rate.py            # report
    python3 scripts/check_pack_as_rate.py --strict   # exit 1 on any finding

THE DEFECT. An `ls-recipe-seed` row carries Lightspeed's own recipe cost for a
ProductID. Sometimes that figure is the price of the WHOLE CONTAINER, filed
against pack_qty 1 with a per-ml or per-g unit — so a $35 gallon of hot sauce
becomes $35 per LITRE, and a $21 tub of honey becomes $21 per KILO.

It is the ILG case/bottle defect one level up: a price that counts a container
against a unit that counts its contents. It survived because it OVER-states cost,
and the guards in this repo are tuned to catch the direction that flatters GP.

THE SIGNATURE, and why it needs no judgement. Where the same goods are also
bought on a real invoice, the cost book holds both. Divide the seeded rate by the
invoice-fed rate and, when this bug is present, the answer is the PACK SIZE
printed in the product's own name — 3 for a 3 kg tub, 3.785 for a gallon. That is
a coincidence no price difference produces.

WHAT IT DELIBERATELY DOES NOT REPORT. Four ingredients differ from their
invoice-fed twins by 1.5-2.0x — brown sugar, demerara, spanish onion, prosciutto.
Those ratios match no pack size in their names, so they are ordinary price
differences (or different specs) and are none of this check's business. A
detector that also fires on real price movement gets switched off.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COGS = ROOT / "data" / "cogs_list.csv"

SEEDS = ("ls-recipe-seed", "bo-seed", "bo-ingredient-seed")
_PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(KG|KGS|G|GM|GRAM|L|LT|LTR|LITRE|ML)\b", re.I)
_GAL = re.compile(r"(\d+(?:\.\d+)?)\s*GALLN", re.I)
_TO = {"kg": 1000, "kgs": 1000, "g": 1, "gm": 1, "gram": 1,
       "l": 1000, "lt": 1000, "ltr": 1000, "litre": 1000, "ml": 1}
TOL = 0.15          # the ratio must land within 15% of the stated pack size


def _norm(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]", " ", s or "")
    return " ".join(sorted(w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2))


def _rate(row):
    """cost per kg or per L, or None."""
    try:
        c = float(row.get("cost_per_base_unit") or 0)
    except (TypeError, ValueError):
        return None
    u = (row.get("pack_unit") or "").strip().lower()
    if c <= 0 or u not in ("kg", "l", "g", "ml"):
        return None
    return c * 1000 if u in ("g", "ml") else c


def _stated_pack(desc: str):
    """The pack size in the product's own name, expressed in kg or L."""
    m = _GAL.search(desc or "")
    if m:
        return float(m.group(1)) * 3.78541
    m = _PACK.search(desc or "")
    if not m:
        return None
    base = float(m.group(1)) * _TO.get(m.group(2).lower(), 0)
    return base / 1000 if base else None


def _resolved() -> set:
    """purchasable ids with a confirmed pack override.

    An override IS the resolution: it pins the real container size so the seeded
    price divides down to the invoice rate. Without this the check would keep
    reporting the two it has already fixed, and a guard that cries after it has
    been satisfied gets switched off.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from core.pack_overrides import load_pack_overrides
        return set(load_pack_overrides(ROOT / "data" / "pack_overrides.yaml"))
    except Exception:                                            # noqa: BLE001
        return set()


def findings(rows):
    fixed = _resolved()
    invoice = defaultdict(list)
    for r in rows:
        if (r.get("supplier") or "") == "Lightspeed":
            continue
        v = _rate(r)
        if v:
            invoice[_norm(r.get("invoice_description", ""))].append(v)

    out = []
    for r in rows:
        if not (r.get("source_invoice") or "").startswith(SEEDS):
            continue
        if f"lightspeed:{r.get('supplier_code','')}" in fixed:
            continue                       # already pinned — see _resolved
        if (r.get("pack_unit") or "").strip().lower() not in ("g", "ml"):
            continue
        if (r.get("pack_qty") or "").strip() not in ("1", "1.0", ""):
            continue
        desc = r.get("invoice_description", "")
        pack = _stated_pack(desc)
        seeded = _rate(r)
        twin = invoice.get(_norm(desc))
        if not (pack and pack > 1 and seeded and twin):
            continue
        ref = sum(twin) / len(twin)
        if ref <= 0:
            continue
        ratio = seeded / ref
        if abs(ratio - pack) > pack * TOL:
            continue                       # not the pack size -> a real price gap
        out.append({
            "code": r.get("supplier_code", ""), "description": desc[:46],
            "seeded": round(seeded, 2), "invoice": round(ref, 2),
            "ratio": round(ratio, 2), "pack": round(pack, 3),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    found = findings(rows)
    if not found:
        print(f"pack-as-rate: ok — no seeded rate is its own container's price "
              f"({len(rows)} rows)")
        return 0
    print(f"pack-as-rate: {len(found)} seeded rate(s) look like the price of the "
          f"whole container, not the unit:")
    for f in found:
        print(f"   {f['description']:<46} ({f['code']})")
        print(f"      seeded ${f['seeded']:,.2f} vs invoice ${f['invoice']:,.2f} "
              f"= {f['ratio']}x, and the name says a {f['pack']} pack")
    return 1 if a.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
