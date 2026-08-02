#!/usr/bin/env python3
"""
Build data/costs.csv — the cost fact table.

    python3 modules/recipes/pipeline/build_costs.py

WHAT THIS IS
------------
One row per dated, evidenced price observation, IN THE UNIT A RECIPE USES:

    ingredient, observed_on, cost_per_unit, unit, venue, source_invoice, pack

Append-only in spirit: it is derived from invoices, and an invoice is a fact.
Rebuilding it must reproduce it (CI checks this).

WHY IT EXISTS — a real 5000x bug
--------------------------------
ARCHITECTURE.md decision 2 says costs are dated observations. I built the
as-of lookup and then fed it data/cogs_list.csv directly, which quotes prices
PER PACK ($57.00 for a 5kg box of squid, basis 'unit'). A recipe says "200 g".
Multiplying those gave $11,400 per serve — arithmetically perfect, physically
absurd. Exactly the class of error the invoice validator exists to stop, and I
walked into it one layer up.

The lesson is not "add a check" (though cost_on now refuses on unit mismatch).
It is that the cost feed must publish the unit the consumer uses. A pack price
is not a gram price and no amount of care downstream fixes that.

So: pack cost / pack size -> cost per gram/ml/each, with the pack recorded so
the arithmetic is auditable. Where the pack cannot be read confidently, the
row is SKIPPED, not guessed — see build_ingredients.py for why (camembert
parsed to $364/kg on its first run).
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.domain import purchasable_id                                   # noqa: E402
from core.pack_overrides import load_pack_overrides                      # noqa: E402
from modules.recipes.pipeline.build_ingredients import resolve_pack      # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "data" / "costs.csv"
PACK_OVERRIDES = ROOT / "data" / "pack_overrides.yaml"
PRODUCT_MAP = ROOT / "data" / "product_map.csv"


def load_bridge() -> dict:
    """
    supplier:code  ->  lightspeed:<ProductID>, from data/product_map.csv.

    This is the seam that makes a REAL INVOICE update a beverage's cost. Beverage
    costs are seeded from the Lightspeed export keyed by ProductID; invoices arrive
    keyed by supplier code. The map links the two, so an invoice line's cost is
    ALSO emitted under the bottle's ProductID identity — and since the seed is
    dated in the past, the newer invoice observation wins the as-of lookup. One
    bottle, one identity, invoices keep it current. Evidence-based (each row was a
    real invoice line matched to a real export product), never fuzzy at read time.
    """
    if not PRODUCT_MAP.exists():
        return {}
    out = {}
    for r in csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")):
        sup, code, pid = r.get("supplier"), r.get("supplier_code"), r.get("product_id")
        if sup and code and pid:
            out[purchasable_id(sup, code)] = f"lightspeed:{pid.strip()}"
    return out

FIELDS = ["ingredient", "observed_on", "cost_per_unit", "unit", "venue",
          "source_invoice", "pack", "description"]


def main() -> int:
    overrides = load_pack_overrides(PACK_OVERRIDES)   # chef-confirmed pack sizes
    bridge = load_bridge()                            # supplier:code -> lightspeed:ProductID
    cogs_rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))

    # The BO seed defines each bottle's cost UNIT and the divisor to reach it (Aperol
    # = a 700 ml bottle -> $/ml, so divisor 700, unit "ml"; a beer -> $/can, divisor
    # 1). When we bridge an INVOICE cost onto that ProductID we must express it the
    # SAME way, or the bottle carries two costs in two units and a recipe can't read
    # the newer one. Take the seed's OWN resolved (qty, unit) — not its raw pack_unit,
    # which can differ ("each" vs the resolved "can").
    seed_conv: dict[str, tuple[Decimal, str]] = {}
    for r in cogs_rows:
        if (r.get("source_invoice") or "").startswith("bo-seed"):
            pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
            try:
                q, u, _p, _h, _b = resolve_pack(
                    r["invoice_description"].strip(), Decimal(r["cost_per_unit_incl_gst"]),
                    basis=r.get("basis", ""), note=r.get("note", ""),
                    code=(r.get("supplier_code") or "").strip())
                if q and u:
                    seed_conv[pid] = (q, u)
            except Exception:
                pass
        # a confirmed recipe-bridge baseline records its own resolved unit directly
        # (Zak-confirmed), so a future invoice for the mapped supplier code can be
        # emitted onto this food ProductID in the same unit and supersede it.
        elif (r.get("source_invoice") or "").startswith("recipe-bridge-seed"):
            pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
            try:
                seed_conv[pid] = (Decimal("1"), (r.get("pack_unit") or "").strip())
            except Exception:
                pass

    rows, skipped, bridged = [], [], 0
    for r in cogs_rows:
        code = (r.get("supplier_code") or "").strip()
        if not code:
            skipped.append((r["supplier"], r["invoice_description"], "no supplier_code — no identity"))
            continue

        iid = purchasable_id(r["supplier"], code)
        desc = r["invoice_description"].strip()
        pack_cost = Decimal(r["cost_per_unit_incl_gst"])

        # ONE resolver for every line — liquor, weight-priced produce, packs,
        # discrete units — reading the invoice's basis + note, not just the
        # description. Refuses (skips) exactly when the ingredient UI would flag.
        #
        # Pass `code` too — the UI (build_ingredients) does, and some suppliers
        # (Fresh Fruit Team) encode the sold unit in the code's trailing word
        # ("ONBRKG Kilogram", "TCPUN Punnet"). Without it, resolve_pack couldn't
        # read those, so the cost engine SKIPPED them while the picker showed a
        # price — a recipe using that ingredient then costed to null (Onion Jam did
        # exactly this). Now both read the code, so their identities and costs agree.
        qty, unit, per, how, bad = resolve_pack(
            desc, pack_cost, basis=r.get("basis", ""), note=r.get("note", ""), code=code)
        # A confirmed pack (chef or catalogue) is AUTHORITATIVE — it wins even over
        # a resolved pack, so it can CORRECT a wrong one (a box of loose produce
        # that parsed to "1 box" becomes the real weight). Must match build_ingredients.
        if iid in overrides:
            oq, ou = overrides[iid]
            qty, unit, bad, how = oq, ou, "", "chef-confirmed"
            per = (pack_cost / oq).quantize(Decimal("0.000001"))
        if not qty or not unit:
            skipped.append((r["supplier"], desc, f"pack unreadable ({how})"))
            continue
        if bad:
            skipped.append((r["supplier"], desc, bad))   # arithmetically fine, physically absurd
            continue

        row = dict(
            ingredient=iid,
            observed_on=r["invoice_date"], cost_per_unit=str(per), unit=unit,
            venue=r.get("venue") or "", source_invoice=r.get("source_invoice", ""),
            pack=how, description=desc,
        )
        rows.append(row)
        # BRIDGE: if this supplier code is a known bottle (product_map), ALSO record
        # the cost under its ProductID identity, so the invoice supersedes the BO
        # seed and any recipe referencing the bottle by ProductID stays current.
        # Convert into the SEED's unit (bottle price / 700 ml -> $/ml) so the two
        # observations are comparable and the newer (invoice) one wins the as-of
        # lookup. Skip seed rows themselves; skip if the size is unknown or units
        # can't reconcile (never emit a wrong-unit cost).
        pid = bridge.get(iid)
        if pid and not iid.startswith("lightspeed:"):
            sc = seed_conv.get(pid)
            if sc and sc[0] > 0:
                sqty, sunit = sc
                if sunit == unit:                       # already the seed's unit
                    bper = per
                elif unit in ("bottle", "keg", "can", "ea", "each"):
                    # invoice priced per whole selling unit; the seed splits that
                    # unit into sqty of sunit (700 ml). $/sunit = whole cost / sqty.
                    bper = (pack_cost / sqty).quantize(Decimal("0.000001"))
                else:
                    bper = None                         # units don't reconcile — skip
                if bper is not None:
                    rows.append({**row, "ingredient": pid, "unit": sunit,
                                 "cost_per_unit": str(bper), "pack": f"{how} (via {iid})"})
                    bridged += 1

    rows.sort(key=lambda x: (x["ingredient"], x["observed_on"]))
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} cost observations -> {OUT.relative_to(ROOT)}")
    print(f"  {bridged} invoice costs also bridged to a ProductID identity")
    print(f"  skipped {len(skipped)} (not guessed — see below)")
    for s, d, why in skipped[:8]:
        print(f"    {s:<13} {d[:34]:<36} {why[:60]}")
    print("\nsample:")
    for r in rows[:6]:
        print(f"  {r['ingredient']:<22} {r['observed_on']}  ${r['cost_per_unit']:>10}/{r['unit']:<6} "
              f"(pack {r['pack']}, inv {r['source_invoice']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
