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


def _uom_token(raw_uom: Optional[str]) -> Optional[str]:
    """First alpha word of the UOM/notes field, lowercased (KG, 'Box', 'Kilo')."""
    if not raw_uom:
        return None
    m = re.search(r"[A-Za-z][A-Za-z.]*", raw_uom)
    return m.group(0).lower().rstrip(".") if m else None


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
            return _content(description or "") or (Decimal("1"), "ea")
        # unrecognised token — fall through to reading the description

    # No informative UOM: read the pack out of the description (bottled/dry goods).
    return _content(f"{description or ''} {raw_uom or ''}") or (Decimal("1"), "ea")
