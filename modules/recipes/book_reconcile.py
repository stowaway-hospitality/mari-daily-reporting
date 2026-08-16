"""
Reconcile the recipe book against ITSELF — the class of error a human catches by
eye and no arithmetic check can.

WHY THIS EXISTS
---------------
Everything this platform audits verifies ARITHMETIC: stated quantity x sourced
price, and whether the sourced price came from a real invoice. Nothing verified
that the stated QUANTITY describes what the kitchen does. Two defects got past
every check and were caught by Zak looking at a screen:

  1. "Lettuce Cos Baby Twin Pack [Each]" entered at qty 1 — a whole twin-pack of
     baby cos, $2.75, on an $8.20 burger. Wrong because the two burgers that
     already carried it took 0.083 of the pack. Nothing to do with kitchen
     knowledge: the book contradicted itself.
  2. "Chicken Roast" missing the Yorkshire Pudding and Gravy lines that Pork,
     Lamb, Beef and Nut Roast all carry at the SAME quantity to the gram — and
     its whole bird logged as "0.5 ml".

Both are detectable from internal consistency alone. That is what this module
does, and it is the only reason it can run unattended: it never asks "is 250 g
of shallot a lot?", it asks "does this line agree with the other 891 recipes?".

IT NEVER CORRECTS A QUANTITY. A quantity is a kitchen fact. Every finding here
is a QUESTION for a human, emitted into data/cost_book_flags.json by
scripts/build_cost_book_flags.py and rendered on the Flags tab of /recipes/ with the rest of
the work queue. The one correction layer that may rewrite a line
(data/recipe_line_unit_fixes.yaml) demands an arithmetic proof and is edited by
hand.

CALIBRATION — measured against the real book, 892 recipes / 3,041 saved lines
(data/lightspeed_recipes_costed.json), 2026-08-08. flagged / true / false:

    missing_standard_component     2 /  2 / 0    over 5 coherent families
    batch_overflow                 4 /  4 / 0    over 24 recipes that declare a yield
    price_conflict                 4 /  4 / 0    over 461 (ingredient, unit) groups
    whole_pack_outlier             0 /  0 / 0    over 9 eligible ingredient groups

`whole_pack_outlier` is the lettuce rule and it flags nothing today, because the
lettuce never reached the saved book — it was caught in the builder. It ships as
a tripwire for the day one does, exactly as the 0/0/0 rules in
dashboard/_shared/recipe_line_guard.js do.

TWO DETECTORS WERE BUILT, MEASURED AND DROPPED. Both are recorded here because
the measurement is the finding:

  MAGNITUDE OUTLIER — "this recipe draws >5x the median quantity of an
  ingredient". 13 lines of 3,041 (26 before excluding batches and multi-serve
  packs), and on inspection ZERO are errors: a PartyJar really does hold six
  margaritas, a 6-pack really is six cans, Holy Guacamole really is the corn-chip
  dish (155 g against a 20 g median that is dominated by tacos), and 5 g of flake
  salt really does rim a frozen margarita. The lettuce was 12x its peers, so the
  useful part of this idea survives as `whole_pack_outlier`, which asks a much
  narrower question. The general form is a rule that is wrong every time it
  fires.

  PORTION SHARE — "this line is a far bigger share of this dish than of any other
  dish it appears in" (>50% of the dish AND >4x its own median share). 4 lines
  flagged, 4 wrong: Holy Guacamole is 57% guacamole, Free Garlic Cheese Regular
  is 86% mozzarella. recipe_line_guard.js reached the same conclusion from the
  other direction (23 flagged, 0 true) and declined to implement it.

PURE. No file reads, no dates, no network — it is handed the book and returns
findings. scripts/build_cost_book_flags.py does the I/O and the wording;
modules/recipes/tests/test_book_reconcile.py runs every rule against the real
feed and against hand-built regressions for the lettuce and the Chicken Roast.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import mean, median

# --------------------------------------------------------------------------
# family construction
# --------------------------------------------------------------------------

# The variant words this book puts around one dish. Stripping them collapses
# "Gluten-free Little Italy [Dine-in]", "Large Little Italy Wings Deal" and
# "Regular Little Italy" onto one base, so a family is DISHES, not menu rows.
_VARIANT_PREFIX = re.compile(r"^(gluten-free|large|regular|free|btl)\s+", re.I)
_VARIANT_SUFFIX = [
    re.compile(r"\s*\[dine-in\]$", re.I),
    re.compile(r"\s*\[hg\]$", re.I),
    re.compile(r"\s+wings deal$", re.I),
    re.compile(r"\s+d$"),                       # the delivery twin, case-sensitive
    re.compile(r"\s*\(old\)$", re.I),
    re.compile(r"\s+-\s+(regular|large|bottle|glass|pint|schooner|jug|large glass|classic)$", re.I),
]

# A family needs enough members for "all but one" to mean anything. Three is not
# enough: at three, one dissenter is a third of the evidence.
FAMILY_MIN_MEMBERS = 4
# How alike the members' ingredient lists must be before we are willing to read a
# gap as an omission. MEASURED: at 0.30 the book yields exactly five families —
# roast 0.71, margarita 0.64, burrito 0.64, dark & stormy 0.50, old fashioned
# 0.33 — every one of them genuinely the same plate or the same build. Drop the
# bar and the head noun starts inventing families: "vegan" pairs a Sanchez VEGAN
# pizza with a Seitan Katsu Curry, "1kg" pairs Chipotle Mayo with Cooked Beef
# Brisket, and every ingredient of one reads as missing from the other.
FAMILY_MIN_COHERENCE = 0.30
# How many siblings must carry a component before its absence is a finding.
COMPONENT_MIN_CARRIERS = 3


def variant_base(name: str) -> str:
    """The dish behind a menu row: "Large Sorrento Wings Deal" -> "Sorrento"."""
    s = str(name or "")
    changed = True
    while changed:
        changed = False
        m = _VARIANT_PREFIX.match(s)
        if m:
            s, changed = s[m.end():], True
        for rx in _VARIANT_SUFFIX:
            s2 = rx.sub("", s)
            if s2 != s:
                s, changed = s2, True
    return s.strip()


def _ingredient_names(recipe) -> set:
    return {ln.get("name") for ln in (recipe.get("ingredients") or []) if ln.get("name")}


def _jaccard(a: set, b: set) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def coherent_families(recipes) -> list:
    """-> [(head_noun, [recipe name, ...], coherence)] for families worth comparing.

    A family is every DISH whose name ends in the same word — the four Roasts,
    the eleven Margaritas, the four Burritos. One member per dish (the fullest
    variant), so a pizza's six menu rows cannot outvote anything.

    The head noun alone is a bad family key and the coherence test is what makes
    it usable: it demands that the members already agree about most of their
    ingredients before any disagreement is treated as news.

    Large-vs-Regular is deliberately NOT done here. audit_book.py owns that
    comparison and already reports both directions; two rules with two ideas of
    the same finding is how a work queue starts arguing with itself.
    """
    fullest: dict = {}
    for name, r in (recipes or {}).items():
        b = variant_base(name)
        if not b:
            continue
        cur = fullest.get(b)
        if cur is None or len(r.get("ingredients") or []) > len(recipes[cur].get("ingredients") or []):
            fullest[b] = name

    heads: dict = defaultdict(list)
    for b, name in fullest.items():
        toks = re.sub(r"[^A-Za-z0-9 ]", " ", b).split()
        if toks:
            heads[toks[-1].lower()].append(name)

    out = []
    for head, members in heads.items():
        if len(members) < FAMILY_MIN_MEMBERS:
            continue
        sets = [_ingredient_names(recipes[m]) for m in members]
        pairs = [_jaccard(sets[i], sets[j])
                 for i in range(len(sets)) for j in range(i + 1, len(sets))]
        coh = mean(pairs) if pairs else 0.0
        if coh >= FAMILY_MIN_COHERENCE:
            out.append((head, sorted(members), round(coh, 3)))
    return sorted(out)


# --------------------------------------------------------------------------
# 1. the Chicken Roast detector
# --------------------------------------------------------------------------

def missing_standard_component(recipes) -> list:
    """A component every sibling carries AT THE SAME QUANTITY, missing from one.

    THE IDENTICAL QUANTITY IS THE WHOLE RULE, and it is what separates a missing
    line from a different drink. Pork, Lamb, Beef and Nut Roast all carry Gravy
    Prep at 145 g and Yorkshire Pudding at 1 — the same ladle and the same tray on
    every plate — so a fifth roast without them is missing them. Nine of eleven
    margaritas carry triple sec, but at 9.25 ml and at 10 ml, and the two that
    skip it are a Cadillac (Grand Marnier instead) and a Tommy's (agave instead).
    Requiring the carriers to agree to the decimal drops both of those and keeps
    both roasts.

    MEASURED on the real book: 2 findings, both plausible omissions (Cauliflower
    Burrito has no cheese; Fish Burrito has no lime). Re-run against the book as
    it stood before HEAD~1, it also returns Chicken Roast's Gravy Prep and
    Yorkshire Pudding and nothing else — the regression in
    test_book_reconcile.py.
    """
    out = []
    for head, members, coh in coherent_families(recipes):
        carried: Counter = Counter()
        for m in members:
            carried.update(_ingredient_names(recipes[m]))
        carried_by_all = (set.intersection(*[set(_ingredient_names(recipes[m])) for m in members])
                          if members else set())
        for ing, n in carried.items():
            if n < COMPONENT_MIN_CARRIERS or n == len(members):
                continue
            lines = {m: _line(recipes[m], ing) for m in members if ing in _ingredient_names(recipes[m])}
            stated = {(str(ln.get("qty")), str(ln.get("unit"))) for ln in lines.values()}
            if len(stated) != 1:
                continue                      # the carriers do not agree — not a standard
            qty, unit = next(iter(stated))
            # A member carrying a DIFFERENT ingredient in the same slot — same
            # quantity, same unit, and an ingredient its siblings lack — has
            # SUBSTITUTED, not omitted. Verified in Lightspeed 2026-08-09:
            # Cauliflower Burrito has no "Cheese Mexican Blend Shredded [2kg]"
            # because it carries "Vegan Shredded Cheese [500g]" at the same 55 g,
            # which is exactly what a vegan burrito should do — the flag was asking
            # the kitchen to put dairy cheese in the vegan dish. Same reasoning the
            # docstring already applies to the Cadillac (Grand Marnier) and Tommy's
            # (agave) margaritas, now applied per MEMBER rather than per family.
            def _substituted(m, _q=qty, _u=unit):
                others = set(_ingredient_names(recipes[m])) - carried_by_all
                for o in others:
                    ln = _line(recipes[m], o) or {}
                    if (str(ln.get("qty")), str(ln.get("unit"))) == (_q, _u):
                        return True
                return False

            missing = [m for m in members
                       if ing not in _ingredient_names(recipes[m]) and not _substituted(m)]
            if not missing:
                continue
            per_serve = median([float(ln.get("eff_cost") or 0) for ln in lines.values()])
            for m in missing:
                out.append({
                    "rule": "missing_standard_component",
                    "family": head,
                    "recipe": m,
                    "ingredient": ing,
                    "qty": qty,
                    "unit": unit,
                    "carriers": sorted(lines),
                    "carrier_count": n,
                    "family_size": len(members),
                    "coherence": coh,
                    "per_serve_cost": round(per_serve, 6),
                })
    return sorted(out, key=lambda f: (-f["per_serve_cost"], f["recipe"], f["ingredient"]))


def _line(recipe, ingredient_name):
    for ln in recipe.get("ingredients") or []:
        if ln.get("name") == ingredient_name:
            return ln
    return None


# --------------------------------------------------------------------------
# 2. the lettuce detector
# --------------------------------------------------------------------------

# A batch really does take a whole bunch of thyme or a whole tray of avocados, so
# every quantity rule here is off for one. Same list as the converter's own
# `prep_ish`, kept in step deliberately.
_PREP_RE = re.compile(r"\[(batch|prep|\d+\s*(kg|g|l|ml))\]|\b(prep|mix|marination|batch|blend)\b", re.I)

# How far above the fraction its peers take a whole-pack entry must sit. The
# lettuce was 1 against 0.083 — twelve times. MEASURED: the bare rule ("peers
# take a fraction, this one takes a whole") flags 56 lines and every one is
# correct, because a Large pizza legitimately takes 1 of an oregano shake where a
# Regular takes 0.716. At 5x, and only where the extra reaches 50c, the book
# yields nothing at all and the lettuce still yields 12x and $2.52.
WHOLE_PACK_MULTIPLE = 5.0
WHOLE_PACK_MIN_EXTRA = 0.50
WHOLE_PACK_MIN_PEERS = 2


def prep_ish(name: str, recipes) -> bool:
    used_as_sub = {ln.get("ref") for r in (recipes or {}).values()
                   for ln in (r.get("ingredients") or [])
                   if ln.get("kind") == "subrecipe" and ln.get("ref")}
    return name in used_as_sub or bool(_PREP_RE.search(str(name or "")))


def whole_pack_outliers(recipes) -> list:
    """An ingredient the book takes as a FRACTION of a pack, taken whole once.

    A quantity below 1 can only mean a share of a countable pack — nobody writes
    0.083 grams. So when most recipes take a share and one takes the lot, the odd
    one out is claiming the whole twin-pack, the whole punnet, the whole tray.
    That is the lettuce, and it is visible without knowing anything about salad.
    """
    subs = {ln.get("ref") for r in (recipes or {}).values()
            for ln in (r.get("ingredients") or [])
            if ln.get("kind") == "subrecipe" and ln.get("ref")}
    by: dict = defaultdict(list)
    for name, r in (recipes or {}).items():
        if name in subs or _PREP_RE.search(name or ""):
            continue
        for ln in r.get("ingredients") or []:
            if ln.get("kind") != "id":
                continue
            try:
                q = float(ln.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if q <= 0:
                continue
            by[(ln.get("ref"), ln.get("name"))].append(
                (name, q, float(ln.get("eff_cost") or 0)))

    out = []
    for (_ref, ing), uses in by.items():
        if len(uses) < COMPONENT_MIN_CARRIERS:
            continue
        frac = [u for u in uses if u[1] < 1]
        whole = [u for u in uses if u[1] >= 1]
        if len(frac) < WHOLE_PACK_MIN_PEERS or len(frac) <= len(whole):
            continue
        peer_qty = median([u[1] for u in frac])
        peer_cost = median([u[2] for u in frac])
        for name, q, cost in whole:
            if peer_qty <= 0 or q < WHOLE_PACK_MULTIPLE * peer_qty:
                continue
            if cost - peer_cost < WHOLE_PACK_MIN_EXTRA:
                continue
            out.append({
                "rule": "whole_pack_outlier",
                "recipe": name,
                "ingredient": ing,
                "qty": q,
                "peer_qty": peer_qty,
                "peers": sorted(u[0] for u in frac),
                "multiple": round(q / peer_qty, 1),
                "line_cost": round(cost, 6),
                "peer_cost": round(peer_cost, 6),
                "extra_per_serve": round(cost - peer_cost, 6),
            })
    return sorted(out, key=lambda f: -f["extra_per_serve"])


# --------------------------------------------------------------------------
# 3. a batch that holds more than it makes
# --------------------------------------------------------------------------

_DECLARED_YIELD = re.compile(
    r"\[\s*([0-9]*\.?[0-9]+)\s*(kg|kgs|g|gm|l|lt|ltr|litre|ml|mls)\s*\]\s*$", re.I)
import re as _re
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[2]   # repo root, for data/ lookups

_TO_BASE = {"kg": 1000.0, "kgs": 1000.0, "g": 1.0, "gm": 1.0,
            "l": 1000.0, "lt": 1000.0, "ltr": 1000.0, "litre": 1000.0,
            "ml": 1.0, "mls": 1.0}

# How far past its own declared yield a batch must sit. ONE DIRECTION ONLY, and
# that is the point: a batch can hold LESS than it makes (Super Lime Juice is 3
# limes, acid and a litre of water; Burrito Rice is 924 g of dry rice that comes
# out at 3 kg) but it can never hold MORE. Water and swelling make the low side
# meaningless; nothing makes the high side meaningless.
#
# MEASURED over the 24 recipes whose name declares a yield: at 1.5x it flags 6,
# and two of those are reductions that strain their solids out (Toasted Rice
# Syrup at 2.2x, Massenez Lychee at 1.7x). At 3.0x it flags 4 and every one is a
# batch that states between 4 and 11 times its own name.
YIELD_OVERFLOW_X = 3.0


_REAL_YIELDS = None


def _real_yields() -> dict:
    """The Expected yield each batch states in Lightspeed Produce, harvested into
    data/recipe_yields.yaml on 2026-08-09.

    Zak: "the [1L] labels are LABELS not yields. the yields are on lightspeed."
    He is right — the name is a naming convention, and reading it as the yield was
    wrong by 7.5x on Jalapeno Tequila (a [1L] batch that makes 7,500 mL) and 10.5x
    on Cooked Beef Brisket. The scrape never captured the field, so the name was
    all there was. Now it is not: the real figure wins and the name is the fallback
    for anything not yet harvested.
    """
    global _REAL_YIELDS
    if _REAL_YIELDS is None:
        _REAL_YIELDS = {}
        try:
            import yaml
            f = ROOT / "data" / "recipe_yields.yaml"
            d = yaml.safe_load(f.read_text(encoding="utf-8-sig")) if f.exists() else None
            # The scraped block first, then the cook-yield estimates OVER it. For a
            # COOKED protein Produce's 'Expected yield' holds the RAW weight —
            # 10,500 g against a 10,000 g brisket batch, which would have meat
            # leaving a braise heavier than it went in — so the scraped figure is
            # kept as the record of what Produce says and the estimate is what we
            # cost off. See the note in data/recipe_yields.yaml.
            for block in ("yields", "cook_yield_estimates"):
                for nm, spec in ((d or {}).get(block) or {}).items():
                    q, u = spec.get("yield"), str(spec.get("unit") or "").lower()
                    fct = _TO_BASE.get(u)
                    if q and fct:
                        _REAL_YIELDS[nm] = (float(q) * fct,
                                            "g" if u in ("kg", "kgs", "g", "gm") else "ml")
        except Exception:                                        # noqa: BLE001
            _REAL_YIELDS = {}
    return _REAL_YIELDS


def declared_yield(name: str):
    """-> (base quantity, base unit) the recipe actually makes, or None.

    Lightspeed's Expected yield first (the source of truth — see _real_yields);
    the number in the NAME only when we have not harvested one.
    """
    real = _real_yields().get(str(name or ""))
    if real:
        return real
    est = _prep_yield_numbers().get(str(name or ""))
    if est:
        return est

    # THE NAME IS NOT A FALLBACK EITHER. See resolve_yield in
    # modules/recipes/pipeline/build_recipe_feeds.py for the full reasoning: the
    # bracket was wrong every single time it disagreed with a written yield, and
    # keeping it as a fallback is what let a forgotten yield silently become
    # whatever number happened to be in a recipe's name.
    return None


def batch_overflow(recipes) -> list:
    """A batch whose stated inputs come to several times what its name says it makes.

    Grams and millilitres are summed together at density 1, which is the same
    reading data/recipe_line_unit_fixes.yaml uses to prove the Peking Sauce case
    ("read every quantity as g/mL and they sum to EXACTLY the 6.75L the recipe
    name declares"). It is coarse and it does not matter: this rule only ever
    speaks at 3x or more.

    IT DOES NOT SAY WHICH NUMBER IS WRONG. "Cooked Beef Brisket [1Kg]" states 10 kg
    of raw brisket; either the name is wrong or the quantity is, and only the
    kitchen knows which. Both readings are in the finding.
    """
    out = []
    for name, r in (recipes or {}).items():
        y = declared_yield(name)
        if not y:
            continue
        # A YIELD COUNTED IN THINGS IS NOT COMPARABLE TO A MASS. "Brownie Prep
        # [24 pcs] makes 24" against 3,106 g of batter is not a 124x defect, it
        # is 129 g a brownie. This never fired while declared_yield fell back to
        # the name -- the bracket regex only matched g/ml/kg/L, so "[24 pcs]"
        # returned nothing and the batch was skipped. Reading prep_yields
        # instead surfaced the comparison, and with it the same mistake
        # audit_book made in July.
        if str(y[1]).lower() not in ("g", "ml"):
            continue
        total = 0.0
        biggest = None
        for ln in r.get("ingredients") or []:
            try:
                q = float(ln.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            f = _TO_BASE.get(str(ln.get("unit") or "").lower())
            if not f or q <= 0:
                continue
            total += q * f
            if biggest is None or q * f > biggest[1]:
                biggest = (ln.get("name"), q * f, str(ln.get("qty")), str(ln.get("unit")))
        if y[0] <= 0 or total < YIELD_OVERFLOW_X * y[0]:
            continue
        out.append({
            "rule": "batch_overflow",
            "recipe": name,
            "declared": y[0],
            "declared_unit": y[1],
            "inputs": round(total, 2),
            "multiple": round(total / y[0], 2),
            "biggest_line": biggest,
            "batch_cost": float(r.get("our_cost") or 0),
        })
    return sorted(out, key=lambda f: -f["multiple"])



# How much more a batch may claim to make than its lines contain before it is a
# finding. Deliberately tight where batch_overflow is loose: overflow is caught
# at 3x because the ambiguity is which number is wrong, whereas a yield that
# EXCEEDS its contents is arithmetically impossible at any multiple above one.
# 1.05 leaves room for rounding and for a line recorded to the nearest 10 g.
YIELD_OVERSTATED_X = 1.05

# A basis or a name that explains a yield larger than its costed contents.
_DILUTED = _re.compile(r"water|hydrat|dilut|syrup|brine|broth|stock|soda|tea|infus|"
                       # super juice is peels, acid and WATER -- the technique is
                       # a dilution and the name never says so.
                       r"super\s+\w+\s+juice|"
                       # rice and grains take up their cooking water; 2.5x dry to
                       # cooked is the expected ratio, not a defect.
                       r"\brice\b|couscous|quinoa|pasta|noodle", _re.I)


def _priced_quantity(ln):
    """A line's real quantity, recovering pack counts from what they cost.

    The scrape records "2 ml" of a [4L] sauce meaning TWO 4-LITRE PACKS. Summing
    the label says House BBQ Sauce holds 3 ml and makes 11 L -- a 3,666x nonsense
    that buries every real finding beneath it. Where a line carries both a
    per-unit rate and a line cost, cost / rate is the quantity the converter
    actually priced, which is the one that means something.
    """
    try:
        qty = float(ln.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        rate, eff = float(ln.get("our_cost")), float(ln.get("eff_cost"))
        if rate > 0:
            implied = eff / rate
            if qty <= 0 or abs(implied - qty) / max(qty, 1e-9) > 0.01:
                return implied
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    f = _TO_BASE.get(str(ln.get("unit") or "").lower())
    return qty * f if f and qty > 0 else 0.0


def yield_overstated(recipes, estimates=None) -> list:
    """A batch that claims to MAKE more than its lines contain. The mirror of
    batch_overflow, and the dangerous half.

    Overflow makes a batch's per-unit rate too HIGH, so dishes look expensive and
    somebody investigates. This makes it too LOW: the dish under-costs, GP reads
    better than it is, and nothing ever prompts a question. CLAUDE.md names that
    direction as the one nobody looks at, and until 2026-08-16 there was no rule
    for it at all -- batch_overflow had been running alone since it was written.

    Two escapes, and each must be visible in the recipe or its basis:
      * uncosted water -- pizza dough's 62% hydration, super juice's dilution, a
        broth. The yield legitimately exceeds the costed contents.
      * a yield counted in things (24 pieces, 110 puddings), which is not
        comparable to a mass at all.
    """
    est = estimates if estimates is not None else _prep_yield_bases()
    out = []
    for name, r in (recipes or {}).items():
        if not r.get("is_prep"):
            continue
        y = declared_yield(name)
        if not y or y[0] <= 0:
            continue
        if str(y[1]).lower() not in ("g", "ml"):
            continue                       # a count is not comparable to a mass
        basis = (est.get(name) or "")
        if _DILUTED.search(basis) or _DILUTED.search(str(name)):
            continue
        contents = sum(_priced_quantity(ln) for ln in (r.get("ingredients") or []))
        if contents <= 0 or y[0] < YIELD_OVERSTATED_X * contents:
            continue
        out.append({
            "rule": "yield_overstated",
            "recipe": name,
            "declared": y[0],
            "declared_unit": y[1],
            "contents": round(contents, 2),
            "multiple": round(y[0] / contents, 2),
            "batch_cost": float(r.get("our_cost") or 0),
            "basis": basis[:200],
        })
    return sorted(out, key=lambda f: -f["multiple"])


_PREP_YIELD_NUMBERS = None


def _prep_yield_numbers() -> dict:
    """prep name -> (qty, unit) from data/prep_yields.yaml.

    declared_yield used to fall back to the [1L] in a name. It no longer does,
    so the written estimates have to be reachable from here or every rule built
    on declared_yield goes blind the moment Lightspeed has not harvested one.

    CACHED, like _real_yields beside it. declared_yield is called once per
    recipe per rule -- roughly 900 times a run -- and parsing a YAML file on
    every one of those took the test suite from seconds to minutes. It did not
    fail, it just stopped, which is the worst way for a mistake to present.
    """
    global _PREP_YIELD_NUMBERS
    if _PREP_YIELD_NUMBERS is None:
        _PREP_YIELD_NUMBERS = {}
        try:
            import yaml as _yaml
            f = ROOT / "data" / "prep_yields.yaml"
            doc = _yaml.safe_load(f.read_text(encoding="utf-8-sig")) if f.exists() else None
            for k, v in (doc or {}).items():
                try:
                    _PREP_YIELD_NUMBERS[k] = (float(v["yield_qty"]), str(v["yield_unit"]))
                except (KeyError, TypeError, ValueError):
                    pass
        except Exception:                                    # noqa: BLE001
            pass
    return _PREP_YIELD_NUMBERS


def _prep_yield_bases() -> dict:
    """prep name -> the `basis` text prep_yields.yaml states for its yield."""
    import yaml as _yaml
    f = ROOT / "data" / "prep_yields.yaml"
    if not f.exists():
        return {}
    doc = _yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
    return {k: str((v or {}).get("basis") or "") for k, v in doc.items()}


# --------------------------------------------------------------------------
# 4. our price and Lightspeed's price for the same product
# --------------------------------------------------------------------------

# Where a real disagreement about PRICE starts. MEASURED across the 461
# (ingredient, unit) groups that carry both figures: the median group agrees to
# 0.1%, 380 of 461 agree within 10%, and only 14 sit at 2x or worse. The band
# between 1.1x and 2x is ordinary drift — our invoice-fed rate is simply newer
# than Lightspeed's — and includes ginger at 1.89x and prosciutto at 1.63x, both
# of which are just this month's price.
PRICE_CONFLICT_LO = 2.0
# Above this the two figures are not two opinions about a price, they are two
# different UNITS: Lightspeed charging a whole 4 L jug against a line that says
# "2 ml", our per-millilitre rate against its per-bottle one. Those belong to the
# quantity rules, not here, and reporting them as a price argument sends someone
# to Back Office to fix a price that is correct. 8 of the 14 sit above 50x.
PRICE_CONFLICT_HI = 50.0


def price_conflicts(recipes, adjudicated=(), exclude_refs=()) -> list:
    """The same product held at two prices that are not two opinions about a price.

    Lightspeed's own per-line dollar figure implies a rate; our invoice-fed book
    holds a rate. Where they differ by 2x-50x, one of the two is wrong and the
    recipe costs off ours.

    `adjudicated` is data/product_map.csv — every ProductID somebody has already
    reconciled against a supplier invoice, with the reasoning recorded there.
    Havana Club sits at exactly 2.0000x and is in that file: "ILG book price MAR
    2026 for 355-055-2 is $49.20 a bottle, so the $58.01 line is ONE bottle. The
    seed said $29.09 — below ILG's own book price, impossible." Re-raising a
    settled question is how a queue loses its reader, so those are excluded and
    the flag says how many were.

    `exclude_refs` is audit_book's twin list — a stock item Back Office holds
    twice already has a flag of its own.
    """
    adjudicated = set(adjudicated or ())
    exclude = set(exclude_refs or ())
    by: dict = defaultdict(list)
    for name, r in (recipes or {}).items():
        for ln in r.get("ingredients") or []:
            if ln.get("kind") != "id" or ln.get("our_cost") in (None, ""):
                continue
            try:
                q, ls = float(ln.get("qty") or 0), float(ln.get("ls_cost") or 0)
            except (TypeError, ValueError):
                continue
            if q <= 0 or ls <= 0:
                continue
            by[(ln.get("ref"), ln.get("name"), str(ln.get("unit") or "").lower())].append(
                (name, q, ls, float(ln["our_cost"]), float(ln.get("eff_cost") or 0)))

    out = []
    for (ref, ing, unit), uses in by.items():
        ours = median([u[3] for u in uses])
        theirs = median([u[2] / u[1] for u in uses])
        if ours <= 0 or theirs <= 0:
            continue
        ratio = max(ours / theirs, theirs / ours)
        if not (PRICE_CONFLICT_LO <= ratio <= PRICE_CONFLICT_HI):
            continue
        if ref in adjudicated or ref in exclude:
            continue
        out.append({
            "rule": "price_conflict",
            "ref": ref,
            "ingredient": ing,
            "unit": unit,
            "our_rate": ours,
            "ls_rate": theirs,
            "ratio": round(ratio, 2),
            "ours_is_dearer": ours > theirs,
            "recipes": sorted(u[0] for u in uses),
            "our_line_total": round(sum(u[4] for u in uses), 4),
            "ls_line_total": round(sum(u[2] for u in uses), 4),
        })
    return sorted(out, key=lambda f: -abs(f["our_line_total"] - f["ls_line_total"]))
