#!/usr/bin/env python3
"""
Everything that sold in the last four weeks should stand on an invoice.

    python3 scripts/check_invoice_coverage.py            # the worklist, ranked
    python3 scripts/check_invoice_coverage.py --strict   # exit 1 if it got worse
    python3 scripts/check_invoice_coverage.py --rebase   # pin the current count

THE RULE, in Zak's words (2026-08-20): "literally ALL live items, that have
sales in the last 4 weeks, should have invoices. if not, we need to dig deeper
to find them."

He is right, and the wine pours were the tell. Six wines sold every week stood
on two contradicting JANUARY seeds, and the question "which seed is right" only
existed because no invoice had ever arrived to settle it. A seed is what you
stand on until the first invoice; a LIVE seller still on a seed in August means
the invoice exists somewhere — a supplier the mailbox doesn't parse, a line
that never bridged to its Lightspeed id, a paper invoice nobody scanned — and
finding it is worth more than any adjudication, because an invoice reprices
the ingredient forever while a ruling prices it once.

WHAT THIS MEASURES. Every product with revenue in the last four weeks, walked
down through its recipe to the purchasable ingredients, and every ingredient
whose cost history contains NOT ONE invoice — seeds only. Ranked by the
four-week revenue standing on each, because that is the order the digging pays.

Measured the day it was written: 291 never-invoiced ingredients under live
sellers, and the top of the list is not obscure garnish — it is Parsley,
Pizza Flour, Pizza Yeast, Vegetable Oil, Brown Onions, Sugar, Salt and
PEPPERONI, staples bought weekly whose invoice lines demonstrably flow through
the pipeline and have never been bridged to these Lightspeed ids. So each
finding carries CANDIDATE invoice lines — invoice-fed supplier rows whose
description shares the ingredient's name — to make closing one a one-look
`product_map.csv` confirmation rather than a hunt.

RATCHETED, because 291 is a workload, not a commit. The count may only fall.
A NEW never-invoiced ingredient under a live seller means a product went on
sale standing on a seed, which is precisely the moment to go find its invoice
— while the delivery is still in the coolroom.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines" / "invoice_coverage.json"
LIVE_WEEKS = 4

_STOP = {"the", "and", "of", "a", "in", "with", "per", "kg", "g", "ml", "l",
         "box", "tin", "can", "pack", "carton", "bag", "ctn", "each", "ea"}


def _tokens(s: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower())
            if len(t) > 2 and t not in _STOP and not t.isdigit()}


def _live_products() -> dict:
    """product name -> its revenue over the last LIVE_WEEKS, per the rollups."""
    cutoff = (date.today() - timedelta(weeks=LIVE_WEEKS)).isoformat()
    out: dict[str, float] = {}
    for v in ("stow", "hg", "mari"):
        p = ROOT / "dashboard" / "sales" / "products" / f"rollup_{v}.json"
        if not p.exists():
            continue
        for prod in json.loads(p.read_text(encoding="utf-8-sig")).get("products") or []:
            rev = sum(float(w.get("sales_ex") or 0)
                      for w in (prod.get("weekly") or [])
                      if (w.get("we") or "") >= cutoff)
            if rev > 0:
                name = (prod.get("name") or "").strip()
                out[name] = out.get(name, 0.0) + rev
    return out


def _cost_history():
    """(ever-invoiced ids, latest row per id, invoice-fed rows for matching)."""
    invoiced: set = set()
    latest: dict = {}
    supplier_rows: list = []
    p = ROOT / "data" / "costs.csv"
    if not p.exists():
        return invoiced, latest, supplier_rows
    for r in csv.DictReader(p.open(encoding="utf-8-sig")):
        k, src = r["ingredient"], (r["source_invoice"] or "")
        seedy = "seed" in src.lower()
        if not seedy:
            invoiced.add(k)
            supplier_rows.append(r)
        if k not in latest or r["observed_on"] > latest[k]["observed_on"]:
            latest[k] = r
    return invoiced, latest, supplier_rows


def findings() -> list[dict]:
    book_p = ROOT / "data" / "lightspeed_recipes_costed.json"
    if not book_p.exists():
        return []
    book = json.loads(book_p.read_text())["recipes"]
    bl = {k.strip().lower(): k for k in book}
    live = _live_products()
    invoiced, latest, supplier_rows = _cost_history()

    names = {}
    ing_p = ROOT / "data" / "ingredients.json"
    if ing_p.exists():
        doc = json.loads(ing_p.read_text())
        for i in (doc["ingredients"] if isinstance(doc, dict) else doc):
            names[i["id"]] = i.get("name") or i.get("description") or ""

    def leaf_ids(name, seen):
        r = book.get(name)
        if not r:
            return
        for ln in (r.get("ingredients") or []):
            ref = ln.get("ref")
            if not ref:
                continue
            if ln.get("kind") == "subrecipe":
                if ref not in seen:
                    seen.add(ref)
                    leaf_ids(ref, seen)
            else:
                seen.add(("id", ref))

    exposure: dict[str, float] = {}
    carriers: dict[str, list] = {}
    for sold, rev in live.items():
        key = bl.get(sold.lower())
        if not key:
            continue
        seen: set = set()
        leaf_ids(key, seen)
        for item in seen:
            if not isinstance(item, tuple):
                continue
            _, ref = item
            if ref in invoiced or ref not in latest:
                continue
            exposure[ref] = exposure.get(ref, 0.0) + rev
            carriers.setdefault(ref, []).append(sold)

    out = []
    for ref, rev in sorted(exposure.items(), key=lambda kv: -kv[1]):
        nm = names.get(ref) or latest[ref].get("description") or ref
        want = _tokens(nm)
        cands = []
        if want:
            scored = {}
            for r in supplier_rows:
                got = _tokens(r.get("description") or "")
                hit = len(want & got)
                if hit >= max(1, len(want) - 1):
                    k = f"{r['ingredient']} {r.get('description', '')[:40]}"
                    scored[k] = max(scored.get(k, 0), hit)
            cands = [k for k, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:3]]
        out.append({"id": ref, "name": nm, "live_rev_4wk": round(rev),
                    "products": len(carriers[ref]),
                    "latest_source": latest[ref].get("source_invoice"),
                    "candidates": cands})
    return out


def load_classes() -> dict:
    """data/coverage_classes.yaml — every open entry's adjudicated class.

    THE ROCK-SOLID CONTRACT (Zak, 2026-08-20): "I need this system absolutely
    rock solid to the point where the ONLY work remaining is chefs weighing
    items." A raw count cannot prove that; a CLASSIFIED count can. Every id
    this guard reports must carry a class saying whose move it is — a chef's
    scale, an authored recipe, one answer from Zak, a supplier to forward, or
    simply the next delivery. An UNCLASSIFIED id is the only state that means
    "nobody has looked", and strict mode now fails on it: the moment a new
    product goes on sale standing on a seed, somebody must either bridge it or
    say, in the file, exactly what kind of unfinished it is.
    """
    sys.path.insert(0, str(ROOT))
    from core.declarations import COVERAGE_CLASSES
    return (COVERAGE_CLASSES.load() or {}).get("ids") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--rebase", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()

    found = findings()
    base = (json.loads(BASELINE.read_text()) if BASELINE.exists()
            else {"never_invoiced": len(found)})

    print(f"never-invoiced ingredients under products sold in the last "
          f"{LIVE_WEEKS} weeks: {len(found)} (baseline {base.get('never_invoiced')})")

    classes = load_classes()
    from collections import Counter
    tally = Counter()
    unclassified = []
    for f in found:
        c = classes.get(f["id"])
        f["class"] = (c or {}).get("class", "UNCLASSIFIED")
        tally[f["class"]] += 1
        if c is None:
            unclassified.append(f)
    if classes:
        print("\nwhose move each one is (data/coverage_classes.yaml):")
        for cls, n in tally.most_common():
            print(f"   {cls:<26}{n:>4}")
    if unclassified:
        print("\n   UNCLASSIFIED — nobody has looked at these yet:")
        for f in unclassified[:12]:
            print(f"      {f['name'][:48]:<50}{f['live_rev_4wk']:>9,}")

    print(f"\n{'ingredient':<44}{'4wk rev on it':>14}{'products':>10}   dig here")
    for f in found[:a.top]:
        cand = f["candidates"][0] if f["candidates"] else "(no invoice line resembles it)"
        print(f"{f['name'][:43]:<44}{f['live_rev_4wk']:>13,}{f['products']:>10}   {cand[:60]}")
    if len(found) > a.top:
        print(f"    ... and {len(found) - a.top} more (--top {len(found)} for all)")

    if a.rebase:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {"never_invoiced": len(found),
             "ruled_by": "Zak 2026-08-20: all live items must have invoices",
             "note": "ingredients under live sellers whose cost history is "
                     "seeds only. May fall, may not rise. The candidates "
                     "column names invoice-fed rows that resemble each one — "
                     "closing a finding is usually a product_map.csv bridge, "
                     "not a new invoice."},
            indent=1) + "\n")
        print(f"\npinned at {len(found)}")
        return 0

    if a.strict and len(found) > base.get("never_invoiced", len(found)):
        print(f"\n::error::{len(found) - base['never_invoiced']} more live "
              f"ingredient(s) with no invoice behind them. A product went on "
              f"sale standing on a seed — find its invoice while the delivery "
              f"is still in the coolroom.")
        return 1
    if a.strict and classes and unclassified:
        print(f"\n::error::{len(unclassified)} live ingredient(s) carry NO class "
              f"in data/coverage_classes.yaml. Rock-solid means every open item "
              f"names whose move it is — bridge it, or classify it with the "
              f"reason. 'Nobody has looked' is the one state that is not allowed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
