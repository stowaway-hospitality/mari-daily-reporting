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
from core.conversions import load_declared_conversions, conversion_key   # noqa: E402
# The Alehouse Crisp/Premium guard. Imported, never restated: one definition of
# "material in BOTH dollars and percent" or the band drifts apart. See §8 in
# load_bridge for why this file is the caller resolve.py never had.
from modules.invoices.resolve import is_suspect                          # noqa: E402
from modules.recipes.pipeline.build_ingredients import (                 # noqa: E402
    out_of_bounds, resolve_pack)

ROOT = Path(__file__).resolve().parents[3]
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "data" / "costs.csv"
PACK_OVERRIDES = ROOT / "data" / "pack_overrides.yaml"
PRODUCT_MAP = ROOT / "data" / "product_map.csv"
ILG_PRICEBOOK = ROOT / "data" / "ilg_pricebook.csv"


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

    THE BACK-OFFICE COST GUARD, now actually running
    ------------------------------------------------
    modules/invoices/resolve.py has held this guard since the module was written
    and NOTHING in the invoice->cost path called it: its only importers were its
    own tests. So the story it exists for was never checked here.

        ILG 122-2867  "ALEHOUSE CRISP KEG"    -> 20487313 Summer Mid [Keg]   $184.94
        ILG 122-2858  "ALEHOUSE PREMIUM KEG"  -> 20487298 Draught Lager [Keg] $212.44

    Both match /ALEHOUSE .* KEG/, the sensible guess is backwards, and they are
    $27.50 apart. `is_suspect` is imported from resolve.py rather than restated,
    so the band that separates Sprite's real 22.2% drift from Alehouse's real
    14.9% error can only ever have one definition. A row whose own recorded Back
    Office and invoice costs are material apart in BOTH dollars and percent does
    not bridge: an unbridged bottle keeps its seed and shows up in audit_book; a
    WRONG bridge writes a wrong cost against a real SKU.

    Measured on this tree: 0 of 189 rows are suspect (70 carry both costs; the
    other 119 have no bo_cost recorded and cannot be checked at all). It changes
    nothing today. That is the point — it is armed before the next map row, not
    after it.

    VENUE IS DELIBERATELY NOT A FILTER HERE, and that is a decision, not an
    oversight. `Resolver.__init__` filters by venue because it resolves ONE
    INVOICE LINE for one venue's ledger. This function builds an IDENTITY map,
    and identity is venue-free: all three venues ring through one Lightspeed
    till, so a ProductID is the same bottle or the same box of cauliflower
    whichever door it came in. The venue column records where a delivery landed.

    Filtering on it was measured before being rejected: the 11 rows tagged
    harry_gatos/marilynas carry 82 of the 3,877 rows in costs.csv, across ten
    real products — Carrot Large (30 observations), Cauliflower Florets (28),
    Broccolini (6), Capsicum (5), Bocconcini (4), Dry Slaw (4), Kewpie mayo (2),
    Pumpkin, Lettuce Mesculin, Corn Flour. Dropping them would freeze all ten on
    a January seed while their invoices sat unread. That is the flattering
    direction on stale seeds and it deletes evidence to enforce a distinction
    the data model does not make.
    """
    if not PRODUCT_MAP.exists():
        return {}
    out: dict[str, list[str]] = {}
    refused: list[str] = []
    for r in csv.DictReader(PRODUCT_MAP.open(encoding="utf-8-sig")):
        sup, code, pid = r.get("supplier"), r.get("supplier_code"), r.get("product_id")
        if not (sup and code and pid):
            continue
        bo, inv = (r.get("bo_cost") or "").strip(), (r.get("invoice_cost") or "").strip()
        if bo and inv:
            try:
                if is_suspect(Decimal(bo), Decimal(inv)):
                    refused.append(
                        f"{sup} {code} -> {pid} {(r.get('product_name') or '')[:30]}: "
                        f"Back Office ${bo} vs invoice ${inv} — material in BOTH "
                        f"dollars and percent, so this is likely the WRONG PRODUCT "
                        f"(cf. Alehouse Crisp/Premium, $27.50 apart). Not bridging.")
                    continue
            except (ArithmeticError, ValueError):
                pass          # an unreadable cost is no evidence either way
        targets = out.setdefault(purchasable_id(sup, code), [])
        target = f"lightspeed:{pid.strip()}"
        if target not in targets:
            targets.append(target)
    if refused:
        print(f"  bridge: {len(refused)} row(s) REFUSED by the Back Office cost guard")
        for m in refused:
            print(f"    {m}")
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


def bo_declared_units():
    """ProductID -> the base unit Back Office says the product is measured in.

    THE ONLY THING A ZERO-COST PRODUCT STILL TELLS YOU. The bridge below turns an
    invoice price into a ProductID's cost by converting it into that ProductID's
    own unit, and it takes that unit from `seed_conv` — which is built from the
    product's existing seed. A product with NO cost has no seed, so it has no
    conversion, so the bridge emits nothing, so it keeps no cost. Nothing was
    wrong with any single step and the loop never terminated:

        22962978 White Pepper   4 recipes, 10 g and 7.5 g a batch, $0.00
        22962975 bicarb         2 marinations, 50 g each, $0.00
        22874517 Yuzu Juice     Shiba Highball, 10 ml, $0.00

    Back Office states `Unit` for every one of them — g, g, ml — and only
    `CostPriceIncTax` is missing. The basis was never unknown; it just was not
    being read from the one file that had it.

    ONLY BASE UNITS ARE RETURNED. "unit", "each" and Lightspeed's "UNIT" default
    say nothing about how a recipe portions the thing, and a bridge that guessed
    at those is how a keg price lands on a per-ml beer.
    """
    out: dict[str, str] = {}
    for p in sorted((ROOT / "data" / "bo_exports").glob("*products*.csv")):
        try:
            rdr = csv.DictReader(p.open(encoding="utf-8-sig"))
        except OSError:
            continue
        for r in rdr:
            pid = (r.get("ProductID") or "").strip()
            u = (r.get("Unit") or "").strip().lower()
            if pid and u in ("g", "ml"):
                out[f"lightspeed:{pid}"] = u
    return out


def _ilg_key(code: str) -> str:
    """ILG writes one code two ways — "175-042-0" in the price book, "175-0420"
    on an invoice, and "395-6785P" for the same product as "395-6785". The digits
    are the part both agree on."""
    return re.sub(r"[^0-9]", "", code or "")


def pricebook_selling_unit_ml(path: Path = None) -> dict:
    """ILG code digits -> the size of ONE SELLING UNIT, in ml. -> {key: Decimal}

    `size_ml` is the size of one ITEM and `units_per_selling_unit` says how many
    items the U.C. price buys, so the selling unit is the product of the two (see
    scripts/build_ilg_pricebook.py). A row whose denominator could not be PROVED
    carries a blank there and is skipped: an unproved size is exactly the kind of
    number this whole finding is about.
    """
    path = path or ILG_PRICEBOOK
    out: dict = {}
    if not path.exists():
        return out
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if rows and "units_per_selling_unit" not in rows[0]:
        print("  ILG price book has no units_per_selling_unit column — re-run "
              "scripts/build_ilg_pricebook.py where the corpus lives; the seed "
              "cross-check below cannot run without it")
        return out
    for r in rows:
        try:
            n = int(r["units_per_selling_unit"])
            ml = Decimal(r["size_ml"]) * n
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
        if ml > 0:
            out[_ilg_key(r.get("code"))] = ml
    return out


def invoice_stated_bottle_ml(cogs_rows) -> dict:
    """ILG code digits -> the bottle size(s) ILG's own delivery notes state.

    Every ILG stock line carries a case note in its own hand — "6x700ML",
    "12x750ML", "6x1LT". It is the same statement `seed_matched_liquor_cost`
    already treats as the authority for one bottle's size. Collected as a SET so
    a code whose deliveries disagree with each other can be refused rather than
    averaged.
    """
    out: dict = {}
    for r in cogs_rows:
        if (r.get("supplier") or "").strip().upper() != "ILG":
            continue
        m = _CASE_NOTE.search(r.get("note") or "")
        if not m:
            continue
        base = _liq_base(m.group(3))
        if not base or base[1] != "ml":
            continue
        out.setdefault(_ilg_key(r.get("supplier_code")), set()).add(
            Decimal(m.group(2)) * base[0])
    return out


def corroborated_bottle_ml(cogs_rows, bridge) -> tuple:
    """lightspeed:<ProductID> -> (size, "ml"), where two ILG sources agree.

    THE DEFECT
    ----------
    `seed_conv[pid]` is the divisor that turns a whole-bottle invoice price into
    the per-ml cost a recipe reads, and for a bridged bottle it comes from the
    Lightspeed side only — a size typed into a Back Office product name, or a
    `per_L` basis that names the unit and claims nothing about the bottle at all.
    Neither is a statement by the people who packed the bottle.

    `lightspeed:20484285` is named "Antica Formula Rosso Vermouth **700ML**" in
    the BO export, so seed_conv = (700, "ml"). ILG's price book says code
    175-042-0 is 1000 ml and every ILG delivery note for it reads "6x1LT". ILG
    invoice 03729959 arrived per_bottle at $64.27 and the bridge published
    64.27/700 = **$0.091814/ml — 43% OVER** a true $0.064270.

    Ten bridged ProductIDs carry a seed the book contradicts (measured on this
    tree; the audit's fifteen was taken before better_seed_pack landed and
    settled De Bortoli 15 L and Fee Bros 150 ml, which now agree exactly):

        20484285 Antica Formula          seed  700  book 1000  note 6x1LT
        20445895 Aperol                  seed 1000  book  700  note 6x700ML
        20445833 Rooster Rojo Blanco     seed 1000  book  700  note 6x700ML
        21999746 Havana 3yr              seed 1000  book  700  note 6x700ML
        20445887 Dolin Blanc Vermouth    seed 1000  book  700  note 6x700ML
        20445832 Don Julio 1942          seed 1000  book  750  note 6x750ML
        20484784 Fever-Tree Light Tonic  seed 1000  book  500  note 8x500ML
        20487286 Four Pillars Bloody Sh. seed  750  book  700  note 6x700ML
        20492689 Domaine de Canton       seed  700  book  750  note 6x750ML
        20727770 Bickfords Raspberry     seed  700  book  750  note 12x750ML

    THE RULE — two sources, or nothing
    ----------------------------------
    A size is corroborated only where ILG's PUBLISHED PRICE BOOK and ILG's OWN
    DELIVERY NOTES for the same code state the same thing. They are two separate
    artefacts — a catalogue and a docket — and neither is derived from the seed
    under suspicion, which is precisely what the Havana Club $29.09 seed lacked
    for months. On all ten above they agree, and they contradict the seed.

    Where they disagree with each other, or where either is missing, this returns
    NOTHING for that product and the seed stands. Refusing is cheap; a guessed
    divisor writes a wrong cost against a real bottle.

    -> (pack: dict, refused: list[(pid, why)])
    """
    book = pricebook_selling_unit_ml()
    notes = invoice_stated_bottle_ml(cogs_rows)
    if not book:
        return {}, []

    per_code: dict = {}
    refused: list = []
    for iid in bridge:
        if not iid.startswith("ilg:"):
            continue
        key = _ilg_key(iid.split(":", 1)[1])
        bml, nml = book.get(key), notes.get(key)
        if bml is None or not nml:
            continue
        if len(nml) > 1:
            refused.append((iid, f"ILG's own delivery notes disagree "
                                 f"({'/'.join(str(x) for x in sorted(nml))} ml)"))
            continue
        n = next(iter(nml))
        if n != bml:
            refused.append((iid, f"price book says {bml} ml, delivery notes say "
                                 f"{n} ml — no second source, refusing to guess"))
            continue
        per_code[iid] = bml

    pack: dict = {}
    clash: set = set()
    for iid, ml in per_code.items():
        for pid in bridge.get(iid) or ():
            if pid in pack and pack[pid][0] != ml:
                clash.add(pid)
            pack[pid] = (ml, "ml")
    for pid in clash:                    # two codes, two sizes, same product
        pack.pop(pid, None)
        refused.append((pid, "two ILG codes corroborate different sizes"))
    return pack, refused


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


def build_seed_conv(cogs_rows, overrides, bo_rate=None, book_pack=None):
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

    `book_pack` (see corroborated_bottle_ml) is the supplier's own answer to the
    same question, and where it exists it PINS the pack — the price book and the
    delivery notes both describe the bottle ILG shipped, which neither the BO
    product name nor a `per_L` basis does.

    It changes `seed_price` only where the row's size was a claim about ONE
    SELLING UNIT (PACK_STATED). A basis-derived row is a RATE — $41.55 per litre
    is $0.04155/ml whatever the bottle turns out to hold — so its reference is
    left alone, exactly as the paragraph above requires.
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
                # THE SUPPLIER'S OWN SIZE, where two of its documents agree. Above
                # a chef confirmation (which is more specific still) and below
                # nothing else: a BO product name and a per_L basis are both
                # Lightspeed-side readings of a bottle neither of them packed.
                bp = (book_pack or {}).get(pid)
                if pid in overrides:
                    oq, ou = overrides[pid]
                    evidence[pid] = (oq, ou, "chef-confirmed")
                    seed_conv[pid] = (oq, ou)
                    own = oq
                elif bp and (not u or bp[1] == u):
                    evidence[pid] = (bp[0], bp[1], "ilg-book+notes")
                    seed_conv[pid] = bp
                    # A STATED size was a claim about this row's own selling unit
                    # and the claim was wrong, so the row's price divides by the
                    # real bottle. A basis-derived row states a RATE and keeps it.
                    own = (q if (q and u and pack_evidence(how) == PACK_FROM_BASIS)
                           else bp[0])
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


COUNTABLE_SEED = ("can", "ea", "each", "tin", "bottle", "stubbie", "unit")


def build_countable_seeds(cogs_rows):
    """ProductID -> the Back Office price for ONE of the thing. -> {pid: Decimal}

    seed_price only ever holds a per-BASE-UNIT figure, because build_seed_conv
    expresses a seed through stated_pack_in_base_units(), which returns nothing for
    a container word. So a product Lightspeed holds per CAN has no seed price at
    all — and seed_matched_liquor_cost, which needs one to judge against, cannot
    fire for it.

    That is why 77 of 104 ILG codes were missing from the cost book. Nine of them
    are the cans and bottles the bar sells as bought: Peroni, Asahi, Two Bays,
    Young Henrys, Monteith's, Fellr, Better Beer. Every one has a readable pack on
    the invoice (0.330 L) and a price, and every one was dropped as "pack
    unreadable" — so add_passthrough_products fell back to the Back Office cost and
    the book stayed tied to Lightspeed for a product we hold real invoices for.
    """
    out: dict[str, Decimal] = {}
    for r in cogs_rows:
        if (r.get("supplier") or "") != "Lightspeed":
            continue
        if not str(r.get("source_invoice") or "").startswith(("bo-seed", "bo-ingredient-seed")):
            continue
        if (r.get("pack_unit") or "").strip().lower() not in COUNTABLE_SEED:
            continue
        if str(r.get("pack_qty") or "").strip() not in ("1", "1.0", ""):
            continue
        try:
            v = Decimal(r["cost_per_unit_incl_gst"])
        except (ArithmeticError, ValueError, KeyError, TypeError):
            continue
        if v > 0:
            out[f"lightspeed:{(r.get('supplier_code') or '').strip()}"] = v
    return out


def seed_matched_countable_cost(pack_cost, seed_per_unit, band=Decimal("3")):
    """A sold-as-bought can, judged the same way the liquor rescue judges a bottle.

    No conversion is involved and that is the whole argument: Back Office holds the
    product per CAN, the invoice line is one can, so the invoice's own unit price IS
    the cost. The only question is whether this line is one can or a CASE of them,
    and the band answers it — a 6-pack lands at 6x the seed and a carton at 24x,
    both refused, while every real one here sits between 0.98x and 1.17x.

    Peroni is the proof that the two sides are the same number: Back Office says
    $2.5217 and ILG 115-4173 invoices $2.5217, identical to four decimals across
    four invoices. The Back Office figure was set from this line.

    Returns None when nothing matches, so a line that agrees with neither reading
    stays OUT of the book — under-costing a drink is the flattering direction.
    """
    if seed_per_unit is None:
        return None
    try:
        pc, seed = Decimal(str(pack_cost)), Decimal(str(seed_per_unit))
    except (ArithmeticError, ValueError, TypeError):
        return None
    if pc <= 0 or seed <= 0:
        return None
    ratio = pc / seed
    if (Decimal(1) / band) <= ratio <= band:
        return pc.quantize(Decimal("0.000001")), "ea", f"one unit vs seed ${seed}"
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

    # The SUPPLIER's own bottle size, where its price book and its delivery notes
    # agree. Antica Formula was published at $0.091814/ml — 43% over — because a
    # 1 L bottle was divided by the 700 in a Back Office product name.
    book_pack, book_refused = corroborated_bottle_ml(cogs_rows, bridge)
    if book_pack:
        print(f"  {len(book_pack)} bridged ProductID(s) take ILG's own stated "
              f"bottle size (price book + delivery note agree)")
    for what, why in book_refused:
        print(f"    NOT pinned: {what} — {why}")

    seed_conv, seed_price = build_seed_conv(cogs_rows, overrides, bo_rate, book_pack)
    # ...and the per-CAN seeds, which build_seed_conv cannot express (see the
    # docstring): a container word has no base-unit size to divide by.
    countable_seed = build_countable_seeds(cogs_rows)
    # The base unit Back Office declares, for products that have no seed at all.
    bo_units = bo_declared_units()
    # Loaded before the row loop since 2026-08-20: declared conversions now
    # apply at row CREATION (see the keg block below), not only in the
    # post-pass, so the ProductID bridge sees base units.
    declared = load_declared_conversions()

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
            # A CONFIRMED PACK IS THE SIZE OF ONE PIECE, AND A CARTON HOLDS N OF
            # THEM. The description path a few lines up already multiplies a
            # single piece by its CTN-N note; this path did not, so a line that
            # bought a CARTON was divided by ONE piece. Foodlink 100175:
            #
            #   BEANS BLACK WHOLE TIN A10   $8.70 "EA" -> $0.0029/g
            #                              $52.20 "CTN-6" -> $0.0174/g   6x OVER
            #
            # and 6 x $8.70 is exactly $52.20, so the carton reading is not a
            # judgement call. `pack_size` is the discriminator the invoice
            # already carries: the parser sets it to N when it has ALREADY
            # divided the carton into pieces (camembert SI4480678, $3.80 with
            # pack_size 12), and leaves it 1 when the price is the whole line
            # (SI4467596, $45.60 with pack_size 1). Multiply only in the second
            # case. Both camembert rows then land on the same $0.0304/g, which is
            # the check that this is right rather than merely consistent.
            _ctn = re.search(r"CTN[-\s]?(\d+)", r.get("note", "") or "", re.I)
            _ps = (r.get("pack_size") or "").strip()
            if _ctn and _ps in ("", "1"):
                oq = oq * Decimal(_ctn.group(1))
                how = f"chef-confirmed x CTN-{_ctn.group(1)} (invoice)"
            else:
                how = "chef-confirmed"
            qty, unit = oq, ou
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
                # SOLD-AS-BOUGHT RESCUE. The liquor path needs a per-ml seed; a can
                # has none. Same discipline, different unit: one can against the
                # Back Office price for one can, refused unless they agree.
                # A COUNTABLE SEED IS NOT ENOUGH — the product must not be a
                # MEASURED good. Brown onion carries a per-"can" Back Office seed of
                # $1.54 that actually means $1.54 a KILO, and the first cut of this
                # rescue matched it 1.00x and emitted "one unit". The number came out
                # right only because a chef-confirmed 1000 g override converted it
                # afterwards; on a product without that override it would have been a
                # unit error. So refuse anything either source calls measured:
                #   * a chef-confirmed pack override (someone declared a weight), or
                #   * a Back Office Unit of g / ml.
                # What is left is what is genuinely bought and sold by the piece.
                _cid = next((p for p in (bridge.get(iid) or ())
                             if p in countable_seed
                             and p not in overrides
                             and bo_units.get(p) not in ("g", "ml")), None)
                fb2 = seed_matched_countable_cost(pack_cost, countable_seed.get(_cid)) if _cid else None
                if fb2:
                    per, unit, _lbl = fb2
                    qty, bad, seed_liquor = Decimal(1), None, True
                    how = f"seed-matched {_lbl}"
            if not qty or not unit:
                # DECLARED-VESSEL RESCUE, the third and narrowest. The Alehouse
                # kegs, 2026-08-20: ILG's description is "ALEHOUSE CRISP KEG"
                # with raw_uom "1xKEG49." (their truncation), so resolve_pack
                # has no size to read; the liquor rescue needs a mass/volume
                # pack and refuses an "ea"; the countable rescue refuses
                # anything Back Office measures in ml. Forty-six weekly keg
                # rows — nineteen deliveries of each tap beer — died here as
                # "pack unreadable" while data/declared_conversions.yaml held
                # the answer the whole time: keg = 49,500 ml, chef-confirmed,
                # pinned in tests, declared FOR THIS VERY IDENTITY.
                #
                # So: when the identity carries a declared whole-vessel
                # conversion and the invoice line is ONE of that vessel
                # (pack_qty 1, unit ea/each or the vessel word), the line is
                # one vessel at the line price. No arithmetic is invented —
                # the declaration supplies the only number the parser lacked,
                # and the conversion block below restates it into base units
                # exactly as it does for the rows whose basis said per_keg.
                dv = declared.get(conversion_key(iid))
                if (dv and dv["from_unit"] in ("keg", "bottle", "can")
                        and str(r.get("pack_unit") or "").lower()
                            in ("ea", "each", dv["from_unit"])
                        and str(r.get("pack_qty") or "").strip() in ("1", "1.0", "1.000")):
                    qty, unit = Decimal(1), dv["from_unit"]
                    per = pack_cost
                    bad = None
                    how = f"declared vessel ({dv['from_unit']}, {dv['evidence'][:40]})"

        if not qty or not unit:
            skipped.append((r["supplier"], desc, f"pack unreadable ({how})"))
            continue
        if bad:
            skipped.append((r["supplier"], desc, bad))   # arithmetically fine, physically absurd
            continue

        # DECLARED CONVERSIONS APPLY AT CREATION, not only in the post-pass —
        # and an "each" of a declared keg IS that keg. The Alehouse case, found
        # 2026-08-20: ILG bills the kegs weekly with raw_uom "1xKEG49." (their
        # own truncation), which parses to 1 ea at the whole-keg price. The
        # declared conversion (keg = 49,500 ml, chef-confirmed) matched only
        # rows whose unit was literally "keg" — ONE row all year, the 14 Jul
        # invoice whose cost_basis happened to say per_keg — so nineteen weekly
        # deliveries of each keg sat in the book per-ea, un-restatable and, in
        # the post-pass order, invisible to the ProductID bridge below (which
        # runs before the post-pass ever restated them). The tap beers costed
        # off January seeds while their kegs were invoiced every week.
        #
        # Restating HERE means the bridge block sees millilitres: for the
        # Alehouse ids Back Office's own declared unit is ml, so the no-seed
        # fallback carries the rate across at 1:1 and every keg delivery
        # supersedes the seed the day it lands. The "ea" extension is narrow by
        # construction: it fires only for an identity that carries a DECLARED
        # whole-vessel conversion (keg/bottle/can), where "one each" and "one
        # vessel" are the same physical object.
        conv = declared.get(conversion_key(iid))
        declared_restated = False
        if conv and (str(unit).lower() == conv["from_unit"]
                     or (str(unit).lower() in ("ea", "each")
                         and conv["from_unit"] in ("keg", "bottle", "can"))):
            per = (per / conv["to_qty"]).quantize(Decimal("0.000001"))
            unit = conv["to_unit"]
            how = (f"{how} (declared {conv['from_unit']}"
                   f"={conv['to_qty']}{conv['to_unit']})")
            declared_restated = True

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
            # LAST RESORT, and deliberately the narrowest one available: a product
            # with NO seed has no conversion, so the block below never runs and the
            # product keeps costing $0 forever no matter how many invoices arrive.
            # Where Back Office DECLARES the base unit and the invoice is already
            # in that same unit, there is nothing to convert — no divisor, no
            # scale factor, no inference — so the rate can be taken as it stands.
            #
            # The magnitude guard further down cannot fire here (it compares
            # against a seed price, and there is none), which is exactly why this
            # refuses anything needing arithmetic. g->g and ml->ml only. A
            # different unit, a pack/each basis, or no declared unit all fall
            # through to the old behaviour: emit nothing.
            if not (sc and sc[0] > 0):
                _bu = bo_units.get(pid)
                if _bu and _bu == unit:
                    sc = (Decimal(1), _bu)
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
                # ...UNLESS the rate came through a DECLARED conversion that
                # names this very ProductID. The guard exists to refuse a
                # heuristic mapping whose magnitude betrays it; a conversion in
                # data/declared_conversions.yaml is the opposite of a heuristic
                # — a documented, evidenced ruling (the Alehouse keg's 49,500 ml
                # is a chef confirmation, pinned in tests). And the reference
                # the guard compares against is itself untrustworthy exactly
                # here: build_seed_conv reads the keg seeds' per-ml rate as if
                # it were per-keg, so seed_price for 20487298 comes out
                # 0.0000869/ml — 49,500x low — and the CORRECT invoice rate
                # reads as "49x out" and is refused. That is how nineteen
                # weekly keg deliveries never once superseded a January seed:
                # the one guard between them and the book was comparing against
                # a number that had been divided twice.
                if (bper is not None and sp and sp > 0
                        and not (declared_restated
                                 and declared.get(conversion_key(pid)))):
                    ratio = float(bper) / float(sp)
                    if ratio > 10 or ratio < 0.1:
                        bper = None
                if bper is not None:
                    rows.append({**row, "ingredient": pid, "unit": sunit,
                                 "cost_per_unit": str(bper), "pack": f"{how} (via {iid})"})
                    bridged += 1

    # DECLARED-CONVERSION RESTATEMENT (data/declared_conversions.yaml).
    # A supplier series priced per bottle cannot join a per-ml canon without
    # building the mixed-unit series this file refuses everywhere else. Where
    # a conversion is DECLARED — documented convention or BO name, never a
    # guess — restate the pack-unit rows into base units at derivation, so the
    # ingredient map's fence compares like with like. An id with no entry
    # keeps its source's unit; refusal stays the default.
    declared = load_declared_conversions()
    restated = 0
    for row in rows:
        conv = declared.get(conversion_key(row["ingredient"]))
        if conv and str(row["unit"]).lower() == conv["from_unit"]:
            per = (Decimal(str(row["cost_per_unit"])) / conv["to_qty"]
                   ).quantize(Decimal("0.000001"))
            row["cost_per_unit"] = str(per)
            row["unit"] = conv["to_unit"]
            row["pack"] = (f"{row.get('pack', '')} "
                           f"(declared {conv['from_unit']}={conv['to_qty']}{conv['to_unit']})").strip()
            restated += 1
    if restated:
        print(f"  {restated} pack-unit rows restated to base units via declared conversions")

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
