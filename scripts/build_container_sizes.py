#!/usr/bin/env python3
"""How big is one of the things a person counts?

Nobody counts in millilitres. A stocktake says "three quarters of a bottle",
"0.8 of a keg", "0.035 of the 20L drum" — and to turn that into ml we have to
know how big one bottle is. This builds that table.

SOURCES, STRONGEST FIRST. Each row records which one it came from, because the
weakest of them is the one most likely to be wrong:

  1. pack_overrides.yaml   a human opened one and wrote down what was in it.
                           Beats everything, including a name that disagrees.
  2. the product name      "Havana 3yr [700ml]", "East Coast Pineapple Juice
                           [2L]". This is Lightspeed's own declaration and it is
                           usually right — but a name is edited by hand and does
                           not have to follow the bottle it describes. Flagged
                           `product_name` so a variance traced back to one of
                           these is a suspect, not a mystery.
  3. base unit is `each`   a pizza box is one pizza box. The conversion is 1 and
                           there is nothing to get wrong.

Anything else REFUSES. An item counted in bottles whose bottle size nobody has
stated cannot be converted, and guessing 700ml because most spirits are 700ml is
exactly the error that compounds on every count forever.

Run: python3 scripts/build_container_sizes.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import load_base_units                              # noqa: E402
from core.pack_overrides import load_pack_overrides             # noqa: E402

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
OUT = ROOT / "data" / "container_sizes.csv"
_BO_SIZES: dict = {}
_TWINS: dict = {}

# "[700ml]", "[2L]", "[1Kg]", "500g". Deliberately anchored to a unit word —
# "Pizza Base Gluten Free 11in" must not read 11 as a size.
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|kg|g)\b", re.I)
TO_BASE = {"ml": (Decimal(1), "ml"), "l": (Decimal(1000), "ml"),
           "g": (Decimal(1), "g"), "kg": (Decimal(1000), "g")}


def item_names() -> dict[str, str]:
    book = json.loads(BOOK.read_text())["recipes"]
    out: dict[str, str] = {}
    for r in book.values():
        for i in r.get("ingredients", []):
            if i.get("kind") == "id" and (i.get("ref") or "").startswith("lightspeed:"):
                out.setdefault(i["ref"], i.get("name") or "")
    return out



def _bo_default_sizes() -> dict:
    """ProductID -> (base_qty, base_unit) from the Back Office export.

    THE STRONGEST SOURCE WE HAVE, and it was going unread. Lightspeed
    distinguishes a POS SALE ITEM from a STOCK ITEM, and the stock item states
    its own container:

        Archie Rose Signature Gin           InventoryType ''   Unit unit  DefaultSize 1
        Archie Rose Signature Gin [Bottle]  InventoryType '1'  Unit ml    DefaultSize 700

    The bottle is what we keep stock of; the pour is what we sell. A recipe line
    drawing "30" of the bottle is 30 ML, and $63.92 / 700 ml x 30 = $2.74 --
    which is what that pour actually costs. Without the size the same line reads
    as 30 BOTTLES and prices a gin and tonic at $1,862.40.

    648 stock items carry a real size this way, against 254 rows the name-parsing
    source could find. Zak, 2026-08-19: "differentiate the POS sale items from the
    stock items that we actually want to track inventory for... we don't keep
    stock of archie rose [30ml]."

    Only InventoryType == '1' is read: a sale item's DefaultSize is 1 by
    convention and means nothing.
    """
    import csv as _csv
    out = {}
    for f in ("stowaway_products.csv", "harry_gatos_products.csv"):
        path = ROOT / "data" / "bo_exports" / f
        if not path.exists():
            continue
        for r in _csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
            if (r.get("InventoryType") or "").strip() != "1":
                continue
            pid = (r.get("ProductID") or "").strip()
            unit = (r.get("Unit") or "").strip().lower()
            try:
                size = Decimal((r.get("DefaultSize") or "").strip())
            except Exception:                                # noqa: BLE001
                continue
            if not pid or size <= 1 or unit not in TO_BASE:
                continue
            out[f"lightspeed:{pid}"] = (size, unit)
    return out


def _stem(name: str) -> str:
    """Product name with its bracketed suffixes and trailing punctuation gone.

    "Appleton Rum [House]" and "Appleton Rum [Bottle]" are the same rum in two
    roles, and the brackets are exactly what distinguishes the role.
    """
    n = re.sub(r"\s*\[[^]]*\]", "", name).strip().rstrip(".").lower()
    return re.sub(r"\s+", " ", n)


def _stock_twins() -> dict:
    """Stem -> the sizes its stock-tracked records agree on (or don't).

    A recipe that draws "30" of the POS SALE item is unpriceable: the sale item
    is the POUR, carries no container, and Lightspeed says so (InventoryType '').
    The bottle beside it does carry one:

        Archie Rose Signature Gin           InventoryType ''   Unit unit  DefaultSize 1
        Archie Rose Signature Gin [Bottle]  InventoryType '1'  Unit ml    DefaultSize 700

    126 recipe references point at the sale item. 106 have a stock twin holding
    the answer. Zak, 2026-08-19: "we don't keep stock of archie rose [30ml]."

    The join is a name stem, and a name stem is a MATCHER -- the thing that has
    already handed this book Hahn Super Dry as Asahi and Coke Zero as Better
    Beer. So it is only allowed to speak when it cannot be wrong: every stock
    record sharing the stem must agree on the size. Most apparent ambiguity is
    just the same bottle appearing in both venue exports, which agrees with
    itself. Real disagreement is real:

        Campari      [Bottle] 750  vs  [Bottle] 700
        Pepperoni    unit 1        vs  [3kg] 3000 g
        Lemon        [Sliced] 1kg  vs  [ea] 1  vs  [kg] 1kg

    Those are questions, not facts. They go to the review file for a human.
    """
    import csv as _csv
    by_stem: dict = {}
    for f in ("stowaway_products.csv", "harry_gatos_products.csv"):
        path = ROOT / "data" / "bo_exports" / f
        if not path.exists():
            continue
        for r in _csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
            if (r.get("InventoryType") or "").strip() != "1":
                continue
            unit = (r.get("Unit") or "").strip().lower()
            try:
                size = Decimal((r.get("DefaultSize") or "").strip())
            except Exception:                                # noqa: BLE001
                continue
            if size <= 1 or unit not in TO_BASE:
                continue
            by_stem.setdefault(_stem(r.get("ProductName") or ""), []).append(
                (size, unit, (r.get("ProductName") or "").strip()))
    return by_stem


def _resolve_bo(size: Decimal, unit: str, name_qty, base: str):
    """Turn a raw Back Office (DefaultSize, Unit) pair into a base quantity.

    THE LABEL IS NOT EVIDENCE. Back Office stores both conventions under the
    same unit string and nothing distinguishes them but the number:

        Tomato Ketchup 4L Heinz    Unit=l  DefaultSize=4       <- 4 litres
        Tomato Sauce Heinz [4L]    Unit=l  DefaultSize=4000    <- 4000 ML, mislabelled l

    Read the label as gospel and the second one becomes four thousand litres --
    a 1000x error, which is the same shape as every other unit failure in this
    book. So the NUMBER is what we take from Back Office; the SCALE is decided
    by evidence, in this order:

      1. The product name, if it states a size. Whichever reading of the number
         reproduces the name wins. Both examples above resolve on this rule.
      2. Failing that, plausibility. An ingredient container measured in l/kg is
         realistically 1-30 of them; at 100+ the number is already in ml/g.
      3. Between 30 and 100, nothing decides it. REFUSE -- an unpriced line is a
         visible gap, a wrongly-scaled one is a silent $1,862 gin and tonic.
    """
    factor, implied = TO_BASE[unit]
    if implied != base:
        return None, f"BO says {unit} but recipes draw {base}"

    scaled, raw = size * factor, size          # e.g. 4 l -> 4000 ml, or 4000 already ml
    if factor == 1:
        return scaled, f"BO stock item states {scaled:g}{base}"

    if name_qty is not None:
        if name_qty == scaled:
            return scaled, f"BO {size:g}{unit} = {scaled:g}{base}, name agrees"
        if name_qty == raw:
            return raw, f"BO number {size:g} is already {base} (label says {unit}); name agrees"
        # Numbers disagree outright: the name quoted a PIECE size and Back Office
        # is quoting the pack (150g schnitzels in a 6kg carton). The pack is what
        # we buy and what the invoice prices, so it wins -- on the scaled reading,
        # because a genuine mislabel would have matched the name above.
        return scaled, f"BO pack {size:g}{unit} = {scaled:g}{base} (name quotes a piece size)"

    if size <= 30:
        return scaled, f"BO stock item states {size:g}{unit} = {scaled:g}{base}"
    if size >= 100:
        return raw, f"BO number {size:g} reads as {base} already (label {unit} implausible at this size)"
    return None, f"BO {size:g}{unit} is ambiguous (30-100 with no name to check it against)"


def main() -> int:
    global _BO_SIZES
    global _TWINS
    _BO_SIZES = _bo_default_sizes()
    _TWINS = _stock_twins()
    names = item_names()
    base_units = load_base_units()
    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")

    rows, refused = [], []
    for item, name in sorted(names.items()):
        base = base_units.get(item)

        ov = overrides.get(item)
        if ov:
            qty, unit = ov
            unit = {"ea": "each"}.get(unit, unit)
            rows.append({"item_id": item, "item_name": name, "container": "pack",
                         "base_qty": qty, "base_unit": unit,
                         "source": "pack_override", "evidence": "human-confirmed"})
            continue

        if base == "each":
            rows.append({"item_id": item, "item_name": name, "container": "each",
                         "base_qty": 1, "base_unit": "each",
                         "source": "unit_is_each", "evidence": "one is one"})
            continue

        # BACK OFFICE FIRST: the stock item states its own container size, which
        # beats parsing it out of a name. Only where the base unit agrees --
        # a size in ml against a recipe that draws grams is a question, not a fact.
        m = SIZE.search(name)

        # BACK OFFICE FIRST: a stock item states its own container size, which
        # beats parsing one out of a name -- but only its NUMBER is evidence.
        bo, via_twin = _BO_SIZES.get(item), None
        if bo is None and base:
            # No container on this record. If it is a POS sale item, the stock
            # item standing behind it has one -- but only take it if every
            # candidate agrees (see _stock_twins).
            twins = _TWINS.get(_stem(name), [])
            agreed = {(t[0], t[1]) for t in twins}
            if len(agreed) == 1:
                bo, via_twin = next(iter(agreed)), twins[0][2]
            elif len(agreed) > 1:
                refused.append((item, name, "stock twins disagree on size: "
                                + " vs ".join(f"{t[2]} {t[0]:g}{t[1]}" for t in twins[:3])))
                continue

        if bo and base:
            name_qty = None
            if m:
                f_n, impl_n = TO_BASE[m.group(2).lower()]
                if impl_n == base:
                    name_qty = Decimal(m.group(1)) * f_n
            qty, why = _resolve_bo(bo[0], bo[1], name_qty, base)
            if qty is not None:
                if via_twin:
                    # Say so. This row's size came from a DIFFERENT record --
                    # the stock item standing behind a POS sale item -- and an
                    # audit trail that hides that is not an audit trail.
                    why = f"{why}; via stock twin \"{via_twin}\""
                rows.append({"item_id": item, "item_name": name,
                             "container": "container", "base_qty": qty,
                             "base_unit": base,
                             "source": "back_office_twin" if via_twin else "back_office",
                             "evidence": why})
                continue
            refused.append((item, name, why))
            continue

        if m and base:
            factor, implied = TO_BASE[m.group(2).lower()]
            if implied == base:
                rows.append({"item_id": item, "item_name": name,
                             "container": "container",
                             "base_qty": Decimal(m.group(1)) * factor,
                             "base_unit": base, "source": "product_name",
                             "evidence": f"name states {m.group(1)}{m.group(2).lower()}"})
                continue
            refused.append((item, name, f"name says {m.group(2).lower()} but recipes use {base}"))
            continue

        refused.append((item, name, "no stated size" if base else "no base unit"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item_id", "item_name", "container",
                                          "base_qty", "base_unit", "source",
                                          "evidence"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} of {len(names)} items convertible")
    for s, n in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"    {s:16} {n:4}")
    print(f"\n{len(refused)} item(s) REFUSED — a count in containers cannot be converted:")
    for item, name, why in refused[:12]:
        print(f"    {item:26} {name[:36]:38} {why}")
    if len(refused) > 12:
        print(f"    ... and {len(refused) - 12} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
