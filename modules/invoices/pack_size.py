"""
Reduce an invoice line to a canonical unit — $/kg, $/L or $/each — so the recipe
builder and the cross-supplier comparison see like-for-like costs.

THE HARD PART is not the arithmetic, it's deciding what the base unit IS, and the
description alone lies. Eggs print "700G" — that's the grade (weight per dozen),
not the pack — so dividing a $48 box by 0.7kg gives $68/kg of "egg", nonsense. A
tray of avocados has no weight at all. Meanwhile "SOUR CREAM 2LT" genuinely holds
2 litres.

So we lead with the UNIT OF MEASURE the parser read off the invoice (FFT's Kilo /
Each / Box / Tray / Dozen column, B&E's KG/CTN), and only fall back to scavenging
a number out of the description when the UOM is absent or uninformative:

    parse_pack("Eggs 700 Grams", "Box")          -> (1, "box")   # unknown inners
    parse_pack("EGG 700GM PACK", "doz")          -> (12, "ea")   # $/egg
    parse_pack("Avocado Hass",   "Tray")         -> (1, "tray")
    parse_pack("SOUR CREAM 2LT", "Each")         -> (2, "L")     # 2L IS the content
    parse_pack("OLIVES 2KG",     None)           -> (2, "kg")    # desc fallback

Rule of thumb: a bulk multi (box/carton/case/tray) hides an unknown count, so we
NEVER turn its description weight into $/kg — that is the entire egg/tray bug.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

_MULTI = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|gms?|gram?s?|g|ml|lt?r?|litres?|l)\b", re.I)
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kilo(?:gram)?s?|gms?|gram?s?|g)\b", re.I)
_VOLUME = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|litres?|ltr?|l)\b", re.I)

# UOM vocabularies. The token is the first alpha word of the raw UOM/notes field.
_WEIGHT_UOM = {"kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"}
_VOLUME_UOM = {"l", "lt", "ltr", "litre", "litres", "liter", "liters", "ml"}
_DOZEN_UOM = {"doz", "dozen", "dozens", "dz"}
# A bulk multi: holds an unknown number of inner items, no aggregate weight — so
# any weight in the description is a per-inner spec, never the pack. Priced whole.
_BULK_LABEL = {
    "box": "box", "boxes": "box", "carton": "box", "cartons": "box", "ctn": "box",
    "cartn": "box", "case": "case", "cases": "case", "crate": "case", "crates": "case",
    "tray": "tray", "trays": "tray", "flat": "tray",
}
# A single sellable unit. Reduce to weight/volume ONLY if the description states
# the content of THIS one unit (a 2LT tub, a 200G punnet) — otherwise it's 1 each.
_EACH_UOM = {
    "each", "ea", "unit", "units", "pc", "pcs", "piece", "pieces", "punnet",
    "punnets", "bunch", "bunches", "bch", "head", "heads", "btl", "bottle",
    "bottles", "jar", "jars", "tub", "tubs", "can", "cans", "tin", "tins", "pkt",
    "pack", "packet", "bag", "bags", "sack", "sacks",
}


def _kg(v: Decimal, unit: str) -> Decimal:
    return v / 1000 if unit.lower().startswith("g") else v      # g/gm/gram -> kg


def _litres(v: Decimal, unit: str) -> Decimal:
    return v / 1000 if unit.lower() == "ml" else v             # ml -> L


def _d(s: str) -> Optional[Decimal]:
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _KNOWN() -> set:
    return _WEIGHT_UOM | _VOLUME_UOM | _DOZEN_UOM | set(_BULK_LABEL) | _EACH_UOM


def names_a_unit(text: Optional[str]) -> bool:
    """Does `text` actually name a unit we know? -> bool

    For a caller stitching a wrapped UOM column back together: "200g punnet"
    does, "Cabbage 500g" does not — the second is description text that bled into
    the unit column on a row whose layout had shifted, and taking it as the unit
    turns a per-kilogram line into a 500 g pack. A measure alone is not enough,
    because a description is full of measures; the UOM has to say what the thing
    IS.
    """
    if not text:
        return False
    known = _KNOWN()
    return any(m.group(0).lower().rstrip(".") in known
               for m in re.finditer(r"[A-Za-z][A-Za-z.]*", text))


def single_unit_content(raw_uom: Optional[str]):
    """The size of ONE sellable unit, where the UOM states it. -> (qty, kg|L)|None

    For a caller that already has a description and wants to know whether the
    supplier's own UOM overrides it. Answers ONLY for a UOM that names a single
    sellable thing and gives that thing's size — "200g punnet" -> (0.2, kg).

    It deliberately refuses a MULTI ("6x700ML") and a bulk label ("CTN-6").
    Those describe a container of unknown-to-this-function inners, and reading
    them as one unit is the case/bottle error: ILG's "6x700ML" would hand back
    4.2 L, a whole case, against a price that may be for one bottle. That
    discrimination needs a second source (see seed_matched_liquor_cost) and is
    not this function's to make.
    """
    tok = _uom_token(raw_uom)
    if tok not in _EACH_UOM:
        return None
    if _MULTI.search(raw_uom or ""):        # "6x700ML" is a case, not a unit
        return None
    return _content(raw_uom or "")


def _uom_token(raw_uom: Optional[str]) -> Optional[str]:
    """The UOM word, lowercased (KG, 'Box', 'Kilo'). -> str | None

    THE FIRST ALPHA RUN IS NOT ALWAYS THE WORD. Fresh Fruit Team prints the
    selling unit as "200g punnet", and taking the first run gives "g" — which is
    in no vocabulary, so the whole thing fell through to scavenging the
    description. On a line whose description had wrapped to
    "Punnet) 8 x 100g packs supplied for" that scavenge found "8 x 100g" and
    called the punnet 800 g, four times its real 200 g, and King Brown mushrooms
    went into the book at $7.56/kg against the $30.25/kg every other delivery of
    the same code states.

    So prefer the first run that is a unit we actually know; fall back to the
    first run otherwise, which is the old behaviour for everything else.
    """
    if not raw_uom:
        return None
    runs = [m.group(0).lower().rstrip(".") for m in re.finditer(r"[A-Za-z][A-Za-z.]*", raw_uom)]
    if not runs:
        return None
    known = _KNOWN()
    return next((r for r in runs if r in known), runs[0])


def _content(text: str) -> Optional[tuple[Decimal, str]]:
    """Weight/volume stated in the text, as (qty, kg|L). None if absent."""
    m = _MULTI.search(text)
    if m:
        n, size, unit = _d(m.group(1)), _d(m.group(2)), m.group(3)
        if n and size:
            return ((_litres(n * size, unit), "L") if unit.lower().startswith(("ml", "l"))
                    else (_kg(n * size, unit), "kg"))
    m = _VOLUME.search(text)
    if m and _d(m.group(1)):
        return _litres(_d(m.group(1)), m.group(2)), "L"
    m = _WEIGHT.search(text)
    if m and _d(m.group(1)):
        return _kg(_d(m.group(1)), m.group(2)), "kg"
    return None


def parse_pack(description: str, raw_uom: Optional[str] = None,
               is_weight_priced: bool = False) -> tuple[Decimal, str]:
    """(base_qty, base_unit) in one purchase unit. base_unit in kg | L | ea | box | tray | case."""
    if is_weight_priced:                       # basis is already $/kg — trust it
        return Decimal("1"), "kg"

    tok = _uom_token(raw_uom)
    if tok is not None:
        if tok in _WEIGHT_UOM:
            c = _content(raw_uom)              # "5kg" in the UOM itself
            return (c[0], "kg") if c and c[1] == "kg" else (Decimal("1"), "kg")
        if tok in _VOLUME_UOM:
            c = _content(f"{raw_uom} {description or ''}")
            return (c[0], "L") if c and c[1] == "L" else (Decimal("1"), "L")
        if tok in _DOZEN_UOM:                  # per dozen -> per each (÷12)
            return Decimal("12"), "ea"
        if tok in _BULK_LABEL:                 # box/carton/case/tray: unknown inners
            return Decimal("1"), _BULK_LABEL[tok]
        if tok in _EACH_UOM:                   # single unit — use its stated content if any
            # The UOM FIRST. "200g punnet" is the supplier stating the size of
            # the very unit it is pricing; the description is a free-text field
            # that wraps, gets truncated, and carries substitution notes ("8 x
            # 100g packs supplied for same price" is the TOTAL of a 4-punnet
            # line, not one punnet). Leading with the UOM is what this module
            # says it does everywhere else — this branch was the exception.
            return (_content(raw_uom or "") or _content(description or "")
                    or (Decimal("1"), "ea"))
        # unrecognised token — fall through to reading the description

    # No informative UOM: read the pack out of the description (bottled/dry goods).
    return _content(f"{description or ''} {raw_uom or ''}") or (Decimal("1"), "ea")
