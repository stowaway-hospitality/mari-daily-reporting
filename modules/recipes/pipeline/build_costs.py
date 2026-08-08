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

from core.domain import (canonical_purchasable, cogs_row_key,            # noqa: E402
                         purchasable_id)
from core.pack_overrides import load_pack_overrides                      # noqa: E402
from modules.recipes.pipeline.build_ingredients import (                 # noqa: E402
    out_of_bounds, resolve_pack)

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

    ONE CODE CAN FEED SEVERAL IDENTITIES, and this used to be a plain dict, so
    the LAST row silently won. Lightspeed keeps a spirit twice — the stock bottle
    ("Buffalo Trace [Bottle]") and the pour a recipe names ("Buffalo Trace
    [House]") — and product_map legitimately carries a row for each. Keeping one
    meant 26 of 179 bridges wrote to a ProductID **no recipe references**, while
    the sibling the recipes DO use stayed frozen on its January seed:

        20487225 Grand Marnier [Bottle]  0 recipes   <- the invoice landed here
        20445871 Grand Marnier            2 recipes   0.075414 vs 0.097720  22.8% under
        20483410 Rooster Rojo [Bottle]   0 recipes
        20445833 Rooster Rojo           19 recipes   1.7% under, ~$535/yr

    So emit onto every mapped identity. Each one is still checked against its OWN
    seed by the magnitude guard downstream, so a bad pairing yields no cost
    rather than a wrong one — the same safety the P/non-P twin extension relies on.

    (Duplicate keys were also how a WRONG bridge hid: ILG 285-0409P is Four
    Pillars *Bloody Shiraz* and carried rows for Olive Leaf and Rare Dry too.
    Last-row-wins landed it on Rare Dry. Those two rows are now corrected to
    285-1480 and 285-0132P, whose invoice rates match each product's own seed to
    four decimal places.)
    """
    if not PRODUCT_MAP.exists():
        return {}
    out: dict[str, list[str]] = {}
    for r in csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")):
        sup, code, pid = r.get("supplier"), r.get("supplier_code"), r.get("product_id")
        if sup and code and pid:
            targets = out.setdefault(purchasable_id(sup, code), [])
            target = f"lightspeed:{pid.strip()}"
            if target not in targets:
                targets.append(target)
    multi = {k: v for k, v in out.items() if len(v) > 1}
    if multi:
        print(f"  bridge: {len(multi)} supplier code(s) feed more than one ProductID "
              f"(emitting onto all)")
        for k, v in sorted(multi.items()):
            print(f"    {k} -> {', '.join(v)}")
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
    for iid, pids in list(bridge.items()):
        twin = iid[:-1] if iid.endswith("P") else iid + "P"
        if twin in bridge or twin not in desc or iid not in desc:
            continue
        if desc[iid] and desc[iid] == desc[twin]:
            bridge[twin] = list(pids)
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

# HOW SPECIFIC IS A PACK READING? resolve_pack returns a `how` label saying where
# the number came from, and the two sources are not equally trustworthy about the
# size of ONE SELLING UNIT.
#
# "per L (invoice)" / "per kg (invoice)" come from the row's BASIS. They mean "this
# price is quoted per litre" — a measurement denominator. resolve_pack must return
# 1000 for them, because that is what per-litre means. They say NOTHING about how
# big the bottle is.
#
# Anything else was read off the description, the code, or a chef override
# ("750ML") — a stated size for one selling unit.
_BASIS_DERIVED_HOW = ("per L (invoice)", "per kg (invoice)")

PACK_FROM_BASIS = 1     # the price's denominator; names the unit, not the pack
PACK_STATED = 2         # a real size for one selling unit


def pack_evidence(how: str) -> int:
    return PACK_FROM_BASIS if (how or "") in _BASIS_DERIVED_HOW else PACK_STATED


def stated_pack_in_base_units(pack_qty, pack_unit):
    """A row's OWN stated pack, in the unit a recipe portions in. -> (qty, unit)|None

    A `recipe-bridge-seed` row is a Zak-confirmed baseline: it states its pack in
    its own columns ("1", "L") and its price is ALREADY per that pack. Two things
    went wrong with reading it.

    Downstream, `seed_conv[pid]` was set to `(1, "L")` verbatim. Invoices arrive
    in ml and g, so `sunit == unit` never matched and "L" is not a whole selling
    unit either — so the bridge emitted nothing and **118 invoice observations
    were silently dropped**, every affected product frozen on its January seed:
    Buffalo Trace [House] (18 dropped, 12 recipes), Sailor Jerry [House] (13, 9
    recipes), Pizza Tomato Sauce (22), Herb Chives (13), Milk (8), Kewpie (8).

    So express the stated pack in base units — 1 L is 1000 ml, 1 kg is 1000 g —
    and the two sides can meet.

    ONLY a measurable unit is returned. "box"/"ea"/"bunch" is a CONTAINER, not a
    measure: there the price IS per container and the description is the only
    thing that says how much is inside. Barramundi states "1 box" at $83.00 and
    its name carries the 5 kg — taking the container literally would publish
    $83/box against a recipe asking for 180 g, a 5000x error in the direction
    that flatters nobody but breaks the dish. So containers stay with the
    description reader and this returns None for them.
    """
    u = (pack_unit or "").strip()
    if not u:
        return None
    base = _liq_base(u)
    if not base:
        return None                       # a container, not a measure — see above
    try:
        q = Decimal(str(pack_qty if pack_qty not in (None, "") else 1))
    except Exception:
        return None
    if q <= 0:
        return None
    mult, base_unit = base
    return q * mult, base_unit


def better_seed_pack(current, incoming):
    """
    Which of two seed readings describes ONE SELLING UNIT? -> the winner.

    Each argument is `(qty, unit, how)` or None.

    WAS: last row in the file wins. `seed_conv[pid]` does double duty — it is the
    seed's unit AND the divisor that turns a whole-bottle invoice price into that
    unit — and cogs_list.csv is sorted oldest-first. `bo-seed` is dated 2026-01-01
    and states a size; `ls-recipe-seed` is dated 2026-01-02 and carries
    `basis=per_L`. So a 1000 nobody claimed overwrote every stated bottle size:

        2026-01-01  20655236  Geppetto Pinot Noir 750ML   $17.5608  basis ''
        2026-01-02  20655236  Geppetto Pinot Noir-Bottle  $23.4000  basis 'per_L'

    A bottle invoice was then divided by 1000, not 750: $0.017560/ml recorded
    against a true $0.023413/ml. Exactly 0.75, which sits inside the magnitude
    guard's 0.1-10 band at the bridge, so nothing refused it. Wine by the glass
    under-costed 25%; Version Two Pinot Grigio published 87.9% GP against 83.8%.

    96 seeded ProductIDs carried a stated size the per_L row contradicted, and it
    runs BOTH ways — 0.15x on 150 ml bitters, 50x on a 50 L keg (where the
    magnitude guard then refused the line outright, so keg invoices reached the
    book not at all rather than wrong).

    The rule is specificity, not recency, and it is deliberately narrow:

      * a STATED size beats a basis-derived one **in the same base unit** — the
        two are describing the same thing and one of them is guessing;
      * across DIFFERENT units it does not fire. A `per_bottle` seed resolves to
        a countable `(1, "bottle")`, which is a COUNT, not a size; the per_L row
        is what gives that product a per-ml basis at all, and a recipe portioning
        30 ml can only read a per-ml cost;
      * within a tier, last row still wins — the same tie-break as everywhere
        else, so nothing this finding does not touch can move.
    """
    if current is None:
        return incoming
    if incoming is None:
        return current
    cq, cu, chow = current
    iq, iu, ihow = incoming
    if (cu == iu
            and pack_evidence(chow) == PACK_STATED
            and pack_evidence(ihow) == PACK_FROM_BASIS):
        return current
    return incoming


def bo_stated_rates(cogs_rows):
    """ProductID -> the Back Office's STATED rate per base unit.

    The second opinion every seed guard is checked against. It reads `bo-seed`
    AND `bo-ingredient-seed`: both are stated figures from the export, and
    leaving the latter out meant the eight spirits that carry only a
    bo-ingredient-seed had no second opinion at all — which is how a
    recipe-bridge-seed claiming Jack Daniels at $6.55 a LITRE (ILG publish
    $43.28 for 700 ml, i.e. $61.83/L) had nothing to contradict it.
    """
    out: dict[str, Decimal] = {}
    for r in cogs_rows:
        if not (r.get("source_invoice") or "").startswith(("bo-seed", "bo-ingredient-seed")):
            continue
        pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
        try:
            q = Decimal(r["pack_qty"])
            if (r.get("pack_unit") or "").strip().lower() in ("ml", "g") and q > 0:
                out[pid] = Decimal(r["cost_per_unit_incl_gst"]) / q
        except Exception:
            pass
    return out


def bridge_seed_is_misread(row, bo_rate):
    """Is this recipe-bridge-seed contradicted by the Back Office's own figure?

    These rows state "1 L" and a price that is supposed to be per litre. For
    eight spirits it is not, and the gap is not subtle:

        Jack Daniels        seed $0.00655/ml   BO $0.064643/ml    9.9x LOW
        Four Pillars Olive  seed $0.015902/ml  BO $0.117971/ml    7.4x LOW
        Sailor Jerry        seed $0.010605/ml  BO $0.063529/ml    6.0x LOW
        Buffalo Trace       seed $0.012726/ml  BO $0.075643/ml    5.9x LOW

    ILG's published price book agrees with the BO figure to within their usual
    broken-carton premium, so the seed is the outlier, not the bargain. It went
    unnoticed because resolve_pack could not read a sizeless spirit description
    and dropped these rows entirely — the right answer for the wrong reason. The
    moment they became readable they undercut ILG's own published price by 4-10x,
    which is the flattering direction and exactly what must never ship.

    Same shape, same band and same reasoning as ls_seed_is_misread.
    """
    if not (row.get("source_invoice") or "").startswith("recipe-bridge-seed"):
        return False
    sp = stated_pack_in_base_units(row.get("pack_qty"), row.get("pack_unit"))
    if not sp:
        return False                      # a container: no rate to compare
    pid = f"lightspeed:{(row.get('supplier_code') or '').strip()}"
    try:
        rate = Decimal(row["cost_per_unit_incl_gst"]) / sp[0]
    except Exception:
        return False
    return ls_seed_is_misread(rate, bo_rate.get(pid))


def build_seed_conv(cogs_rows, overrides, bo_rate=None):
    """
    -> (seed_conv, seed_price), keyed by `lightspeed:<ProductID>`.

    The BO seed defines each bottle's cost UNIT and the divisor to reach it
    (Aperol = a 700 ml bottle -> $/ml, so divisor 700, unit "ml"; a beer ->
    $/can, divisor 1). When we bridge an INVOICE cost onto that ProductID we must
    express it the SAME way, or the bottle carries two costs in two units and a
    recipe can't read the newer one. Take the seed's OWN resolved (qty, unit) —
    not its raw pack_unit, which can differ ("each" vs the resolved "can").

    `seed_price` is the per-base-unit reference the magnitude guard and
    seed_matched_liquor_cost are checked against. It is built from the row's OWN
    divisor, never from whichever pack the product ends up keeping: a per_L row
    quotes $23.40 PER LITRE, so its rate is 23.40/1000 even on a 750 ml bottle.
    Dividing it by the retained 750 would inflate the reference 33% and start
    refusing correct invoices.
    """
    seed_conv: dict[str, tuple[Decimal, str]] = {}
    seed_price: dict[str, Decimal] = {}
    evidence: dict[str, tuple[Decimal, str, str]] = {}

    for r in cogs_rows:
        # every seed family that defines a product's cost BASIS belongs here, or a
        # bridged invoice can't be expressed in that basis and is silently dropped —
        # which is how prosciutto kept quoting a $45.71/kg January scrape while B&E
        # were invoicing it at $28.00/kg.
        if (r.get("source_invoice") or "").startswith(("bo-seed", "ls-recipe-seed",
                                                       "bo-ingredient-seed")):
            pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
            try:
                q, u, _p, how, _b = resolve_pack(
                    r["invoice_description"].strip(), Decimal(r["cost_per_unit_incl_gst"]),
                    basis=r.get("basis", ""), note=r.get("note", ""),
                    code=(r.get("supplier_code") or "").strip())
                # A confirmed pack size is authoritative for the BASIS too. Without
                # this the seed row itself is per-box ($0.584 a pizza box) while the
                # bridged invoice comes back per-carton ($32.13), and the newer
                # carton price wins — reintroducing the very per-unit error the
                # override was added to fix.
                if pid in overrides:
                    oq, ou = overrides[pid]
                    evidence[pid] = (oq, ou, "chef-confirmed")
                    seed_conv[pid] = (oq, ou)
                    own = oq
                elif q and u:
                    evidence[pid] = better_seed_pack(evidence.get(pid), (q, u, how))
                    seed_conv[pid] = (evidence[pid][0], evidence[pid][1])
                    own = q
                else:
                    own = seed_conv.get(pid, (None, None))[0]
                if own:
                    seed_price[pid] = Decimal(r['cost_per_unit_incl_gst']) / own
            except Exception:
                pass
        # a confirmed recipe-bridge baseline records its own resolved unit directly
        # (Zak-confirmed), so a future invoice for the mapped supplier code can be
        # emitted onto this food ProductID in the same unit and supersede it.
        elif (r.get("source_invoice") or "").startswith("recipe-bridge-seed"):
            pid = f"lightspeed:{(r.get('supplier_code') or '').strip()}"
            if bridge_seed_is_misread(r, bo_rate or {}):
                continue         # contradicted by the BO export — see the guard
            try:
                # Was `(Decimal("1"), pack_unit)` verbatim — a pack of "1 L" that
                # no ml-denominated invoice could ever match. See
                # stated_pack_in_base_units: 118 observations died here.
                sp = stated_pack_in_base_units(r.get("pack_qty"), r.get("pack_unit"))
                if sp is None:
                    # A container ("ea", "box", "bunch") has no base unit, but it
                    # is still the seed's unit and an invoice priced per each must
                    # keep matching it — dropping these took Garlic Bread, Corn
                    # Baby Sweet and Pizza Box Inserts out of the book entirely.
                    unit = (r.get("pack_unit") or "").strip()
                    if not unit:
                        continue
                    sp = (Decimal(str(r.get("pack_qty") or 1)), unit)
                    # ...and do NOT give a container seed a reference rate. Its
                    # "per each" is the least reliable figure in the file: Garlic
                    # Bread is seeded at $59.81 "ea" when the Gulli line is a case
                    # of 40 (~$1.43 each), and Pizza Box Inserts at $11.055 "ea"
                    # against a case of 100 (~$0.11). Arming the magnitude guard
                    # with those would refuse the CORRECT invoice rate and leave
                    # the product on a 40x/100x seed — the guard would be doing
                    # damage in the flattering direction. scripts/audit_book.py
                    # reports these instead, where a human can fix the seed.
                    evidence[pid] = better_seed_pack(
                        evidence.get(pid), (sp[0], sp[1], "recipe-bridge-seed"))
                    seed_conv[pid] = (evidence[pid][0], evidence[pid][1])
                    continue
                # A MEASURABLE bridge-seed pack is a PRICE BASIS, not a bottle
                # size: "1 L" means the confirmed price is per litre. Patron
                # Silver, Kraken and Wolf Lane each carry one of these AND a
                # bo-seed stating the real bottle (700/700/500 ml), and the real
                # size must win — the same rule, and for the same reason, as the
                # per_L collision in better_seed_pack. It happened to come out
                # right on file order alone; now it is decided by evidence.
                evidence[pid] = better_seed_pack(
                    evidence.get(pid), (sp[0], sp[1], _BASIS_DERIVED_HOW[0]))
                seed_conv[pid] = (evidence[pid][0], evidence[pid][1])
                # A MEASURABLE seed (per L, per kg) is trustworthy — it is the
                # confirmed baseline this row family exists to record — so give it
                # a reference rate and let the bridge's magnitude guard cover it.
                if sp[0] > 0:
                    seed_price[pid] = Decimal(r["cost_per_unit_incl_gst"]) / sp[0]
            except Exception:
                pass

    return seed_conv, seed_price


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


def _read_cogs_rows():
    """data/cogs_list.csv, ONE ROW PER (invoice, product). -> list[dict]

    THE DEDUPE HAS TO BE ON THE READ. build_cogs_list only ever applied its
    identity check to lines it was about to add, so a second writer that appended
    straight to the file bypassed it — and three rows got in that way (Paramount
    5441124: Carpano 10015926, De Bortoli 44583, Sprite 98541, identical price,
    date, code and basis, differing only in the diagnostic `note`).

    as_of never noticed: same day, same price. CostSeries.rolling did — it counts
    the duplicated observation TWICE in the trailing-30-day mean, which is the
    live menu-costing path, so one stray row silently reweights a real average.

    Deduping HERE rather than rewriting the file is deliberate. cogs_list.csv is
    an append-only fact table; both notes are evidence and nothing in this repo
    permanently deletes a fact. The derived table is where the duplicate must not
    survive, and a check a writer cannot reach is a check that cannot be bypassed.
    First occurrence wins — the file is in the order the writer produced it.
    """
    seen: set[tuple[str, str]] = set()
    rows, dropped = [], []
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        k = cogs_row_key(r.get("source_invoice", ""), r.get("supplier_code", ""),
                         r.get("invoice_description", ""))
        if k in seen:
            dropped.append(r)
            continue
        seen.add(k)
        rows.append(r)
    if dropped:
        print(f"  {len(dropped)} duplicate cogs_list row(s) ignored "
              f"(same invoice + product; would double-weight the rolling average)")
        for r in dropped:
            print(f"    {r.get('source_invoice')} {r.get('supplier_code')} "
                  f"{(r.get('invoice_description') or '')[:34]}")
    return rows


def main() -> int:
    # stdout is output too: under an ASCII locale a single em-dash in a progress
    # line kills the run *after* the fact table is written, so the file is right
    # and the exit code says otherwise. Pin it for the same reason we pin the file.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Chef-confirmed pack sizes, re-keyed through the same canonicalisation the
    # identity uses — four confirmations were filed under a bled code spelling
    # and must keep landing on the product they were made for. See
    # core.domain.canonical_purchasable.
    overrides = {canonical_purchasable(k): v
                 for k, v in load_pack_overrides(PACK_OVERRIDES).items()}
    bridge = load_bridge()                            # supplier:code -> lightspeed:ProductID
    cogs_rows = _read_cogs_rows()

    # BO-export rate per ProductID, in the recipe's own base unit. The second
    # opinion every seed guard below is checked against. Built FIRST, because
    # build_seed_conv needs it to refuse a bridge seed the export contradicts.
    bo_rate = bo_stated_rates(cogs_rows)

    seed_conv, seed_price = build_seed_conv(cogs_rows, overrides, bo_rate)

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

        # A recipe-bridge-seed row STATES its own pack, and its price is already
        # per that pack. resolve_pack reads only the DESCRIPTION, so the size in
        # the name got applied to a price that had already been divided by it:
        #
        #   Heinz BBQ Sauce [4L]        $3.475/L stated -> recorded $0.87/L   4x UNDER
        #   Sunshine Smokey BBQ [3L]    $4.167/L stated -> recorded $1.39/L   3x UNDER (7 live recipes)
        #   Milk Full Cream 2L          $1.75/L  stated -> recorded $0.875/L  2x UNDER
        #   T2 Milk Bun [85g]           $11.53/kg stated -> recorded $135.64/kg  11.8x OVER
        #
        # Large BBQ Chicken Pizza booked 61 ml of sauce at $0.085 instead of
        # $0.254 and published 82.8% GP with fully_our_book:true.
        #
        # The row's own columns are the authority here precisely because this seed
        # family exists to record a confirmed baseline. (Note cost_per_base_unit is
        # NOT usable: on this family it repeats the per-pack price, and on
        # Passionfruit Puree it still holds a superseded 12x-undercost figure.)
        if (r.get("source_invoice") or "").startswith("recipe-bridge-seed"):
            if bridge_seed_is_misread(r, bo_rate):
                skipped.append((r["supplier"], desc,
                                f"recipe-bridge-seed contradicts the BO export "
                                f"{bo_rate.get(f'lightspeed:{code}')}/u by "
                                f">{SEED_CONFLICT_X}x — not a per-pack price"))
                continue
            _sp = stated_pack_in_base_units(r.get("pack_qty"), r.get("pack_unit"))
            if _sp:
                qty, unit = _sp
                per = (pack_cost / qty).quantize(Decimal("0.000001"))
                how = f"stated {r.get('pack_qty')}{r.get('pack_unit')} (recipe-bridge-seed)"
                bad = out_of_bounds(per, unit)

        # A confirmed pack (chef or catalogue) is AUTHORITATIVE — it wins even over
        # a resolved pack, so it can CORRECT a wrong one (a box of loose produce
        # that parsed to "1 box" becomes the real weight). Must match build_ingredients.
        #
        # AUTHORITATIVE ABOUT THE PACK, NOT ABOUT THE RATE. This block used to set
        # `bad = ""`, so confirming a pack ALSO switched the plausibility guard off
        # for every line under that code — including lines the supplier billed on a
        # different basis. Foodlink bills 100487 camembert both ways: $3.80 for one
        # 125 g piece (note "EA") and $45.60 for a carton (note "UOM CTN-12"). The
        # 125 g pin is right for the piece and 12x wrong for the carton, and the
        # carton row published $364.80/kg against the same code's own $30.40/kg for
        # 16-22 Jul with nothing to stop it. An override pins the pack; out_of_bounds
        # still judges what comes out of it, and a rate no food has skips the book.
        if iid in overrides:
            oq, ou = overrides[iid]
            qty, unit, how = oq, ou, "chef-confirmed"
            per = (pack_cost / oq).quantize(Decimal("0.000001"))
            bad = out_of_bounds(per, ou)

        # LIQUOR RESCUE. resolve_pack refuses a sizeless liquor description
        # ("BOMBAY DRY GIN") and the line is dropped — but if this code bridges to a
        # SEEDED ProductID we can read the explicit case pack against that seed and
        # tell a case price from a bottle price (see seed_matched_liquor_cost). Only
        # where a seed exists; never a guess. This is what puts the gin / Rooster /
        # Aperol invoices back into the book instead of leaving 13 cocktails on a seed.
        seed_liquor = False   # a rescue that exists only to feed the bridge, below
        if (not qty or not unit) and not (iid in overrides):
            # Any mapped identity that carries a seed will do as the reference —
            # they are the same bottle under two Lightspeed records.
            _pid = next((p for p in (bridge.get(iid) or ()) if p in seed_price), None)
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
        for pid in (bridge.get(iid) or ()):
            if iid.startswith("lightspeed:"):
                break
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
                    # SAME DIMENSION, DIFFERENT SCALE. An invoice priced per kg
                    # against a seed held per g is not a unit clash, it is a
                    # thousandfold. Refusing it dropped Passionfruit Puree the
                    # moment its seed started being expressed in base units.
                    # kg<->g and L<->ml only, exactly like core cost.py's guard —
                    # never pack/each <-> a base unit, which is the conversion
                    # that produced the original $11,400 serve.
                    conv = _liq_base(unit)
                    bper = ((per / conv[0]).quantize(Decimal("0.000001"))
                            if conv and conv[1] == sunit else None)
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
