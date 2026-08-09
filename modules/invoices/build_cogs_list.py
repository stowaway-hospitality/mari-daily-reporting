#!/usr/bin/env python3
"""
Aggregate validated invoices -> data/cogs_list.csv (the recipe cost feed).

    python3 modules/invoices/build_cogs_list.py

THE MISSING LINK. run.py turns one PDF into one validated invoice JSON in
data/invoices/. Nothing rolled those into cogs_list.csv — so the list was
hand-built during the first sweep, and every new invoice meant manual work
(exactly what Zak watched happen). This closes the loop: a validated invoice in
data/invoices/ becomes rows in the recipe system, no hands.

MERGE, NOT REGENERATE. cogs_list.csv already holds hand-entered rows from the
sweep that have no invoice JSON behind them. This ADDS validated invoice lines
that aren't already present (keyed by invoice_ref + supplier_code/description),
re-derives the ones that are (see below), and leaves everything else untouched.
Idempotent: run twice, same result.

IDEMPOTENT AGAINST ITSELF, NOT AGAINST EVERYONE. That claim only ever covered
rows this script adds. Three rows reached the file another way — Paramount
invoice 5441124: Carpano 10015926, De Bortoli 44583, Sprite 98541, each present
twice at an identical price, date, code and basis and differing only in the
diagnostic `note`. A duplicate is invisible to the as-of lookup and NOT invisible
to CostSeries.rolling, which counts it twice in the trailing-30-day mean.

So the row identity now lives in core.domain.cogs_row_key and the CONSUMER
applies it too (build_costs._read_cogs_rows). A check on the way in can be
bypassed; a check on the way out cannot. The three rows stay in the file —
cogs_list.csv is an append-only fact table and both notes are evidence — and
this script reports them so a bypass is never silent again.

MERGE, AND RE-DERIVE WHAT IT DERIVED. Adding only what was missing made every
parser fix FORWARD-ONLY: the fix reached invoices that arrived after it and
nothing else, because the identity was already present and the row was skipped.
That is how 344 ILG lines kept a case price in a per-bottle field for months
after `units_on_line` was corrected — the correction could not reach them.

A row that came from a validated invoice JSON is DERIVED, and derived values
track their source. So the four fields this script computes from the invoice —
cost_per_unit_incl_gst, pack_qty, pack_unit, cost_per_base_unit — are refreshed
in place when the source JSON now says something different, and every move is
printed. The rest of the row is NOT touched: `lightspeed_product`, `basis` and
`pack_size` are where a human's judgement is recorded (14 ILG rows carry a
hand-set per_bottle / per_can / per_keg basis and a bridged product name), and
re-deriving would silently delete that work. `note` is filled only when blank.
A row with no invoice JSON behind it — the hand-built sweep rows — is left
entirely alone, as before.

Only STOCK lines become cost rows. Freight, fuel levies, WET adjustments and
'waiting on stock' lines are excluded — they are not ingredients.

The `supplier` column must be the SHORT name the recipe pipeline recognises
(build_ingredients.KITCHEN_SUPPLIERS), not the long legal name on the invoice —
so a per-supplier alias lives here. A supplier with no alias falls back to its
display name and simply won't be treated as a kitchen good until added.
"""

from __future__ import annotations

import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.domain import cogs_row_key   # noqa: E402  the ONE definition of a row's identity

ROOT = Path(__file__).resolve().parents[2]
INVOICES = ROOT / "data" / "invoices"          # PASS invoices from run.py
COGS = ROOT / "data" / "cogs_list.csv"

FIELDS = ["supplier", "supplier_code", "invoice_description", "lightspeed_product",
          "cost_per_unit_incl_gst", "basis", "pack_size",
          "pack_qty", "pack_unit", "cost_per_base_unit",   # canonical $/kg, $/L, $/each
          "venue", "source_invoice", "invoice_date", "in_bounds", "note"]

# supplier_key (suppliers.yaml) -> the short name cogs_list / the recipe
# pipeline uses. Kitchen names here MUST match build_ingredients.KITCHEN_SUPPLIERS.
SUPPLIER_ALIAS = {
    "fresh_fruit_team": "Fresh Fruit Team", "select_fresh": "Select Fresh",
    "be_foods": "B&E", "foodlink": "Foodlink", "gulli": "Gulli",
    "andrews_meat": "Andrews Meat", "aquarius": "Aquarius", "mj_chickens": "M&J Chickens",
    "cookers": "Cookers", "torino": "Torino", "captains_of_trade": "Captains of Trade",
    "ilg": "ILG", "ilg_distribution_coop": "ILG", "paramount": "Paramount",
    "lion": "Lion", "viticult": "Viticult", "nelson_wine": "Nelson",
    "combined_wines": "Combined Wines", "bacchus": "Bacchus", "grifter": "Grifter",
    "philter": "Philter", "young_rashleigh": "Young & Rashleigh",
    "mountain_culture": "Mountain Culture", "four_pines": "4 Pines",
}


# Computed from the invoice by _rows_from_invoice — a stale one is a wrong cost,
# so these track their source. See the module docstring.
DERIVED = ("cost_per_unit_incl_gst", "pack_qty", "pack_unit", "cost_per_base_unit")
# A human's judgement, recorded against the row. Never re-derived.
JUDGED = ("lightspeed_product", "basis", "pack_size")
# A PRICE AND ITS BASIS ARE ONE STATEMENT, so a row may never take half of each.
# "$12.20" means nothing until you know it is per kg and not per bag, and B&E
# 12776 is what taking halves looks like:
#
#   stored   $61.00  basis per_unit   desc "...5MM PREMIUM 5KG BAG"
#            -> the description's 5 kg divides it: $0.0122/g. Correct.
#   parser   $12.20  basis per_kg
#            -> per kg (invoice) divides by 1 kg: $0.0122/g. Also correct.
#   mixed    $12.20  basis per_unit   (price re-derived, basis pinned)
#            -> the description's 5 kg divides a PER-KG price: $0.00244/g.
#               $2.44/kg for chicken breast, five times under, from two readings
#               that are each individually right.
#
# basis stays JUDGED — re-deriving it moved 19 unrelated rows off a per-keg /
# per-bottle / per-can basis a human had chosen — so _cheaper refuses any
# re-derive that would change the basis AND the price together. Neither source
# is wrong; the mixture is, and the mixture is what is forbidden.

# How much cheaper a re-derive may come out before it is held for review. Not a
# judgement about money — it separates two populations that do not overlap.
# Re-deriving through a different division path moves the LAST STORED DIGIT of a
# 4-dp figure ($3.7687 -> $3.7686, 1 part in 40,000); a misread pack moves it by
# a whole pack factor (2x, 6x, 12x — see _cheaper). 1% sits two orders of
# magnitude above the rounding and two below the smallest real error.
HOLD_BAND = Decimal("0.99")


def _cheaper(old: dict, new: dict) -> str:
    """Does this re-derive LOWER the comparable cost? -> why, or "" if not.

    A RE-DERIVE MAY RAISE A COST FREELY AND MAY NOT QUIETLY LOWER ONE. The two
    directions are not symmetric: a cost that comes out too HIGH makes a dish
    look unprofitable and someone goes and looks at it, while a cost that comes
    out too LOW flatters GP and nothing ever asks. This whole re-derive exists to
    let parser fixes reach old rows, and a parser fix is exactly as capable of
    being wrong as the code it replaced. Two of the first four it unblocked were:

      Y&R  Villa Fresco Sangiovese 24 - OPO   $12.06 -> $6.03  (2x low)
          "24" is the VINTAGE in the product name, read as a case count; the
          raw_uom is "C750", a case of 12, so 2 cartons is 24 bottles, not 48.
          Lightspeed's own BO export states $12.06 for the same ProductID.
      Foodlink  FLOUR TORTILLAS 12X91GM      $33.60 -> $5.60  (6x low)
          one CTN-6 split into 6 boxes, against a recipe-bridge-seed row that
          states $33.60 a box, "confirmed bridge from Foodlink 101113".

    Both would have published a lower food cost on the strength of a parser
    nobody had checked. So the comparison is on `cost_per_base_unit` — the
    canonical $/kg, $/L, $/each — because that is the number a recipe actually
    costs off, and it is the one the ILG fix deliberately holds CONSTANT while
    the raw unit price falls sixfold (a case price becoming a bottle price is not
    a cost reduction). Filling a blank is not a move. A changed pack_unit is not
    comparable — $/ea and $/L are different questions — so it is allowed through
    and reported.
    """
    # A price and its basis are ONE statement, and `basis` is JUDGED (not
    # re-derived), so a re-derive that moves the price under a DIFFERENT basis
    # would leave the row holding half of each reading — see the note above
    # DERIVED. Refuse the whole row rather than assemble a false one.
    ob, nb = (old.get("basis") or "").strip(), (new.get("basis") or "").strip()
    op = (old.get("cost_per_unit_incl_gst") or "").strip()
    np_ = (new.get("cost_per_unit_incl_gst") or "").strip()
    if ob and nb and ob != nb and op != np_:
        return (f"basis {ob} -> {nb} while the price moves {op} -> {np_}; the row "
                f"keeps its basis, so it would hold half of each reading")

    ou, nu = (old.get("pack_unit") or "").strip(), (new.get("pack_unit") or "").strip()
    cbu_was, cbu_now = ((old.get("cost_per_base_unit") or "").strip(),
                        (new.get("cost_per_base_unit") or "").strip())
    # Judge on the canonical rate where there IS one to judge on: same pack_unit,
    # stated both sides. Otherwise fall back to the raw unit price — a blank
    # cost_per_base_unit is not permission, it is the absence of a second
    # opinion, and treating it as permission published B&E CHICKEN BREAST at
    # $2.44/kg (the row moved $61.00 -> $12.20/kg while its pack stayed the whole
    # 5 kg line, so the book divided by 5 a second time; every other delivery of
    # the same code states $11.90-$12.20/kg).
    if cbu_was and cbu_now and ou == nu:
        field, was, now, per = "cost_per_base_unit", cbu_was, cbu_now, f" per {nu or 'unit'}"
    else:
        field, was, now, per = "cost_per_unit_incl_gst", (
            old.get("cost_per_unit_incl_gst") or "").strip(), (
            new.get("cost_per_unit_incl_gst") or "").strip(), ""
    if not was or not now:
        return ""                          # a fill, not a move
    try:
        a, b = Decimal(was), Decimal(now)
    except (InvalidOperation, ValueError):
        return ""
    if a <= 0 or b >= a * HOLD_BAND:
        return ""
    return f"{field} {a} -> {b}{per} ({a / b:.2f}x lower)"


def _refresh(old: dict, new: dict) -> tuple[list[str], str]:
    """Bring `old`'s DERIVED fields up to what the invoice now says.

    -> ([what moved], held_reason). A re-derive is never silent — the whole
    failure this fixes was a correction that could not be seen because it could
    not be applied — and it never lowers a cost without being asked (see
    _cheaper). When held, `old` is left exactly as it was."""
    held = _cheaper(old, new)
    if held:
        return [], held
    moved = []
    for f in DERIVED:
        was, now = (old.get(f) or "").strip(), (new.get(f) or "").strip()
        if was == now:
            continue
        old[f] = now
        moved.append(f"{f} {was or '(blank)'} -> {now or '(blank)'}")
    if not (old.get("note") or "").strip() and (new.get("note") or "").strip():
        old["note"] = new["note"]          # fill a blank; never overwrite one
    return moved, ""


def _key(source_invoice: str, code: str, desc: str) -> tuple[str, str]:
    """Identity of a cost row: one line per (invoice, product).

    Delegates to core.domain so the writer and every reader share ONE definition;
    when they were two, a duplicate could satisfy one and not the other."""
    return cogs_row_key(source_invoice, code, desc)


def _load_existing() -> tuple[list[dict], set[tuple[str, str]]]:
    """Existing rows, untouched, plus the set of identities already present.

    Rows are returned AS-IS: this script rewrites the whole file, and dropping a
    duplicate here would delete a fact. Duplicates are reported instead — the
    consumer (build_costs) is where they must not survive, because that is the
    file a rolling average is computed from."""
    if not COGS.exists():
        return [], set()
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    seen: set[tuple[str, str]] = set()
    dupes = []
    for r in rows:
        k = _key(r["source_invoice"], r.get("supplier_code", ""), r["invoice_description"])
        (dupes.append(r) if k in seen else None)
        seen.add(k)
    if dupes:
        print(f"  ⚠ {len(dupes)} duplicate row(s) already in {COGS.name} — one "
              f"(invoice, product) is one row, so something wrote past this script:")
        for r in dupes:
            print(f"      {r.get('source_invoice')} {r.get('supplier_code')} "
                  f"{(r.get('invoice_description') or '')[:34]}  note={r.get('note')!r}")
    return rows, seen


def _rows_from_invoice(payload: dict) -> list[dict]:
    inv = payload["invoice"]
    supplier = SUPPLIER_ALIAS.get(inv.get("supplier_key", ""),
                                  inv.get("supplier_name_raw", inv.get("supplier_key", "")))
    venue = (inv.get("venue") or "unknown")
    ref = inv.get("invoice_ref", "")
    d = inv.get("invoice_date", "")
    out = []
    for ln in inv.get("lines", []):
        if ln.get("line_class") != "stock":        # only real ingredients
            continue
        code = ln.get("supplier_code") or ""
        desc = (ln.get("description") or "").strip()
        price = ln.get("unit_price_incl")
        if price is None:                           # derive per-unit if needed
            tot, qty = ln.get("line_total_incl"), ln.get("qty")
            if tot and qty and str(qty) not in ("0", "0.0"):
                from decimal import Decimal
                price = str((Decimal(str(tot)) / Decimal(str(qty))).quantize(Decimal("0.0001")))
        if price is None:
            continue
        note = "; ".join(ln.get("notes", []) or []) or (ln.get("raw_uom") or "")
        # canonical cost per base unit ($/kg, $/L, $/each) — comparable across suppliers
        from decimal import Decimal, InvalidOperation
        pq, pu = ln.get("pack_qty"), ln.get("pack_unit") or "ea"
        try:
            base = ((Decimal(str(price)) / Decimal(str(pq))).quantize(Decimal("0.0001"))
                    if pq and Decimal(str(pq)) > 0 else Decimal(str(price)))
        except (InvalidOperation, TypeError):
            base = price
        out.append({
            "supplier": supplier,
            "supplier_code": code,
            "invoice_description": desc,
            "lightspeed_product": ln.get("lightspeed_product_name") or "",
            "cost_per_unit_incl_gst": str(price),
            "basis": ln.get("cost_basis") or "per_unit",
            "pack_size": str(ln.get("pack_size") or 1),
            "pack_qty": str(pq or 1),
            "pack_unit": pu,
            "cost_per_base_unit": str(base),
            "venue": venue,
            "source_invoice": ref,
            "invoice_date": d,
            "in_bounds": "yes",                     # only PASS invoices reach here
            "note": note,
        })
    return out


def main() -> int:
    # stdout is output too: under an ASCII locale a single em-dash in a progress
    # line kills the run *after* the fact table is written, so the file is right
    # and the exit code says otherwise. Pin it for the same reason we pin the file.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows, seen = _load_existing()
    by_key = {}
    for r in rows:
        by_key.setdefault(
            _key(r["source_invoice"], r.get("supplier_code", ""), r["invoice_description"]), r)
    added, invoices, refreshed, moves, held = 0, 0, 0, [], []
    for p in sorted(INVOICES.glob("*.json")) if INVOICES.exists() else []:
        try:
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print(f"  skip {p.name}: {e}")
            continue
        invoices += 1
        for row in _rows_from_invoice(payload):
            k = _key(row["source_invoice"], row["supplier_code"], row["invoice_description"])
            if k in seen:
                # Already present — but it was DERIVED from this same invoice, so
                # re-derive it. Skipping here is what made every parser fix
                # forward-only. See the module docstring.
                what, why = _refresh(by_key[k], row)
                if why:
                    held.append((row["supplier"], row["source_invoice"],
                                 row["supplier_code"], row["invoice_description"], why))
                elif what:
                    refreshed += 1
                    moves.append((row["supplier"], row["source_invoice"],
                                  row["supplier_code"], row["invoice_description"], what))
                continue
            seen.add(k)
            by_key[k] = row
            rows.append(row)
            added += 1

    if moves:
        print(f"  {refreshed} row(s) re-derived from their invoice — the parser now "
              f"reads them differently:")
        for sup, ref, code, desc, what in moves:
            print(f"      {sup} {ref} {code} {desc[:32]}")
            for w in what:
                print(f"          {w}")

    if held:
        print(f"  ** {len(held)} re-derive(s) HELD — the invoice now reads CHEAPER than "
              f"the row already in the book, and a lower cost flatters GP. The row is "
              f"unchanged; confirm the parser before letting these through: **")
        for sup, ref, code, desc, why in held:
            print(f"      {sup} {ref} {code} {desc[:40]}")
            print(f"          {why}")

    rows.sort(key=lambda r: (r["invoice_date"], r["supplier"], r["supplier_code"], r["invoice_description"]))
    with COGS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"{added} new rows + {refreshed} re-derived from {invoices} validated "
          f"invoice(s) -> {COGS.relative_to(ROOT)} ({len(rows)} rows total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
