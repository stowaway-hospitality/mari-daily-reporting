#!/usr/bin/env python3
"""
Par model v2 — the compute engine.

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

import yaml

# ── tunables ────────────────────────────────────────────────────────────────
COVERAGE_WEEKS = 1.0        # weekly Sunday ordering cycle
SAFETY_BUFFER = 1.20        # fallback buffer when volatility can't be measured
BUFFER_LO = 1.12
BUFFER_HI = 1.30
GROWTH_CAP_LO = 0.60
GROWTH_CAP_HI = 1.80
SPIKE_FLOOR_MULT = 1.20     # worst-window-week * this = never-below floor (raised 1.10->1.20 to harden against stockouts on spiky low-volume SKUs, 2026-08-09)
DECLINE_FLOOR = 0.75        # declining items still keep 75% of the peak in the floor
RECENT_WEEKS = 8            # recent weighted-mean window
WINDOW_WEEKS = 13           # ~3-month spike-floor / volatility window
SEASONAL_CV = 0.25          # cv above this = seasonal (surge-net disabled)

SPIRIT_NIP_ML = 30.0
WINE_REGULAR_ML = 150.0
WINE_LARGE_ML = 250.0
KEG_ML = 50000.0

SPIRIT_RGS = {
    "Gin", "Vodka", "Tequila", "Whisky", "Rum", "Liqueurs",
    "Amaro / Aperitif / Fortified Wine",
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

VENUE_RECIPE_FILE = {"stow": "stowaway", "hg": "harry_gatos"}
VENUE_SCRAPE_FILE = {"stow": "_scrape_stow_20260809.json", "hg": "_scrape_hg_20260809.json"}
VENUE_BO_FILE = {"stow": "stowaway", "hg": "harry_gatos"}


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
        for nm in names:
            self.exact.setdefault(_norm(nm), nm)
            if _is_bulk_par(nm):
                base = _strip_variant(nm)
                # prefer keg, then bottle, then sized casks; keep first strong hit
                if base not in self.bulk_base or "[keg]" in nm.lower():
                    self.bulk_base[base] = nm

    def resolve_exact(self, name):
        return self.exact.get(_norm(name))

    def resolve_bulk(self, name):
        return self.bulk_base.get(_strip_variant(name))

    def resolve_ingredient(self, bo_name):
        """Recipe ingredient BO-name -> par SKU (exact first, then bulk base)."""
        return self.resolve_exact(bo_name) or self.resolve_bulk(bo_name)


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


def _spike_floor(window_peak, growth):
    if window_peak <= 0:
        return 0.0
    adj = max(growth, DECLINE_FLOOR) if (growth is not None and growth < 1.0) else 1.0
    return math.ceil(window_peak * adj * SPIKE_FLOOR_MULT * 10) / 10


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
    return rec_par


# ── consumption + assembly ──────────────────────────────────────────────────
def _build_consumption(rows, venue, idx, leaves_by_norm, matcher, id2name, weeks):
    widx = {w: i for i, w in enumerate(weeks)}
    n = len(weeks)
    pour = defaultdict(lambda: [0.0] * n)
    recipe = defaultdict(lambda: [0.0] * n)
    for r in rows:
        if r["venue"] != venue:
            continue
        wk = r["week_ending"]
        if wk not in widx:
            continue
        i = widx[wk]
        name, rg, qty = r["product_name"], r["reporting_group"], r["qty"]
        if qty == 0:
            continue
        rm = matcher(name)
        if rm is not None and rm in leaves_by_norm:
            for leaf_id, ml in leaves_by_norm[rm].items():
                if not leaf_id.startswith("lightspeed:"):
                    continue
                bo_name = id2name.get(leaf_id.split(":", 1)[1])
                if not bo_name:
                    continue
                sku = idx.resolve_ingredient(bo_name)
                if not sku:
                    continue
                recipe[sku][i] += qty * ml / bottle_ml(sku)
            continue
        low = name.lower()
        if rg in SPIRIT_RGS:
            if "[mixer]" in low:
                continue
            sku = idx.resolve_bulk(name)
            if not sku:
                continue
            bml = bottle_ml(sku)
            pml = bml if low.rstrip().endswith(" d") else SPIRIT_NIP_ML
            pour[sku][i] += qty * pml / bml
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
            elif kind == "BOTTLE":
                sku = idx.resolve_bulk(name)
                if sku:
                    pour[sku][i] += qty
            else:  # BARE — in products_weekly wine variants are collapsed to a
                # single bare line that is overwhelmingly by-the-glass. Treat as a
                # standard 150ml glass pour into the bottle par. (Assumption noted
                # for human review; a bare 'direct' par wins if one exists.)
                sku = idx.resolve_bulk(name)
                if sku:
                    pour[sku][i] += qty * WINE_REGULAR_ML / bottle_ml(sku)
                else:
                    ex = idx.resolve_exact(name)
                    if ex:
                        pour[ex][i] += qty
        elif rg in DIRECT_RGS:
            sku = idx.resolve_exact(name)
            if sku:
                pour[sku][i] += qty
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


def compute_venue(venue, data_dir="data", rows=None):
    """Compute par recommendations for a venue. Returns (recs, meta).
    recs: {sku: row-dict}; meta: {weeks, recent_weeks, coverage_gaps, ...}."""
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

    weeks = sorted({r["week_ending"] for r in rows})
    recent_weeks = weeks[-RECENT_WEEKS:]
    pour, recipe = _build_consumption(rows, venue, idx, leaves_by_norm, matcher, id2name, weeks)

    universe = set(pour) | set(recipe) | set(scrape) | set(overrides)
    n = len(weeks)
    recs = {}
    for sku in sorted(universe):
        p = pour.get(sku, [0.0] * n)
        rc = recipe.get(sku, [0.0] * n)
        series = [p[i] + rc[i] for i in range(n)]
        forecast, method, growth, cv, window_peak, buf = forecast_sku(series)
        true_wk = forecast  # variance channel absent in products_weekly
        rec_par = round(true_wk * COVERAGE_WEEKS * buf, 1) if true_wk > 0 else 0.0
        floor = _spike_floor(window_peak, growth)
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
        held = False
        if true_wk <= 0 and cur is not None and (ov is None or ov.get("type") in ("min",)):
            rec_par = float(cur)
            spike_floored = False
            held = True

        pre_ov = rec_par
        rec_par = round(apply_override(rec_par, ov), 1)

        flags = []
        if ov:
            flags.append(f"override:{ov.get('type')}")
        if spike_floored:
            flags.append("spike_floored")
        if held:
            flags.append("held_no_recent_demand")
        if method == "No recent sales" and not ov and not held:
            flags.append("no_recent_sales")
        if cur is None and rec_par > 0:
            flags.append("new_par")
        if ov and abs(pre_ov - rec_par) > 1e-9:
            flags.append("override_changed")

        recs[sku] = {
            "product": sku,
            "venue": venue,
            "reporting_group": bo_meta.get(sku, {}).get("rg", ""),
            "rec_par": rec_par,
            "current_par": cur,
            "drivers": {
                "pour_wk": round(_weighted_recent(p), 2),
                "recipe_wk": round(_weighted_recent(rc), 2),
                "variance_wk": 0.0,
            },
            "spike_floor": round(floor, 1),
            "forecast_method": method,
            "growth": round(growth, 3) if growth is not None else None,
            "buffer": buf,
            "override": ({"type": ov.get("type"), "value": ov.get("value")} if ov else None),
            "flags": flags,
        }

    gaps = coverage_gap(rows, venue, matcher, leaves_by_norm, recent_weeks)
    meta = {
        "venue": venue,
        "weeks": n,
        "week_range": [weeks[0], weeks[-1]] if weeks else [],
        "recent_weeks": recent_weeks,
        "coverage_gaps": gaps,
        "n_skus": len(recs),
    }
    return recs, meta
