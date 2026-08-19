#!/usr/bin/env python3
"""
A correction that has stopped correcting anything.

    python3 scripts/check_declarations_bind.py            # report
    python3 scripts/check_declarations_bind.py --strict   # exit 1 if it got worse

THE DEFECT. This repo holds its hard-won rulings as DECLARATIONS: a pack size in
data/pack_overrides.yaml, a unit relabel in data/batch_yield_units.yaml. Each one
is a fact somebody established once — read off an invoice, weighed, or ruled by
Zak — written down with its arithmetic so it never has to be established again.

Every one of them binds to a record it does not own. `pack_overrides` keys on a
purchasable id; `line_qty_unit_fixes` keys on a recipe name, an ingredient name
AND a quantity. Chefs edit those records daily in the builder. Suppliers rename
products. Nothing tells the declaration its target moved.

So a declaration can go dead: still sitting in the file, still carrying its
paragraph of evidence, still looking for all the world like the question is
settled -- and matching nothing. Reading the file tells you it is fixed. It is
not fixed.

THREE OF THESE IN ONE WEEK, all found by accident:

  * The Tandoori relabel was written against "1,000 g chicken + 1 ml sauce".
    Renan re-saved the recipe as "1,700 g + 400 ml". `from_qty: 1` stopped
    matching that day. Six products costed at up to $187.76 against a $19.50
    menu price, main went red on every commit, and a previous session recorded
    -- correctly, and without knowing why -- that declaring the fix "does
    relabel the line but does not move the cost".

  * The same file was read ONLY by the staged book. The live converter, which
    is what the P&L reads, had never opened it. Every fix in it was dead where
    it mattered.

  * Corn chips: the pack size was in the 10 July invoice description and gone
    from the two after it. Nothing noticed the product had stopped declaring
    its own size, so it sat at a third of its real price since January.

The cost was not the errors. It was that each one was already SOLVED, and the
solution had quietly detached.

WHAT COUNTS AS BOUND. A declaration is bound if the thing it names exists and
its stated `from` still describes it. A pack override is bound if its id is a
live ingredient. A line fix is bound if the recipe holds that ingredient at the
unit the fix expects -- or has already been relabelled by it (unit_was).

WHAT IS DELIBERATELY ALLOWED. Not every unbound declaration is a defect:

  * A PRE-DECLARATION. Two corn chip bag records were given sizes today for
    stock items we hold but have never been invoiced for. They are answers
    waiting for a question, which is the right way round.
  * A DISCONTINUED LINE. Barebones Clear Ice and 4 Pines Kolsch are gone; their
    declarations are history, not error, and this repo does not delete history.
  * A SUPERSEDED ENTRY in an append-only log. `ilg:460-1639` appears twice on
    purpose -- Zak's "case of 12" and the correction that the invoice prices one
    1.25 L bottle. Last wins; the earlier one is a record of the reasoning.

So this does not fail on unbound declarations. It RATCHETS them: the count is
pinned, and CI fails when it GROWS. A new dead declaration means one of two
things happened since the pin -- a record moved out from under a ruling, or a
ruling was written that never bound at all -- and both want a person, today,
while the change that caused it is still in someone's head.

Pin the new count with --rebase, and put the reason in the commit message.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines" / "declaration_binding.json"


def _ingredient_ids() -> set[str]:
    """Every id a pack override can land on.

    NOT just ingredients.json. A pack override is applied in build_costs.py to
    rows keyed by purchasable_id, and ingredients.json only shows the SURVIVOR
    of an identity collapse -- so an override on a supplier id that has been
    bridged to a Lightspeed anchor is doing its job while being invisible there.
    Checking the wrong file called four live overrides dead, among them Zak's
    Coke Zero ruling. A detector that cries wolf gets switched off, which is
    the failure mode this whole script exists to prevent.
    """
    ids: set[str] = set()
    p = ROOT / "data" / "ingredients.json"
    if p.exists():
        d = json.loads(p.read_text())
        ids |= {i["id"] for i in (d["ingredients"] if isinstance(d, dict) else d)}
    c = ROOT / "data" / "costs.csv"
    if c.exists():
        import csv
        ids |= {r["ingredient"] for r in csv.DictReader(c.open(encoding="utf-8-sig"))}
    return ids


def _book() -> dict:
    p = ROOT / "data" / "lightspeed_recipes_costed.json"
    return json.loads(p.read_text())["recipes"] if p.exists() else {}


def unbound() -> list[dict]:
    """Every declaration that currently describes nothing."""
    out: list[dict] = []
    ids, book = _ingredient_ids(), _book()

    ov = ROOT / "data" / "pack_overrides.yaml"
    if ov.exists() and ids:
        # An append-only log: only the LAST entry for an id is live, so an
        # earlier one being unbound is not a finding -- it is the audit trail.
        seen_last: dict[str, dict] = {}
        for d in (yaml.safe_load(ov.read_text(encoding="utf-8-sig")) or []):
            if isinstance(d, dict) and d.get("id"):
                seen_last[str(d["id"]).strip()] = d
        for _id, d in seen_last.items():
            if _id not in ids:
                out.append({"kind": "pack_override", "target": _id,
                            "detail": f"{d.get('pack_qty')} {d.get('pack_unit')}",
                            "why": "no ingredient and no cost row with this id"})

    by = ROOT / "data" / "batch_yield_units.yaml"
    if by.exists() and book:
        doc = yaml.safe_load(by.read_text(encoding="utf-8-sig")) or {}

        for f in (doc.get("line_qty_unit_fixes") or []):
            r = book.get(f["recipe"])
            if r is None:
                out.append({"kind": "line_qty_unit_fix", "target": f["recipe"],
                            "detail": f["ingredient"], "why": "no such recipe"})
                continue
            # Bound if the line is still as declared, or has already been
            # relabelled BY this declaration (unit_was carries the original).
            hit = any(
                ln.get("name") == f["ingredient"]
                and (ln.get("unit") == f["from_unit"]
                     or ln.get("unit_was") == f["from_unit"])
                for ln in (r.get("ingredients") or []))
            if not hit:
                have = [f"{ln.get('qty')} {ln.get('unit')}"
                        for ln in (r.get("ingredients") or [])
                        if ln.get("name") == f["ingredient"]]
                out.append({
                    "kind": "line_qty_unit_fix", "target": f["recipe"],
                    "detail": f"{f['ingredient']} @ {f['from_qty']} {f['from_unit']}",
                    "why": f"line now reads {have}" if have else "line not in recipe"})

        for f in (doc.get("line_unit_fixes") or []):
            if f.get("recipe") == "*":
                continue                       # applies wherever it appears
            r = book.get(f["recipe"])
            if r is None or not any(ln.get("name") == f["ingredient"]
                                    for ln in (r.get("ingredients") or [])):
                out.append({"kind": "line_unit_fix", "target": f["recipe"],
                            "detail": f["ingredient"],
                            "why": "recipe or line gone"})

        for f in (doc.get("yield_unit_fixes") or []):
            if f["batch"] not in book:
                out.append({"kind": "yield_unit_fix", "target": f["batch"],
                            "detail": f"{f.get('from_unit')} -> {f.get('to_unit')}",
                            "why": "no such batch"})


    # ---- venue-resolution rules that never see their signal -----------------
    #
    # suppliers.yaml declares how to tell one venue's invoice from another's:
    #
    #     venue_resolution.by_supplier.ilg.account_codes:
    #       '2428': stowaway
    #       '3622': harry_gatos
    #
    # A rule with an answer in it. EXTRACTION.md carries the worked example --
    # two Select Fresh invoices, same day, same address, same delivery code,
    # and only the account code telling them apart -- and instructs the
    # extractor to emit `unknown` rather than guess.
    #
    # Not one invoice from any of those suppliers has ever carried an
    # account_code. 173 of them, and every single ILG invoice came back
    # "stowaway" -- never once "unknown". So Harry Gatos' entire liquor spend
    # has been landing on Stowaway's books, and HG's bar has been costed off
    # January seeds because its own invoices were filed under the other venue.
    #
    # Zak, 2026-08-19: "hg ilg invoices are definitely in the pipeline, you
    # just aren't reading them." He was right, and this is where.
    #
    # Same shape as every other finding in this file: the rule exists, is
    # correct, and reaches nothing.
    sup = ROOT / "modules" / "invoices" / "suppliers.yaml"
    inv_dir = ROOT / "data" / "invoices"
    if sup.exists() and inv_dir.is_dir():
        doc = yaml.safe_load(sup.read_text(encoding="utf-8-sig")) or {}
        by_sup = ((doc.get("venue_resolution") or {}).get("by_supplier") or {})
        seen: dict[str, list[int]] = {}
        for f in inv_dir.glob("*.json"):
            try:
                iv = json.loads(f.read_text(encoding="utf-8-sig"))["invoice"]
            except Exception:                                # noqa: BLE001
                continue
            k = iv.get("supplier_key")
            if k not in by_sup:
                continue
            tally = seen.setdefault(k, [0, 0])
            tally[0] += 1
            if not iv.get("account_code"):
                tally[1] += 1
        for k, (total, blind) in sorted(seen.items()):
            if blind:
                out.append({
                    "kind": "venue_resolution", "target": k,
                    "detail": f"{blind}/{total} invoices carry no signal",
                    "why": "the rule that names the venue has never been applied"})

    return sorted(out, key=lambda f: (f["kind"], f["target"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if more declarations are unbound than the pin")
    ap.add_argument("--rebase", action="store_true",
                    help="pin the current count as the new baseline")
    a = ap.parse_args()

    found = unbound()
    base = (json.loads(BASELINE.read_text()) if BASELINE.exists()
            else {"unbound": 0, "pinned": None})

    by_kind: dict[str, int] = {}
    for f in found:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    print(f"unbound declarations: {len(found)} (baseline {base['unbound']}, "
          f"pinned {base.get('pinned')})")
    for k, v in sorted(by_kind.items()):
        print(f"  {k:<20}{v}")
    for f in found:
        print(f"    {f['kind']:<20}{f['target'][:34]:<36}{f['detail'][:26]:<28}{f['why']}")

    if a.rebase:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {"unbound": len(found), "pinned": "rebased",
             "note": "count of declarations that currently describe nothing; "
                     "see scripts/check_declarations_bind.py for why this is "
                     "ratcheted rather than failed outright"},
            indent=1) + "\n")
        print(f"pinned at {len(found)}")
        return 0

    if a.strict and len(found) > base["unbound"]:
        print(f"::error::{len(found) - base['unbound']} declaration(s) stopped "
              f"binding. A ruling with evidence behind it is now correcting "
              f"nothing — find what moved, or --rebase with the reason.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
