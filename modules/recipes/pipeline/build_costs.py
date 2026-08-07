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
import math
import re
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
    return _extend_bridge_to_p_codes(out)


def _extend_bridge_to_p_codes(bridge: dict) -> dict:
    """ILG bills the same product under two codes: "395-6785" and "395-6785P".

    Seven products in the book carry both, and in every one of the seven the two
    codes share an identical invoice description — APEROL and APEROL, BOMBAY DRY
    GIN and BOMBAY DRY GIN. They are one product with two ILG codes.

    A bridge built from one invoice therefore covers only the code that invoice
    happened to use. Aperol is bridged on "395-6785P" and not on "395-6785", so a
    $215 delivery never reached the book — and the next split like it would be
    silent in the same way.

    Extending a bridge across the pair needs no judgement: the descriptions must
    match exactly, on the same supplier, and only the trailing "P" may differ. It
    is also safe if that ever turns out to be wrong — a bridged liquor line is
    still checked by seed_matched_liquor_cost against the product's OWN seed rate
    and skipped if it disagrees, so a bad pairing yields no cost rather than a
    wrong one."""
    if not COGS.exists():
        return bridge
    desc: dict = {}
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        code = (r.get("supplier_code") or "").strip()
        if not code:
            continue
        try:
            iid = purchasable_id(r["supplier"], code)
        except ValueError:
            continue
        desc.setdefault(iid, set()).add((r.get("invoice_description") or "").strip().upper())

    added = 0
    for iid, pid in list(bridge.items()):
        twin = iid[:-1] if iid.endswith("P") else iid + "P"
        if twin in bridge or twin not in desc or iid not in desc:
            continue
        if desc[iid] and desc[iid] == desc[twin]:
            bridge[twin] = pid
            added += 1
    if added:
        print(f"  bridge extended to {added} ILG P/non-P code twin(s) "
              f"(identical description, same supplier)")
    return bridge

FIELDS = ["ingredient", "observed_on", "cost_per_unit", "unit", "venue",
          "source_invoice", "pack", "description"]

# Mass/volume pack units -> (multiplier to base, base unit). A recipe portions in
# g / ml, so this is the only dimension the liquor discriminator can work in.
_LIQ_BASE = {
    "ML": (Decimal(1), "ml"), "L": (Decimal(1000), "ml"), "LT": (Decimal(1000), "ml"),
    "LTR": (Decimal(1000), "ml"), "LITRE": (Decimal(1000), "ml"),
    "KG": (Decimal(1000), "g"), "G": (Decimal(1), "g"), "GM": (Decimal(1), "g"),
}
# The invoice's own "6x700ML" note states the true case structure: N bottles of
# SIZE. It is stated data, not a guess — the authority for one bottle's size.
_CASE_NOTE = re.compile(r"(\d+)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(ML|LTR|LITRE|LT|L|KG|GM|G)\b", re.I)


def _liq_base(unit: str):
    return _LIQ_BASE.get((unit or "").strip().upper())


# A ls-recipe-seed beyond this multiple of the SAME product's BO-export seed is a
# unit misread, not a price. 3x is wide on purpose: real drift never reaches it,
# and the two failures it catches are 10.5x and 6.45x.
SEED_CONFLICT_X = 3.0


def ls_seed_is_misread(ls_rate, bo_rate, band: float = SEED_CONFLICT_X) -> bool:
    """
    Is this Lightspeed recipe-derived seed contradicted by the Back Office export?

    Two seeds can describe one ProductID: `bo-seed` (a STATED cost from the BO
    export) and `ls-recipe-seed` (Lightspeed's own computed recipe-line cost —
    precisely the number this project exists to escape). When they disagree wildly
    the LS one is a unit misread, and because it is dated a day later it otherwise
    wins the as-of lookup forever:

        Massenez Elderflower  BO $0.0506/ml   LS $0.004833/ml   10.5x LOW
        Bittermen's Tiki      BO $0.037186/ml LS $0.240000/ml    6.45x HIGH

    Elderflower at $24.17 for 5L is cheaper than sesame oil and 10x off every
    Massenez sibling; it is what made Hugo Spritz report 92.9% GP.

    Returns True only when a second, independent, stated figure contradicts the
    derived one by more than `band`. Never fires on a missing/zero reference —
    absence of a second opinion is not evidence of error.
    """
    if ls_rate is None or bo_rate is None:
        return False
    try:
        ls_v, bo_v = float(ls_rate), float(bo_rate)
    except (TypeError, ValueError):
        return False
    if ls_v <= 0 or bo_v <= 0:
        return False
    ratio = ls_v / bo_v
    return ratio > band or ratio < 1.0 / band


def seed_matched_liquor_cost(pack_cost, pack_qty, pack_unit, note, seed_per_unit,
                             seed_single_base=None, band=Decimal("3")):
    """
    Rescue a liquor invoice line that resolve_pack skipped — WITHOUT guessing.

    The description ("BOMBAY DRY GIN") carries no size, so resolve_pack refuses and
    build_costs drops the line: $1,583 of gin since June never reaches the book and
    13 cocktails price off a January seed. The line DOES carry an explicit
    (pack_qty, pack_unit), but ILG records it as the CASE (4.2 L = 6x700 ML) while
    pricing SOME lines per BOTTLE — so dividing by it is right for Bombay's $296.60
    case and 6x wrong for Patron's $76.52 bottle. The columns alone can't tell which.

    So form BOTH candidate per-ml costs — the case reading (÷ whole pack) and the
    single-bottle reading (÷ one bottle, size taken from the "6x700ML" note) — and
    keep whichever agrees with the product's own seed rate (the bridged ProductID's
    seeded price). If neither lands within `band` of the seed, return None and let
    the caller skip it: under-costing spirits is the flattering, dangerous direction,
    so a line that matches nothing stays out of the book.

    Fires only where a seed exists (a bridged product) — never invents a rate.

    -> (cost_per_base_unit: Decimal, base_unit: str, label: str) | None
    """
    if seed_per_unit is None:
        return None
    base = _liq_base(pack_unit)
    if base is None:
        return None                       # not a mass/volume pack (e.g. a keg 'EA')
    mult, base_unit = base
    try:
        seed = Decimal(str(seed_per_unit))
        pc = Decimal(str(pack_cost))
        pq = Decimal(str(pack_qty))
    except Exception:
        return None
    if seed <= 0 or pc <= 0 or pq <= 0:
        return None

    cands = []
    case_base = pq * mult
    if case_base > 0:
        cands.append((pc / case_base, base_unit, f"case /{case_base}{base_unit}"))

    # one bottle's size: the "6x700ML" note is the authority; the seed's own
    # single-unit size is the fallback when the note is silent.
    single_base = None
    m = _CASE_NOTE.search(note or "")
    if m:
        nb = _liq_base(m.group(3))
        if nb and nb[1] == base_unit:
            single_base = Decimal(m.group(2)) * nb[0]
    if single_base is None and seed_single_base:
        try:
            single_base = Decimal(str(seed_single_base))
        except Exception:
            single_base = None
    if single_base and single_base > 0 and single_base != case_base:
        cands.append((pc / single_base, base_unit, f"single /{single_base}{base_unit}"))

    if not cands:
        return None
    lo, hi = Decimal(1) / band, band
    best = None
    for per, u, label in cands:
        ratio = per / seed
        dist = abs(math.log(float(ratio)))
        if best is None or dist < best[0]:
            best = (dist, per, u, label, ratio)
    _, per, u, label, ratio = best
    if lo <= ratio <= hi:
        return per.quantize(Decimal("0.000001")), u, label
    return None


def main() -> int:
    # stdout is output too: under an ASCII locale a single em-dash in a progress
    # line kills the run *after* the fact table is written, so the file is right
    # and the exit code says otherwise. Pin it for the same reason we pin the file.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
    seed_price: dict[str, Decimal] = {}
    for r in cogs_rows:
        # every seed family that defines a product's cost BASIS belongs here, or a
        # bridged invoice can't be expressed in that basis and is silently dropped —
        # which is how prosciutto kept quoting a $45.71/kg January scrape while B&E
        # were invoicing it at $28.00/kg.
        if (r.get("source_invoice") or "").startswith(("bo-seed", "ls-recipe-seed",
                                                       "bo-ingredient-seed")):
            pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
            try:
                q, u, _p, _h, _b = resolve_pack(
                    r["invoice_description"].strip(), Decimal(r["cost_per_unit_incl_gst"]),
                    basis=r.get("basis", ""), note=r.get("note", ""),
                    code=(r.get("supplier_code") or "").strip())
                # A confirmed pack size is authoritative for the BASIS too. Without
                # this the seed row itself is per-box ($0.584 a pizza box) while the
                # bridged invoice comes back per-carton ($32.13), and the newer
                # carton price wins — reintroducing the very per-unit error the
                # override was added to fix.
                if pid in overrides:
                    seed_conv[pid] = overrides[pid]
                elif q and u:
                    seed_conv[pid] = (q, u)
                sq, su = seed_conv.get(pid, (None, None))
                if sq:
                    seed_price[pid] = Decimal(r['cost_per_unit_incl_gst']) / sq
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

    # BO-export rate per ProductID, in the recipe's own base unit. The second
    # opinion the ls-recipe-seed guard below is checked against.
    bo_rate: dict[str, Decimal] = {}
    for r in cogs_rows:
        if not (r.get("source_invoice") or "").startswith("bo-seed"):
            continue
        pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
        try:
            q = Decimal(r["pack_qty"])
            if (r.get("pack_unit") or "").strip().lower() in ("ml", "g") and q > 0:
                bo_rate[pid] = Decimal(r["cost_per_unit_incl_gst"]) / q
        except Exception:
            pass

    rows, skipped, bridged = [], [], 0
    for r in cogs_rows:
        code = (r.get("supplier_code") or "").strip()
        if not code:
            skipped.append((r["supplier"], r["invoice_description"], "no supplier_code — no identity"))
            continue

        # SEED CONFLICT GUARD. A ls-recipe-seed that contradicts the SAME product's
        # BO export by >3x is a unit misread, and since it is dated a day later it
        # would win the as-of lookup forever. Drop it and let the stated BO cost
        # stand. (Massenez Elderflower read 10.5x LOW -> Hugo Spritz 92.9% GP;
        # Bittermen's Tiki Bitters read 6.45x HIGH.) See ls_seed_is_misread.
        if (r.get("source_invoice") or "").startswith("ls-recipe-seed"):
            _pid = f"lightspeed:{code}"
            try:
                _lsr = Decimal(r.get("cost_per_base_unit") or 0)
            except Exception:
                _lsr = None
            if ls_seed_is_misread(_lsr, bo_rate.get(_pid)):
                skipped.append((r["supplier"], r["invoice_description"],
                                f"ls-recipe-seed {_lsr}/u contradicts BO export "
                                f"{bo_rate[_pid]}/u by >{SEED_CONFLICT_X}x — unit misread"))
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
        # The row's explicit pack_qty/pack_unit columns look like the fix —
        # resolve_pack reads only the DESCRIPTION, a liquor description is just
        # "BOMBAY DRY GIN" with no size, and 1,152 of the 1,154 rejected lines DO
        # carry an explicit pack ($1,583 of gin since June never reaching the book).
        #
        # The NAIVE fix (just divide by the pack) is WRONG and stays out: ILG records
        # pack_qty as the CASE (4.2L = 6x700ML) but prices SOME lines per BOTTLE, so
        # dividing Patron's $76.52 by 4.2L under-costs it 6x ($2.94 -> $0.61 a pour).
        # The columns can't say which basis the PRICE is on.
        #
        # DONE, the safe way, below the override block: seed_matched_liquor_cost tests
        # BOTH readings against the product's OWN seed rate (via the bridge) and keeps
        # the one that agrees — case for Bombay, bottle for Patron — or skips if
        # neither does. Never guesses; fires only where a seed exists. The lines it
        # still can't reach (no bridge / no seed) remain listed by scripts/audit_book.py.

        # A confirmed pack (chef or catalogue) is AUTHORITATIVE — it wins even over
        # a resolved pack, so it can CORRECT a wrong one (a box of loose produce
        # that parsed to "1 box" becomes the real weight). Must match build_ingredients.
        if iid in overrides:
            oq, ou = overrides[iid]
            qty, unit, bad, how = oq, ou, "", "chef-confirmed"
            per = (pack_cost / oq).quantize(Decimal("0.000001"))

        # LIQUOR RESCUE. resolve_pack refuses a sizeless liquor description
        # ("BOMBAY DRY GIN") and the line is dropped — but if this code bridges to a
        # SEEDED ProductID we can read the explicit case pack against that seed and
        # tell a case price from a bottle price (see seed_matched_liquor_cost). Only
        # where a seed exists; never a guess. This is what puts the gin / Rooster /
        # Aperol invoices back into the book instead of leaving 13 cocktails on a seed.
        seed_liquor = False   # a rescue that exists only to feed the bridge, below
        if (not qty or not unit) and not (iid in overrides):
            _pid = bridge.get(iid)
            _sp = seed_price.get(_pid) if _pid else None
            if _sp is not None:
                _ssb = (seed_conv.get(_pid) or (None, None))[0]
                fb = seed_matched_liquor_cost(
                    pack_cost, r.get("pack_qty"), r.get("pack_unit"),
                    r.get("note", ""), _sp, seed_single_base=_ssb)
                if fb:
                    per, unit, _lbl = fb
                    qty, bad, seed_liquor = Decimal(1), None, True
                    how = f"seed-matched {_lbl} vs seed ${_sp}"

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
        # A seed-matched liquor cost is a per-ml (recipe-unit) number derived ONLY to
        # supersede the seed on the bridged ProductID — which is what recipes read
        # (no recipe references a raw ilg:/paramount: liquor code). Emitting it under
        # the supplier code too would put a per-ml row next to that code's real
        # per-bottle pricebook row (Aperol, Rooster, Antica), giving one identity two
        # units and making its as-of lookup return whichever is newest. So feed the
        # bridge only; leave the supplier code's series in its own purchased unit.
        if not seed_liquor:
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
                # MAGNITUDE GUARD. A supplier code can map to a product whose seed is
                # on a totally different basis (a $300 KEG invoice against a per-ml
                # beer): the units 'agree' but the number is 50x out, and schooners
                # priced at $124. A real price move is never 10x, so refuse.
                sp = seed_price.get(pid)
                if bper is not None and sp and sp > 0:
                    ratio = float(bper) / float(sp)
                    if ratio > 10 or ratio < 0.1:
                        bper = None
                if bper is not None:
                    rows.append({**row, "ingredient": pid, "unit": sunit,
                                 "cost_per_unit": str(bper), "pack": f"{how} (via {iid})"})
                    bridged += 1

    rows.sort(key=lambda x: (x["ingredient"], x["observed_on"]))
    with OUT.open("w", newline="", encoding="utf-8") as f:
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
