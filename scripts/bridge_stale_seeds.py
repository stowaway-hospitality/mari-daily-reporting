#!/usr/bin/env python3
"""
Find Lightspeed products whose cost is still a STALE SEED while we hold real
invoices for the same physical product under a supplier identity, and bridge them.

    python3 scripts/bridge_stale_seeds.py            # review candidates
    python3 scripts/bridge_stale_seeds.py --apply    # write data/product_map.csv rows

THE BUG THIS FIXES
------------------
One product, two identities. The recipe book names "Prosciutto Sliced [500g]"
(lightspeed:22480046) and the only cost it ever had is a January scrape seed of
$45.71/kg. Meanwhile B&E have invoiced the same prosciutto 24 times, most
recently at $28.00/kg (b-e:10796) — 39% cheaper. Nothing joined the two, so the
chef's picker quoted the stale number and every recipe using it was over-costed.

That is the whole point of this project: the price must come from the invoice.

HOW THE JOIN IS MADE (evidence, not guesswork)
----------------------------------------------
A candidate needs the normalised product names to agree — one being a prefix of
the other after norm() strips sizes/units ("prosciutto sliced" vs "prosciutto
sliced 500g"). Suppliers who name things completely differently ("B Flute Lock
Top 13\" Pizza Boxes x 50" for "Large Pizza Box 13\"") cannot be matched on name
and are listed as UNMATCHED for a human to map by hand.

Writing a product_map row is all that is required: build_costs already re-emits
each invoice under the mapped ProductID, and because invoices are dated recent
they win the as-of lookup over the seed automatically.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from convert_lightspeed_recipes import norm  # noqa: E402

COSTS = ROOT / "data" / "costs.csv"
PRODUCT_MAP = ROOT / "data" / "product_map.csv"

SEED_SOURCES = ("ls-recipe-seed", "bo-seed", "recipe-bridge-seed", "bo-ingredient-seed")
DRIFT_LIMIT = 60.0   # percent; beyond this a "price move" is really a pack clash
MAP_FIELDS = ["supplier", "supplier_code", "product_id", "product_name", "venue",
              "bo_cost", "invoice_cost", "delta", "confidence", "source_invoice", "invoice_date"]


def load_costs():
    rows = list(csv.DictReader(COSTS.open(encoding="utf-8-sig")))
    ls, sup = defaultdict(list), defaultdict(list)
    for r in rows:
        (ls if r["ingredient"].startswith("lightspeed:") else sup)[r["ingredient"]].append(r)
    return ls, sup


def is_seed(r):
    return str(r.get("source_invoice") or "").startswith(SEED_SOURCES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ls, sup = load_costs()

    # Lightspeed products whose every observation is a seed — no invoice has ever
    # reached them, so whatever they quote is as old as the scrape.
    stale = {k: v for k, v in ls.items() if v and all(is_seed(r) for r in v)}

    # supplier items, newest observation each
    latest_sup = {}
    for k, v in sup.items():
        r = max(v, key=lambda x: x["observed_on"])
        latest_sup[k] = r

    existing = set()
    if PRODUCT_MAP.exists():
        for r in csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")):
            existing.add((r.get("supplier_code"), r.get("product_id")))

    cands, unmatched, review = [], [], []
    for lid, obs in sorted(stale.items()):
        seed = max(obs, key=lambda x: x["observed_on"])
        ln = norm(seed.get("description") or "")
        if not ln:
            continue
        hits = []
        for sid, r in latest_sup.items():
            sn = norm(r.get("description") or "")
            if not sn:
                continue
            if sn == ln or sn.startswith(ln) or ln.startswith(sn):
                if min(len(sn), len(ln)) >= 6:
                    hits.append((sid, r))
        if not hits:
            unmatched.append((lid, seed.get("description"), 0))
            continue
        # Several suppliers can carry the same product (prosciutto: Andrews once,
        # B&E 24 times). The one we actually BUY is the right cost, so prefer the
        # most recently invoiced, then the most frequently invoiced.
        hits.sort(key=lambda h: (h[1]["observed_on"], len(sup[h[0]])), reverse=True)
        sid, r = hits[0]
        pid = lid.split(":", 1)[1]
        if (r_code := sid.split(":", 1)[1]) and (r_code, pid) in existing:
            continue
        # only bridge when the units agree — build_costs needs a common basis
        if r["unit"] != seed["unit"]:
            unmatched.append((lid, f"{seed.get('description')} (unit {seed['unit']} vs {r['unit']})", -1))
            continue
        seed_c, inv_c = float(seed["cost_per_unit"]), float(r["cost_per_unit"])
        drift = (inv_c - seed_c) / seed_c * 100 if seed_c else 0
        # A seed and an invoice for the SAME pack should be within a normal price
        # move of each other. 50x apart (Angostura $0.0067 vs $0.3356 per ml) is a
        # pack-size disagreement, not a price rise — bridging it would import the
        # error. Park those for review instead of shipping a wrong number.
        if abs(drift) > DRIFT_LIMIT:
            review.append((seed.get("description"), seed_c, inv_c, drift, sid))
            continue
        cands.append(dict(
            supplier=r["ingredient"].split(":", 1)[0], supplier_code=r_code,
            product_id=pid, product_name=seed.get("description", ""), venue=r.get("venue", ""),
            bo_cost=f"{seed_c:.6f}", invoice_cost=f"{inv_c:.6f}",
            delta=f"{drift:+.1f}%", confidence="name-prefix",
            source_invoice=r.get("source_invoice", ""), invoice_date=r.get("observed_on", ""),
        ))

    print(f"{len(stale)} Lightspeed products are still priced from a stale seed")
    print(f"{len(cands)} can be bridged to a real invoice by name:\n")
    print(f"{'seed $':>10} {'invoice $':>10} {'drift':>8}  product")
    for c in sorted(cands, key=lambda x: -abs(float(x["delta"].rstrip('%')))):
        print(f"{float(c['bo_cost']):10.4f} {float(c['invoice_cost']):10.4f} {c['delta']:>8}  "
              f"{c['product_name'][:44]}  <- {c['supplier']}:{c['supplier_code']}")
    print(f"\n{len(review)} matched by name but the prices disagree too much to trust —\n  a pack-size clash to resolve by hand, NOT applied:")
    for d, sc, ic, dr, sid in sorted(review, key=lambda x: -abs(x[3])):
        print(f"    {str(d)[:40]:42} seed ${sc:<10.4f} inv ${ic:<10.4f} {dr:+.0f}%  <- {sid}")
    print(f"\n{len(unmatched)} could not be matched on name (map by hand if they matter):")
    for lid, d, n in unmatched[:20]:
        print(f"    {str(d)[:52]:54} ({'ambiguous' if n > 1 else 'no match' if n == 0 else 'unit clash'})")

    if args.apply and cands:
        rows = []
        if PRODUCT_MAP.exists():
            rows = list(csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")))
        rows.extend(cands)
        with PRODUCT_MAP.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=MAP_FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"\napplied -> data/product_map.csv now {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
