#!/usr/bin/env python3
"""
Seed the cost book with beverage costs from the Lightspeed Back Office export.

    python3 scripts/seed_beverage_costs.py            # write data/beverage_seed.csv (review)
    python3 scripts/seed_beverage_costs.py --apply    # merge those rows into data/cogs_list.csv

WHY
---
Food costs reach the recipe cost engine from invoices. Beverage costs mostly do
NOT yet — a bar rings ~500 bottles/cans/kegs, and only the handful invoiced in
the window are costed. But the cost is already sitting in the Back Office product
export (CostPriceIncTax on each STOCK item). This lifts those into the cost book
so a cocktail can be costed today, WITHOUT waiting for every bottle to be invoiced.

IDENTITY — Lightspeed ProductID, not supplier:code.
A bottle's stable, venue-consistent identity is its Lightspeed ProductID (what the
till, stocktake and me&u already use). The export carries no supplier code, so we
key beverage costs `lightspeed:<ProductID>` (purchasable_id("Lightspeed", pid)).
build_costs (stage 2) bridges an invoice's supplier:code onto the same ProductID
so a REAL invoice supersedes this seed by date — the seed is a floor, invoices win.

POS vs STOCK — only inventory-tracked bottles.
The export mixes sell-only POS items (a "Hendricks & Tonic" serve, a "Guinness
Pint") with the real stock items (the bottle, the keg). Only InventoryType=1 rows
are stock; the serves derive their cost from the bottle/keg and are skipped.

UNIT — per ml for pourable liquor, per unit for cans/tins.
A cocktail uses 30-60 ml, so spirits/liqueurs/wine must cost per ml: we put the
bottle size (700ml default for spirits, 750ml for wine) into the description so
resolve_pack yields $/ml. Cans/tins are sold and used whole -> per unit. Kegs are
recorded per keg and flagged (a schooner cost is a serve calc, done elsewhere).
"""

from __future__ import annotations

import argparse
import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = [
    ("stowaway", ROOT / "data" / "bo_exports" / "stowaway_products.csv"),
    ("harry_gatos", ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"),
]
COGS = ROOT / "data" / "cogs_list.csv"
SEED_OUT = ROOT / "data" / "beverage_seed.csv"

# The export has no capture date; use a fixed baseline so ANY real invoice (which
# carries its own date) is newer and wins the as-of lookup. Kept older than the
# invoice history so the seed never shadows a real observation.
SEED_DATE = "2026-01-01"
SEED_SOURCE = "bo-seed"          # source_invoice tag; build_cogs_list keeps these

BEV_CATS = {"SPIRITS", "WINE", "BEERS", "NON-ALC", "SAKE / SOJU", "MARILYNA'S DRINKS"}

# A pourable bottle/keg/can size in the ProductName, e.g. "[700ml]", "[5L]",
# "[Bottle]", "[Can]", "[Keg]". Beer/wine sometimes say "- Bottle"/"- Can".
_SIZE = re.compile(r"\[\s*(bottle|can|tin|keg|[\d.]+\s*(?:ml|lt?|litre)s?)\s*\]", re.I)
_SIZE_ALT = re.compile(r"[-–]\s*(bottle|can|tin|keg)\b", re.I)
_ML = re.compile(r"([\d.]+)\s*(ml|lt?|litre)s?", re.I)

# Serves (a pour), not stock — cost derives from the bottle/keg. Never a stock row.
_SERVE = re.compile(r"\b(pint|schooner|glass|nip|pot|middy|jug)\b", re.I)

# Kitchen goods that carry a [size] but are NOT bar/beverage (they belong to food
# costing, reached via their own invoices). Kept tight and explicit.
_KITCHEN = re.compile(
    r"\b(bbq sauce|cooking cream|canola|olive oil|veg(?:etable)? oil|sriracha|"
    r"mayo|aioli|caps?\b|char-?grilled|cooking|stock\b|ghee|butter|"
    r"passata|napoli|tomato sauce|fish sauce|soy sauce|oyster sauce|"
    # kitchen vinegars/condiments whose NAME carries a drink word (wine/port/rice
    # 'wine' vinegar, mirin, ponzu, dashi) — not bar stock.
    r"vinegar|mirin|ponzu|dashi|balsamic|verjuice|cordial cooking)\b", re.I)

# Alcohol / bar-mixer identity words — lets us include (no-category) stock bottles
# that are clearly bar items, and bar syrups/bitters/cordials a cocktail uses.
_BAR = re.compile(
    r"\b(gin|vodka|tequila|mezcal|rum|whisk\w*|bourbon|scotch|cognac|brandy|pisco|"
    r"cachaca|cachaça|absinthe|aperol|campari|vermouth|amaro|amaretto|liqueur|"
    r"aperitif|aperitivo|sherry|port|prosecco|champagne|wine|shiraz|cabernet|"
    r"sauv|riesling|chardonnay|pinot|rose|rosé|rosso|rosso|bianco|sake|soju|"
    r"cider|lager|ale|ipa|stout|pilsner|seltzer|schnapps|curacao|curaçao|"
    r"triple sec|cointreau|chartreuse|maraschino|bitters|cordial|syrup|"
    r"tonic|falernum|umeshu|yuzushu|shochu|awamori|makgeolli|junmai|daiginjo|ginjo|nigori|genshu|honjozo|tokubetsu|limoncello|cassis|framboise|luxardo|italicus|"
    r"lillet|cocchi|cynar|braulio|pimm|frangelico|disaronno|chambord|"
    r"grand marnier|benedictine|drambuie|kahlua|baileys|midori|galliano)\b", re.I)

# Wine identity -> a wine [Bottle] is 750 ml, a spirit [Bottle] is 700 ml.
_WINE = re.compile(
    r"\b(wine|shiraz|cabernet|sauv|riesling|chardonnay|pinot|rose|rosé|"
    r"prosecco|champagne|semillon|grenache|merlot|malbec|tempranillo|"
    r"nebbiolo|sangiovese|vermentino|moscato|gewurz|viognier|blanc|rouge)\b", re.I)


def _cost(r) -> Decimal | None:
    try:
        c = Decimal(str(r.get("CostPriceIncTax") or "0"))
        return c if c > 0 else None
    except (InvalidOperation, TypeError):
        return None


def _bev_cats(r) -> list[str]:
    return [c.strip() for c in (r.get("CategoryNames") or "").split(",") if c.strip() in BEV_CATS]


def is_beverage_stock(r) -> bool:
    """An inventory-tracked bar/beverage stock item (not a serve, not a kitchen good)."""
    if (r.get("InventoryType") or "").strip() != "1":
        return False
    name = r.get("ProductName") or ""
    if _SERVE.search(name):
        return False                       # a pour, not a stock item
    if _KITCHEN.search(name):
        return False                       # kitchen good, costed via its own invoice
    if _bev_cats(r):
        return True
    # (no category) stock bottle: include only if it names a bar/alcohol/mixer word
    return bool((_SIZE.search(name) or _SIZE_ALT.search(name)) and _BAR.search(name))


# Words that mean POUR (costed per ml — a cocktail uses 30-60 ml): spirits,
# liqueurs, fortified/aromatised wine, bar syrups/bitters/cordials. Deliberately
# EXCLUDES beer/cider/seltzer/soft-drink words (those are sold and used whole).
_POUR = re.compile(
    r"\b(gin|vodka|tequila|mezcal|rum|whisk\w*|bourbon|scotch|cognac|brandy|pisco|"
    r"cachaca|cachaça|absinthe|aperol|campari|vermouth|amaro|amaretto|liqueur|"
    r"aperitif|aperitivo|sherry|port\b|schnapps|curacao|curaçao|triple sec|"
    r"cointreau|chartreuse|maraschino|bitters|cordial|syrup|falernum|cassis|"
    r"framboise|luxardo|italicus|lillet|cocchi|cynar|braulio|pimm|frangelico|"
    r"disaronno|chambord|grand marnier|benedictine|drambuie|kahlua|midori|"
    r"galliano|umeshu|yuzushu|shochu|awamori|makgeolli|junmai|daiginjo|ginjo|nigori|genshu|honjozo|tokubetsu|limoncello|select aperitivo|dubonnet|suze|becherovka)\b", re.I)

_SIZE_IN_NAME = re.compile(r"([\d.]+)\s*(ml|lt?|litres?)\b", re.I)


def classify(r) -> tuple[str, int | None]:
    """
    (unit_kind, ml) for a stock row.
      'ml'   -> poured; ml is the bottle volume (cocktail uses part of it)
      'each' -> sold/used whole (beer/cider/seltzer/can/tin/soft drink)
      'keg'  -> per keg (a serve calc lives elsewhere)
    """
    name = r["ProductName"]
    low = name.lower()
    cats = _bev_cats(r)
    if re.search(r"\bkeg\b", low) or "[keg]" in low:
        return "keg", None
    is_wine = ("WINE" in cats) or bool(_WINE.search(name))
    pour = ("SPIRITS" in cats) or ("SAKE / SOJU" in cats) or is_wine or bool(_POUR.search(name))
    # explicit can/tin/seltzer/cider, or a beer/soft category -> whole unit
    whole = bool(re.search(r"\b(can|tin|tinnie|seltzer|cider|kombucha)\b", low)) \
        or ("BEERS" in cats) or ("NON-ALC" in cats) or ("MARILYNA'S DRINKS" in cats)
    if pour and not whole:
        mm = _SIZE_IN_NAME.search(name)
        if mm:
            v = Decimal(mm.group(1))
            return "ml", int(v * 1000) if mm.group(2).lower().startswith("l") else int(v)
        return "ml", (750 if is_wine else 700)     # bare [Bottle]
    return "each", None


def clean_name(name: str) -> str:
    """Drop the trailing [size]/- Bottle marker for a clean cost-book description."""
    n = _SIZE.sub("", name)
    n = _SIZE_ALT.sub("", n)
    return re.sub(r"\s+", " ", n).strip(" -–")


def collect() -> tuple[list[dict], list[str]]:
    # gather every beverage stock item per venue, with cost (own venue first)
    per_venue: dict[str, list[dict]] = {}
    for venue, path in EXPORTS:
        if not path.exists():
            continue
        per_venue[venue] = [r for r in csv.DictReader(path.open(encoding="utf-8-sig"))
                            if is_beverage_stock(r)]
    # cross-venue cost fill: same bottle name costed in the other venue
    name_cost: dict[str, Decimal] = {}
    for rows in per_venue.values():
        for r in rows:
            c = _cost(r)
            if c:
                name_cost.setdefault(clean_name(r["ProductName"]).lower(), c)

    seed, flags, seen = [], [], set()
    for venue, rows in per_venue.items():
        for r in rows:
            pid = (r.get("ProductID") or "").strip()
            if not pid or pid in seen:
                continue
            name = r["ProductName"]
            disp = clean_name(name)
            cost = _cost(r) or name_cost.get(disp.lower())
            if cost is None:
                flags.append(f"NO COST anywhere: {disp} ({venue})")
                continue
            seen.add(pid)
            kind, ml = classify(r)
            if kind == "keg":
                basis, desc, pq, pu = "keg", disp, "1", "keg"
                flags.append(f"KEG (per-keg only, no serve calc): {disp}")
            elif kind == "ml":
                basis, desc, pq, pu = "", f"{disp} {ml}ML", str(ml), "ml"
            else:                                   # each — beer / cider / seltzer / soft
                basis, desc, pq, pu = "can", disp, "1", "each"
            seed.append({
                "supplier": "Lightspeed",
                "supplier_code": pid,
                "invoice_description": desc,
                "lightspeed_product": disp,
                "cost_per_unit_incl_gst": str(cost),
                "basis": basis,
                "pack_size": "1",
                "pack_qty": pq,
                "pack_unit": pu,
                "cost_per_base_unit": "",
                "venue": venue,
                "source_invoice": f"{SEED_SOURCE}-{venue}",
                "invoice_date": SEED_DATE,
                "in_bounds": "yes",
                "note": f"BO export cost ({kind}); ProductID {pid}",
            })
    return seed, flags


FIELDS = ["supplier", "supplier_code", "invoice_description", "lightspeed_product",
          "cost_per_unit_incl_gst", "basis", "pack_size", "pack_qty", "pack_unit",
          "cost_per_base_unit", "venue", "source_invoice", "invoice_date", "in_bounds", "note"]


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def apply_to_cogs(seed: list[dict]) -> int:
    """Merge seed rows into cogs_list.csv. The seed is a fallback FLOOR, so it is
    STICKY: a bottle that the current export no longer costs (Lightspeed sometimes
    blanks a bottle's InventoryType/CostPriceIncTax on re-export — Hendrick's,
    Cointreau, Chartreuse all went to $0 this way) keeps its LAST-KNOWN seed cost
    rather than losing it. Fresh seed wins on any ProductID it still carries;
    invoices (dated recent) still win over both via the as-of lookup."""
    existing = list(csv.DictReader(COGS.open(encoding="utf-8-sig"))) if COGS.exists() else []
    fresh_pids = {r.get("supplier_code") for r in seed}
    carried = [r for r in existing
               if (r.get("source_invoice") or "").startswith(SEED_SOURCE)
               and r.get("supplier_code") not in fresh_pids]     # last-known, not re-seeded
    kept = [r for r in existing if not (r.get("source_invoice") or "").startswith(SEED_SOURCE)]
    merged = kept + seed + carried
    merged.sort(key=lambda r: (r.get("invoice_date", ""), r.get("supplier", ""),
                               r.get("supplier_code", ""), r.get("invoice_description", "")))
    _write(COGS, merged)
    print(f"  carried forward {len(carried)} prior seed rows the current export no longer costs")
    return len(merged)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="merge into data/cogs_list.csv")
    args = ap.parse_args()

    seed, flags = collect()
    _write(SEED_OUT, seed)
    print(f"{len(seed)} beverage cost rows -> {SEED_OUT.relative_to(ROOT)}")
    byv: dict[str, int] = {}
    for r in seed:
        byv[r["venue"]] = byv.get(r["venue"], 0) + 1
    print("  by venue:", byv)
    if flags:
        print(f"  {len(flags)} flagged:")
        for fl in flags[:30]:
            print(f"    - {fl}")
    if args.apply:
        n = apply_to_cogs(seed)
        print(f"applied -> data/cogs_list.csv now {n} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
