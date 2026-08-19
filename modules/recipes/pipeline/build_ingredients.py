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

import sys
import csv
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
import re as _re
from pathlib import Path

import sys  # noqa: E402
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.domain import (canonical_purchasable, normalize_code,       # noqa: E402
                         prefer_cost_row,
                         purchasable_id)   # the SAME natural key the cost engine uses
from core.pack_overrides import load_pack_overrides   # noqa: E402
from modules.invoices.pack_size import single_unit_content   # noqa: E402
COGS = ROOT / "data" / "cogs_list.csv"
OUT = ROOT / "data" / "ingredients.json"
PACK_OVERRIDES = ROOT / "data" / "pack_overrides.yaml"

# Suppliers whose goods a chef cooks with. Liquor is a different UI problem
# (a bottle IS the unit); keep this list explicit rather than clever.
#
# MATCHED ON THE TRADING NAME, NOT THE EXACT STRING. This used to be an exact
# `in` test against the supplier column, and the supplier column carries whatever
# the invoice header says — which for the same company is not one string:
#
#     "Jun Pacific"                            4 rows   (matched, in the picker)
#     "Jun Pacific Corporation Pty Ltd"      128 rows   (did NOT match, invisible)
#     "Sun Circle"                             4 rows   (matched, but code-less)
#     "Sun Circle Food Manufacturing Pty Ltd"  8 rows   (did NOT match, invisible)
#
# So 86 recent coded Jun Pacific rows (23 SKUs of Asian pantry goods) and every
# Sun Circle dumpling were silently missing from the chef's picker — not because
# of a parser or a price, but because the legal name has "Corporation Pty Ltd" on
# the end. Nothing reported it: an ingredient that never appears raises no flag.
#
# _is_kitchen strips the legal suffixes and matches the trading name as a prefix,
# so any of the above spellings resolves to the same supplier. The list below
# stays explicit — that part of the original instinct was right — and the test
# in test_ingredient_quality pins BOTH the inclusions and the liquor exclusions,
# because a prefix rule that quietly swallowed ILG or Paramount would put bottles
# in a food picker.
KITCHEN_SUPPLIERS = {
    "Select Fresh", "B&E", "Foodlink", "Gulli", "Sun Circle",
    "Fresh Fruit Team", "FFT", "Andrews Meat", "Jun Pacific",
    # Kitchen suppliers that were never listed at all, so their costs have never
    # reached a recipe: JFC (Japanese dry goods), The Berry Man (produce),
    # F J Chickens / Farmer Joes (poultry), Nicholas Seafood (fish).
    "JFC", "JFC Australia", "The Berry Man", "F J Chickens", "Farmer Joes",
    "Nicholas Seafood", "Aquarius",          # Aquarius Fisheries — seafood
}

# Legal-entity noise that is never part of a trading name.
# "the" is in here because the legal name is "The Fresh Fruit Team Pty Ltd" while
# the trading name is "Fresh Fruit Team" — dropping it from BOTH sides means the
# article can sit on either and still match ("The Berry Man" resolves the same).
_SUPPLIER_NOISE = re.compile(
    r"\b(the|pty|ltd|limited|inc|incorporated|corporation|corp|"
    r"co|company|group|holdings|australia|aust|nsw|qld|vic)\b", re.I)


def _norm_supplier(s: str) -> str:
    s = _SUPPLIER_NOISE.sub(" ", (s or ""))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


_KITCHEN_NORM = {_norm_supplier(s) for s in KITCHEN_SUPPLIERS}
_KITCHEN_NORM.discard("")


def is_kitchen_supplier(supplier: str) -> bool:
    """True if this invoice's supplier is one a chef cooks from.

    Prefix match on the NORMALISED trading name, so "Jun Pacific",
    "Jun Pacific Corporation Pty Ltd" and "JUN PACIFIC CORP" are one supplier.
    Prefix (not equality) is what catches "Sun Circle Food Manufacturing", where
    the extra words are a description of the business rather than legal noise.
    """
    n = _norm_supplier(supplier)
    if not n:
        return False
    return any(n == k or n.startswith(k + " ") for k in _KITCHEN_NORM)

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
# description ("Hass" instead of "Avocado Hass").
#
# normalize_code NOW LIVES IN core/domain.py, because the bleed is an identity
# problem and identity is core's job: purchasable_id applies it, so the COST KEY
# and the picker id collapse to one product instead of two. Re-exported here
# because this is where it was born and where the pack parser still needs it —
# resolve_pack reads that same trailing word to learn the sold unit, so the raw
# code goes to the parser and the normalised one to the identity.


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
    # FFT codes that echoed into the name on older invoices (the real name sits in
    # the note column: "Eggplant", "Lime"). Confirmed from the same rows' notes.
    "EGPKG": "Eggplant", "EGPLBX": "Eggplant",  # EGPLBX = Eggplant box (note col confirms)
    "LMKG": "Lime", "PKG": "Plums", "LKKG": "Leek",
}


# BRANDS THE INVOICE DROPS AND THE PICKER NEEDS. Zak: "i need brands in things
# like this so it's certain when i pick it."
#
# Some suppliers print the brand in the description and some do not, and B&E is
# the one that does not: its invoice line reads "TOMATO - PIZZA SAUCE" while its
# own catalogue calls the same code 14580 "Tomato - Pizza Sauce 5x3kg Ctn
# #Kau04-4 Kagome". So the sauce Marilyna's puts on every pizza WAS in the
# picker, correctly costed at $2.33/kg over the 15 kg carton — it just could not
# be found by the name anyone actually uses for it, and two different tomato
# sauces sat next to each other with nothing to tell them apart.
#
# Keyed by (supplier, code) rather than code alone: B&E's codes are numeric and
# a bare number is far too easy to collide with another supplier's. The brand is
# APPENDED rather than replacing the name, so it composes with _NAME_FIX and with
# the descriptions suppliers already get right, and it is skipped when the name
# already contains it (Foodlink writes "PECAN NUT 1KG Natures" itself).
#
# Confirmed on befoodsonline 2026-08-15, by code and price.
_BRAND = {
    ("B&E", "14580"): "Kagome",      # Tomato - Pizza Sauce 5x3kg Ctn, $35.00/CTN
}


def with_brand(supplier: str, code: str, desc: str) -> str:
    """Append the supplier's brand when the invoice description omits it."""
    b = _BRAND.get((supplier, (code or "").strip()))
    if not b or b.lower() in (desc or "").lower():
        return desc
    return f"{desc} {b}".strip()


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


def _word_suffix(frag: str, full: str) -> bool:
    """True if `full` is `frag` with extra WORDS on the front ("Carrot Large" vs
    "Large"). Word-boundary anchored, so "Bean Green" does not match "Green Bean"
    and "Radish Red" cannot swallow an unrelated "Red"."""
    if len(full) <= len(frag) or not full.lower().endswith(frag.lower()):
        return False
    return full[len(full) - len(frag) - 1] in " -/"


def _undo_dropped_prefix(seq: list[str]) -> str:
    """
    `seq` is one supplier code's descriptions, NEWEST FIRST. Normally the latest
    name wins — a supplier renaming its own product must show the current name.

    But a parse artefact is not a rename. Until 2026-08-15 the FFT parser dropped
    the description's FIRST WORD into the unit column on any invoice whose header
    sat left of a hard-coded boundary, so one code carried both "Carrot Large" and
    "Large", and whichever the most recent invoice happened to be decided the name
    the chef saw. That is how "Large", "Hass", "Jap" and "Sweet" became products.

    A dropped leading word is recognisable without guessing: the fragment is a
    WORD-BOUNDARY SUFFIX of a longer spelling of the SAME code. A genuine rename
    is not ("Carrot Large" -> "Carrot Jumbo" shares no suffix), so this can only
    undo the artefact. Measured on the live cost book: it repairs 44 of the 52 FFT
    codes that carry more than one description, and touches nothing else.

    The parser fix stops new ones appearing; this repairs the history already in
    data/, which cannot be re-parsed (the source PDFs live behind the Supabase
    service key, which this pipeline must never hold).
    """
    latest = seq[0]
    best = latest
    for other in seq:
        if _word_suffix(latest, other) and len(other) > len(best):
            best = other
    return best


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

    # THE SUPPLIER'S OWN UOM OUTRANKS A SCAVENGED DESCRIPTION, where the UOM
    # names one sellable thing and states its size ("200g punnet"). A description
    # is free text: it wraps, it truncates, and it carries substitution notes.
    # MKB500PUNN arrived as "Punnet) 8 x 100g packs supplied for" — a fragment
    # whose "8 x 100g" is the TOTAL of a four-punnet line — and reading it as one
    # punnet booked King Brown mushrooms at $7.56/kg against the $30.25/kg every
    # other delivery of the same code states.
    #
    # single_unit_content refuses a multi ("6x700ML") and a bulk label ("CTN-6")
    # precisely so this cannot become the case/bottle error in a new place: those
    # need a second source to tell a case price from a bottle price, which is
    # seed_matched_liquor_cost's job, not this one's.
    suc = single_unit_content(note)
    if suc:
        _q, _u = suc
        _base = (_q * 1000).quantize(Decimal("0.000001"))
        _bu = "g" if _u == "kg" else "ml"
        _per = (cost / _base).quantize(Decimal("0.000001"))
        return _base, _bu, _per, f"{note.strip()} (invoice UOM)", out_of_bounds(_per, _bu)

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



def _container_sizes() -> dict:
    """item_id -> (qty, unit): the SIZE OF THE THING YOU DRAW FROM.

    Zak, 2026-08-19: "for bottles of spirits, such as tequila, i want to see the
    price of the bottle in the recipe, not the per L price... i just want to know
    the weight/volume of the thing i'm drawing from."

    A rate answers "what does a litre cost". Nobody buys a litre of tequila. The
    question a person actually has, standing at the shelf, is what the BOTTLE
    cost and how big it is -- and a rate quietly hides both. $79.70/L is a 700 ml
    bottle at $55.79, and only one of those two numbers is a thing you can pick
    up.

    data/container_sizes.csv has carried the answer since the Back Office
    DefaultSize harvest; nothing was reading it into the picker.
    """
    import csv as _csv
    out = {}
    f = ROOT / "data" / "container_sizes.csv"
    if not f.exists():
        return out
    for r in _csv.DictReader(f.open(encoding="utf-8-sig")):
        try:
            q = Decimal(str(r["base_qty"]))
        except Exception:                                    # noqa: BLE001
            continue
        if q > 0:
            out[r["item_id"]] = (q, r["base_unit"], r.get("container") or "container")
    return out


def main() -> int:
    # stdout is output too — see build_costs.py. An em-dash in a progress line
    # under an ASCII locale kills a run whose files are already correct.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = list(csv.DictReader(COGS.open(encoding="utf-8-sig")))
    cutoff = date.today() - timedelta(days=RECENT_DAYS)
    # Chef-confirmed pack sizes, re-keyed through the SAME canonicalisation the
    # identity uses. The log was written when a bled code was its own product, so
    # four confirmations are filed under the bled spelling ("...:TGL10BX BOX",
    # "...:LRW15BG BOX", "...:HCMB MARKET", "...:HCDRMB MARKET"). Left raw they
    # would stop matching the moment the identity collapsed, and the four
    # ingredients they cover would fall back to an uncostable "1 box".
    overrides = {canonical_purchasable(k): v
                 for k, v in load_pack_overrides(PACK_OVERRIDES).items()}

    # A pack override is either a MEASURE ("this unit holds 500 ml / 2500 g") or
    # a COUNT ("a carton holds 40 of them"). The two mean different things and
    # only the first may be applied to a cost-book row — see the long note at the
    # cost-book branch, and the 40x-288x understatement it documents.
    _MEASURE_UNITS = {"ml", "l", "lt", "litre", "litres", "g", "kg", "gram",
                      "grams", "kilogram", "kilograms"}

    def _ou_is_measure_unit(ov) -> bool:
        if not ov:
            return False
        _q, _u = ov
        return (_u or "").strip().lower() in _MEASURE_UNITS

    # THE LATEST INVOICE FOR EACH THING, NOT THE FIRST ONE IN THE FILE.
    #
    # cogs_list.csv is sorted oldest-first, and this loop used to keep whichever
    # row it met first and skip the rest. So the picker a chef builds a recipe in
    # showed the OLDEST price inside the 90-day window: 257 of 477 ingredients
    # were on a superseded row and 98 of those had since changed price. Sriracha
    # read $7.10 against a current $4.70; a tin of black beans read $52.20 against
    # $8.70, a 6x overstatement sitting in the UI.
    #
    # It also put ingredients.json and costs.csv on different observations of the
    # same product — costs.csv keys by the LATEST, as every consumer does — which
    # is what the pipeline seam test kept catching and what a "unit split" between
    # the two files really was.
    #
    # Ties go to the later row in the file, which is the same tie-break the cost
    # book uses, so the two files can never disagree about which one it is.
    rows.sort(key=lambda r: r.get("invoice_date") or "")
    out, review = [], 0
    seen: set[str] = set()
    # THE FULLEST NAME ACROSS EVERY SPELLING OF ONE IDENTITY. The bled-code row
    # carries a truncated description — the parser generation that moved the unit
    # word into the code also moved the first word of the name into the note, so
    # "AH20T Tray" is described as "Hass" while "AH20T" is "Avocado Hass". Now
    # that both are one identity only ONE row survives the loop (the latest, as
    # the cost book keys), and on 2026-08-04 that row happens to be the truncated
    # one — so the merge that used to happen in the collapse pass below has to
    # happen here instead, or the picker loses the real name.
    #
    # Keyed by (identity, RAW code) and holding that code's LATEST name, then
    # merged across codes. Not "the best name in the whole history": a supplier
    # renaming its own product must still show the current name, and pooling
    # every row would have quietly reverted 20 unrelated descriptions to older
    # spellings. Only the parser artefact is being undone.
    name_hist: dict[tuple[str, str], list[str]] = {}
    for r in reversed(rows):
        if not is_kitchen_supplier(r["supplier"]):
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
        # purchasable_id normalises a unit word the PDF parse bled onto the code,
        # so "AH20T" and "AH20T Tray" are ONE ingredient here exactly as they are
        # one series in costs.csv. resolve_pack below still gets the RAW code,
        # because that trailing word is how some Fresh Fruit Team lines state
        # their sold unit.
        key = purchasable_id(r["supplier"], code)
        name_hist.setdefault((key, code.upper()), []).append(clean_name(desc))  # newest-first
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
        #
        # AUTHORITATIVE ABOUT THE PACK — NOT ABOUT THE RATE. `bad` used to be
        # cleared to "" here, which turned a chef confirmation into a blanket
        # silencer for the plausibility guard. A per-carton line divided by a
        # per-piece override then published an absurd rate that nothing looked at:
        # Foodlink 100487 camembert billed $45.60 for a carton (note "UOM CTN-12")
        # against a 125 g pin read $364.80/kg, twelve times the $30.40/kg the same
        # code's own "EA" lines charge. The override still decides the pack; the
        # bounds still judge what falls out of it. See build_costs.py.
        if key in overrides:
            oq, ou = overrides[key]
            # A CONFIRMED PACK IS THE SIZE OF ONE PIECE, AND A CARTON HOLDS N OF
            # THEM. The resolve_pack path above already multiplies a single piece
            # by its CTN-N note; THIS path did not, so a line that bought a
            # CARTON was divided by ONE piece. Foodlink 100175:
            #
            #   BEANS BLACK WHOLE TIN A10   $8.70 "EA"    -> $0.0029/g
            #                              $52.20 "CTN-6" -> $0.0174/g   6x OVER
            #
            # and 6 x $8.70 is exactly $52.20, so the carton reading is not a
            # judgement call. build_costs.py has carried this rule since the
            # camembert (100487) and its comment says "Must match
            # build_ingredients" — it did not, and the two files disagreed by
            # exactly 6x until the rate-comparison seam test caught it in CI.
            # Kept character-for-character in step with build_costs.py: the
            # discriminator is `pack_size`, which the parser sets to N when it has
            # ALREADY divided the carton into pieces and leaves at 1 when the
            # price is the whole line, so we multiply only in the second case.
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

    # Undo the dropped leading word BEFORE merging across codes (see
    # _undo_dropped_prefix). This is the half the (key, code) keying could not do:
    # both spellings share one raw code, so "latest wins" kept whichever the most
    # recent invoice happened to carry — and for 44 FFT codes that was the
    # truncated one.
    names = {k: _undo_dropped_prefix(v) for k, v in name_hist.items()}

    # the fullest description across the spellings of this identity (see `names`)
    best: dict[str, str] = {}
    for (key, _code), nm in names.items():
        best[key] = _better_name(best[key], nm) if key in best else nm
    for it in out:
        it["description"] = best.get(it["id"], it["description"])

    # Also expose the Lightspeed cost-book items (beverages, seeded + bridged foods)
    # as first-class ingredients, keyed lightspeed:<ProductID> — the SAME id the
    # scraped recipes reference — so every recipe ingredient is pickable and costs
    # LIVE off the invoice-fed book (not a frozen number). These aren't 90-day
    # windowed: they're the recipe ingredient universe and must always be available.
    bo_name: dict[str, str] = {}
    for _p in (ROOT / "data" / "bo_exports" / "stowaway_products.csv",
               ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"):
        if _p.exists():
            for _r in csv.DictReader(_p.open(encoding="utf-8-sig")):
                bo_name.setdefault(_r["ProductID"], _r["ProductName"])
    latest_ls: dict[str, tuple[str, str, str]] = {}
    _costs = ROOT / "data" / "costs.csv"
    if _costs.exists():
        for _r in csv.DictReader(_costs.open(encoding="utf-8-sig")):
            _id = _r["ingredient"]
            if not _id.startswith("lightspeed:"):
                continue
            _cand = (_r["cost_per_unit"], _r["unit"], _r["observed_on"])
            if prefer_cost_row(latest_ls.get(_id), _cand):
                latest_ls[_id] = _cand
    for _id, (_cost, _unit, _d) in latest_ls.items():
        pid = _id.split(":", 1)[1]
        if _id in seen:
            continue
        seen.add(_id)
        # normalise to the RECIPE base unit (recipes portion in ml / g). The cost
        # book may hold a spirit per-LITRE or a food per-KG; the builder multiplies
        # cost x a ml/g qty, so a per-L cost x 60 ml would read $3216 for a nip.
        try:
            _per = Decimal(_cost)
        except Exception:
            continue
        _u = (_unit or "").strip().lower()
        # Was the cost book already holding a RATE (per litre / per kilo), or the
        # price of one whole CONTAINER? The answer decides whether a confirmed
        # pack may be applied below, so it is recorded rather than inferred twice.
        _is_rate = _u in ("l", "lt", "litre", "litres", "kg", "ml", "g")
        if _u in ("l", "lt", "litre", "litres"):
            _per, _unit = _per / 1000, "ml"
        elif _u == "kg":
            _per, _unit = _per / 1000, "g"
        else:
            _unit = _unit or "ea"
        _qty, _how, _cpbu = Decimal(1), "cost-book", _per

        # A CONFIRMED PACK APPLIES HERE TOO. This branch used to hard-code
        # pack_qty "1" and never look at `overrides`, which meant
        # data/pack_overrides.yaml had NO EFFECT on any lightspeed:* row — the
        # exact population that needs it most, because the Back Office seed
        # writes "can" for anything it cannot size. Every "wrong unit" flag the
        # daily product review has raised against a cost-book row (about thirty
        # spirits on 2026-08-15, San Pellegrino and the A12 peppers on 08-16) was
        # therefore unfixable by the one mechanism built for fixing it: Zak could
        # confirm a pack and the feed would ignore him.
        #
        # TWO GUARDS, AND BOTH WERE EARNED BY A REAL 40x-288x ERROR THIS EXACT
        # CHANGE PRODUCED ON ITS FIRST DRAFT. Caught by diffing the feed before
        # and after, which is the standard this pipeline has had since the 6x
        # understatement of 2026-08-15.
        #
        #   1. NOT A RATE. If the cost book already holds a rate (a spirit per
        #      litre, a meat per kilo) then _per is ALREADY per ml or per g, and
        #      dividing by the pack size again understates by the whole pack — a
        #      700 ml gin would read 700x cheap.
        #
        #   2. THE OVERRIDE MUST CONVERT TO A MEASURE, NOT A COUNT. A cost-book
        #      row is priced per ONE PURCHASABLE UNIT. An override in ml or g
        #      says "one of those units CONTAINS this much", which is the only
        #      way to make it costable by mass/volume — that is a real
        #      conversion and it applies. An override in "ea" says something
        #      else entirely: "a CARTON holds N of them". That is a fact about
        #      the carton, not this row, and the upstream bridge has ALREADY
        #      applied it — costs.csv records the evidence in its pack column
        #      ("x40 (count) (via gulli:AGBGARBRE-B)", "chef-confirmed").
        #      Applying it a second time here divided nine already-correct
        #      products by their pack size again:
        #          Garlic Bread          $1.4953/ea -> $0.0374   (40x cheap)
        #          Large Pizza Box 13"   $0.6426/ea -> $0.0129   (50x)
        #          Flour Tortillas 6"    $0.1167/ea -> $0.0004  (288x)
        #      Every one of those is the flattering direction, and none would
        #      have tripped a bound or a test.
        _ou_is_measure = _ou_is_measure_unit(overrides.get(_id))
        if not _is_rate and _ou_is_measure:
            _oq, _ou = overrides[_id]
            if _oq and _oq > 0:
                _qty, _unit, _how = _oq, _ou, "chef-confirmed"
                _cpbu = (_per / _oq).quantize(Decimal("0.000001"))
        out.append({
            "id": _id, "description": clean_name(bo_name.get(pid, pid)),
            "supplier": "Lightspeed", "supplier_code": pid,
            "pack_cost_incl": str(_per), "source_invoice": "cost-book", "last_seen": _d,
            "venue": "stowaway", "pack_qty": str(_qty), "pack_unit": _unit,
            "pack_parsed_as": _how, "cost_per_base_unit": str(_cpbu),
            "needs_pack_review": False,
        })

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
        # Brand LAST, so it composes with the fix above rather than being
        # overwritten by it — and before the name-collapse below, because two
        # sauces that differ only by brand must stop colliding once the brand
        # is on them.
        it["description"] = with_brand(it.get("supplier") or "",
                                       it.get("supplier_code") or "",
                                       it["description"])

    # Second pass: two DIFFERENT codes can still be the same product once the
    # names match (Carrot Large 'CLKG' per-kg vs 'CL20KGBX' the 20kg box — same
    # carrots, same $/kg). Collapse identical (supplier, name), keeping the most
    # useful row: resolved over flagged, a weight/volume unit over a discrete one
    # (a chef portions by gram), then the cheaper. Exact-name within one supplier
    # is safe — it is the same product bought two ways.
    # ...BUT ONLY IF THE MONEY AGREES. "Same name, one supplier" is NOT enough on
    # its own, and the tiebreak below prefers the CHEAPER row, so a wrong merge
    # here silently deletes the dearer product — the "errors that flatter you"
    # direction this codebase exists to refuse.
    #
    # Fresh Fruit Team sells the same herb as a single bunch AND as a MARKET
    # bunch, and calls both "Herb Chives" on the invoice; only the code and the
    # price tell them apart (HCBCH $2.42 vs HCMB $15.40, HCB $2.64 vs HCDRMB
    # $7.70). They escaped this pass purely because the old parser bug had
    # truncated one of each pair to "Chives" / "Coriander". Repairing those names
    # on 2026-08-15 made the names match and the collapse promptly dropped both
    # market bunches — a 6x and a 3x understatement on two packs Zak had himself
    # confirmed in pack_overrides. Caught before shipping by diffing the feed
    # against the pre-change build.
    #
    # So: merge only when the canonical $/unit agree within 10%. If they do not,
    # these are two different packs that share a name — keep BOTH and disambiguate
    # with the supplier's own code, which is the only distinguishing fact we have
    # that was not invented here.
    def _rank(it):
        return (0 if not it.get("needs_pack_review") else 1,
                0 if it.get("pack_unit") in ("g", "ml") else 1,
                float(it.get("cost_per_base_unit") or 1e9))

    def _cost(it):
        try:
            return float(it.get("cost_per_base_unit"))
        except (TypeError, ValueError):
            return None

    def _same_money(a, b) -> bool:
        ca, cb = _cost(a), _cost(b)
        if ca is None or cb is None:
            return True                     # nothing to compare -> old behaviour
        if a.get("pack_unit") != b.get("pack_unit"):
            return False                    # $/ea vs $/g is not the same question
        hi = max(ca, cb)
        return hi <= 0 or abs(ca - cb) / hi <= 0.10

    byname: dict[tuple[str, str], dict] = {}
    kept_apart: list[dict] = []
    for it in out:
        k = (it["supplier"], it["description"])
        cur = byname.get(k)
        if cur is None:
            byname[k] = it
        elif _same_money(cur, it):
            if _rank(it) < _rank(cur):
                byname[k] = it              # same product bought two ways
        else:
            kept_apart.append(it if _rank(cur) <= _rank(it) else cur)
            if _rank(it) < _rank(cur):
                byname[k] = it
    for it in kept_apart:
        code = (it.get("supplier_code") or "").strip()
        # WHOLE WORD, not substring. Select Fresh sells limes by the kg (LIMK)
        # and by the tray (LIM), both described "Limes" at different $/unit, so
        # both are kept and one takes its code as a suffix. It never did: "LIM"
        # is a substring of "LIMES", the check thought the code was already in
        # the name, and the feed shipped two ingredients called "Limes" — which
        # is precisely the duplicate this pass exists to prevent.
        if code and not _re.search(rf"\b{_re.escape(code.upper())}\b",
                                   (it["description"] or "").upper()):
            it["description"] = f"{it['description']} ({code})"
    out = list(byname.values()) + kept_apart
    review = sum(1 for i in out if i.get("needs_pack_review"))

    out.sort(key=lambda i: (i["needs_pack_review"], i["description"]))

    # WHAT AM I DRAWING FROM? Stamp the container on every ingredient that has
    # a declared one, so the picker can say "$55.79 / 700ml bottle" instead of
    # "$79.70/L". A rate is a derived number; the bottle is the thing on the
    # shelf, and it is what a person can check. See _container_sizes().
    _cont = _container_sizes()
    _stamped = 0
    for _it in out:
        _c = _cont.get(_it["id"])
        if not _c:
            # THE PACK IS ALREADY THE CONTAINER for anything invoice-fed. Corn
            # chips are bought as a 3,000 g carton at $47.30; container_sizes
            # never had a row because it only covers ids RECIPES reference, but
            # the ingredient has been carrying the answer in its own pack
            # columns all along. pack_qty of 1 is a RATE, not a container -- a
            # cost-book row saying "$0.0797 per 1 ml" describes no object.
            try:
                _pq = Decimal(str(_it.get("pack_qty") or 0))
            except Exception:                                # noqa: BLE001
                continue
            if _pq <= 1:
                continue
            _c = (_pq, _it.get("pack_unit") or "", "pack")
        _q, _u, _kind = _c
        try:
            _rate = Decimal(str(_it["cost_per_base_unit"]))
        except Exception:                                    # noqa: BLE001
            continue
        # Only where the container is measured in the SAME dimension the rate is.
        # A per-EACH price against a 700 ml container is not a container price,
        # it is two different questions.
        if _u != (_it.get("pack_unit") or ""):
            continue
        _it["container_qty"] = str(_q)
        _it["container_unit"] = _u
        _it["container_kind"] = _kind
        _it["container_cost_incl"] = str((_rate * _q).quantize(Decimal("0.01")))
        _stamped += 1
    print(f"  container size on {_stamped} of {len(out)} ingredients "
          f"(the picker can show the bottle, not the litre)")

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
    }, indent=2), encoding="utf-8")

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
