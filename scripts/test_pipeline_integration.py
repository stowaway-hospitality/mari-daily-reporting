#!/usr/bin/env python3
"""
End-to-end integration battletest: invoice -> cogs_list -> costs / ingredients /
compare -> recipes -> GP. Unit tests prove each STAGE in isolation; this proves
the SEAMS between them hold on the real committed data — which is where the
expensive bugs live, because every stage passes its own tests while the join
silently breaks.

Real seams it guards (each has already bitten this project once):

  1. A recipe's saved ingredient id must be a key the cost engine actually has.
     (A recipe saved 'foodlink-102689' while costs used 'foodlink:102689' — the
     dash/colon split made the dish uncostable, and nothing failed.)
  2. costs.csv and ingredients.json must agree on a key's unit — they are built
     by the same resolver, so a divergence means one path regressed.
  3. Every sub-recipe a dish references must exist, or the dish can't be costed.
  4. Every current recipe must actually cost without raising.
  5. /pricing and the recipe book must resolve a line to the SAME unit family,
     now that both go through resolve_pack — a divergence means the shared
     resolver got forked again.

    python3 scripts/test_pipeline_integration.py     # exit 0 = seams hold

Regenerates the derived feeds from committed data/cogs_list.csv first (they are
deterministic), so it never depends on a stale build.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def _regen() -> None:
    for gen in ("modules/recipes/pipeline/build_costs.py",
                "modules/recipes/pipeline/build_ingredients.py",
                "modules/invoices/build_price_compare.py"):
        r = subprocess.run([sys.executable, str(ROOT / gen)],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode:
            print(f"  regen FAILED: {gen}\n{r.stderr}")
            sys.exit(2)


def main() -> int:
    if not (ROOT / "data" / "cogs_list.csv").exists():
        print("no cogs_list.csv — skipping integration battletest")
        return 0
    _regen()

    from core.domain import CostSeries, load_cost_observations
    from modules.recipes.cost import load_recipes, cost_on, MissingCost, CircularRecipe
    from modules.recipes.pipeline.build_ingredients import resolve_pack

    costs = CostSeries(load_cost_observations())
    today = date.today()
    VENUES = ("stowaway", "harry_gatos", "marilynas")

    # cost keys + the unit IN FORCE.
    #
    # setdefault took the FIRST row in the file, which is the OLDEST observation.
    # Every consumer — load_our_costs, CostSeries, the recipe engine — takes the
    # LATEST. So the seam test was reading the opposite end of the history from
    # the code it exists to protect, and it failed on four products whose unit
    # legitimately improved: Barrel One Coffee Concentrate was seeded from the BO
    # export as one $15.26 "can" on 1 Jan and re-seeded per millilitre on 2 Jan.
    # Nothing reads that superseded can row. The mismatch was in the test.
    #
    # (A ProductID whose cost history changes DIMENSION is still worth knowing
    # about — costing a past day could hit the old unit. audit_book reports it;
    # it is a history question, not a seam that breaks today's numbers.)
    _latest: dict[str, tuple[str, str, str]] = {}
    for r in csv.DictReader((ROOT / "data" / "costs.csv").open(encoding="utf-8-sig")):
        k, d = r["ingredient"], r["observed_on"]
        if k not in _latest or d >= _latest[k][0]:
            _latest[k] = (d, r["unit"], r["cost_per_unit"])
    cost_unit: dict[str, str] = {k: v[1] for k, v in _latest.items()}
    cost_rate: dict[str, str] = {k: v[2] for k, v in _latest.items()}

    # ---- SEAM 1: every recipe line id resolves to a cost key -----------------
    #
    # RESOLVES TO, not equals. A unit word the PDF parse bled onto a supplier code
    # is no longer part of identity (core.domain.normalize_code), so four lines
    # saved before that fix hold "fresh-fruit-team:ONBRKG KILOGRAM" while the cost
    # book now writes "fresh-fruit-team:ONBRKG". CostSeries canonicalises both
    # sides precisely so a recipe is never snapped by a rename, and this seam has
    # to ask the same question the engine asks or it reports a break that is not
    # one. An id that canonicalises to nothing still fails, which is the check.
    from core.domain import canonical_purchasable
    cost_unit = {canonical_purchasable(k): v for k, v in cost_unit.items()}
    cost_rate = {canonical_purchasable(k): v for k, v in cost_rate.items()}
    orphan = []
    for v in VENUES:
        for rec in load_recipes(v):
            for ln in rec.lines:
                if ln.subrecipe:
                    continue
                if ln.ingredient and canonical_purchasable(ln.ingredient) not in cost_unit:
                    orphan.append(f"{v}/{rec.product} -> {ln.ingredient!r}")
    check("every recipe ingredient id is a real cost key", not orphan,
          f"{len(orphan)} orphan(s): {orphan[:3]}")

    # ---- SEAM 2: costs.csv unit == ingredients.json unit --------------------
    ing = json.loads((ROOT / "data" / "ingredients.json").read_text())
    items = ing["ingredients"] if isinstance(ing, dict) else ing
    # Compare in BASE units and compare the RATE, not the unit string.
    #
    # A kg and a g are the same dimension at a different scale, and every consumer
    # normalises them at read time (_to_base) — so "costs=kg ingredients=g" was
    # only ever a spelling difference, and pack_overrides.yaml pins the Berry Man
    # passionfruit in kg deliberately (the bridge re-expresses the invoice in the
    # seed's own unit; forcing g there brought back a 12x undercost).
    #
    # What DOES matter is whether the two files agree on the price, and a string
    # compare could never see that. So: convert both sides to g/ml, then require
    # the same base unit AND the same rate. Strictly stronger than what it
    # replaces — a real 12x disagreement would now fail, where before it passed
    # as long as both files happened to say "kg".
    def _base(rate, unit):
        u = (unit or "").lower()
        try:
            v = float(rate)
        except (TypeError, ValueError):
            return None, u
        if u in ("kg", "l", "lt", "litre"):
            return v / 1000.0, ("g" if u == "kg" else "ml")
        return v, u

    unit_split = []
    for i in items:
        iid = i["id"]
        if not i.get("pack_unit") or iid not in cost_unit:
            continue
        cr, cu = _base(cost_rate[iid], cost_unit[iid])
        ir, iu = _base(i.get("cost_per_base_unit"), i.get("pack_unit"))
        if cu != iu:
            unit_split.append(f"{iid} costs={cost_unit[iid]} ingredients={i.get('pack_unit')}")
        elif cr and ir and abs(cr - ir) > max(1e-9, 0.005 * max(cr, ir)):
            unit_split.append(f"{iid} costs=${cr:.6f}/{cu} ingredients=${ir:.6f}/{iu}")
    check("costs.csv and ingredients.json agree on unit", not unit_split,
          f"{len(unit_split)} split(s): {unit_split[:3]}")

    # ---- SEAM 3: every sub-recipe reference exists --------------------------
    missing_sub = []
    for v in VENUES:
        recs = load_recipes(v)
        prods = {r.product for r in recs}
        for r in recs:
            for ln in r.lines:
                if ln.subrecipe and ln.subrecipe not in prods:
                    missing_sub.append(f"{v}/{r.product} -> {ln.subrecipe}")
    check("every sub-recipe reference resolves", not missing_sub,
          f"{missing_sub[:3]}")

    # ---- SEAM 4: every current recipe costs without raising -----------------
    raised = []
    for v in VENUES:
        recs = load_recipes(v)
        latest: dict[str, object] = {}
        for r in recs:
            cur = latest.get(r.product)
            if cur is None or (r.effective_from or date.min) >= (cur.effective_from or date.min):
                latest[r.product] = r
        for r in latest.values():
            try:
                cost_on(r, costs, today, price_mode="rolling", recipes=recs)
            except (MissingCost, CircularRecipe) as e:
                raised.append(f"{v}/{r.product}: {str(e)[:60]}")
    check("every current recipe costs without raising", not raised,
          f"{raised[:3]}")

    # ---- SEAM 5: /pricing and the recipe feed share the resolver ------------
    # Both go through resolve_pack now. Confirm the price-compare build imports the
    # same function object, not a forked copy — a fork is how the units drift.
    import modules.invoices.build_price_compare as bpc
    check("price-compare uses the recipe resolver (single source of truth)",
          bpc.resolve_pack is resolve_pack)

    # ---- SEAM 6: recipe pricing is INDEPENDENT of the Xero push --------------
    # An invoice's prices must reach the recipe COGS book from PARSING alone — the
    # moment it validates — never gated on being approved or pushed to Xero. That
    # keeps the recipe/costing side alive even if the "dext-replacer" accounting
    # (the Xero queue + approval poller) is never used. Guard it at the source:
    # the pricing chain must not import the push/approval machinery, and must not
    # filter cogs rows on approval state. A future "only cost approved invoices"
    # change would couple them silently — this fails it loudly.
    import re as _re
    push_markers = ("xero_push", "xero_process_approvals", "build_invoice_queue",
                    "xero_invoice_id", "AUTHORISED", "approved")
    coupled = []
    for rel in ("modules/invoices/build_cogs_list.py",
                "modules/recipes/pipeline/build_costs.py",
                "modules/recipes/cost.py"):
        src = (ROOT / rel).read_text()
        hits = [m for m in push_markers if _re.search(rf"\b{_re.escape(m)}\b", src)]
        if hits:
            coupled.append(f"{rel}: {hits}")
    check("recipe pricing chain never references the Xero push / approval state",
          not coupled, f"{coupled}")

    print()
    print("ALL SEAMS HOLD" if not fails else f"{len(fails)} SEAM(S) BROKEN")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
