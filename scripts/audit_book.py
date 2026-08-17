#!/usr/bin/env python3
"""Adversarial sweep of the whole cost + recipe book.

    python3 scripts/audit_book.py            # every finding, grouped
    python3 scripts/audit_book.py --severe   # only the ones that misstate money

WHY THIS EXISTS
---------------
Every defect this project has shipped looked like a valid number at the time. A
$0 bottle reads as 100% GP. A missed dict key reads as "0 sold". A per-ml rate in
a per-pack column reads as a cheap ingredient. None of them throw. The only way
to catch that class is to state, out loud, what a SANE book looks like and list
everything that isn't.

Findings are graded:
  SEVERE  — the number shown to a human is wrong and flatters or alarms
  WARN    — probably wrong, needs an eye
  INFO    — known/accepted, listed so it stays visible

Exit code is 1 if any SEVERE remains, so CI can hold the line.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_EXEMPT_PATS = None


_SERVE_PORTIONS = None


def _serve_portion(name: str):
    """(qty, unit) for a batch-shaped recipe that sells as a portion, or None.

    See data/serve_portions.yaml. A recipe holding a tray of ingredients against
    a one-bowl menu price is a real finding -- but only until somebody says how
    big the bowl is, and then it is arithmetic. Before this the rule had nowhere
    to look for that answer, so the finding sat at SEVERE describing a fact
    nobody disputed and could never be closed.
    """
    global _SERVE_PORTIONS
    if _SERVE_PORTIONS is None:
        _SERVE_PORTIONS = {}
        try:
            import yaml as _yaml
            f = ROOT / "data" / "serve_portions.yaml"
            doc = _yaml.safe_load(f.read_text(encoding="utf-8-sig")) if f.exists() else None
            for e in ((doc or {}).get("portions") or []):
                try:
                    _SERVE_PORTIONS[e["product"]] = (float(e["portion_qty"]),
                                                     str(e["portion_unit"]))
                except (KeyError, TypeError, ValueError):
                    pass
        except Exception:                                    # noqa: BLE001
            pass
    return _SERVE_PORTIONS.get(name)


def _exempt(name: str) -> bool:
    """Is this product on the flags spec's exempt list?

    ONE LIST, READ TWICE. data/cost_book_flags.yaml already carried the
    exemptions -- open-price keys, fees, and now two discontinued products --
    and build_cost_book_flags honoured them while this audit did not. So a
    product could be a documented, accepted non-issue on the Flags tab and a
    permanent SEVERE here at the same time, which is how a ratchet pinned at 7
    ends up holding two findings nobody intends to fix.

    A missing cost is only a defect if we could have costed it. Something we no
    longer sell is a menu problem, not a costing one.
    """
    global _EXEMPT_PATS
    if _EXEMPT_PATS is None:
        _EXEMPT_PATS = []
        try:
            import yaml as _yaml
            f = ROOT / "data" / "cost_book_flags.yaml"
            spec = _yaml.safe_load(f.read_text(encoding="utf-8-sig")) if f.exists() else None
            for e in ((spec or {}).get("exempt") or []):
                if e.get("match"):
                    _EXEMPT_PATS.append(re.compile(e["match"], re.I))
        except Exception:                                    # noqa: BLE001
            pass
    return any(p.search((name or "").strip()) for p in _EXEMPT_PATS)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
# Identity is core's, not rebuilt by hand here — see the "never reached the cost
# book" rule, which spelled a purchasable id out locally and so read five
# well-priced Fresh Fruit Team lines as unpriced the moment a bled unit word
# stopped being part of the key.
from core.domain import canonical_purchasable, purchasable_id  # noqa: E402
# The size-variant collapse is DEFINED there; importing it keeps one whitelist
# rather than a second, looser copy drifting in the auditor. See sold().
from build_products_weekly import normalize_product  # noqa: E402
from cogs_blend import _load_book_costs, _stripped_key, book_cost  # noqa: E402

COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"
INGREDIENTS = ROOT / "data" / "ingredients.json"
COSTS = ROOT / "data" / "costs.csv"
COGS = ROOT / "data" / "cogs_list.csv"
PRODUCTS_WEEKLY = ROOT / "data" / "products_weekly.csv"

def _nrm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _closest_recipes(name, recipes, limit=3):
    """Costed dishes that share a distinctive word with this POS product.

    Deliberately dumb and deliberately not a decision: it proposes, a human
    disposes. Shared-word overlap finds "Chicken Katsu Curry" for "Katsu Curry"
    and finds NOTHING for "Outback Prawn Toast" vs "Devon's Prawn Toast" — which
    is the honest result, because those names have nothing in common and only
    someone who knows the menu could ever have paired them. Silence here means
    "I cannot help", not "it does not exist".
    """
    stop = {"the", "and", "of", "with", "side", "extra", "add", "swap", "for",
            "a", "d", "kids", "large", "regular"}
    def words(x):
        return set(re.findall(r"[a-z]+", re.sub(r"\[.*?\]", "", x or "").lower())) - stop
    t = words(name)
    if not t:
        return ""
    hits = []
    for n, r in recipes.items():
        if r.get("is_prep") or money(r.get("our_cost")) <= 0:
            continue
        shared = len(t & words(n))
        if shared:
            hits.append((shared, -abs(len(words(n)) - len(t)), n, money(r.get("our_cost"))))
    if not hits:
        return ""
    hits.sort(reverse=True)
    return "; ".join(f"{n} ${c:.2f}" for _, _, n, c in hits[:limit])


def _nrm_bottle(s: str) -> str:
    """A bottle's name with Back Office's bracketed suffix removed.

    Back Office files the same bottle as "Hendrick's Gin [Bottle]" and ILG's
    price book calls it "Hendricks Gin". _nrm_name keeps the bracket — "bottle"
    and "700ml" survive as letters — so the two never meet. Dropping the bracket
    (and the apostrophe, which _nrm_name already handles) takes the confident
    name+size pairs from 2 to ~30. The SIZE is never taken from the name; it is
    matched separately from Back Office's own DefaultSize column, so this loosens
    only the half that is safe to loosen."""
    return _nrm_name(re.sub(r"\[.*?\]", "", s or ""))

# What a sane bar/kitchen ingredient costs, per BASE unit, incl GST. Anything past
# these is a pack/unit confusion, not a real price. Calibrated on the real book:
# the dearest legitimate per-g item is saffron-class spice; the dearest per-ml is
# a top-shelf spirit at roughly $0.35/ml.
CEIL = {"g": 0.20, "ml": 0.60}
# Verified against the invoice and genuinely this dear, so the ceiling would
# only ever cry wolf. Select Fresh 3064370: "KUTJERA BUSH TOMATO WHOLE100GM"
# $48.00 for 100g — a premium native spice used a pinch at a time.
DEAR_BUT_REAL = {"select-fresh:BUSHTOMG"}
# Products whose high GP Zak has confirmed is REAL, with what he said. A rule
# that keeps reporting a checked answer is how a list stops being read.
VERIFIED_HIGH_GP = {
    # Zak, 2026-08-06: "grapefruit soda only uses sherbet". 30 ml of house
    # sherbert in soda water; there is no second ingredient to be missing.
    "Stow Soda - Grapefruit": "sherbet only — confirmed by Zak 2026-08-06",
    # Zak, 2026-08-06: "soda + lime's cost is only 1/12 of a lime unit." A twelfth
    # of a $0.50 lime is 4.2c, the soda is postmix off the gun, and there is no
    # third thing in the glass. 98.5% GP on a $3.00 drink is the whole truth of it.
    "Soda & Lime Glass": "a twelfth of a lime, soda off the gun — confirmed by Zak 2026-08-06",
}

# Delivery twins that legitimately cost more than the dish they copy, because
# they are not in fact the same serve. Keyed on the DINE-IN name (the rule's
# `name`), value is the reason, in Zak's words where he gave them.
#
# The twin rule assumes "X D" is X in a box. Where that assumption is wrong the
# gap is the answer, not the question, and re-reporting a checked answer every
# run is how a work queue turns into wallpaper.
VERIFIED_TWIN_GAP = {
    # Zak, 2026-08-06: "bundaberg ginger beer D is a WHOLE BOTTLE vs dine-in's
    # smaller ml serve." Dine-in pours 200 ml of a 750 ml bottle at $5.00
    # ($0.86); delivery sends the whole 750 ml bottle at $8.00 ($3.22). Both
    # recipes are right and both GPs are right — 3.75x the cost for 1.6x the
    # price is a real margin decision, not a costing defect.
    "Bundaberg Ginger Beer": "delivery sends the whole 750 ml bottle, dine-in "
                             "pours 200 ml — confirmed by Zak 2026-08-06",
}
FLOOR = 0.000_01          # a real ingredient is never free
ABSURD_SERVE = 120.0      # no single non-prep menu item costs more than this
GP_FLATTER = 95.0
# Below this a POS "price" is a placeholder or a staff/comp SKU, not a menu
# price — the cost engine already refuses to compute GP under it, and an
# auditor that ignores the threshold just reports the engine working.
MENU_PRICED = 3.0         # a 95%+ GP on a food/drink item means a missing cost
# How far under ILG's published book price a bottle may sit before it is a
# finding rather than a discount. Measured, not chosen: over the products where
# a Back Office cost and a price-book line agree on name AND size, the ratio
# runs 0.85 to 1.12 with a median of 1.02. Real buying moves a bottle by 15%;
# a pack misread or a Produce-derived seed moves it by 40% or more.
PRICEBOOK_FLOOR = 0.80
# TWO IDENTITIES FOR ONE STOCK ITEM. Back Office files the same bottle, keg or
# mixer once per venue, so nearly every liquor line exists twice — and nothing
# has ever compared the two copies to each other. Where they disagree, one of
# them is what a cocktail is costed on.
#
# Measured on the seeded book: of the 17 name-matched pairs, 13 sit between
# 1.10x and 1.25x and that band is real — Stowaway and Harry Gatos buy on
# separate ILG accounts and the two exports were taken on different days. 1.35x
# is outside anything buying explains and inside every failure it catches:
#
#     Angostura Bitters   HG $1.34/200 ml vs stow $0.10/ml         14.93x
#     Plantation 3 Stars  HG $60.83/4500 ml vs stow $60.83/700 ml   6.43x
#
# Both cheap copies read LOW, which is the flattering direction, and the
# Angostura one feeds four live Harry Gatos cocktails.
TWIN_IDENTITY_X = 1.35

# Above this many grams or millilitres of ingredients, a "serve" is a batch.
#
# Not a guess: the heaviest real menu item in the 829-recipe book is a Large
# Super House Special at 1,006 g, and a one-litre beer jug is 1,000 ml. The next
# thing up is Potato Salad at 1,780 g — one kilo of potato, half a kilo of Kewpie
# — against a $7.00 side. A side dish is not 1.8 kg. Produce holds the batch and
# has no portion size on it, which is a different defect from "this dish loses
# money", and reporting it as the latter sends someone to fix the price.
SERVE_MAX_BASE_UNITS = 1200.0


_SIZE_TOKEN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|g|gm|kg)\b", re.I)
# Back Office tags the container two ways and the tag is not the product:
# "Angostura Bitters [200ml]" (Stowaway) and "Angostura Bitters - Bottle 200ml"
# (Harry Gatos) are one 200 ml bottle. _nrm_bottle already drops the bracketed
# form; the dashed form is the same tag with different punctuation.
_CONTAINER_TAIL = re.compile(
    r"\s*[-–]\s*(bottle|keg|can|house|tap|glass|jug|schooner|pint|pot)\s*$", re.I)


def stock_item(name: str) -> tuple[str, str]:
    """A Back Office product name -> (the stock item it names, its size token).

    The size comes OUT of the name and is KEPT, not thrown away. Back Office
    files one product as "Angostura Bitters [200ml]" and the other as "Angostura
    Bitters - Bottle 200ml", which is the same 200 ml bottle written twice — but
    it also files "Salt Cooking 10kg Olssons" and "Salt Cooking 1kg Olssons",
    which are two real pack sizes whose $/g differ 7x for the ordinary reason
    that bulk is cheaper. Dropping the size silently merged those and reported
    buying salt in bulk as a defect. So a group is only compared when the sizes
    agree or are not stated — a STATED difference is the answer, not the finding.
    """
    m = _SIZE_TOKEN.search(name or "")
    size = f"{float(m.group(1)):g}{m.group(2).lower()}" if m else ""
    bare = _SIZE_TOKEN.sub(" ", name or "").strip()
    prev = None
    while bare != prev:
        prev, bare = bare, _CONTAINER_TAIL.sub("", bare).strip()
    return _nrm_bottle(bare), size


def bo_product_names() -> dict[str, str]:
    """ProductID -> the name Back Office gives it, both venues."""
    out: dict[str, str] = {}
    for p in (ROOT / "data" / "bo_exports" / "stowaway_products.csv",
              ROOT / "data" / "bo_exports" / "harry_gatos_products.csv"):
        if p.exists():
            for r in csv.DictReader(p.open(encoding="utf-8-sig")):
                out.setdefault(r["ProductID"], r["ProductName"])
    return out


def cost_book_latest() -> dict[str, tuple[str, float, str]]:
    """lightspeed:<ProductID> -> (observed_on, cost_per_unit, unit), latest only."""
    out: dict[str, tuple[str, float, str]] = {}
    if not COSTS.exists():
        return out
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        iid = r["ingredient"]
        if not iid.startswith("lightspeed:"):
            continue
        d, c = r["observed_on"], money(r.get("cost_per_unit"))
        if c <= 0:
            continue
        if iid not in out or d >= out[iid][0]:
            out[iid] = (d, c, r.get("unit") or "")
    return out


def twin_identity_conflicts(latest: dict, names: dict, band: float = None) -> list:
    """Stock items the cost book holds twice at materially different prices.

    -> [(ratio, [(cost, id, name, date, unit), ...]), ...], dearest gap first.

    Grouped by (stock item, base unit): a per-ml copy is never compared with a
    per-each one, because that is a unit question and other rules own it. Only
    Lightspeed identities take part — both sides are then Back Office's OWN
    naming of its own product, which is why a name join is safe here and was not
    for the ILG price book (see that rule: matching two companies' names for one
    bottle found almost nothing).
    """
    band = TWIN_IDENTITY_X if band is None else band
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for iid, (d, c, u) in latest.items():
        nm = names.get(iid.split(":", 1)[1])
        item, size = stock_item(nm or "")
        if not nm or len(item) < 4:
            continue                      # no name to join on: say nothing
        groups[(item, u)].append((c, iid, nm, d, u, size))
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        if len({m[5] for m in members if m[5]}) > 1:
            continue                      # two STATED pack sizes: not one stock item
        members.sort()
        lo, hi = members[0][0], members[-1][0]
        if lo <= 0 or hi / lo < band:
            continue
        out.append((hi / lo, [m[:5] for m in members]))
    return sorted(out, key=lambda x: -x[0])


def _pack_count_hint(observed, median):
    """"  (a case of 12 read as one unit)" when the ratio is a whole pack count.

    A misread pack is not a random price move — it is the line total divided by
    the wrong number of units, so the ratio lands on an integer. The camembert
    sat at exactly 12.0x its own median and the black beans at 6.0x, which names
    the defect instead of just flagging it: a case of 12 and a case of 6, each
    priced as a single unit. A real price rise does not arrive at 12.00x.

    Silent unless the ratio is within 2% of a whole number between 2 and 24 —
    below 2 there is nothing to say, and past 24 the "case" reading stops being
    the obvious explanation."""
    if median <= 0 or observed <= 0:
        return ""
    hi, lo = max(observed, median), min(observed, median)
    ratio = hi / lo
    n = round(ratio)
    if not (2 <= n <= 24) or abs(ratio - n) > 0.02 * n:
        return ""
    return (f"   (looks like a case of {n} priced as one unit)" if observed > median
            else f"   (looks like one unit priced as a case of {n})")


def money(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _money_cell(x):
    """A Lightspeed Insights currency cell: "$1,234.50", "($12.00)", "".

    money() cannot read these — it returns 0.0 for anything with a $ or a comma,
    which would report every row as costing nothing."""
    s = str(x or "").strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").strip()
    if not s:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


# A recipe name carries the venue; a POS product name does not.
_VENUE_TAG = re.compile(r"\s*\[(hg|harrys|harry gatos)\]\s*$", re.I)


def load_sales(weeks=13):
    """POS product name (normalised) -> (units, revenue ex GST) over `weeks`.

    WHY THE AUDIT NEEDS THIS
    ------------------------
    Without it every finding weighs the same, and the list is sorted by name. So
    "Corpse Reviver No. 2" — one sold, ever, in June 2025 — sat above "Kids Spag
    Bol", which is 303 serves and $3,618 and has no recipe at all. The audit was
    telling the truth and burying it.

    A defect on a dormant SKU is still a defect, but it misstates nothing: there
    is no revenue for it to misstate. It stays listed, at WARN, saying so.

    Only qty and sales_ex_gst are read. products_weekly's `cost` column is
    incomplete (the Looker backfill has null costs) and is not used here — an
    earlier pass built a "$54k missing from the P&L" claim on it and was wrong.
    """
    if not PRODUCTS_WEEKLY.exists():
        return {}
    pw = list(csv.DictReader(PRODUCTS_WEEKLY.open(encoding="utf-8-sig")))
    wks = sorted({r["week_ending"] for r in pw})
    if not wks:
        return {}
    cut = wks[max(0, len(wks) - weeks)]
    out: dict = {}
    for r in pw:
        if r["week_ending"] < cut:
            continue
        a = out.setdefault(_nrm_name(r["product_name"]), [0.0, 0.0])
        a[0] += money(r.get("qty"))
        a[1] += money(r.get("sales_ex_gst"))
    return {k: tuple(v) for k, v in out.items()}


def sold(sales, name):
    """(units, revenue, whole_product) for a recipe, or None if nothing matches.

    "No sales record" and "sold nothing" are different claims and the audit must
    not conflate them: many of the 829 recipes are preps and delivery twins whose
    names were never POS product names.

    Three lookups, in order: the name as written; the name with its venue tag
    removed; and the name with its SIZE suffix removed. That last one is not a
    fuzzy match invented here — products_weekly.py deliberately collapses
    "- Pint"/"- Schooner"/"- Regular"/"- Large" so a beer's pints and schooners
    report as one drink, off a whitelist that never touches a flavour
    ("- Passionfruit") or a delivery zone. Reusing that exact function is the
    only safe way to cross the gap, and skipping it left every tap beer and
    wine-by-the-glass reading "no POS sales record" — $95,000 of Stowaway
    revenue with no weight on it at all.

    `whole_product` is True when the match came from that collapse, because then
    the revenue belongs to ALL the sizes, not to the one variant the finding is
    about. The caller says so rather than implying a precision it does not have.
    """
    hit = sales.get(_nrm_name(name))
    if hit is not None:
        return hit[0], hit[1], False
    bare = _VENUE_TAG.sub("", name)
    hit = sales.get(_nrm_name(bare))
    if hit is not None:
        return hit[0], hit[1], False
    collapsed = normalize_product(bare)
    if collapsed != bare:
        hit = sales.get(_nrm_name(collapsed))
        if hit is not None:
            return hit[0], hit[1], True
    return None


def weigh(sales, product, sev, detail):
    """-> (revenue_at_stake, severity, detail) for one finding.

    A defect on a dormant SKU is still a defect, but it misstates nothing: there
    is no revenue for it to misstate. It drops to WARN and says why, so the
    SEVERE list is the things costing money this quarter.

    Revenue is floored at zero. A handful of POS products are discount and refund
    SKUs whose 13-week revenue is negative; that is real, but it must not sort a
    finding BELOW one worth nothing."""
    s = sold(sales, product)
    if s is None:
        return 0.0, sev, f"{detail}   [no POS sales record]"
    qty, rev, whole = s
    if qty <= 0:
        return 0.0, "WARN", f"{detail}   [dormant — 0 sold in 13wk]"
    scope = "all sizes" if whole else "13wk"
    return max(0.0, rev), sev, f"{detail}   [{qty:,.0f} sold, ${rev:,.0f} {scope}]"


def costed_keys(recipes) -> set:
    """Every name-form under which the book prices something, ready to match a
    POS product name against.

    WHAT COUNTS AS COVERED MUST BE WHAT THE P&L ACTUALLY MATCHES.

    An auditor with its own, stricter idea of a match reports work that is
    already done: cogs_blend resolves "Bombay Dry [House]" to "Bombay Dry Gin
    [House]", so listing it under "sells well, has no costed recipe" would send
    someone to write a recipe that exists. _stripped_key is imported from there
    rather than reimplemented, so the two can only ever agree.

    On top of it, products_weekly collapses size variants, so the recipe side is
    collapsed the same way — otherwise every tap beer reads as uncovered when in
    fact all its sizes are costed.

    A SIZE/COUNT SUFFIX AND A PLURAL ARE NOT A DIFFERENT DISH.

    Zak, 2026-08-06: "duck spring rolls are done too!!!!!" They were. The book
    holds "Duck Spring Roll" at $3.57 and "Duck Spring Rolls [2pc]" at $3.20;
    the POS sells "Duck Spring Rolls". Exact-name matching met none of them, so
    a costed dish was reported as uncosted — and it was the third time in one
    session that this auditor sent someone to write a recipe that already
    existed.

    That is the worst failure mode a work queue has. A missed defect costs you
    the defect; a FALSE defect costs you the reader, and after two or three the
    whole list stops being believed.

    So: strip the bracketed suffix as well as the venue tag, and match singular
    against plural. Both are lossy in the safe direction — they can only ever
    mark something as covered that is covered by a near-identical name, and the
    cost figure itself is untouched.

    Module-level, and it must stay that way: scripts/build_cost_book_flags.py
    publishes the same gaps to the recipe book's flags panel, and a panel that
    disagreed with the audit about what is costed would be worse than no panel.
    One definition, two readers.
    """
    costed = set()
    for n, r in recipes.items():
        if r.get("is_prep") or money(r.get("our_cost")) <= 0:
            continue
        bare = _VENUE_TAG.sub("", n)
        unsized = re.sub(r"\s*\[.*?\]\s*$", "", bare).strip()
        for form in (n, bare, unsized, normalize_product(bare), normalize_product(unsized)):
            k = _nrm_name(form)
            if k:
                costed.add(k)
                costed.add(k.rstrip("s"))       # roll / rolls, wing / wings
            sk = _stripped_key(form)
            if sk:
                costed.add(sk)
    return costed


def coverage(recipes, weeks=13):
    """-> (revenue_by_venue, covered_by_venue, {(venue, product, group): revenue})

    The last `weeks` of products_weekly, split into what the cost book reaches
    and what falls through to Lightspeed. The third value is the work queue.
    """
    costed = costed_keys(recipes)
    tot = defaultdict(float)
    cov = defaultdict(float)
    gaps = defaultdict(float)
    pw2 = list(csv.DictReader(PRODUCTS_WEEKLY.open(encoding="utf-8-sig")))
    wks2 = sorted({r["week_ending"] for r in pw2})
    cut2 = wks2[max(0, len(wks2) - weeks)] if wks2 else ""
    for r in pw2:
        if r["week_ending"] < cut2:
            continue
        rev = money(r.get("sales_ex_gst"))
        if rev <= 0:
            continue
        tot[r["venue"]] += rev
        if (_nrm_name(r["product_name"]) in costed
                or (_stripped_key(r["product_name"]) or "\x00") in costed):
            cov[r["venue"]] += rev
        else:
            gaps[(r["venue"], r["product_name"], r.get("reporting_group") or "")] += rev
    return tot, cov, gaps


def audit():
    recipes = json.loads(COSTED.read_text(encoding="utf-8-sig"))["recipes"]
    # data/ingredients.json is DELIBERATELY not committed — it is a 90-day window
    # off date.today(), so a committed copy would rot on whatever Tuesday an
    # invoice crossed the line. On a clean checkout it does not exist until
    # build_ingredients has run, and reading it blind turned "the audit has
    # nothing to say about ingredients yet" into a FileNotFoundError traceback
    # that took CI down. Say so and audit everything else.
    ings: list = []
    if INGREDIENTS.exists():
        ing_raw = json.loads(INGREDIENTS.read_text(encoding="utf-8-sig"))
        ings = ing_raw["ingredients"] if isinstance(ing_raw, dict) else ing_raw
    by_id = {i["id"]: i for i in ings}

    sales = load_sales()

    F = defaultdict(list)   # (severity, rule) -> [(revenue_at_stake, detail)]

    def add(sev, rule, detail, product=None):
        """Record a finding, weighted by what the product actually sells.

        `product` names the POS item the finding is about. Given one, the detail
        gains a 13-week sales tail and the rule sorts by revenue, so the biggest
        real number is at the top instead of whatever starts with 'A'. A finding
        on something with no sales in 13 weeks is demoted to WARN — the number is
        still wrong, but there is no money for it to misstate, and leaving it at
        SEVERE crowds out the ones that cost something today."""
        rev = 0.0
        if product is not None:
            rev, sev, detail = weigh(sales, product, sev, detail)
        F[(sev, rule)].append((rev, detail))

    if not ings:
        add("INFO", "ingredients.json not built — ingredient-level rules skipped",
            "run modules/recipes/pipeline/build_ingredients.py first")

    # ---------- RECIPES ----------
    def _input_mass(rec):
        """Total grams + millilitres of ingredients. Countables are not summed —
        "2 ea" of anything says nothing about weight."""
        m = 0.0
        for l in (rec.get("ingredients") or []):
            if (l.get("unit") or "").lower() in ("g", "ml"):
                m += money(l.get("qty"))
        return m

    # A recipe that is physically a batch cannot also be judged as a serve: its
    # cost is a tray's cost and its POS price is one portion's. Identify them
    # first so the GP rules below can defer rather than shout the wrong thing.
    batch_shaped = {n for n, rec in recipes.items()
                    if not rec.get("is_prep")
                    and money(rec.get("sell_incl")) >= MENU_PRICED
                    and _input_mass(rec) > SERVE_MAX_BASE_UNITS}
    for n in sorted(batch_shaped):
        rec = recipes[n]
        mass = _input_mass(rec)
        # A DECLARED PORTION CLOSES IT. The shape is still a batch, but once the
        # kitchen has said how much goes in the bowl, the tray cost divides into
        # a serve cost and there is nothing left to ask. Reported as INFO so the
        # division stays visible rather than disappearing.
        if (portion := _serve_portion(n)) and mass > 0:
            per = money(rec.get("our_cost")) * (portion[0] / mass)
            add("INFO", "batch-shaped, but a serve portion is declared",
                f"{n[:34]:36} {portion[0]:,.0f} {portion[1]} of {mass:,.0f} "
                f"= ${per:.2f} a serve against a "
                f"${money(rec.get('sell_incl')):.2f} menu price", product=n)
            continue
        add("SEVERE", "recipe is a BATCH, not a serve — it has no portion size",
            f"{n[:34]:36} {mass:,.0f} g/ml of inputs costing ${money(rec.get('our_cost')):,.2f}"
            f" against a ${money(rec.get('sell_incl')):.2f} menu price"
            f"  (${100 * money(rec.get('our_cost')) / mass:.2f}/100g)", product=n)

    for name, r in sorted(recipes.items()):
        prep = bool(r.get("is_prep"))
        sell, cost = money(r.get("sell_incl")), money(r.get("our_cost"))
        lines = r.get("ingredients") or []
        gp = r.get("gp_pct")
        if name in batch_shaped:
            # already reported above, as what it is
            continue

        if sell >= MENU_PRICED and cost == 0 and not prep and not _exempt(name):
            add("SEVERE", "sells for money but costs $0 (reads as 100% GP)",
                f"${sell:>7.2f}  {name}", product=name)
        if not lines and not prep:
            add("WARN", "no ingredient lines at all", name)
        # A sold-as-bought bottle is exempt: ABSURD_SERVE exists to catch a
        # unit/pack confusion (the $11,400 serve), and a pass-through cannot have
        # one — its cost is one unit of the thing, read off the product being
        # sold. A bottle of Dom Pérignon costing $332 against a $440 menu price
        # is not a costing defect, it is bottle service. If the price were wrong
        # the GP rules would say so on their own.
        if cost > ABSURD_SERVE and not prep and not r.get("passthrough"):
            add("SEVERE", f"single serve costs more than ${ABSURD_SERVE:.0f}",
                f"${cost:>8.2f}  {name}", product=name)
        if sell >= MENU_PRICED and cost > sell and not prep:
            add("SEVERE", "costs more than it sells for",
                f"cost ${cost:>7.2f} vs sell ${sell:>7.2f}  {name}", product=name)
        if gp is not None and gp >= GP_FLATTER and not prep:
            if name in VERIFIED_HIGH_GP:
                add("INFO", "high GP, confirmed real",
                    f"{gp:>5.1f}%  {name}  ({VERIFIED_HIGH_GP[name]})")
            else:
                add("WARN", f"GP >= {GP_FLATTER:.0f}% — a cost is probably missing",
                    f"{gp:>5.1f}%  ${cost:>6.2f} -> ${sell:>7.2f}  {name}", product=name)
        if gp is not None and gp < 0 and not prep:
            add("SEVERE", "negative GP", f"{gp:>7.1f}%  {name}", product=name)

        for ln in lines:
            if not ln.get("kind"):
                add("WARN", "ingredient line resolves to nothing",
                    f"{name} -> {ln.get('name') or ln.get('id')}")
            elif money(ln.get("eff_cost")) == 0 and money(ln.get("qty")) > 0:
                add("WARN", "line contributes $0 despite a real quantity",
                    f"{name} -> {ln.get('name') or ln.get('product') or ln.get('id')}"
                    f" ({ln.get('qty')}{ln.get('unit') or ''})")

    # ---------- PRICED BELOW COST (any price, not just menu-priced) ----------
    # The "costs more than it sells for" rule above only looks at items over
    # $3, because a $1 POS price is usually a placeholder. But a REAL recipe
    # behind a $2 price is a different animal: Pepperoni [Dine-in] carries a
    # full pizza (dough, sauce, mozzarella, pepperoni = $2.11) against a $2.00
    # price, and every dine-in sibling sells for $15. That is a POS price
    # error losing money on every order, and it hid under the threshold.
    for name, r in sorted(recipes.items()):
        if r.get("is_prep"):
            continue
        sell, cost = money(r.get("sell_incl")), money(r.get("our_cost"))
        lines = r.get("ingredients") or []
        if 0 < sell < MENU_PRICED and cost > sell and len(lines) >= 2:
            add("SEVERE", "real recipe priced below cost (POS price looks wrong)",
                f"sell ${sell:>6.2f} vs cost ${cost:>6.2f}  ({len(lines)} lines)  {name}",
                product=name)

    # ---------- A COMBO THAT CONTAINS NOTHING EXTRA ----------
    # "Large X Wings Deal" is a pizza AND wings, so it must cost more than the
    # plain "Large X". All 22 of them were byte-identical to the base pizza —
    # the wings were never added — so each reported ~88% GP on a $30 item.
    # Generalised: any recipe whose name extends another recipe's name must not
    # have an identical ingredient list to it.
    for name, r in sorted(recipes.items()):
        if r.get("is_prep"):
            continue
        for suffix in (" Wings Deal", " Deal", " Combo", " + Wings", " & Wings"):
            if not name.endswith(suffix):
                continue
            base = name[: -len(suffix)].strip()
            b = recipes.get(base)
            if not b:
                continue
            def _sig(rec):
                # compare quantities NUMERICALLY — the feed writes "259" in one
                # recipe and "259.0" in the other, which is the same 259g.
                return sorted((str(l.get("ref") or l.get("name")), money(l.get("qty")))
                              for l in (rec.get("ingredients") or []))
            if _sig(r) == _sig(b) and _sig(r):
                add("SEVERE", "combo costs the same as its base — the extra item is missing",
                    f"${money(r.get('our_cost')):>6.2f}  {name}  ==  {base}")
            break

    # ---------- SIZE REGRESSION: a LARGE carrying less than its REGULAR ----------
    # A Large pizza cannot contain less of an ingredient than the Regular. Found
    # on ham (55g vs 85g), spanish onion (20g vs 33g on 8 pizzas), chicken,
    # mozzarella, pesto. Also catches a topping present in one size and absent
    # from the other, which is the "Hawaiian had no ham" class.
    # PACKAGING IS SUPPOSED TO DIFFER BY SIZE. A Regular pizza goes in the 11"
    # box and a Large in the 13" — convert_lightspeed_recipes assigns them that
    # way on purpose. Comparing the two sizes' line lists then reports every
    # pizza twice, once for each box, and 31 of the 40 findings this pair of
    # rules produced were exactly that. A rule that is 78% noise teaches whoever
    # reads it to skim, which costs more than the rule earns.
    _PACKAGING = re.compile(r"pizza box|box insert", re.I)

    # Zak's WEIGHED regular-pizza grams. The audit reads them for one reason: to
    # say WHICH of the two numbers is a measurement.
    #
    # 16 of the 19 "large carries less than the regular" findings are a regular
    # that Zak weighed against a large that Produce derived — Spanish onion is
    # 33 g on a weighed regular and 20 g on a guessed large, seven times over.
    # The rule was reporting those as if the recipe were inconsistent. It is not:
    # the large has simply never been weighed, and saying so turns the finding
    # into the one action that clears it.
    #
    # The match rule is re-stated here rather than imported, deliberately: the
    # audit must not reach into the converter's internals, and if the two ever
    # drift this one falls back to "derived", which under-claims. That is the
    # safe direction for an auditor.
    _WEIGHED: list = []
    try:
        import yaml
        _wp = ROOT / "data" / "pizza_regular_grams.yaml"
        if _wp.exists():
            _WEIGHED = [s for s in (yaml.safe_load(_wp.read_text(encoding="utf-8-sig")) or []) if s.get("match")]
    except Exception:                                          # noqa: BLE001
        _WEIGHED = []

    def _is_weighed(stem, ingredient_name, qty):
        """Did this REGULAR quantity come from the weighed sheet?"""
        low = (stem or "").lower()
        for s in _WEIGHED:                     # first match wins; `when` before default
            if not re.search(s["match"], ingredient_name or "", re.I):
                continue
            if s.get("when") and s["when"].lower() not in low:
                continue
            try:
                return abs(float(s["grams"]) - float(qty)) < 0.51
            except (TypeError, ValueError):
                return False
        return False

    def _lines_by_ref(rec):
        out = {}
        for l in (rec.get("ingredients") or []):
            nm = str(l.get("name") or "")
            if _PACKAGING.search(nm):
                continue
            k = str(l.get("ref") or nm)
            try:
                out[k] = (float(l.get("qty") or 0), nm or k)
            except (TypeError, ValueError):
                pass
        return out
    for name, r in sorted(recipes.items()):
        if not name.startswith("Large ") or r.get("is_prep"):
            continue
        stem = name[len("Large "):]
        reg = recipes.get(f"Regular {stem}")
        if not reg:
            continue
        L, R = _lines_by_ref(r), _lines_by_ref(reg)
        for ref, (rq, rname) in R.items():
            if ref not in L:
                add("WARN", "ingredient in the REGULAR but missing from the LARGE",
                    f"{stem}: {rname[:30]} ({rq:g} in regular, absent in large)")
            elif rq > 0 and L[ref][0] < rq:
                why = ("  <- regular is WEIGHED, large is still Produce's derived figure"
                       if _is_weighed(stem, rname, rq) else "")
                add("WARN", "LARGE carries LESS of an ingredient than the REGULAR",
                    f"{stem}: {rname[:30]:<32} large {L[ref][0]:g} < regular {rq:g}{why}")
        for ref, (lq, lname) in L.items():
            if ref not in R:
                add("INFO", "ingredient in the LARGE but missing from the REGULAR",
                    f"{stem}: {lname[:30]} ({lq:g} in large, absent in regular)")

    # ---------- A BATCH THAT CANNOT FIT IN ITS OWN CONTAINER ----------
    # Compares a batch's inputs against its DECLARED yield (data/prep_yields.yaml).
    #
    # It does NOT read the bracket in the name. Zak, 2026-08-06: "i was naming
    # sub-recipes such as jalapeno tequila [1L] to describe the unit of measurement
    # that sub recipe was defined in" — [1L] means "this one is measured in
    # litres", not "this batch makes one litre". An earlier version of this rule
    # read it as a yield and so flagged every correctly-built infusion in the book:
    # Jalapeño Tequila (7L), Coconut-washed Rooster (4.2L) and Cooked Beef Brisket
    # were all false positives against a convention nobody had written down.
    #
    # A declared yield is a stated fact with its basis recorded. A name is a label.
    # Only the first can carry this check.
    #
    # Deliberately blunt: 3x headroom so a genuine reduction (stock, caramel) never
    # trips it.
    _YIELD_IN_NAME = re.compile(r"\[\s*([\d.]+)\s*(ml|l|kg|g)\s*\]", re.I)
    _TO_BASE = {"ml": 1.0, "l": 1000.0, "g": 1.0, "kg": 1000.0}
    # A DECLARED yield in data/prep_yields.yaml beats the name every time: the name
    # is a label someone typed, the yaml is a stated basis with working shown.
    # Jalapeño Tequila is named "[1L]" but makes 7L (10 bottles of tequila), which
    # is exactly why reading the name costed it at $551/L.
    _declared = {}
    try:
        import yaml as _yaml
        _py = ROOT / "data" / "prep_yields.yaml"
        if _py.exists():
            for _k, _v in (_yaml.safe_load(_py.read_text(encoding="utf-8-sig")) or {}).items():
                try:
                    _declared[_k] = (float(_v["yield_qty"]), str(_v["yield_unit"]).lower())
                except (KeyError, TypeError, ValueError):
                    pass
    except Exception:                                        # noqa: BLE001
        pass
    for name, r in sorted(recipes.items()):
        if name not in _declared:
            continue                      # no stated yield -> nothing to check against
        declared, _unit = _declared[name]
        if declared <= 0:
            continue
        # A YIELD IN A COUNT IS NOT COMPARABLE TO A MASS. "BBQ Wings makes 1
        # serve" against 500 g of wings is not a 500x defect, it is a serve with
        # half a kilo of wings in it. The unit fell through to the g/kg branch
        # and this fired the moment those serve-yields moved out of a script and
        # into prep_yields.yaml where they belong.
        if _unit not in ("ml", "l", "g", "kg"):
            continue
        # sum only same-dimension inputs (ml/l with ml/l, g/kg with g/kg)
        want = {"ml", "l"} if _unit in ("ml", "l") else {"g", "kg"}
        total = 0.0
        for ln in (r.get("ingredients") or []):
            u = (ln.get("unit") or "").lower()
            if u in want:
                total += money(ln.get("qty")) * _TO_BASE.get(u, 1.0)
        if total > 3 * declared:
            add("SEVERE", "batch uses far more input than the yield in its own name",
                f"{name}: {total:,.0f} vs {declared:,.0f} declared "
                f"({total/declared:.1f}x)  costs ${money(r.get('our_cost')):,.2f}")

    # collisions: two recipes that normalise to one name double-count in rollups
    seen = defaultdict(list)
    for n in recipes:
        seen[re.sub(r"[^a-z0-9]+", "", n.lower())].append(n)
    for k, v in seen.items():
        if len(v) > 1:
            add("INFO", "names collide once normalised (keep them distinct)", " | ".join(v))

    # ---------- INGREDIENTS ----------
    # An ingredient problem only misstates money if a recipe draws on it. All 75
    # of the unconfirmed packs are referenced by exactly zero recipes, and at
    # WARN they sat at the top of the list ahead of everything that does. They
    # still matter — a chef building a new recipe would meet them — so they stay,
    # at INFO, counted rather than listed one by one.
    used_by_recipe = {ln.get("ref") for r in recipes.values()
                      for ln in (r.get("ingredients") or []) if ln.get("ref")}

    for i in ings:
        rate, unit = money(i.get("cost_per_base_unit")), (i.get("pack_unit") or "").lower()
        desc = i.get("description") or i.get("id")
        if unit in CEIL and rate > CEIL[unit] and i.get("id") not in DEAR_BUT_REAL:
            add("SEVERE", "per-unit rate above anything real (pack/unit confusion)",
                f"${rate:.4f}/{unit}  {desc}")
        if rate and rate < FLOOR:
            add("WARN", "priced at effectively zero", f"${rate:.8f}  {desc}")
        if i.get("needs_pack_review"):
            live = i.get("id") in used_by_recipe
            add("WARN" if live else "INFO",
                "pack size unconfirmed, and a recipe uses it" if live
                else "pack size unconfirmed (no recipe uses it — nothing is "
                     "mispriced today, but a new recipe would meet it)",
                desc)

    # ---------- MONEY SPENT THAT NEVER REACHES THE COST BOOK ----------
    # A line resolve_pack can't read is SKIPPED — correctly, because guessing a pack
    # is how a $76 bottle becomes $12.75. But a skip is silent, and the product then
    # costs off whatever stale seed it had. $1,583 of Bombay Dry gin was invoiced
    # since June and never reached the book; 13 cocktails priced off a January seed.
    # Ranked by spend, because that is the order worth fixing them in.
    # Ids are compared through core.domain (imported at the top), not rebuilt by
    # hand here. A unit word the PDF parse bled onto a supplier code is not part
    # of identity, so "fresh-fruit-team:AH20T TRAY" and "fresh-fruit-team:AH20T"
    # are one code — spelling them out locally made five well-priced FFT lines
    # (avocado, chives, broccolini, eggs, rocket) read as "never reached the book".
    priced = {canonical_purchasable(r["ingredient"])
              for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))}
    # A supplier code is "reached" if it is priced directly OR it bridges to a
    # ProductID that is priced. A seed-matched liquor line (Rooster, De Bortoli)
    # feeds the bridge only — the bottle invoice supersedes the seed on the
    # ProductID recipes read — so the code is genuinely in the book even though no
    # row carries the raw supplier id. Without this join those codes would read as
    # "never reached" the moment build_costs stopped double-emitting them.
    pmap = ROOT / "data" / "product_map.csv"
    bridged = {}
    if pmap.exists():
        for r in csv.DictReader(pmap.open(encoding="utf-8-sig")):
            sup, code, pdi = r.get("supplier"), r.get("supplier_code"), r.get("product_id")
            if sup and code and pdi:
                bridged[purchasable_id(sup, code)] = f"lightspeed:{pdi.strip()}"
    spend, seen = defaultdict(float), {}
    if COGS.exists():
        for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
            sup, code = (r.get("supplier") or ""), (r.get("supplier_code") or "").strip()
            if not code or sup == "Lightspeed" or (r.get("invoice_date") or "") < "2026-06-01":
                continue
            iid = purchasable_id(sup, code)
            if iid in priced or bridged.get(iid) in priced:
                continue
            spend[iid] += money(r.get("cost_per_unit_incl_gst"))
            seen[iid] = r.get("invoice_description")
    for iid, amt in sorted(spend.items(), key=lambda x: -x[1]):
        if amt < 100:
            continue
        add("WARN", "bought since June but never reached the cost book (pack unreadable)",
            f"${amt:>9,.0f}  {str(seen[iid])[:38]:40} {iid}")

    # ---------- COST BOOK: PRICE OUTLIERS ----------
    # A pack misread doesn't look wrong on its own — it looks like a price. It only
    # shows up NEXT TO the same ingredient's other invoices. Foodlink's black beans
    # invoiced at $8.70 a tin twice and $52.20 once (a CTN-6 carton read as one tin);
    # the camembert went the other way, a per-piece price divided by a carton of 12.
    # Comparing each observation to its own median catches both directions.
    hist = defaultdict(list)
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        c = money(r.get("cost_per_unit"))
        if c > 0:
            hist[(r["ingredient"], r["unit"])].append((c, r))
    for (iid, _u), obs in hist.items():
        if len(obs) < 3:
            continue
        med = sorted(x[0] for x in obs)[len(obs) // 2]
        if med <= 0:
            continue
        for c, r in obs:
            if c > 3 * med or c < med / 3:
                newest = max(x[1]["observed_on"] for x in obs)
                live = " <-- THIS IS THE LIVE PRICE" if r["observed_on"] == newest else ""
                add("SEVERE" if live else "WARN",
                    "invoice price way off this ingredient's own history (pack misread)",
                    f"{c / med:>5.1f}x median  ${c:<10.6f} {str(r.get('description'))[:26]:28}"
                    f" {r.get('source_invoice', '')[:12]} {r['observed_on']}"
                    f"{_pack_count_hint(c, med)}{live}")

    # ---------- WHERE LIGHTSPEED REPORTS NO COST AT ALL ----------
    # A CORRECTION, AND IT MATTERS MORE THAN WHAT IT REPLACES.
    #
    # This block used to assert "the POS cost column is ~3.6x low, on
    # everything", calling it the project's founding thesis, finally measured.
    # It was measured on data/products_weekly.csv — and 3,767 of that file's
    # 5,018 rows in the 13-week window carry a cost of exactly zero, because the
    # Looker backfill that built it has no costs. Summing those zeros against
    # real recipe costs manufactures a 0.28x ratio out of nothing. The same file
    # had already produced one retracted claim ("$54k missing from the P&L") and
    # its own docstring warns the cost column is incomplete; the rule kept using
    # it anyway.
    #
    # Measured instead on the daily Insights exports, which is what the P&L
    # actually reads, over 5,636 rows and $306,618 of revenue:
    #
    #     where Lightspeed states a cost, it agrees with our invoice-fed
    #     book to 0.96x — within four percent.
    #
    # Lightspeed's cost column is not wrong. It is ABSENT: 17.5% of revenue has
    # Cost $0.00, booked at 100% gross profit, because Lightspeed has no recipe
    # for that product. daily_aggregator's own comment said exactly this in July
    # ("11 products report $0.00 cost — 4.6% of revenue at 100% GP"); the share
    # has since more than tripled.
    #
    # So the value of this project is not that it corrects a wrong number. It is
    # that it puts a number where there was none — and what is left below is the
    # revenue where neither source has one.
    # Three kinds of thing hide in that number and they need different work, so
    # the rule says which. The patterns are TRIAGE LABELS ONLY — they change no
    # figure, they just stop "$45,370 at 100% GP" reading as one problem:
    #   a fee has no food cost and never will;
    #   a deal contains real product whose contents are not declared anywhere
    #     (data/recipe_missing_lines.yaml is where the Wings Deals were settled);
    #   everything else is a dish nobody has written a recipe for.
    _FEE_SKU = re.compile(r"delivery fee|surcharge|gift|voucher|open price|booking"
                          r"|tip|service charge|deposit|donation", re.I)
    _BUNDLE_SKU = re.compile(r"\$\d|deal|feast|banquet|party|soiree|shindigg|monty"
                             r"|combo|package", re.I)
    split = {"fee": 0.0, "bundle": 0.0, "dish": 0.0}

    for ven, key in (("stow", "stowaway"), ("hg", "harry"), ("mari", "marilynas")):
        book = _load_book_costs(key)
        tot = uncosted = 0.0
        rows_n = 0
        worst: dict = defaultdict(float)
        for f in sorted((ROOT / "data").glob(f"insights_{ven}_*.csv")):
            try:
                rows = list(csv.DictReader(f.open(encoding="utf-8-sig")))
            except Exception:                                    # noqa: BLE001
                continue
            for r in rows:
                nm = (r.get("Product Name") or "").strip()
                rev = _money_cell(r.get("$ Sales"))
                if not nm or rev <= 0:
                    continue
                tot += rev
                if _money_cell(r.get("Cost")) == 0 and book_cost(book, nm) is None:
                    uncosted += rev
                    rows_n += 1
                    worst[nm] += rev
                    kind = ("fee" if _FEE_SKU.search(nm)
                            else "bundle" if _BUNDLE_SKU.search(nm) else "dish")
                    split[kind] += rev
        if tot <= 0 or uncosted <= 0:
            continue
        add("WARN", "revenue booked at 100% GP — Lightspeed has no cost for it "
                    "and neither do we",
            f"[{ven}] ${uncosted:>9,.0f} of ${tot:>10,.0f} = {100 * uncosted / tot:4.1f}% "
            f"across {rows_n} product-days")
        for nm, rev in sorted(worst.items(), key=lambda x: -x[1])[:8]:
            if rev < 500:
                break
            add("WARN", "revenue booked at 100% GP — Lightspeed has no cost for it "
                        "and neither do we",
                f"[{ven}] ${rev:>9,.0f}   {nm[:44]}")

    if sum(split.values()) > 0:
        add("WARN", "revenue booked at 100% GP — Lightspeed has no cost for it "
                    "and neither do we",
            f"{'':<6} SPLIT: ${split['dish']:>8,.0f} dishes with no recipe · "
            f"${split['bundle']:>7,.0f} deals whose contents are undeclared · "
            f"${split['fee']:>7,.0f} fees that have no food cost by nature")

    # ---------- WHAT THE BOOK STILL DOES NOT REACH ----------
    # The audit lists what is WRONG. This lists what is ABSENT, which is the
    # bigger number and the one nobody was tracking: revenue whose product has no
    # costed recipe at all, so the P&L falls through to Lightspeed's figure.
    #
    # It is also the work queue. "Build recipes for these seven dishes" is a
    # sentence someone can act on; "coverage is 86%" is not.
    #
    # NOTE on what "falls through to Lightspeed" costs: where Lightspeed states a
    # cost it agrees with our book to 0.96x, measured on the daily exports. The
    # damage is not a wrong number, it is an ABSENT one — see the block above.
    if sales:
        tot, cov, gaps = coverage(recipes)
        for ven in sorted(tot):
            pct = 100 * cov[ven] / tot[ven] if tot[ven] else 0.0
            add("INFO" if pct >= 85 else "WARN",
                "cost book coverage of revenue (the rest falls through to Lightspeed)",
                f"[{ven}] {pct:5.1f}% of ${tot[ven]:>10,.0f}  "
                f"— ${tot[ven] - cov[ven]:>9,.0f} uncosted (13wk)")
        for (ven, nm, g), rev in sorted(gaps.items(), key=lambda x: -x[1])[:25]:
            if rev < 500:
                break
            # THE BOOK MAY ALREADY HOLD IT UNDER ANOTHER NAME.
            #
            # "Outback Prawn Toast" is "Devon's Prawn Toast" — same dish, costed
            # since long before anyone looked. The till and Produce are two
            # naming systems nobody keeps in step, so "has no recipe" and "has no
            # recipe UNDER THIS NAME" are different claims and only the second
            # one is ever provable from here.
            #
            # I made the first claim three times in one session (Unlimited
            # Dumplings twice, Duck Spring Rolls once) and each time sent Zak to
            # write a recipe that already existed. So this rule no longer asserts
            # absence — it shows the nearest costed dishes and lets someone who
            # knows the menu decide. A confirmed pair goes in
            # data/product_recipe_aliases.yaml, which fixes the P&L too:
            # cogs_blend keys on the POS name, so a rename costs real money.
            near = _closest_recipes(nm, recipes)
            add("WARN",
                ("sells well — no costed recipe UNDER THIS NAME (the book may "
                 "hold it under another)"),
                f"[{ven}] ${rev:>8,.0f} (13wk)  {nm[:34]:<36} {g[:18]:<20}"
                + (f"  near: {near}" if near else "  (nothing close — a real gap)"))

    # ---------- A DELIVERY TWIN IS THE SAME DISH ----------
    # "X D" is the Uber/delivery version of X. Same food, same recipe, so a
    # material cost difference means one of them read an ingredient differently.
    # Bang Bang Cauli D took "0.01" of a $9.90 bunch of chives as a WHOLE bunch
    # and cost $12.57 on a $16 dish while the identical Bang Bang Cauli read the
    # same line as 10c — the twin has no scrape line of its own, so there was no
    # raw cost to judge whole-vs-fraction with and the default is the expensive
    # reading. That one is fixed; this keeps the class visible.
    for name in sorted(recipes):
        twin = f"{name} D"
        if twin not in recipes or recipes[name].get("is_prep"):
            continue
        a, b = money(recipes[name].get("our_cost")), money(recipes[twin].get("our_cost"))
        if a <= 0 or b <= 0 or abs(a - b) / max(a, b) <= 0.35:
            continue
        if name in VERIFIED_TWIN_GAP:
            add("INFO", "delivery twin differs, and that is the correct answer",
                f"{name[:30]:32} ${a:>7.2f}   vs   {twin[:32]:34} ${b:>7.2f}"
                f"   ({VERIFIED_TWIN_GAP[name]})")
            continue
        add("WARN", "a delivery twin costs materially more than the dish it copies",
            f"{name[:30]:32} ${a:>7.2f}   vs   {twin[:32]:34} ${b:>7.2f}"
            f"   ({max(a, b) / min(a, b):.1f}x)", product=name)

    # ---------- COSTED BELOW WHAT THE SUPPLIER PUBLISHES ----------
    # ILG send a price book (data/ilg_pricebook.csv, built by
    # scripts/build_ilg_pricebook.py). It is the only price in this system that
    # neither we nor Lightspeed derived — a supplier stating, in writing, what a
    # bottle costs. That makes it a FLOOR, and a floor is exactly what a cost
    # book that under-costs in the flattering direction has been missing.
    #
    # This rule has its own origin story: Havana Club sat at $29.09 a bottle
    # against a book price of $49.20 and survived scrutiny for hours, because the
    # only thing it was ever checked against was itself.
    #
    # THE JOIN IS THE ILG CODE, NOT THE NAME. The first version of this matched
    # Back Office names to price-book descriptions and found almost nothing —
    # "Havana 3yr [700ml]" and "Havana Club 700ml 3yo." are the same bottle and
    # no normaliser is going to say so, while the pairs that DO match by name are
    # exactly the well-behaved ones with nothing to report. data/product_map.csv
    # already carries the evidence-based supplier-code -> ProductID link, and the
    # price book is keyed by that same code. Joining on it needs no guessing at
    # all, and it covers the bottles that actually get invoiced.
    _pbk = ROOT / "data" / "ilg_pricebook.csv"
    _pmap = ROOT / "data" / "product_map.csv"
    if _pbk.exists() and _pmap.exists():
        _book = {}
        for r in csv.DictReader(_pbk.open(encoding="utf-8-sig")):
            try:
                _book[r["code"].replace("-", "")] = (r["description"],
                                                     float(r["size_ml"]),
                                                     float(r["book_price_unit"]))
            except (TypeError, ValueError):
                continue
        _latest = {}
        if COSTS.exists():
            for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
                if (r.get("unit") or "") != "ml":
                    continue
                k, d = r["ingredient"], r["observed_on"]
                if k not in _latest or d >= _latest[k][0]:
                    try:
                        _latest[k] = (d, float(r["cost_per_unit"]), r.get("source_invoice") or "")
                    except (TypeError, ValueError):
                        pass
        _under = []
        for r in csv.DictReader(_pmap.open(encoding="utf-8-sig")):
            if (r.get("supplier") or "") != "ILG":
                continue
            code = (r.get("supplier_code") or "").replace("-", "").rstrip("P")
            hit = _book.get(code)
            got = _latest.get(f"lightspeed:{(r.get('product_id') or '').strip()}")
            if not hit or not got:
                continue
            desc, size_ml, book = hit
            ours = got[1] * size_ml
            if book <= 0 or ours <= 0 or ours / book >= PRICEBOOK_FLOOR:
                continue
            _under.append((ours / book, r.get("product_name") or desc, ours, book,
                           r.get("supplier_code"), got[2]))
        for ratio, nm, ours, book, code, src in sorted(_under)[:15]:
            add("WARN", "costed below ILG's own published price for the same bottle",
                f"{nm[:34]:36} ours ${ours:>7.2f}  vs book ${book:>7.2f}"
                f"  ({ratio:.2f}x, {code}, from {src[:18]})", product=nm)
    else:
        add("INFO", "no ILG price book extracted",
            "run scripts/build_ilg_pricebook.py where the corpus lives — without "
            "it a cost has no independent floor to be checked against")

    # ---------- THE /pricing PAGE'S "BIGGEST MOVERS" ----------
    # The page exists so a supplier creeping prices up gets noticed the week it
    # happens. Its top entries are currently: Pellegrino +2300%, Coca Cola
    # +1100%, Bombay Dry Gin +475%, Aperol +480%, Sailor Jerry +481%.
    #
    # None of those moved. Pellegrino's "rise" is exactly 24.00x and Coca Cola's
    # exactly 12.00x — one invoice priced the bottle, the next priced the case.
    # Aperol went from $30.11 a bottle to $174.50 a case, which is $29.08 a
    # bottle: a 3% FALL reported as a 480% rise. The rest are the same shape at
    # 5.7-5.8x, a six-pack with a small real price move on top.
    #
    # A single ingredient's per-unit price does not double between two deliveries
    # from the same supplier. Anything at 2x or more is the pack being read two
    # ways, and while it sits at the top of that page the page cannot be used for
    # what it is for.
    #
    # Reported here rather than fixed in build_price_compare: the fix belongs
    # with resolve_pack reading the invoice's own "6x700ML" note, which is a
    # change to the invoice pipeline and wants its own pass.
    compare = ROOT / "dashboard" / "pricing" / "compare.json"
    if compare.exists():
        try:
            movers = (json.loads(compare.read_text(encoding="utf-8-sig")) or {}).get("movers") or []
        except Exception:                                        # noqa: BLE001
            movers = []
        for m in movers:
            prev, cost = money(m.get("prev")), money(m.get("cost"))
            if prev <= 0 or cost / prev < 2.0:
                continue
            add("WARN", "the /pricing page reports a price rise that is a pack-size "
                        "change (bottle priced one week, case the next)",
                f"{str(m.get('name'))[:28]:30} {str(m.get('supplier'))[:14]:16} "
                f"${prev:>9,.4f} -> ${cost:>9,.2f} = {cost / prev:>5.1f}x "
                f"(shown as +{money(m.get('pct')):,.0f}%)")

    # ---------- TWO IDENTITIES FOR ONE STOCK ITEM ----------
    # Every rule above checks a number against something OUTSIDE the book — a
    # supplier's price list, an invoice, the same code's own history. None of them
    # notices that the book holds the same bottle twice at two prices, because
    # each copy is internally consistent and neither is an outlier on its own.
    #
    # It holds most of them twice by design: Back Office is filed per venue, so
    # Stowaway and Harry Gatos each have their own ProductID for one physical
    # stock item, and a recipe picks whichever its venue's menu was built from.
    # When the two disagree the cheaper one is a discount nobody negotiated:
    #
    #   Angostura Bitters   lightspeed:20747514 (HG)   $1.34 a 200 ml bottle
    #                       lightspeed:20487270 (stow) $20.89 a 200 ml bottle
    #     ILG's own price book lists 390-021-0 at $15.10 a bottle, so the HG copy
    #     is 8.9% of what the supplier charges for it. $1.34 is not a price for
    #     Angostura; it is a keying error, and four HG cocktails cost off it
    #     (Manhattan - Perfect, Manhattan - Dry, Mai Tai, Dark & Stormy).
    #
    #   Plantation 3 Stars  both copies $60.83, but HG states 4500 ml and
    #                       Stowaway 700 ml — one bottle read as a case, 6.4x low.
    #
    # THE JOIN IS THE NAME, and here that is safe where it was not for the price
    # book: both sides are Back Office's OWN naming of its own product, not two
    # companies' names for one bottle. Brackets and the size token come off; the
    # base unit must match, so a per-ml copy is never compared with a per-each one.
    #
    # Not everything this class contains is name-matchable, and the rule says so
    # rather than pretending otherwise. Harry Gatos' "Alehouse Premium Lager"
    # (lightspeed:20744549, 5 products incl. Harry's Lager) is seeded at $185.00 —
    # ILG bill 122-2858 ALEHOUSE PREMIUM KEG at $212.44 and 122-2867 ALEHOUSE
    # CRISP KEG at $184.94, so the Premium product carries the Crisp price, 12.9%
    # low. No name normaliser pairs "Premium Lager" with "Draught Lager", and no
    # ILG invoice in the data was billed to Harry Gatos, so nothing here can say
    # WHICH of the two is wrong — the name or the price. That one needs Zak.
    uses: dict[str, int] = defaultdict(int)
    for _n, _r in recipes.items():
        for _l in (_r.get("ingredients") or []):
            if (_l.get("kind") == "id") and (_l.get("ref") or "").startswith("lightspeed:"):
                uses[_l["ref"]] += 1
    for ratio, members in twin_identity_conflicts(cost_book_latest(), bo_product_names()):
        detail = f"{ratio:>6.2f}x   " + " | ".join(
            f"{nm[:26]} {iid.split(':')[1]} ${c:,.6f}/{u} ({uses.get(iid, 0)} recipes, {d})"
            for c, iid, nm, d, u in members)
        add("WARN", "two identities for one stock item at materially different "
                    "prices (a recipe costs off whichever its venue holds)", detail)

    # ---------- COST BOOK ----------
    rows = list(csv.DictReader(COSTS.open(encoding="utf-8-sig")))
    for r in rows:
        if money(r.get("cost_per_unit")) == 0:
            add("WARN", "cost book row priced at $0",
                f"{r.get('ingredient')} {r.get('description')}")
    return F


def main():
    # stdout is output too — see build_costs.py. An em-dash in a progress line
    # under an ASCII locale kills a run whose files are already correct.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--severe", action="store_true", help="only money-misstating findings")
    args = ap.parse_args()

    F = audit()
    order = {"SEVERE": 0, "WARN": 1, "INFO": 2}
    # Rules with real money behind them first, then the biggest lists. A rule
    # about a product nobody buys should not outrank one about the menu.
    rule_rev = {k: sum(rev for rev, _ in F[k]) for k in F}
    keys = sorted(F, key=lambda k: (order[k[0]], -rule_rev[k], -len(F[k])))
    n_sev = sum(len(F[k]) for k in F if k[0] == "SEVERE")

    for sev, rule in keys:
        if args.severe and sev != "SEVERE":
            continue
        items = sorted(F[(sev, rule)], key=lambda x: -x[0])
        head = f"\n[{sev}] {rule} — {len(items)}"
        if rule_rev[(sev, rule)] > 0:
            head += f"   (${rule_rev[(sev, rule)]:,.0f} of 13wk revenue affected)"
        print(head)
        for _rev, d in items[:20]:
            print(f"    {d}")
        if len(items) > 20:
            print(f"    ... and {len(items) - 20} more")

    print(f"\n{'=' * 62}\nSEVERE {n_sev} | "
          f"WARN {sum(len(F[k]) for k in F if k[0] == 'WARN')} | "
          f"INFO {sum(len(F[k]) for k in F if k[0] == 'INFO')}")
    return 1 if n_sev else 0


if __name__ == "__main__":
    raise SystemExit(main())
