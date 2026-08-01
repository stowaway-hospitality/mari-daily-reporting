#!/usr/bin/env python3
"""
Build data/ingredients.json -- the chef-facing ingredient list.

    python3 scripts/build_ingredients.py

THE POINT
---------
The ingredient list is DERIVED FROM WHAT YOU ACTUALLY BOUGHT. It is not a
database anyone maintains. Buy something -> it appears. Stop buying it -> it
ages out. New supplier, new product, no admin.

This is the thing Lightspeed cannot do. Its product DB is hand-curated, which
is why SKU is populated on 3.9% of Stowaway / 5.4% of HG, why HG liquor is
0/144, and why the food menu -- the part that changed supplier -- has no
recipes and reports $0.00 cost on 4.6% of revenue.

THE CONVERSION THAT MATTERS
---------------------------
An invoice says   "SQUID PINEAPPLE CUT IMP U5 5KG"  $57.00
A chef thinks     "200g per serve"

So every ingredient needs a cost in a unit a chef will actually type. That
means parsing the pack out of the description:

    SQUID ... 5KG              -> 5 kg      -> $11.40/kg  -> $0.0114/g
    CHEESE CAMEMBERT 125GM     -> 125 g     -> $0.3648/g
    CORN CHIPS ... 6X500GM     -> 3000 g    -> $0.0158/g
    FLOUR TORTILLAS 12X63GM    -> 756 g     -> ...

WHERE THE PACK CANNOT BE PARSED WE DO NOT GUESS. The ingredient still ships,
flagged `needs_pack_review`, and the UI asks the chef to state the pack once.
A guessed pack size silently scales every recipe that uses it -- the same
class of error as a case total in a per-unit field, which is what
scripts/invoices/ exists to stop. Fail toward asking.

Traps encoded from real descriptions:
    "10INCH"  is not a pack     "U5" is a grade, not a count
    "200/300" is a size grade   "TRI" is a shape
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import sys  # noqa: E402
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.domain import purchasable_id   # noqa: E402  the SAME natural key the cost engine uses
from core.pack_overrides import load_pack_overrides   # noqa: E402
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "data" / "ingredients.json"
PACK_OVERRIDES = ROOT / "data" / "pack_overrides.yaml"

# Suppliers whose goods a chef cooks with. Liquor is a different UI problem
# (a bottle IS the unit); keep this list explicit rather than clever.
KITCHEN_SUPPLIERS = {
    "Select Fresh", "B&E", "Foodlink", "Gulli", "Sun Circle",
    "Fresh Fruit Team", "FFT", "Andrews Meat", "Jun Pacific",
}

# Consumables and packaging suppliers list alongside food but that are NEVER
# recipe ingredients — a chef should never see "napkins" in the ingredient
# picker. Matched as whole words on the description, kept deliberately tight:
# better to leave a stray non-food item in the list than to hide a real
# ingredient. ("box" is intentionally absent — pizza boxes are a real per-item
# cost Marilyna's tracks.)
_NON_FOOD = re.compile(
    r"\b(napkins?|serviettes?|scourer|stainless steel|container|gloves?|foil|"
    r"cling\s*wrap|garbage|bin\s*liner|paper\s*towel|chux|"
    # cleaning / chemicals — not a recipe ingredient
    r"chemical|detergent|dishwash(?:ing|er)?|sanitiser|sanitizer|degreaser|"
    r"rinse\s*aid|bleach)\b", re.I)


def is_non_food(desc: str) -> bool:
    return bool(_NON_FOOD.search(desc or ""))


# Countable food pieces sold "N x Wgm" — a chef uses ONE of them (one tortilla,
# one patty, one base), so cost PER PIECE, not per kg. This is what tells
# "12x91gm tortillas" (12 pieces -> $/each) apart from "6x2kg beef" (bulk -> $/kg).
_COUNTABLE = re.compile(
    r"\b(tortillas?|wraps?|pita|piadina|flatbreads?|bases?|shells?|"
    r"patt(?:y|ies)|burgers?|schnitzels?|schnitz|cutlets?|fillets?|"
    r"dough\s*balls?|balls?|buns?|rolls?|bagels?|crumpets?|pancakes?|pikelets?|"
    r"waffles?|blinis?|skewers?|sheets?|nuggets?)\b", re.I)


def is_countable(desc: str) -> bool:
    return bool(_COUNTABLE.search(desc or ""))


# Fresh Fruit Team (and occasionally others) leak the UNIT word into the supplier
# CODE — "AH20T Tray", "ONBRKG Kilogram", "HCMB Market" — a column bleed in the
# PDF parse. That mints a SECOND id for a product that already has a clean-code
# row, so the picker shows it twice, and the leaked row also carried a truncated
# description ("Hass" instead of "Avocado Hass"). Stripping the trailing unit
# collapses the two to one identity; the fuller description then wins on merge.
_CODE_UNIT_SUFFIX = re.compile(
    r"\s+(tray|kilogram|kilo|kgs?|litres?|market|each|ea|punnet|box(?:es)?|"
    r"bunch|bch|bags?|dozen|doz|ctn|carton)$", re.I)


def normalize_code(code: str) -> str:
    """Strip a trailing unit word bled into the code. Idempotent, multi-pass
    ('X Kg Each' -> 'X'). Never returns empty — falls back to the original."""
    c = (code or "").strip()
    prev = None
    while c != prev:
        prev, c = c, _CODE_UNIT_SUFFIX.sub("", c).strip()
    return c or (code or "").strip()


# A few FFT codes never appear with a full description anywhere in the data (no
# clean twin to inherit from). Map only the ones the code makes unambiguous;
# leave genuinely-cryptic ones (BBRYP, SPUN) alone rather than guess.
_NAME_FIX = {
    "ONBRKG": "Onion Brown", "KITAPDKG": "Apple Diced", "RHBCH": "Rhubarb",
    # confirmed against the FFT catalogue by exact price match (BBRYP $6.25 =
    # Blackberries punnet; SPUN $3.00 = Strawberries punnet).
    "BBRYP": "Blackberries", "SPUN": "Strawberries",
    # truncated FFT fragments with no clean twin — real names from the FFT catalogue.
    "MSHK": "Mushroom Button", "TGKG": "Tomatoes Gourmet",
    "TGL10BX": "Tomatoes Gourmet Large", "ZGKG": "Zucchini Green",  # was "... 0.5Kg please"
    # Select Fresh: "Tomato Cherry Pun" — "Pun" is a truncated Punnet.
    "TOMCP": "Tomato Cherry",
}


# A bare unit word tacked on the END of a name ("CARROT KG", "ONION BROWN BAG",
# "HERB BASIL BCH") — Select Fresh writes these. It reads raw AND hides near-dupes
# (the same onion as "... BAG" and "... KG" looks like two products). Strip it.
_TRAIL_UNIT = re.compile(
    r"[\s/]+(kgs?|kilogram|gm?|ml|lt?r?|bch|bunch|tray|bags?|box(?:es)?|ctn|carton|"
    r"ea|each|punnet|pkts?|packet|dozen|doz|market)$", re.I)


def clean_name(desc: str) -> str:
    """Display name: drop a trailing bare unit, and Title-case a fully-UPPERCASE
    name (produce) so 'CARROT KG' -> 'Carrot' reads like 'Avocado Hass'. Mixed-case
    names (they already carry brand casing, e.g. '... Heinz') are left untouched."""
    s = (desc or "").strip()
    prev = None
    while s != prev:
        prev, s = s, _TRAIL_UNIT.sub("", s).strip(" /-")
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        s = s.title()
        s = re.sub(r"(\d)([A-Z])", lambda m: m.group(1) + m.group(2).lower(), s)  # 5Mm -> 5mm
    return s or (desc or "").strip()


# Unit words that must never stand alone as a product name.
_UNIT_WORDS = {"punnet", "box", "bunch", "bch", "tray", "each", "ea", "bag",
               "kg", "kilogram", "market", "carton", "ctn", "packet", "pkt"}


def suspect_name(desc: str, code: str) -> str | None:
    """A build-time quality gate. Flags a display name that is almost certainly a
    parse artefact rather than a real product name — the class the FFT bug
    produced ('BBRYP Punnet' where the name IS the code). Deliberately narrow so
    it never fires on a real acronym product (MSG, BBQ): only when the name is
    empty, is nothing but a unit word, or literally echoes the supplier code."""
    n = re.sub(r"[^A-Za-z0-9]", "", desc or "").upper()
    c = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    if not n:
        return "empty name"
    if (desc or "").strip().lower() in _UNIT_WORDS:
        return "name is only a unit word"
    if c and len(n) >= 3 and (n == c or c.startswith(n)):
        return "name echoes the supplier code (parse artefact)"
    return None


def _better_name(a: str, b: str) -> str:
    """The fuller of two descriptions for the SAME product. Ranked by how many
    REAL words it has (a real word has a lowercase letter and no digits), then by
    FEWEST code-like tokens (all-caps blobs / anything with a digit — 'BRL',
    'AH20T'), then length. So 'Broccolini' beats 'BRL Box' and 'Avocado Hass'
    beats 'Hass'."""
    def score(s):
        words = (s or "").strip().split()
        real = sum(1 for w in words if re.search(r"[a-z]", w) and not re.search(r"\d", w) and len(w) > 1)
        codey = sum(1 for w in words if not re.search(r"[a-z]", w) or re.search(r"\d", w))
        return (real, -codey, len((s or "").strip()))
    return a if score(a) >= score(b) else b


RECENT_DAYS = 90

# --- pack parsing ----------------------------------------------------------
# Order matters: multipack before single, or "6X500GM" reads as "500GM".
# Unit alternation: longest first so "LTR"/"LITRE" win over "LT"/"L".
_UNIT = r"KG|GM|G|ML|LITRE|LTR|LT|L"
_MULTI = re.compile(rf"(?<![\d/])(\d{{1,3}})\s*[xX]\s*(\d+(?:\.\d+)?)\s*({_UNIT})\b", re.I)
_SINGLE = re.compile(rf"(?<![\d/xX])(\d+(?:\.\d+)?)\s*({_UNIT})\b", re.I)
# Case format "20/454gm" = twenty pieces of 454 g. The invoice prices the PIECE,
# so the second number is the pack we cost by. Distinct from a size grade like
# "200/300" (no unit) — the trailing unit is what tells them apart, which is why
# this is matched BEFORE _NOT_A_PACK strips bare "\d+/\d+".
_CASEPACK = re.compile(rf"(\d{{1,3}})\s*/\s*(\d+(?:\.\d+)?)\s*({_UNIT})\b", re.I)
# A bare count with no weight: "Pizza Boxes 11\" x 50", "Garlic Bread 9\" x 40".
# N discrete pieces, carton-priced — resolve_pack divides the line by N.
_COUNT = re.compile(rf"[xX]\s*(\d{{1,4}})\s*$")

_TO_BASE = {  # -> (grams|ml), base unit
    "KG": (1000, "g"), "GM": (1, "g"), "G": (1, "g"),
    "L": (1000, "ml"), "LT": (1000, "ml"), "LTR": (1000, "ml"),
    "LITRE": (1000, "ml"), "ML": (1, "ml"),
}

# Things that look like packs but are not. All from real invoice text.
_NOT_A_PACK = re.compile(r"\b(\d+\s*INCH|U\d+|\d+/\d+)\b", re.I)

# --- sanity bounds ---------------------------------------------------------
# A parsed pack can be arithmetically perfect and still 30x wrong, because
# descriptions state the PIECE size while the price is for the CASE. Caught
# on the very first run:
#
#     "CHEESE CAMEMBERT 125GM Rosenberg"  $45.60
#       -> parsed 125g -> $0.3648/g -> $364/kg
#
# Camembert is not $364/kg. The 125g is one wheel; $45.60 buys a box of them.
# This is the ILG/Paramount unit-cost trap in a chef's hat, and it matters:
# Baked Camembert is one of the 11 zero-cost products. Shipping this would
# move it from $0.00/serve (100% GP) to ~$45/serve (negative GP).
#
# Bounds are deliberately WIDE -- a smoke alarm, not a thermostat. Anything
# outside goes to the chef to state the pack, which is 30 seconds and correct,
# rather than into a recipe, which is silent and wrong for a month.
_BOUNDS = {
    #            min $/unit   max $/unit
    "g":        (Decimal("0.0005"), Decimal("0.20")),   # $0.50/kg .. $200/kg
    "ml":       (Decimal("0.0005"), Decimal("0.60")),   # $0.50/L .. $600/L (premium
                                                        # spirits + champagne reach
                                                        # $400-600/L cost; food ml
                                                        # liquids sit far below)
    "bunch":    (Decimal("0.50"),   Decimal("30.00")),
    "tray":     (Decimal("2.00"),   Decimal("120.00")),
    "punnet":   (Decimal("1.00"),   Decimal("30.00")),
    "ea":       (Decimal("0.05"),   Decimal("100.00")),
    "doz":      (Decimal("2.00"),   Decimal("120.00")),
    "box":      (Decimal("2.00"),   Decimal("400.00")),
    "pkt":      (Decimal("0.50"),   Decimal("80.00")),
    "bag":      (Decimal("0.50"),   Decimal("80.00")),
}


def out_of_bounds(cost_per_unit: Decimal, unit: str) -> str | None:
    b = _BOUNDS.get(unit)
    if not b:
        return None
    lo, hi = b
    if cost_per_unit < lo:
        return f"${cost_per_unit}/{unit} is implausibly CHEAP (< ${lo}) — pack likely overstated"
    if cost_per_unit > hi:
        return (f"${cost_per_unit}/{unit} is implausibly DEAR (> ${hi}) — the description "
                f"probably states the PIECE size while the price is for the CASE")
    return None


def parse_pack(desc: str) -> tuple[Decimal | None, str | None, str]:
    """
    -> (qty_in_base_units, base_unit, how)

    Returns (None, None, reason) when the pack is not confidently readable.
    That is a feature. See module docstring.
    """
    # Case format "N/Msize" must be read BEFORE _NOT_A_PACK strips bare "\d+/\d+".
    # The invoice prices one piece, so we cost by the piece size (the 2nd number).
    m = _CASEPACK.search(desc)
    if m:
        size, unit = Decimal(m.group(2)), m.group(3).upper()
        mult, base = _TO_BASE[unit]
        return size * mult, base, f"{m.group(1)}/{size}{unit.lower()} (per piece)"

    d = _NOT_A_PACK.sub(" ", desc)

    m = _MULTI.search(d)
    if m:
        count, size, unit = int(m.group(1)), Decimal(m.group(2)), m.group(3).upper()
        # Countable pieces (tortillas, patties, bases) cost PER PIECE — a chef uses
        # one, not a gram of it. Bulk multipacks (6x2kg beef) stay per-kg.
        if is_countable(desc):
            return Decimal(count), "ea", f"{count} x {size}{unit.lower()} (per piece)"
        mult, base = _TO_BASE[unit]
        return Decimal(count) * size * mult, base, f"{count}x{size}{unit.lower()}"

    m = _SINGLE.search(d)
    if m:
        size, unit = Decimal(m.group(1)), m.group(2).upper()
        mult, base = _TO_BASE[unit]
        return size * mult, base, f"{size}{unit.lower()}"

    # A bunch / each / tray is a legitimate unit -- not a failure to parse.
    for word, unit in (("BCH", "bunch"), ("BUNCH", "bunch"), ("TRAY", "tray"),
                       ("PUNNET", "punnet"), ("EACH", "ea"), ("DOZ", "doz")):
        if re.search(rf"\b{word}\b", desc, re.I):
            return Decimal(1), unit, word.lower()

    # BARE UNIT = PRICED BY THAT UNIT. "ONION BROWN KG" is not a missing pack
    # size; it is how produce is sold -- $2.40 per kg, buy what you like.
    # Missed on the first run and it skipped half of Select Fresh (onion,
    # carrot, lemon, garlic), which is most of what a kitchen actually cooks.
    m = re.search(rf"(?:^|\s)(?:/\s*)?({_UNIT})\s*$", desc, re.I)
    if m:
        u = m.group(1).upper()
        mult, base = _TO_BASE[u]
        return Decimal(mult), base, f"per {u.lower()}"

    # No weight anywhere, but a trailing count ("... x 50") = N discrete pieces.
    # Last resort, so a weighable pack always wins first.
    m = _COUNT.search(desc)
    if m:
        n = int(m.group(1))
        if 1 < n <= 2000:
            return Decimal(n), "ea", f"x{n} (count)"

    return None, None, "no pack found in description"


# Discrete units an invoice may name in the description OR a note, when there is
# no weight to parse. "Celeriac ... Each", "Tomatoes Cherry ... Punnet".
_DISCRETE = [
    ("BUNCH", "bunch"), ("BCH", "bunch"), ("PUNNET", "punnet"), ("TRAY", "tray"),
    ("BOX", "box"), ("EACH", "ea"), ("DOZ", "doz"), ("PKT", "pkt"), ("PACKET", "pkt"),
    ("BAG", "bag"),
]

# The unit some suppliers put as the last word of the code. Weight/volume words
# map to a base quantity; discrete words to a countable unit. "Market" is NOT
# here on purpose — it states a price basis, not a pack size, so it stays a
# confirm-once. Never guess a unit that isn't written down.
_CODE_UNITS = {
    "kilogram": (Decimal(1000), "g"), "kg": (Decimal(1000), "g"),
    "litre": (Decimal(1000), "ml"), "liter": (Decimal(1000), "ml"),
    "punnet": (Decimal(1), "punnet"), "bunch": (Decimal(1), "bunch"),
    "box": (Decimal(1), "box"), "tray": (Decimal(1), "tray"),
    "each": (Decimal(1), "ea"), "dozen": (Decimal(1), "doz"),
}


def _code_unit(code: str):
    w = (code or "").split()
    return _CODE_UNITS.get(w[-1].lower()) if w else None


def resolve_pack(desc: str, cost, basis: str = "", note: str = "", code: str = ""
                 ) -> tuple[Decimal | None, str | None, Decimal | None, str, str | None]:
    """
    THE one place a supplier line becomes a cost in a unit a chef can use.

    -> (qty_in_base_units, unit, cost_per_unit, how, review_reason|None)

    Uses the invoice's STRUCTURED fields, not just the free-text description —
    that is the fix for produce like "Cauliflower Florets" (no weight in the
    name, but the invoice says basis=per_kg) and "Celeriac … Each". Order:

      1. Liquor bases (per_bottle/keg/can): the unit IS the pack.
      2. Sold by weight/volume (per_kg / per_L): price already per kg/L. Cleanest.
      3. per_unit: read the pack weight from the description; a carton note
         (CTN-N) multiplies a single piece — this is what rescues the camembert
         ($45.60 is a box of 12 x 125g, not one 125g wheel).
      4. Still no weight: take a discrete unit the invoice names (Each/Punnet/
         Box/Bunch). Costable in that unit; the chef converts to grams once if
         they portion by weight.
      5. Genuinely unknown: ask, never guess.
    """
    cost = Decimal(str(cost))
    b = (basis or "").lower().replace("per_", "")
    note = note or ""

    if b in ("bottle", "keg", "can"):
        return Decimal(1), b, cost, b, out_of_bounds(cost, b)
    if b == "kg":
        return Decimal(1000), "g", (cost / 1000).quantize(Decimal("0.000001")), "per kg (invoice)", None
    if b in ("lt", "l", "litre"):
        return Decimal(1000), "ml", (cost / 1000).quantize(Decimal("0.000001")), "per L (invoice)", None

    qty, unit, how = parse_pack(desc)
    if qty and unit and unit in ("g", "ml"):
        ctn = re.search(r"CTN[-\s]?(\d+)", note, re.I)
        # A carton note multiplies a SINGLE piece. Skip it when the description
        # already stated the multiplicity ("6x500g") or a per-piece case format
        # ("20/454gm") — those are priced per piece, not per carton.
        if ctn and "x" not in how and "piece" not in how:
            n = int(ctn.group(1))
            qty, how = qty * n, f"{how} x CTN-{n} (invoice)"
        per = (cost / qty).quantize(Decimal("0.000001"))
        return qty, unit, per, how, out_of_bounds(per, unit)
    if qty and unit:                          # a discrete unit or a counted carton
        # Divide by qty so a counted carton ("x 50") costs per piece; harmless for
        # qty=1 (bunch/tray/each), where per == cost.
        per = (cost / qty).quantize(Decimal("0.000001"))
        return qty, unit, per, how, out_of_bounds(per, unit)

    # Some suppliers (Fresh Fruit Team) encode the sold unit in the code's
    # trailing word: "KITOSPKG Kilogram", "TCPUN Punnet", "HTBCH Bunch". Trust it
    # when the description gave us nothing — it is stated data, not a guess.
    cu = _code_unit(code)
    if cu:
        u_qty, u_base = cu
        per = (cost / u_qty).quantize(Decimal("0.000001"))
        return u_qty, u_base, per, f"code:{code.split()[-1].lower()}", out_of_bounds(per, u_base)

    hay = f"{desc} {note}"
    for word, u in _DISCRETE:
        if re.search(rf"\b{word}\b", hay, re.I):
            return Decimal(1), u, cost, f"per {u} (invoice)", out_of_bounds(cost, u)

    return None, None, None, how, "no pack size on the invoice — confirm once"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> int:
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    cutoff = date.today() - timedelta(days=RECENT_DAYS)
    overrides = load_pack_overrides(PACK_OVERRIDES)   # chef-confirmed pack sizes

    out, review = [], 0
    seen: set[str] = set()
    for r in rows:
        if r["supplier"] not in KITCHEN_SUPPLIERS:
            continue
        try:
            seen_date = datetime.fromisoformat(r["invoice_date"]).date()
        except Exception:
            continue
        if seen_date < cutoff:
            continue

        desc = r["invoice_description"].strip()
        if is_non_food(desc):
            continue   # napkins, scourers, containers — not a recipe ingredient
        # THE ID MUST BE THE SAME KEY THE COST ENGINE USES. build_costs keys cost
        # observations by purchasable_id (supplier-slug + ":" + UPPERCASE code).
        # This used to slug the whole thing ("foodlink-102689"), which never
        # matched the cost key ("foodlink:102689") — so EVERY recipe saved from
        # the builder failed to cost (MissingCost). A code-less line has no cost
        # identity (build_costs drops it too), so skip it rather than mint a fake id.
        code = (r["supplier_code"] or "").strip()
        if not code:
            continue
        key = purchasable_id(r["supplier"], code)
        if key in seen:
            continue
        seen.add(key)

        pack_cost = Decimal(r["cost_per_unit_incl_gst"])
        qty, unit, per, how, bad = resolve_pack(
            desc, pack_cost, basis=r.get("basis", ""), note=r.get("note", ""), code=code)
        # A confirmed pack (chef or catalogue) is AUTHORITATIVE — it wins even over
        # a resolved pack, because it also corrects a WRONG resolution: a box of
        # loose produce parses to "1 box" (uncostable in a recipe — you can't use
        # half a box) and the override replaces it with the real weight/count.
        if key in overrides:
            oq, ou = overrides[key]
            qty, unit, bad, how = oq, ou, "", "chef-confirmed"
            per = pack_cost / oq

        item = {
            "id": key,
            "description": clean_name(desc),   # tidy display; raw desc still drove resolve_pack
            "supplier": r["supplier"],
            "supplier_code": r["supplier_code"] or None,
            "pack_cost_incl": str(pack_cost),
            "source_invoice": r["source_invoice"],
            "last_seen": r["invoice_date"],
            "venue": r["venue"],
        }
        if qty and unit:
            item["pack_qty"] = str(qty)
            item["pack_unit"] = unit
            item["pack_parsed_as"] = how
            item["cost_per_base_unit"] = str(per)   # the number the UI multiplies by
            item["needs_pack_review"] = bool(bad)
            if bad:
                item["review_reason"] = bad         # arithmetically fine, physically absurd
                review += 1
            else:
                item["needs_pack_review"] = False
        else:
            item["needs_pack_review"] = True
            item["review_reason"] = bad or how
            review += 1
        out.append(item)

    # Collapse parser-artefact duplicates: two rows whose only real difference is
    # a unit word bled into the supplier code ("AH20T" vs "AH20T Tray") are the
    # SAME product shown twice, and the bled row also carried a truncated name
    # ("Hass" vs "Avocado Hass"). Group by the NORMALISED code, keep the better-
    # resolved row for the pack/id, and give it the fullest name across the pair.
    collapsed: dict[tuple[str, str], dict] = {}
    for it in out:
        nkey = (it["supplier"], normalize_code(it.get("supplier_code") or ""))
        cur = collapsed.get(nkey)
        if cur is None:
            collapsed[nkey] = it
            continue
        keep, other = ((it, cur) if (cur.get("needs_pack_review")
                       and not it.get("needs_pack_review")) else (cur, it))
        keep["description"] = _better_name(keep["description"], other["description"])
        collapsed[nkey] = keep
    out = list(collapsed.values())
    # a few codes never carry a full name anywhere — apply the explicit fix
    for it in out:
        fix = _NAME_FIX.get(normalize_code(it.get("supplier_code") or "").upper())
        if fix:
            it["description"] = fix

    # Second pass: two DIFFERENT codes can still be the same product once the
    # names match (Carrot Large 'CLKG' per-kg vs 'CL20KGBX' the 20kg box — same
    # carrots, same $/kg). Collapse identical (supplier, name), keeping the most
    # useful row: resolved over flagged, a weight/volume unit over a discrete one
    # (a chef portions by gram), then the cheaper. Exact-name within one supplier
    # is safe — it is the same product bought two ways.
    def _rank(it):
        return (0 if not it.get("needs_pack_review") else 1,
                0 if it.get("pack_unit") in ("g", "ml") else 1,
                float(it.get("cost_per_base_unit") or 1e9))
    byname: dict[tuple[str, str], dict] = {}
    for it in out:
        k = (it["supplier"], it["description"])
        cur = byname.get(k)
        if cur is None or _rank(it) < _rank(cur):
            byname[k] = it
    out = list(byname.values())
    review = sum(1 for i in out if i.get("needs_pack_review"))

    out.sort(key=lambda i: (i["needs_pack_review"], i["description"]))

    # QUALITY GATE: a name that echoes its code / is only a unit is a parse
    # artefact (the FFT class of bug). Surface it here and in the feed so it gets
    # noticed the day it appears, not when a chef squints at "BBRYP Punnet".
    suspects = [(i["supplier"], i["description"], i.get("supplier_code"),
                 suspect_name(i["description"], i.get("supplier_code") or ""))
                for i in out if suspect_name(i["description"], i.get("supplier_code") or "")]

    OUT.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": RECENT_DAYS,
        "source": "supplier invoices (scripts/invoices/) via data/cogs_list.csv",
        "note": "Derived from what was actually purchased. Nobody maintains this list.",
        "suspect_names": [{"supplier": s, "name": n, "code": c, "why": w}
                          for s, n, c, w in suspects],
        "ingredients": out,
    }, indent=2))

    print(f"{len(out)} ingredients -> {OUT.relative_to(ROOT)}")
    print(f"  pack parsed:  {len(out)-review}")
    print(f"  needs review: {review}  (UI asks the chef; we do not guess)")
    if suspects:
        print(f"  ⚠ suspect names: {len(suspects)}  (likely a parse artefact — check the parser)")
        for s, n, c, w in suspects:
            print(f"      {s} | {n!r} (code {c!r}) — {w}")
    print("\nsample:")
    for i in out[:8]:
        if i["needs_pack_review"]:
            print(f"  [review] {i['description'][:40]:<42} ${i['pack_cost_incl']:>8}  ({i['review_reason']})")
        else:
            print(f"  {i['description'][:40]:<42} ${i['cost_per_base_unit']}/{i['pack_unit']}"
                  f"   (pack {i['pack_parsed_as']} @ ${i['pack_cost_incl']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
