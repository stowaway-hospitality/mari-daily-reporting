#!/usr/bin/env python3
"""
Par model v3 — the compute engine.

WHAT CHANGED FROM v2 (and why)
------------------------------
1. **Shrinkage is real now.** v2 hard-coded `drivers.variance_wk = 0.0` because
   `products_weekly.csv` only knows what was rung up. `modules/par/shrinkage.py`
   reads the Lightspeed stock counts in `data/stock_counts/` and gives every SKU
   a measured loss rate. The count that taught us this: 28 Jul 2026 showed a
   gross NEGATIVE of $1,598 against a gross positive of $1,636 — a net of +$37
   that looks like a clean count and hides the entire loss. Only the loss side
   is ever taken.
2. **(R,S) service levels replace "worst week × 1.2".** `modules/par/service.py`
   sets an order-up-to level from demand over the real exposure window plus
   z·σ, with the service level chosen per SKU class, and a Poisson quantile for
   low movers where the normal approximation is meaningless.
3. **Seasonality replaces the trailing 13 weeks.** `modules/par/seasonal.py`
   builds a week-of-year index off ~2 years of history (per SKU where there is
   enough history, per reporting group otherwise) and the forecast becomes
   level × index(target week).
4. **The ordering cycle is computed, not fudged.** `modules/par/calendar.py`
   knows that a holiday Monday slips the ILG Wednesday run to Friday, that a
   holiday Friday kills it outright, and therefore that the 23 Dec 2026 delivery
   has to carry 14 days / 21 day-units ≈ 2.1× a normal cycle.
5. **Bookings are computed but NOT applied** (`modules/par/bookings.py`,
   `BOOKINGS_LIVE = False`) — the uplift is recorded as `bookings_uplift_shadow`.
6. The old spike floor is DEMOTED to a sanity floor: the 90th-percentile week of
   the seasonal window, floor only, never the primary driver.

Original v2 notes follow.

Rebuilds Stowaway (`stow`) and Harry Gatos (`hg`) par recommendations from the
LIVE data feeds committed to this repo, replacing the old skill's hard-coded
monthly CSV + scraped-par pipeline:

  data/products_weekly.csv        weekly per-product qty by venue (THE demand source)
  data/recipes/{stowaway,harry_gatos}.yaml   recipe book (subrecipes expanded)
  data/bo_exports/*_products.csv  catalog: ProductID<->name, sizes, reporting group
  data/_scrape_{stow,hg}_*.json   current live pars (ground truth for current_par)
  data/par_overrides.json         41 manual overrides (hold/min/max/zero, protect)

Consumption per par SKU per week =
    pour   (direct spirit nip / wine glass / bottle sales -> bottles)
  + recipe (cocktail/keg qty * ml-per-serve / bottle_ml, subrecipes expanded)
  + variance (0 here — products_weekly carries no stock-count variance channel)

Forecast: recent-8-week weighted mean, lifted by a YoY seasonal projection when a
prior-year analog exists (growth clamped 0.60-1.80), surge-netted to the recent
mean for non-seasonal items. Then * volatility buffer (1.12-1.30, from recent CV),
floored by the 3-month spike floor (worst window week * 1.10, rounded up).
Overrides always win.

Money note: par quantities are physical unit counts (floats); this module performs
NO currency arithmetic, so the repo's Decimal-money rule is satisfied trivially.
Weeks are Mon-Sun labelled by the Sunday (the `week_ending` field).
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import date, timedelta

import yaml

from . import bookings as bookings_mod
from . import calendar as par_calendar
from . import seasonal as seasonal_mod
from . import service as service_mod
from . import shrinkage as shrinkage_mod

# ── tunables ────────────────────────────────────────────────────────────────
COVERAGE_WEEKS = 1.0        # legacy flat cycle — superseded by the calendar's
                            # weighted day-units; kept for back-compat only.
SAFETY_BUFFER = 1.20        # fallback buffer when volatility can't be measured
BUFFER_LO = 1.12
BUFFER_HI = 1.30
GROWTH_CAP_LO = 0.60
GROWTH_CAP_HI = 1.80
SPIKE_FLOOR_MULT = 1.20     # v2's "worst window week x 1.2". DEMOTED in v3 — the
                            # floor is now the 90th-percentile week of the
                            # SEASONAL window and carries no multiplier. The
                            # constant stays so nothing importing it breaks.
DECLINE_FLOOR = 0.75        # declining items still keep 75% of the peak in the floor
RECENT_WEEKS = 8            # recent weighted-mean window
WINDOW_WEEKS = 13           # ~3-month legacy window (v2 spike floor / volatility)
SEASONAL_CV = 0.25          # cv above this = seasonal (surge-net disabled)

# v3
BOOKINGS_LIVE = bookings_mod.BOOKINGS_LIVE   # False — uplift is recorded, not applied
MAX_SHRINK_FRACTION = shrinkage_mod.MAX_UPLIFT_FRACTION
MATERIAL_LOSS_WK = 0.05     # below this a "loss" is count rounding, not shrinkage.
                            # Flags use it so `shrinkage_applied` means something
                            # when a human reads the list.

SPIRIT_NIP_ML = 30.0
WINE_REGULAR_ML = 150.0
WINE_LARGE_ML = 250.0
KEG_ML = 50000.0

# Tap beer. products_weekly.csv collapses the '- Schooner' / '- Pint' variants
# into one bare line, so the exact serve is not recoverable from the model's own
# input. Measured from the per-variant daily insights exports committed under
# data/insights_*.csv: Stowaway 47.1% schooner / 52.9% pint = 501.7ml blended,
# Harry Gatos 59.8% / 40.2% = 483.3ml. 500ml is the honest single constant; the
# residual error per line is ~<12% against the 100% of volume that was being
# dropped before. A '- NNNml' suffix on the POS line (e.g. 'Sapporo - 500ml')
# overrides it, because that one IS recoverable.
TAP_SERVE_ML = 500.0
SCHOONER_ML = 425.0
PINT_ML = 570.0

SPIRIT_RGS = {
    "Gin", "Vodka", "Tequila", "Whisky", "Rum", "Liqueurs",
    "Amaro / Aperitif / Fortified Wine",
    # Harry Gatos pluralises its spirit reporting groups and Stowaway carries a
    # Mezcal group. Neither was in this set, so EVERY HG spirit pour and every
    # Stowaway mezcal pour fell through all four branches below and reached no
    # par SKU at all. Found by the unattributed-volume guard.
    "Gins", "Vodkas", "Tequilas", "Whiskies", "Whisky / Whiskey", "Rums",
    "Liqueur", "Mezcal", "Mezcals",
}
WINE_RGS = {
    "Red Wine", "White Wine", "Rose Wine", "Sparkling Wine",
    "Orange / Skins Wine", "Pet Nat Wine",
}
# Cocktails/mocktails/tap-beer are consumed via the recipe book, never as a pour.
RECIPE_RGS = {
    "Cocktails - Classic", "Cocktails - Signature", "Mocktails",
    "Tap Beer", "Delivery Cocktails",
}
COCKTAIL_RGS = {"Cocktails - Classic", "Cocktails - Signature"}
# Tap beer is nominally a RECIPE_RG, and the recipe book DOES carry a keg line
# for most taps — but the recipes are named per variant ('Stone & Wood -
# Schooner') while products_weekly collapses the line to 'Stone & Wood', so the
# recipe matcher (which requires the POS name to START WITH the recipe name)
# never fired for a single tap beer. Result: 100% of tap volume reached no par
# SKU and every keg sat on `held_no_recent_demand`. A tap line therefore gets a
# direct schooner/pint->keg pour path here, which runs ONLY when the recipe
# matcher did not already claim the line, so nothing is counted twice.
TAP_BEER_RGS = {"Tap Beer"}
# Everything else beverage-ish (cans, bottles, soft drink, non-alc) = direct 1:1.
DIRECT_RGS = {
    "Bottles / Cans Alcoholic", "Non-alcoholic", "Non-Alcoholic",
    "Marilyna's Soft Drinks", "Delivery Alcohol",
}
VERMOUTH_KW = (
    "vermouth", "cocchi", "dolin", "carpano", "noilly", "prat", "vinada",
    "champagne", "prosecco", "framboise", "wild strawberry", "punt e mes",
    "oscar.697", "sherry", " port", "sparkling", "pet nat",
)

ALIAS_FILE = "par_aliases.json"

VENUE_RECIPE_FILE = {"stow": "stowaway", "hg": "harry_gatos"}
VENUE_SCRAPE_FILE = {"stow": "_scrape_stow_20260809.json", "hg": "_scrape_hg_20260809.json"}
VENUE_BO_FILE = {"stow": "stowaway", "hg": "harry_gatos"}
# The venues the par model computes. Also the legal prefixes for a CROSS-VENUE
# alias target ("stow:Kirin [Keg]") — see AliasBook.
VENUE_CODES = ("stow", "hg")


# ── text helpers ────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower().replace("'", "").replace("`", "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


_VARIANT_SUFFIXES = (
    " - large glass", " - large", " - regular", " - glass", " - bottle",
    " tonica",
)


def _strip_variant(name: str) -> str:
    """Collapse a POS pour/variant name (or a par SKU) to its base identity so
    'Bombay Dry [House]', 'Bombay Dry [Bottle]' and 'Bombay Dry' all agree."""
    s = name.lower()
    # drop bracket tokens: [house] [mixer] [bottle] [keg] [700ml] [5l] [20l] ...
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = s.strip()
    changed = True
    while changed:
        changed = False
        for suf in _VARIANT_SUFFIXES:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
    # trailing takeaway/bottle 'd' marker
    if s.endswith(" d") and len(s) > 3:
        s = s[:-2].strip()
    return _norm(s)


def bottle_ml(name: str) -> float:
    """Best-effort bottle/cask/keg volume in ml parsed from a par SKU name."""
    low = name.lower()
    if "[keg]" in low:
        return KEG_ML
    m = re.search(r"\[(\d+(?:\.\d+)?)\s*(ml|l)\s*(?:cask)?\]", low)
    if m:
        val = float(m.group(1))
        return val if m.group(2) == "ml" else val * 1000.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*ml\b", low)
    if m:
        return float(m.group(1))
    if "[bottle]" in low or low.rstrip().endswith("- bottle"):
        return 750.0 if any(k in low for k in VERMOUTH_KW) else 700.0
    return 750.0


def _is_bulk_par(name: str) -> bool:
    low = name.lower()
    return bool(
        "[keg]" in low
        or "[bottle]" in low
        or low.rstrip().endswith("- bottle")
        or re.search(r"\[\d+(?:\.\d+)?\s*(ml|l)\s*(?:cask)?\]", low)
    )


def _is_custom_cocktail(name: str) -> bool:
    return bool(re.search(r"\$\d+\s+.*custom|custom cocktail", name, re.I))


# ── loaders ─────────────────────────────────────────────────────────────────
def load_weekly(data_dir: str):
    rows = []
    with open(os.path.join(data_dir, "products_weekly.csv"), newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                r["qty"] = float(r["qty"] or 0)
            except ValueError:
                r["qty"] = 0.0
            rows.append(r)
    return rows


def load_bo(data_dir: str, venue: str):
    """Return (id2name, name2meta) for a venue's Back Office catalog.
    name2meta[name] = {'rg':..., 'unit':..., 'size':...}."""
    id2name, name2meta = {}, {}
    fn = os.path.join(data_dir, "bo_exports", f"{VENUE_BO_FILE[venue]}_products.csv")
    with open(fn, newline="") as fh:
        for r in csv.DictReader(fh):
            id2name[r["ProductID"]] = r["ProductName"]
            name2meta[r["ProductName"]] = {
                "rg": r.get("ReportingGroup", ""),
                "unit": r.get("Unit", ""),
                "size": r.get("DefaultSize", ""),
            }
    return id2name, name2meta


def load_scrape(data_dir: str, venue: str):
    with open(os.path.join(data_dir, VENUE_SCRAPE_FILE[venue])) as fh:
        return json.load(fh).get("nonzero_pars", {})


def load_overrides(data_dir: str, venue: str):
    with open(os.path.join(data_dir, "par_overrides.json")) as fh:
        doc = json.load(fh)
    out = {}
    for ov in doc.get("overrides", []):
        if ov.get("venue") == venue and ov.get("status", "active") == "active":
            out[ov["product"]] = ov
    return out


def load_recipes(data_dir: str, venue: str):
    """Return (leaves_by_norm, recipe_norms) where leaves_by_norm[normname] is a
    dict of {leaf_id: ml_per_serve} with subrecipes fully expanded. Later
    definitions of the same product win (recipes are effective-dated, appended)."""
    fn = os.path.join(data_dir, "recipes", f"{VENUE_RECIPE_FILE[venue]}.yaml")
    docs = yaml.safe_load(open(fn)) or []
    # Merge in the par-only auto-parsed cocktail recipes. These live in a
    # separate file so the COGS pipeline never reads them (they're not yet
    # cost-finalised), but the par model still needs them for cocktail
    # consumption and subrecipe resolution.
    review_fn = os.path.join(
        data_dir, "recipes", "par_review", f"{VENUE_RECIPE_FILE[venue]}.yaml"
    )
    if os.path.exists(review_fn):
        docs = docs + (yaml.safe_load(open(review_fn)) or [])
    by_name = {}
    for d in docs:
        if isinstance(d, dict) and d.get("product"):
            by_name[d["product"]] = d  # last wins

    def leaves(pname, scale, seen):
        doc = by_name.get(pname)
        if not doc or pname in seen:
            return {}
        seen = seen | {pname}
        out = defaultdict(float)
        for ing in doc.get("ingredients", []) or []:
            if not isinstance(ing, dict):
                continue
            qty = float(ing.get("qty", 0) or 0)
            if "subrecipe" in ing:
                sub = ing["subrecipe"]
                sdoc = by_name.get(sub)
                syield = float(sdoc.get("yield_qty")) if sdoc and sdoc.get("yield_qty") else None
                if sdoc and syield:
                    for k, v in leaves(sub, qty / syield, seen).items():
                        out[k] += v * scale
            else:
                iid = str(ing.get("id", ""))
                if iid:
                    out[iid] += qty * scale
        return out

    leaves_by_norm, recipe_norms = {}, []
    for pname in by_name:
        lv = leaves(pname, 1.0, frozenset())
        nn = _norm(pname)
        leaves_by_norm[nn] = dict(lv)
        recipe_norms.append(nn)
    return leaves_by_norm, recipe_norms


# ── POS-name -> par-SKU aliases ─────────────────────────────────────────────
_VENUE_PREFIX_RE = re.compile(r"^\s*([A-Za-z_]+)\s*:\s*(.+?)\s*$")


def parse_alias_target(value, default_venue):
    """('stow', 'Kirin [Keg]') from the alias value 'stow:Kirin [Keg]'.

    A bare value means "a par SKU in THIS venue" and returns
    (default_venue, value) — the behaviour every existing alias relies on. A
    value prefixed with a known venue code means "this till line is poured from
    THAT venue's stock", which is the shared-stock case: Harry Gatos pours the
    tap beers and Coke/Sprite cans that are held and ordered centrally against
    Stowaway's par SKUs. Only the venue codes in VENUE_CODES are treated as a
    prefix, so a SKU name that merely contains a colon is left alone.
    """
    if not isinstance(value, str):
        return default_venue, value
    m = _VENUE_PREFIX_RE.match(value)
    if m and m.group(1).lower() in VENUE_CODES:
        return m.group(1).lower(), m.group(2)
    return default_venue, value


class AliasBook:
    """The bridge across POS<->Purchase-module naming drift.

    The par model attributes POS volume to par SKUs BY NAME. When the till name
    and the stock name disagree ('Petits Detours Rosé' vs 'Petits Detours Rosé
    Mediterranee - Bottle', 'Fresh is Best Lager' vs 'Alehouse Draught Lager
    [Keg]', 'Baileys' vs "Bailey's ... 1L") the volume was silently dropped and
    the par collapsed. data/par_aliases.json is the curated, reviewed map; this
    class is the only thing that reads it.

    It also carries `_intentionally_unattributed` — post-mix and made-to-order
    drinks that consume no discrete stock unit and therefore CANNOT attribute —
    so the coverage guard can tell "we know about this" from "this is a bug".

    CROSS-VENUE TARGETS. Some lines are poured at one venue out of stock that is
    held and ordered at the other. An alias value may therefore name the venue:
    "stow:Kirin [Keg]" inside the `hg` map means "this Harry Gatos till line
    consumes STOWAWAY's Kirin keg par". Such a line contributes to the target
    venue's par SKU and to NOTHING at the venue that sold it — it is attributed,
    just somewhere else, and the unattributed report records it under
    `attributed_to_other_venue` so it stays auditable.
    """

    def __init__(self, doc, venue):
        self.venue = venue
        self.doc = doc or {}
        raw = {k: v for k, v in (self.doc.get(venue) or {}).items()
               if not k.startswith("_")}
        # An alias value is normally just the par SKU name. It may instead be
        # {"sku": ..., "serve_ml": N} when the POS line is a pour whose size the
        # model cannot infer from the reporting group — e.g. Vinada, a
        # Non-alcoholic line that is 150ml poured out of a 750ml bottle, where
        # every other Non-alcoholic line is a 1:1 whole-unit sale.
        # Either form's SKU may carry a "<venue>:" prefix (see parse_alias_target).
        #
        # self.map keeps the value exactly as written (prefix and all) so the
        # build's error output and the meta block stay readable; self.targets
        # holds the parsed (venue, sku) pair that the model actually uses.
        self.map, self.serve, self.targets = {}, {}, {}
        for pos_name, val in raw.items():
            if isinstance(val, dict):
                self.map[pos_name] = val.get("sku")
                if val.get("serve_ml"):
                    self.serve[_norm(pos_name)] = float(val["serve_ml"])
            else:
                self.map[pos_name] = val
            self.targets[pos_name] = parse_alias_target(self.map[pos_name], venue)
        self._by_norm = {}
        for pos_name, tgt in self.targets.items():
            self._by_norm[_norm(pos_name)] = tgt
        self.intentional = {}
        for e in ((self.doc.get("_intentionally_unattributed") or {}).get(venue) or []):
            nm = e.get("product") if isinstance(e, dict) else e
            if nm:
                self.intentional[_norm(nm)] = (
                    e.get("reason", "") if isinstance(e, dict) else "")
        self.investigate = {}
        for e in ((self.doc.get("_unmapped_investigate") or {}).get(venue) or []):
            nm = e.get("product") if isinstance(e, dict) else e
            if nm:
                self.investigate[_norm(nm)] = (
                    e.get("note", "") if isinstance(e, dict) else "")

    def resolve(self, pos_name):
        """(venue, par SKU) this POS line belongs to, or (None, None).

        `venue` is this book's own venue for a plain target and the named venue
        for a cross-venue "stow:..." target.
        """
        return self._by_norm.get(_norm(pos_name), (None, None))

    def target(self, pos_name):
        """The par SKU this POS line belongs to, or None. Venue-agnostic —
        callers that care which venue's stock it is must use resolve()."""
        return self.resolve(pos_name)[1]

    def target_venue(self, pos_name):
        """The venue whose par SKU this POS line consumes, or None."""
        return self.resolve(pos_name)[0]

    def cross_venues(self):
        """Venues OTHER than this book's own that its aliases point at."""
        return {v for v, _ in self.targets.values() if v and v != self.venue}

    def serve_ml(self, pos_name):
        """Explicit per-alias serve size in ml, or None to use the group rule."""
        return self.serve.get(_norm(pos_name))

    def is_intentional(self, pos_name):
        return _norm(pos_name) in self.intentional

    def intentional_reason(self, pos_name):
        return self.intentional.get(_norm(pos_name), "")

    def is_flagged_for_investigation(self, pos_name):
        return _norm(pos_name) in self.investigate

    def unknown_targets(self, par_names, venue_par_names=None):
        """Alias entries whose TARGET is not a real par SKU. An alias pointing at
        a name that does not exist is worse than no alias: it looks mapped and
        attributes nothing. The build validates this.

        `par_names` is this venue's par universe. `venue_par_names` is an
        optional {venue: names} map used to validate CROSS-VENUE targets — a
        "stow:" target must exist in Stowaway's par universe, and if that
        universe was not supplied the entry is reported rather than assumed
        good, so an unvalidatable target can never pass silently.

        Returns {pos_name: value-as-written}, prefix included.
        """
        known = {self.venue: {_norm(n) for n in par_names}}
        for v, names in (venue_par_names or {}).items():
            known[v] = {_norm(n) for n in names}
        out = {}
        for pos, raw in self.map.items():
            tv, sku = self.targets.get(pos, (None, None))
            pool = known.get(tv)
            if not sku or pool is None or _norm(sku) not in pool:
                out[pos] = raw
        return out


def load_aliases(data_dir: str, venue: str) -> AliasBook:
    path = os.path.join(data_dir, ALIAS_FILE)
    if not os.path.exists(path):
        return AliasBook({}, venue)
    with open(path) as fh:
        return AliasBook(json.load(fh), venue)


def par_universe(data_dir: str, venue: str):
    """The set of par SKU names that exist for a venue.

    Same construction as compute_venue's ParIndex (live pars + hard overrides +
    bulk catalog names), exposed on its own so ONE venue's build can validate a
    cross-venue alias target against ANOTHER venue's par universe without
    computing that venue.
    """
    _, bo_meta = load_bo(data_dir, venue)
    scrape = load_scrape(data_dir, venue)
    ov = {p: o for p, o in load_overrides(data_dir, venue).items()
          if o.get("protect") == "hard"}
    return ParIndex(scrape, ov, bo_meta).names


def tap_serve_ml(pos_name: str) -> float:
    """Serve size for a tap-beer POS line.

    products_weekly collapses '- Schooner'/'- Pint' into one bare line, so the
    blended TAP_SERVE_ML constant is used — UNLESS the line names its own size
    ('Sapporo - 500ml') or survived with an explicit variant suffix.
    """
    low = pos_name.lower()
    m = re.search(r"-\s*(\d+(?:\.\d+)?)\s*ml\s*$", low)
    if m:
        return float(m.group(1))
    if low.rstrip().endswith("schooner"):
        return SCHOONER_ML
    if low.rstrip().endswith("pint"):
        return PINT_ML
    if low.rstrip().endswith("jug"):
        return 1140.0
    return TAP_SERVE_ML


# ── par-SKU name resolution ─────────────────────────────────────────────────
class ParIndex:
    """Resolves a POS product name or a recipe ingredient BO-name to a par SKU.

    Seeded from live pars + overrides + any bulk (size/keg/bottle) catalog name."""

    def __init__(self, scrape, overrides, bo_name2meta):
        names = set(scrape) | set(overrides)
        for nm in bo_name2meta:
            if _is_bulk_par(nm):
                names.add(nm)
        self.names = names
        self.exact = {}
        self.bulk_base = {}
        # DETERMINISM: `names` is a set, so iterating it directly made the
        # winner for a colliding base depend on Python's per-process string
        # hash seed. Six bases collide across the two venues today ('Stow
        # Vermouth Blend' [30ml] vs [Bottle], HG 'Kunizakari Umeshu' [60ml] vs
        # [Bottle], a double-spaced duplicate 'Chambord  [Bottle]', ...) and the
        # par for each of them flapped from build to build with no input
        # change. Sort first, then pick the LARGEST container — a keg beats a
        # bottle beats a single-serve pour line, which is the same preference
        # the old comment described but could not actually enforce.
        for nm in sorted(names):
            self.exact.setdefault(_norm(nm), nm)
            if _is_bulk_par(nm):
                base = _strip_variant(nm)
                cur = self.bulk_base.get(base)
                if cur is None or bottle_ml(nm) > bottle_ml(cur):
                    self.bulk_base[base] = nm

    def resolve_exact(self, name):
        return self.exact.get(_norm(name))

    def resolve_bulk(self, name):
        return self.bulk_base.get(_strip_variant(name))

    def resolve_ingredient(self, bo_name):
        """Recipe ingredient BO-name -> par SKU (exact first, then bulk base)."""
        return self.resolve_exact(bo_name) or self.resolve_bulk(bo_name)


def build_rg_book(bo_name2meta):
    """{stripped base name: reporting group} for every SELLABLE catalog product.

    The par SKUs are the bulk/stock lines ('… [Bottle]', '… [Keg]', '… [1L]')
    and Lightspeed leaves ReportingGroup EMPTY on those — they are stock items,
    not sale items. Left as-is, every par SKU falls into one giant '' group and
    every group-level prior in v3 (seasonal index, cv shrink, shrinkage prior)
    becomes meaningless. The sale-side twin does carry the group ('Rooster Rojo
    Blanco Tequila [House]' -> 'Tequila'), and it shares the base name, so borrow
    it from there.
    """
    book = {}
    for name, meta in bo_name2meta.items():
        rg = (meta or {}).get("rg", "")
        if not rg:
            continue
        book.setdefault(_strip_variant(name), rg)
    return book


def build_recipe_matcher(recipe_norms):
    ordered = sorted(recipe_norms, key=len, reverse=True)

    def match(sales_name):
        sn = _norm(sales_name)
        for rn in ordered:
            if not rn:
                continue
            if sn == rn:
                return rn
            if len(rn) >= 4 and sn.startswith(rn) and (
                len(sn) == len(rn) or not sn[len(rn)].isalnum()
            ):
                return rn
        return None

    return match


# ── forecast maths ──────────────────────────────────────────────────────────
def _weighted_recent(series):
    """Linear-weighted mean of the last RECENT_WEEKS values (recent = heavier)."""
    tail = series[-RECENT_WEEKS:]
    if not tail:
        return 0.0
    wsum = sum((i + 1) for i in range(len(tail)))
    return sum(v * (i + 1) for i, v in enumerate(tail)) / wsum


def _yoy_growth(series):
    """Recent-8wk total vs the same 8 weeks ~1 year earlier, clamped."""
    n = len(series)
    if n < 60:
        return None
    recent = series[-RECENT_WEEKS:]
    pa, pb = n - 52 - RECENT_WEEKS, n - 52
    if pa < 0:
        return None
    prior = series[pa:pb]
    sp, sr = sum(prior), sum(recent)
    if sp <= 0.01 or sr <= 0.0:
        return None
    return max(GROWTH_CAP_LO, min(GROWTH_CAP_HI, sr / sp))


def _prior_season(series):
    """Prior-year analog of the upcoming (forecast) week: mean of a 3-week window
    centred one year before the next ordering week."""
    n = len(series)
    c = n - 52
    lo, hi = c - 1, c + 2
    if lo < 0:
        return None
    window = [v for v in series[lo:hi]]
    if not window or sum(window) <= 0:
        return None
    return sum(window) / len(window)


def forecast_sku(series):
    """Return (forecast_wk, method, growth, cv, window_peak, buffer)."""
    recent_avg = _weighted_recent(series)
    window = series[-WINDOW_WEEKS:]
    window_peak = max(window) if window else 0.0
    recent = series[-RECENT_WEEKS:]
    nz = [v for v in recent]
    mean_r = sum(nz) / len(nz) if nz else 0.0
    cv = (statistics.stdev(nz) / mean_r) if (len(nz) >= 4 and mean_r > 0) else None

    growth = _yoy_growth(series)
    pseason = _prior_season(series)
    if pseason is not None and pseason > 0 and growth is not None:
        forecast = pseason * growth
        method = "YoY seasonal"
        if recent_avg > forecast and (cv is None or cv <= SEASONAL_CV):
            forecast, method = recent_avg, "Recent 8wk (surge)"
    elif recent_avg > 0:
        forecast, method = recent_avg, "Recent 8wk"
    else:
        forecast, method = 0.0, "No recent sales"

    buf = round(min(BUFFER_HI, max(BUFFER_LO, 1.10 + 0.4 * cv)), 3) if cv is not None else SAFETY_BUFFER
    return forecast, method, growth, cv, window_peak, buf


def _burst_floor(series, exp):
    """The 'you can't have 2 cans in the fridge' floor.

    Weekly demand for session products is clumped: the product sells in only some
    weeks, but when it sells a guest takes several back to back. The weekly MEAN
    is therefore a bad guide to how many you must physically hold. Take the 90th
    percentile of the weeks in which the product actually sold, scaled to the
    exposure window — enough to serve a realistic round without stocking to the
    all-time max.

    Returns 0.0 for products with too little signal to judge.
    """
    nz = sorted(v for v in series[-26:] if v > 0)
    if len(nz) < 3:
        return 0.0
    idx = min(len(nz) - 1, int(0.9 * len(nz)))
    burst = nz[idx]
    ratio = (exp["day_units"] / exp["normal_day_units"]) if exp.get("normal_day_units") else 1.0
    return round(burst * max(1.0, ratio), 1)


def _spike_floor(window_peak, growth):
    """v2's spike floor. Retained for the v2-vs-v3 impact comparison only."""
    if window_peak <= 0:
        return 0.0
    adj = max(growth, DECLINE_FLOOR) if (growth is not None and growth < 1.0) else 1.0
    return math.ceil(window_peak * adj * SPIKE_FLOOR_MULT * 10) / 10


# ── v3 forecast: deseasonalised level × week-of-year index ──────────────────
def forecast_v3(series, deseason, target_index):
    """(forecast_wk, level, method).

    The level is the linear-weighted mean of the last RECENT_WEEKS of the
    DESEASONALISED series, so a hot December does not permanently raise the
    baseline; the seasonal index then puts the season back for the target week.
    """
    level = _weighted_recent(deseason)
    if level <= 0:
        # No recent movement on the deseasonalised series — fall back to the raw
        # recent mean so a SKU that only just started selling isn't zeroed.
        level = _weighted_recent(series)
        if level <= 0:
            return 0.0, 0.0, "No recent sales"
        return level * target_index, level, "Recent 8wk x seasonal (raw level)"
    return level * target_index, level, "Deseasonalised level x seasonal index"


def sanity_floor(series, weeks, target_woy, exposure_ratio):
    """The DEMOTED spike floor: never below the 90th-percentile week of the
    seasonal window (same weeks-of-year across ALL years), scaled to the
    exposure window. Floor only — it never drives the par."""
    vals = seasonal_mod.seasonal_window_values(series, weeks, target_woy)
    if not vals:
        return 0.0
    p90 = seasonal_mod.percentile(vals, seasonal_mod.FLOOR_PERCENTILE)
    if p90 <= 0:
        return 0.0
    return math.ceil(p90 * max(exposure_ratio, 0.0) * 10) / 10


def apply_override(rec_par, ov):
    """Overrides win. hold=exact, min=floor (raise only), max=cap, zero=0."""
    if not ov:
        return rec_par
    t = ov.get("type")
    v = ov.get("value")
    if t == "zero":
        return 0.0
    if t == "hold" and v is not None:
        return float(v)
    if t == "min" and v is not None:
        return max(rec_par, float(v))
    if t == "max" and v is not None:
        return min(rec_par, float(v))
    if t == "reserve" and v is not None:
        # Physical drum/infusion float the model can't see (batched stock), ADDED
        # on top of the modelled weekly throughput — not a floor. par = reserve +
        # cover. Keeps the drums full AND covers a week's service.
        return round(rec_par + float(v), 1)
    return rec_par


# ── consumption + assembly ──────────────────────────────────────────────────
def _wine_serve_ml(pos_name, bottle_size_ml):
    """ml drawn from the bottle par by one sale of this wine line."""
    low = pos_name.lower()
    if low.endswith("- large glass") or low.endswith("- large"):
        return WINE_LARGE_ML
    if low.endswith("- glass") or low.endswith("- regular"):
        return WINE_REGULAR_ML
    if low.endswith("- bottle") or low.rstrip().endswith(" d"):
        return bottle_size_ml
    # BARE — products_weekly collapses the wine variants into a single line that
    # is overwhelmingly by-the-glass. Same assumption the un-aliased path makes.
    return WINE_REGULAR_ML


def _alias_units(pos_name, rg, qty, sku, serve_ml=None):
    """Physical units of par SKU `sku` consumed by `qty` of an aliased POS line.

    The alias file says only WHICH par SKU a POS line belongs to. The unit
    conversion is the model's existing one (nip->bottle, glass->bottle,
    schooner/pint->keg), applied to the aliased target. Units are computed ONCE,
    here, straight from qty — there is no second conversion downstream, so an
    aliased line can never be double-converted.
    """
    if not _is_bulk_par(sku):
        return qty                      # sale unit == stock unit (cans, tins)
    bml = bottle_ml(sku)
    if bml <= 0:
        return qty
    low = pos_name.lower()
    if serve_ml:                        # explicit per-alias override
        return qty * float(serve_ml) / bml
    if rg in TAP_BEER_RGS or "[keg]" in sku.lower():
        return qty * tap_serve_ml(pos_name) / bml
    if rg in SPIRIT_RGS:
        if "[mixer]" in low:
            return 0.0
        pml = bml if low.rstrip().endswith(" d") else SPIRIT_NIP_ML
        return qty * pml / bml
    if rg in WINE_RGS:
        return qty * _wine_serve_ml(pos_name, bml) / bml
    # Everything else (Non-alcoholic, Bottles/Cans) is a whole-unit sale: one
    # ring = one physical item off the shelf, which is what the un-aliased
    # DIRECT_RGS path already assumes. A line that is really a POUR out of a
    # bulk container must say so with an explicit `serve_ml` in the alias file —
    # guessing 'glass' here would have quietly divided Bundaberg Ginger Beer
    # (a $3.30 bottle sold whole for $5) by five.
    return qty


def _build_consumption(rows, venue, idx, leaves_by_norm, matcher, id2name, weeks,
                       revenue_out=None, aliases=None, unattributed_out=None,
                       cross_aliases=None, cross_units_out=None,
                       exported_out=None):
    """(pour, recipe) weekly series per par SKU.

    `revenue_out`, if given, is filled with {sku: ex-GST revenue attributed to
    that SKU} — a POS line's revenue is split across the par SKUs it actually
    consumed, in proportion to the bottle-fraction consumed. Used only to break
    ties when assigning service classes; it is never money arithmetic that
    reaches a P&L, so plain floats are correct here.

    CROSS-VENUE STOCK. Stowaway and Harry Gatos share some lines: the stock is
    held and ordered centrally against STOWAWAY par SKUs but it also pours
    through the HG till, so HG's sales were consumption that reached no par SKU
    anywhere and Stowaway's pars under-counted it. `cross_aliases` is
    {other_venue: AliasBook}; a row from `other_venue` whose alias target is
    "<this venue>:<sku>" is ingested here, converted by the SAME `_alias_units`
    rules, and added to that SKU's pour series in the row's own week.

      * `cross_units_out`, if given, is filled with {sku: weekly series} holding
        ONLY the foreign contribution, so a reader (and the safety net in
        compute_venue) can tell demand this venue's own till saw from demand it
        did not.
      * `exported_out`, if given, is filled with the rows of THIS venue that an
        alias sends to ANOTHER venue's par SKU. They contribute nothing here —
        that is the point, a POS line feeds exactly one par SKU in exactly one
        venue — but they are attributed, not lost, so they are recorded rather
        than reported as a miss.
    """
    widx = {w: i for i, w in enumerate(weeks)}
    n = len(weeks)
    pour = defaultdict(lambda: [0.0] * n)
    recipe = defaultdict(lambda: [0.0] * n)
    rev = revenue_out if revenue_out is not None else {}

    def _rev(r):
        try:
            return float(r.get("sales_ex_gst") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _miss(name, rg, wk, qty, rev):
        """Record a POS line whose volume reached NO par SKU. This is the raw
        material of the unattributed-volume coverage guard: the bug class this
        whole change exists to make impossible to reintroduce is 'volume was
        silently dropped', and silence is exactly what this ends."""
        if unattributed_out is None:
            return
        a = unattributed_out.setdefault(name, {
            "product": name, "reporting_group": rg, "weekly": {}})
        a["reporting_group"] = rg or a["reporting_group"]
        w = a["weekly"].setdefault(wk, {"qty": 0.0, "revenue_ex_gst": 0.0})
        w["qty"] += qty
        w["revenue_ex_gst"] += rev

    def _exported(name, rg, wk, qty, rev, tgt_venue, tgt_sku):
        """Record a line of THIS venue whose stock is another venue's par SKU."""
        if exported_out is None:
            return
        a = exported_out.setdefault(name, {
            "product": name, "reporting_group": rg, "target_venue": tgt_venue,
            "target_sku": tgt_sku, "weekly": {}})
        a["reporting_group"] = rg or a["reporting_group"]
        w = a["weekly"].setdefault(wk, {"qty": 0.0, "revenue_ex_gst": 0.0})
        w["qty"] += qty
        w["revenue_ex_gst"] += rev

    for r in rows:
        row_venue = r["venue"]
        wk = r["week_ending"]
        if wk not in widx:
            continue
        i = widx[wk]
        name, rg, qty = r["product_name"], r["reporting_group"], r["qty"]
        if qty == 0:
            continue

        if row_venue != venue:
            # ── CROSS-VENUE. Another venue's till line, poured out of THIS
            # venue's stock. Only an explicit "<venue>:<sku>" alias in that
            # venue's book gets in here; everything else about another venue is
            # none of this build's business. The conversion is the same one an
            # own-venue alias gets, so a schooner is still 500/50000 of a keg
            # and a can is still 1:1.
            ab = (cross_aliases or {}).get(row_venue)
            if ab is None:
                continue
            tgt_venue, tgt_sku = ab.resolve(name)
            if tgt_venue != venue or not tgt_sku:
                continue
            units = _alias_units(name, rg, qty, tgt_sku,
                                 serve_ml=ab.serve_ml(name))
            if units > 0:
                pour[tgt_sku][i] += units
                rev[tgt_sku] = rev.get(tgt_sku, 0.0) + _rev(r)
                if cross_units_out is not None:
                    cross_units_out.setdefault(tgt_sku, [0.0] * n)[i] += units
            continue

        # ── ALIAS FIRST. An alias is a human saying "this till line IS that
        # stock item". It short-circuits BOTH the recipe matcher and the
        # fallback name matcher, so an aliased line contributes to exactly one
        # par SKU and can never be counted twice.
        alias_venue, alias_sku = (aliases.resolve(name) if aliases is not None
                                  else (None, None))
        if alias_sku is not None:
            if alias_venue != venue:
                # The stock lives at the other venue and is counted there. It
                # must add NOTHING here — not to a par SKU, and not to the
                # unattributed report either, because it IS attributed.
                _exported(name, rg, wk, qty, _rev(r), alias_venue, alias_sku)
                continue
            units = _alias_units(name, rg, qty, alias_sku,
                                 serve_ml=aliases.serve_ml(name))
            if units > 0:
                pour[alias_sku][i] += units
                rev[alias_sku] = rev.get(alias_sku, 0.0) + _rev(r)
            continue

        rm = matcher(name)
        if rm is not None and rm in leaves_by_norm:
            drawn = {}
            for leaf_id, ml in leaves_by_norm[rm].items():
                if not leaf_id.startswith("lightspeed:"):
                    continue
                bo_name = id2name.get(leaf_id.split(":", 1)[1])
                if not bo_name:
                    continue
                sku = idx.resolve_ingredient(bo_name)
                if not sku:
                    continue
                units = qty * ml / bottle_ml(sku)
                recipe[sku][i] += units
                drawn[sku] = drawn.get(sku, 0.0) + units
            tot = sum(drawn.values())
            if tot > 0:
                line_rev = _rev(r)
                for sku, u in drawn.items():
                    rev[sku] = rev.get(sku, 0.0) + line_rev * (u / tot)
            else:
                # The recipe exists but every one of its leaves failed to
                # resolve to a par SKU — the volume still reached nothing.
                _miss(name, rg, wk, qty, _rev(r))
            continue
        low = name.lower()
        hit = None
        if rg in SPIRIT_RGS:
            if "[mixer]" in low:
                continue
            sku = idx.resolve_bulk(name)
            if not sku:
                _miss(name, rg, wk, qty, _rev(r))
                continue
            bml = bottle_ml(sku)
            pml = bml if low.rstrip().endswith(" d") else SPIRIT_NIP_ML
            pour[sku][i] += qty * pml / bml
            hit = sku
        elif rg in WINE_RGS:
            if low.endswith("- large glass") or low.endswith("- large"):
                kind = WINE_LARGE_ML
            elif low.endswith("- glass") or low.endswith("- regular"):
                kind = WINE_REGULAR_ML
            elif low.endswith("- bottle") or low.rstrip().endswith(" d"):
                kind = "BOTTLE"
            else:
                kind = "BARE"
            if isinstance(kind, float):
                sku = idx.resolve_bulk(name)
                if sku:
                    pour[sku][i] += qty * kind / bottle_ml(sku)
                    hit = sku
            elif kind == "BOTTLE":
                sku = idx.resolve_bulk(name)
                if sku:
                    pour[sku][i] += qty
                    hit = sku
            else:  # BARE — in products_weekly wine variants are collapsed to a
                # single bare line that is overwhelmingly by-the-glass. Treat as a
                # standard 150ml glass pour into the bottle par. (Assumption noted
                # for human review; a bare 'direct' par wins if one exists.)
                sku = idx.resolve_bulk(name)
                if sku:
                    pour[sku][i] += qty * WINE_REGULAR_ML / bottle_ml(sku)
                    hit = sku
                else:
                    ex = idx.resolve_exact(name)
                    if ex:
                        pour[ex][i] += qty
                        hit = ex
        elif rg in TAP_BEER_RGS:
            # Tap beer's recipe rows are named per variant ('Stone & Wood -
            # Schooner') and products_weekly collapses the sale line to 'Stone &
            # Wood', so the recipe matcher above never claims a tap line and the
            # keg received nothing. Pour it straight into the keg par at the
            # measured blended serve. Only reachable when the recipe path did NOT
            # claim the line, so there is no double count.
            sku = idx.resolve_bulk(name)
            if sku:
                pour[sku][i] += qty * tap_serve_ml(name) / bottle_ml(sku)
                hit = sku
        elif rg in DIRECT_RGS:
            sku = idx.resolve_exact(name)
            if sku:
                pour[sku][i] += qty
                hit = sku
        if hit is not None:
            rev[hit] = rev.get(hit, 0.0) + _rev(r)
        else:
            _miss(name, rg, wk, qty, _rev(r))
    return pour, recipe


def coverage_gap(rows, venue, matcher, leaves_by_norm, recent_weeks):
    """Classic/Signature cocktails with recent sales that DON'T resolve to a
    recipe (open-price '$NN Custom Cocktail' excluded). Returns [(name, qty)]."""
    recent = set(recent_weeks)
    tot = defaultdict(float)
    for r in rows:
        if (r["venue"] == venue and r["reporting_group"] in COCKTAIL_RGS
                and r["week_ending"] in recent):
            tot[r["product_name"]] += r["qty"]
    gaps = []
    for name, q in tot.items():
        if q <= 0 or _is_custom_cocktail(name):
            continue
        rm = matcher(name)
        if rm is None or rm not in leaves_by_norm:
            gaps.append((name, q))
    return sorted(gaps, key=lambda x: -x[1])


# Reporting groups that carry discrete, orderable stock — the groups a par SKU
# can exist for at all. Cocktails/Mocktails/Delivery Cocktails are deliberately
# ABSENT: they are the recipe system's job and already have their own coverage
# gate above. Food, Modifiers and 'Bar / FOH' are absent because they are not
# drinks. 'Non-alcoholic' IS here — it holds Vinada and the non-alc cans, which
# are real stock — and the post-mix lines inside it are excused by name via
# par_aliases.json `_intentionally_unattributed`, not by excusing the group.
STOCK_BEARING_RG = re.compile(
    r"wine|beer|spirit|tequila|gin|vodka|rum|whisk|liqueur|cider|rtd|"
    r"sparkling|champagne|aperitif|amaro|fortified|bottles\s*/\s*cans|"
    r"non.?alcoholic|soft drink",
    re.I,
)
UNATTRIBUTED_WEEKS = 13
# Above this much 13-week ex-GST revenue reaching NO par SKU, the build FAILS.
# $54,794 was reaching nothing when this guard was written.
UNATTRIBUTED_FAIL_REVENUE = 2000.0


def unattributed_report(raw, weeks, aliases=None, window=UNATTRIBUTED_WEEKS):
    """POS lines whose volume reached NO par SKU.

    Returns (offenders, intentional). `offenders` are lines in a stock-bearing
    drink group that should have landed somewhere and didn't — the work queue,
    and the thing the build gate is measured on. `intentional` are the post-mix
    and made-to-order lines declared in par_aliases.json, returned so a reader
    can see they were considered rather than missed.
    """
    window_weeks = set(weeks[-window:]) if window else set(weeks)
    n_weeks = max(len(window_weeks), 1)
    offenders, intentional = [], []
    for name, a in (raw or {}).items():
        rg = a.get("reporting_group") or ""
        qty = sum(w["qty"] for k, w in a["weekly"].items() if k in window_weeks)
        revenue = sum(w["revenue_ex_gst"] for k, w in a["weekly"].items()
                      if k in window_weeks)
        if qty <= 0 and revenue <= 0:
            continue
        row = {
            "product": name,
            "reporting_group": rg,
            "qty_window": round(qty, 2),
            "qty_per_week": round(qty / n_weeks, 2),
            "revenue_ex_gst_window": round(revenue, 2),
            "stock_bearing": bool(STOCK_BEARING_RG.search(rg)),
        }
        if aliases is not None and aliases.is_intentional(name):
            row["reason"] = aliases.intentional_reason(name)
            intentional.append(row)
            continue
        if not row["stock_bearing"]:
            continue
        if aliases is not None and aliases.is_flagged_for_investigation(name):
            row["investigate_note"] = aliases.investigate.get(_norm(name), "")
            row["known_unmapped"] = True
        offenders.append(row)
    offenders.sort(key=lambda r: -r["revenue_ex_gst_window"])
    intentional.sort(key=lambda r: -r["revenue_ex_gst_window"])
    return offenders, intentional


def cross_venue_report(raw, weeks, window=UNATTRIBUTED_WEEKS):
    """POS lines of this venue that are attributed to ANOTHER venue's par SKU.

    These are NOT unattributed — the stock is held and ordered centrally at the
    other venue and the volume lands on that venue's par — but a line vanishing
    from this venue's own numbers with no explanation is exactly the silence the
    unattributed gate exists to end. So they are reported, with their target, in
    `attributed_to_other_venue`.
    """
    window_weeks = set(weeks[-window:]) if window else set(weeks)
    n_weeks = max(len(window_weeks), 1)
    out = []
    for name, a in (raw or {}).items():
        qty = sum(w["qty"] for k, w in a["weekly"].items() if k in window_weeks)
        revenue = sum(w["revenue_ex_gst"] for k, w in a["weekly"].items()
                      if k in window_weeks)
        if qty <= 0 and revenue <= 0:
            continue
        out.append({
            "product": name,
            "reporting_group": a.get("reporting_group") or "",
            "qty_window": round(qty, 2),
            "qty_per_week": round(qty / n_weeks, 2),
            "revenue_ex_gst_window": round(revenue, 2),
            "target_venue": a.get("target_venue"),
            "target_sku": a.get("target_sku"),
        })
    out.sort(key=lambda r: -r["revenue_ex_gst_window"])
    return out


def compute_venue(venue, data_dir="data", rows=None, order_sunday=None,
                  engine="v3"):
    """Compute par recommendations for a venue. Returns (recs, meta).

    recs: {sku: row-dict}; meta: {weeks, recent_weeks, coverage_gaps, exposure, ...}

    `engine="v2"` reruns the OLD maths (recent-8wk + YoY, volatility buffer,
    trailing-13wk spike floor, no shrinkage, flat one-week coverage) on exactly
    the same inputs. It exists so the v2->v3 impact can be measured honestly in
    one process rather than diffed against a stale committed artefact.
    """
    if rows is None:
        rows = load_weekly(data_dir)
    id2name, bo_meta = load_bo(data_dir, venue)
    scrape = load_scrape(data_dir, venue)
    all_overrides = load_overrides(data_dir, venue)
    # Only protect="hard" overrides are ENFORCED by the model. protect="flag"
    # entries (e.g. detected manual raises) are advisory/review-only per the
    # par_overrides.json schema, so they never freeze a modelled reduction.
    overrides = {p: ov for p, ov in all_overrides.items() if ov.get("protect") == "hard"}
    leaves_by_norm, recipe_norms = load_recipes(data_dir, venue)
    idx = ParIndex(scrape, overrides, bo_meta)
    matcher = build_recipe_matcher(recipe_norms)
    aliases = load_aliases(data_dir, venue)
    # Cross-venue stock: another venue's till lines that are poured out of THIS
    # venue's par SKUs (HG's shared taps and Coke/Sprite cans -> Stowaway).
    cross_aliases = {}
    for other in VENUE_CODES:
        if other == venue:
            continue
        ab = load_aliases(data_dir, other)
        if venue in ab.cross_venues():
            cross_aliases[other] = ab
    # ...and the mirror: this venue's aliases that point AT another venue must be
    # validated against THAT venue's par universe, or a typo'd target would look
    # mapped and attribute nothing anywhere.
    other_par_names = {v: par_universe(data_dir, v) for v in aliases.cross_venues()}

    weeks = sorted({r["week_ending"] for r in rows})
    recent_weeks = weeks[-RECENT_WEEKS:]
    revenue = {}
    unattributed_raw = {}
    cross_units = {}
    exported_raw = {}
    pour, recipe = _build_consumption(rows, venue, idx, leaves_by_norm, matcher,
                                      id2name, weeks, revenue_out=revenue,
                                      aliases=aliases,
                                      unattributed_out=unattributed_raw,
                                      cross_aliases=cross_aliases,
                                      cross_units_out=cross_units,
                                      exported_out=exported_raw)

    universe = set(pour) | set(recipe) | set(scrape) | set(overrides)
    n = len(weeks)

    rg_book = build_rg_book(bo_meta)

    def rg_of(sku):
        return bo_meta.get(sku, {}).get("rg", "") or rg_book.get(_strip_variant(sku), "")

    consumption = {}
    for sku in universe:
        p = pour.get(sku, [0.0] * n)
        rc = recipe.get(sku, [0.0] * n)
        consumption[sku] = [p[i] + rc[i] for i in range(n)]

    # ── v3 machinery (skipped entirely for the v2 comparison engine) ────────
    if engine == "v3":
        cal = par_calendar.load_calendar(data_dir)
        order_sunday = par_calendar.next_order_sunday(order_sunday)
        exp = par_calendar.exposure(order_sunday, cal)
        # A cancelled cycle covers nothing; price the cycle that actually lands.
        if exp["day_units"] <= 0:
            exp = par_calendar.exposure(
                date.fromisoformat(exp["next_delivery"]) - timedelta(days=3), cal)
        exposure_ratio = exp["exposure_ratio"]
        target_woy = seasonal_mod.week_of_year(exp["delivery"])

        season = seasonal_mod.SeasonalBook(weeks, consumption, rg_of)
        deseason = {sku: season.deseasonalised(sku, rg_of(sku), s)
                    for sku, s in consumption.items()}
        vol = service_mod.VolatilityBook(deseason, rg_of)
        classes = service_mod.classify(consumption, revenue)
        shrink, shrink_summary = shrinkage_mod.build(
            data_dir, venue, weeks, consumption, id2name,
            idx.resolve_ingredient, rg_of)
        uplift, bookings_status = bookings_mod.shadow_uplift(
            consumption,
            date.fromisoformat(exp["delivery"]),
            date.fromisoformat(exp["next_delivery"]),
        )
    else:
        cal = exp = season = vol = None
        exposure_ratio, target_woy = 1.0, None
        deseason, classes, shrink, shrink_summary = {}, {}, {}, {"counts": [], "periods": []}
        uplift, bookings_status = {}, "not computed (v2 engine)"
        order_sunday = None

    recs = {}
    n_recent = min(RECENT_WEEKS, n)
    for sku in sorted(universe):
        p = pour.get(sku, [0.0] * n)
        rc = recipe.get(sku, [0.0] * n)
        xs = cross_units.get(sku, [0.0] * n)
        series = consumption[sku]
        rg = rg_of(sku)
        # Did THIS venue's own till see any of this recently, or is every unit of
        # recent demand the other venue's? See the safety net below.
        recent_cross = sum(xs[n - n_recent:]) if n_recent else 0.0
        recent_own = sum(series[n - n_recent:]) - recent_cross if n_recent else 0.0
        cross_only = recent_cross > 1e-9 and recent_own <= 1e-9

        if engine == "v2":
            forecast, method, growth, cv, window_peak, buf = forecast_sku(series)
            true_wk = forecast  # v2: variance channel hard-coded absent
            rec_par = round(true_wk * COVERAGE_WEEKS * buf, 1) if true_wk > 0 else 0.0
            floor = _spike_floor(window_peak, growth)
            spike_floored = False
            if floor > 0 and rec_par < floor:
                rec_par, spike_floored = floor, True
            detail = {"path": "v2", "service_class": None, "exposure_ratio": 1.0}
            sk = {"loss_per_week": 0.0, "loss_fraction": 0.0, "n_periods": 0,
                  "capped": False}
            up = 0.0
            season_idx, season_src, level = 1.0, "n/a", None
        else:
            season_idx = season.index_for(sku, rg, target_woy)
            season_src = season.source_for(sku)
            forecast, level, method = forecast_v3(series, deseason[sku], season_idx)
            _, _, growth, _cv_raw, window_peak, buf = forecast_sku(series)
            cv, cv_src = vol.cv_for(sku, rg)
            sk = shrink.get(sku, {"loss_per_week": 0.0, "loss_fraction": 0.0,
                                  "n_periods": 0, "capped": False})
            up = float(uplift.get(sku, 0.0) or 0.0)
            true_wk = forecast + sk["loss_per_week"]
            rec_raw, detail = service_mod.order_up_to(
                forecast_wk=forecast,
                cv=cv,
                exposure_units=exp["day_units"],
                normal_units=exp["normal_day_units"],
                service_class=classes.get(sku, "standard"),
                shrink_fraction=sk["loss_fraction"],
                bookings_uplift=(up if BOOKINGS_LIVE else 0.0),
                burst_floor=_burst_floor(series, exp),
            )
            detail["cv_source"] = cv_src
            detail["seasonal_index"] = round(season_idx, 3)
            detail["seasonal_source"] = season_src
            rec_par = round(rec_raw, 1) if forecast > 0 else 0.0
            floor = sanity_floor(series, weeks, target_woy, exposure_ratio)
            spike_floored = False
            if floor > 0 and rec_par < floor:
                rec_par, spike_floored = floor, True

        cur = scrape.get(sku)
        ov = overrides.get(sku)

        # SAFETY: never auto-zero a currently-stocked SKU purely because no recent
        # demand was matched (naming drift, seasonality, or a POS line we can't
        # see). Hold at the current live par and let par_flag_report surface it.
        # Deliberate reductions to zero happen only via an explicit `zero`/`max`
        # override. Upload stays manual, so holding is the conservative choice.
        #
        # v3 note: the test is on FORECAST, not true_wk. Shrinkage alone must
        # never make a SKU look "alive" — a SKU with no rung-up demand but a
        # measured stock loss is a naming/mapping problem, not a stock policy,
        # and letting true_wk>0 skip this branch is exactly how Coke Zero Can
        # went from a live par of 40.7 to 0 in the first v3 run.
        #
        # CROSS-VENUE COROLLARY: a trickle of demand imported from the OTHER
        # venue is not evidence that this venue's own mapping is healthy. If the
        # only recent demand a SKU has is foreign, releasing the hold would cut a
        # live par on the strength of someone else's till — Stowaway's 'Coke Can'
        # (live par 32.5, own till volume invisible because Marilyna's rings it
        # under its own venue) would have gone to ~1 on 0.23 HG cans a week.
        # Foreign demand may RAISE such a par; it may never lower it.
        held = False
        if cur is not None and (ov is None or ov.get("type") in ("min",)):
            if forecast <= 0:
                rec_par = float(cur)
                spike_floored = False
                held = True
            elif cross_only and rec_par < float(cur):
                rec_par = float(cur)
                spike_floored = False
                held = "cross"

        pre_ov = rec_par
        rec_par = round(apply_override(rec_par, ov), 1)

        flags = []
        if ov:
            flags.append(f"override:{ov.get('type')}")
        if spike_floored:
            flags.append("sanity_floored" if engine == "v3" else "spike_floored")
        if held == "cross":
            flags.append("held_cross_venue_demand_only")
        elif held:
            flags.append("held_no_recent_demand")
        if recent_cross > 1e-9:
            flags.append("cross_venue_demand")
        if method == "No recent sales" and not ov and not held:
            flags.append("no_recent_sales")
        if cur is None and rec_par > 0:
            flags.append("new_par")
        if ov and abs(pre_ov - rec_par) > 1e-9:
            flags.append("override_changed")
        material_loss = sk.get("loss_per_week", 0.0) >= MATERIAL_LOSS_WK
        if sk.get("investigate"):
            flags.append("shrinkage_capped_investigate")
        elif material_loss:
            flags.append("shrinkage_applied")
        if detail.get("path") == "poisson":
            flags.append("low_mover_poisson")
        if detail.get("exposure_ratio", 1.0) > 1.25:
            flags.append("stretched_cycle")
        if material_loss and forecast <= 0:
            # The stock counts can see this SKU moving; products_weekly cannot.
            # That is a till/name mapping gap, not a stock policy — the par is
            # held at the live value and this flag is the work item.
            flags.append("shrinkage_without_demand_mapping")

        pour_wk = round(_weighted_recent(p), 2)
        recipe_wk = round(_weighted_recent(rc), 2)
        cross_wk = round(_weighted_recent(xs), 3)
        variance_wk = round(sk.get("loss_per_week", 0.0), 3)
        recs[sku] = {
            "product": sku,
            "venue": venue,
            "reporting_group": rg,
            "rec_par": rec_par,
            "rec_par_pre_override": round(pre_ov, 1),
            "current_par": cur,
            "drivers": {
                # pour + recipe = what the till saw. variance = what the stock
                # counts say went missing on top of it. true_wk is the sum, and
                # is the number the par is actually built from.
                "pour_wk": pour_wk,
                "recipe_wk": recipe_wk,
                "variance_wk": variance_wk,
                "true_wk": round(pour_wk + recipe_wk + variance_wk, 3),
                # How much of pour_wk arrived from the OTHER venue's till on a
                # shared-stock alias. A SUBSET of pour_wk, not another term —
                # never add it to true_wk.
                "cross_venue_wk": cross_wk,
            },
            "shrinkage": {
                "loss_per_week": variance_wk,
                "loss_fraction": round(sk.get("loss_fraction", 0.0), 4),
                "n_periods": sk.get("n_periods", 0),
                "capped": bool(sk.get("capped")),
            },
            "service": detail,
            "seasonal_index": round(season_idx, 3),
            "seasonal_source": season_src,
            "level_deseasonalised": (round(level, 3) if level is not None else None),
            "forecast_wk": round(forecast, 3),
            "bookings_uplift_shadow": round(up, 3),
            "bookings_applied": bool(BOOKINGS_LIVE),
            "sanity_floor": round(floor, 1),
            "spike_floor": round(floor, 1),   # legacy key — same number, kept so
                                              # existing readers don't break
            "forecast_method": method,
            "growth": round(growth, 3) if growth is not None else None,
            "buffer": buf,
            "override": ({"type": ov.get("type"), "value": ov.get("value")} if ov else None),
            "flags": flags,
        }

    gaps = coverage_gap(rows, venue, matcher, leaves_by_norm, recent_weeks)
    unattr, unattr_intentional = unattributed_report(unattributed_raw, weeks, aliases)
    exported = cross_venue_report(exported_raw, weeks)
    meta = {
        "venue": venue,
        "engine": engine,
        "aliases": {
            "n": len(aliases.map),
            "map": dict(aliases.map),
            "cross_venue_targets": {
                pos: f"{v}:{sku}" for pos, (v, sku) in sorted(aliases.targets.items())
                if v and v != venue
            },
            "unknown_targets": aliases.unknown_targets(idx.names, other_par_names),
        },
        "attributed_to_other_venue": exported,
        "cross_venue_imported": {
            sku: round(sum(s), 3) for sku, s in sorted(cross_units.items())
        },
        "unattributed": unattr,
        "unattributed_intentional": unattr_intentional,
        "unattributed_revenue": round(
            sum(r["revenue_ex_gst_window"] for r in unattr), 2),
        "unattributed_weeks": UNATTRIBUTED_WEEKS,
        "weeks": n,
        "week_range": [weeks[0], weeks[-1]] if weeks else [],
        "recent_weeks": recent_weeks,
        "coverage_gaps": gaps,
        "n_skus": len(recs),
        "order_sunday": (order_sunday.isoformat() if order_sunday else None),
        "exposure": exp,
        "target_week_of_year": target_woy,
        "shrinkage": shrink,
        "shrinkage_summary": shrink_summary,
        "bookings_status": bookings_status,
        "bookings_live": bool(BOOKINGS_LIVE),
        "service_classes": {
            c: sum(1 for v in classes.values() if v == c)
            for c in ("core", "standard", "tail")
        } if classes else {},
    }
    return recs, meta
