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
sys.path.insert(0, str(ROOT / "scripts"))
# The size-variant collapse is DEFINED there; importing it keeps one whitelist
# rather than a second, looser copy drifting in the auditor. See sold().
from build_products_weekly import normalize_product  # noqa: E402

COSTED = ROOT / "data" / "lightspeed_recipes_costed.json"
INGREDIENTS = ROOT / "data" / "ingredients.json"
COSTS = ROOT / "data" / "costs.csv"
COGS = ROOT / "data" / "cogs_list.csv"
PRODUCTS_WEEKLY = ROOT / "data" / "products_weekly.csv"

# A reporting group whose POS cost is below (our cost / this) is not a pricing
# nuance, it is a broken feed. 2x is wide; the real gap measures ~3.6x.
POS_COST_MIN_RATIO = 2.0


def _nrm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

# What a sane bar/kitchen ingredient costs, per BASE unit, incl GST. Anything past
# these is a pack/unit confusion, not a real price. Calibrated on the real book:
# the dearest legitimate per-g item is saffron-class spice; the dearest per-ml is
# a top-shelf spirit at roughly $0.35/ml.
CEIL = {"g": 0.20, "ml": 0.60}
# Verified against the invoice and genuinely this dear, so the ceiling would
# only ever cry wolf. Select Fresh 3064370: "KUTJERA BUSH TOMATO WHOLE100GM"
# $48.00 for 100g — a premium native spice used a pinch at a time.
DEAR_BUT_REAL = {"select-fresh:BUSHTOMG"}
FLOOR = 0.000_01          # a real ingredient is never free
ABSURD_SERVE = 120.0      # no single non-prep menu item costs more than this
GP_FLATTER = 95.0
# Below this a POS "price" is a placeholder or a staff/comp SKU, not a menu
# price — the cost engine already refuses to compute GP under it, and an
# auditor that ignores the threshold just reports the engine working.
MENU_PRICED = 3.0         # a 95%+ GP on a food/drink item means a missing cost


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


def audit():
    recipes = json.loads(COSTED.read_text())["recipes"]
    # data/ingredients.json is DELIBERATELY not committed — it is a 90-day window
    # off date.today(), so a committed copy would rot on whatever Tuesday an
    # invoice crossed the line. On a clean checkout it does not exist until
    # build_ingredients has run, and reading it blind turned "the audit has
    # nothing to say about ingredients yet" into a FileNotFoundError traceback
    # that took CI down. Say so and audit everything else.
    ings: list = []
    if INGREDIENTS.exists():
        ing_raw = json.loads(INGREDIENTS.read_text())
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
    for name, r in sorted(recipes.items()):
        prep = bool(r.get("is_prep"))
        sell, cost = money(r.get("sell_incl")), money(r.get("our_cost"))
        lines = r.get("ingredients") or []
        gp = r.get("gp_pct")

        if sell >= MENU_PRICED and cost == 0 and not prep:
            add("SEVERE", "sells for money but costs $0 (reads as 100% GP)",
                f"${sell:>7.2f}  {name}", product=name)
        if not lines and not prep:
            add("WARN", "no ingredient lines at all", name)
        if cost > ABSURD_SERVE and not prep:
            add("SEVERE", f"single serve costs more than ${ABSURD_SERVE:.0f}",
                f"${cost:>8.2f}  {name}", product=name)
        if sell >= MENU_PRICED and cost > sell and not prep:
            add("SEVERE", "costs more than it sells for",
                f"cost ${cost:>7.2f} vs sell ${sell:>7.2f}  {name}", product=name)
        if gp is not None and gp >= GP_FLATTER and not prep:
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
    def _lines_by_ref(rec):
        out = {}
        for l in (rec.get("ingredients") or []):
            k = str(l.get("ref") or l.get("name") or "")
            try:
                out[k] = (float(l.get("qty") or 0), str(l.get("name") or k))
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
                add("WARN", "LARGE carries LESS of an ingredient than the REGULAR",
                    f"{stem}: {rname[:30]:<32} large {L[ref][0]:g} < regular {rq:g}")
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
            for _k, _v in (_yaml.safe_load(_py.read_text()) or {}).items():
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
    for i in ings:
        rate, unit = money(i.get("cost_per_base_unit")), (i.get("pack_unit") or "").lower()
        desc = i.get("description") or i.get("id")
        if unit in CEIL and rate > CEIL[unit] and i.get("id") not in DEAR_BUT_REAL:
            add("SEVERE", "per-unit rate above anything real (pack/unit confusion)",
                f"${rate:.4f}/{unit}  {desc}")
        if rate and rate < FLOOR:
            add("WARN", "priced at effectively zero", f"${rate:.8f}  {desc}")
        if i.get("needs_pack_review"):
            add("WARN", "pack size unconfirmed", desc)

    # ---------- MONEY SPENT THAT NEVER REACHES THE COST BOOK ----------
    # A line resolve_pack can't read is SKIPPED — correctly, because guessing a pack
    # is how a $76 bottle becomes $12.75. But a skip is silent, and the product then
    # costs off whatever stale seed it had. $1,583 of Bombay Dry gin was invoiced
    # since June and never reached the book; 13 cocktails priced off a January seed.
    # Ranked by spend, because that is the order worth fixing them in.
    priced = {r["ingredient"] for r in csv.DictReader(COSTS.open(encoding="utf-8-sig"))}
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
                bridged[f"{re.sub(r'[^a-z0-9]+', '-', sup.lower()).strip('-')}:{code.strip().upper()}"] = f"lightspeed:{pdi.strip()}"
    spend, seen = defaultdict(float), {}
    if COGS.exists():
        for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
            sup, code = (r.get("supplier") or ""), (r.get("supplier_code") or "").strip()
            if not code or sup == "Lightspeed" or (r.get("invoice_date") or "") < "2026-06-01":
                continue
            iid = f"{re.sub(r'[^a-z0-9]+', '-', sup.lower()).strip('-')}:{code.upper()}"
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

    # ---------- THE POS COST COLUMN IS ~3.6x LOW, ON EVERYTHING ----------
    # This is the project's founding thesis, finally measured. daily_aggregator
    # copies Lightspeed's `cost` column verbatim into the P&L. Triangulated over
    # 13 weeks against two independent sources:
    #
    #     POS cost column     6.7% of revenue      <- what the P&L reports
    #     our recipe book    21.6% of revenue      <- invoice-fed, this project
    #     Xero purchases     32.0% of revenue      <- what was actually bought
    #
    # 6.7% COGS is not a hospitality business. Our book sits between the two and
    # near Xero (purchases legitimately run above consumption: stock build, waste,
    # theft), so our book is the credible one and the POS column is ~3.6x low.
    #
    # It is NOT a beer/pour problem — an earlier pass framed it that way and was
    # wrong. The ratio is flat across categories: pizza 0.29, classic cocktails
    # 0.24, big plates 0.24, small plates 0.29, kitchen specials 0.26. Tap beer is
    # not special; it just has no recipe to compare against, so it hid longer.
    if PRODUCTS_WEEKLY.exists():
        pw = list(csv.DictReader(PRODUCTS_WEEKLY.open(encoding="utf-8-sig")))
        wks = sorted({r["week_ending"] for r in pw})
        if wks:
            cut = wks[max(0, len(wks) - 13)]
            agg = defaultdict(lambda: [0.0, 0.0, 0.0, ""])
            for r in pw:
                if r["week_ending"] < cut:
                    continue
                a = agg[(r["venue"], _nrm_name(r["product_name"]))]
                a[0] += money(r.get("qty")); a[1] += money(r.get("sales_ex_gst"))
                a[2] += money(r.get("cost")); a[3] = r.get("reporting_group") or ""
            bynorm = {_nrm_name(n): r for n, r in recipes.items()}
            grp = defaultdict(lambda: [0.0, 0.0, 0.0])
            for (ven, k), (q, rev, cost, g) in agg.items():
                rec = bynorm.get(k)
                if not rec or rec.get("is_prep") or q <= 0 or rev <= 0:
                    continue
                ours = money(rec.get("our_cost")) * q
                if ours <= 0:
                    continue
                b = grp[(ven, g)]
                b[0] += rev; b[1] += cost; b[2] += ours
            for (ven, g), (rev, pos_c, ours) in sorted(grp.items(), key=lambda x: -x[1][2]):
                if ours <= 0 or rev < 1000:
                    continue
                if pos_c < ours / POS_COST_MIN_RATIO:
                    add("SEVERE", "POS cost column far below our own book "
                                  "(the number the P&L copies is wrong)",
                        f"[{ven}] {g[:24]:<26} POS ${pos_c:>8,.0f} vs ours ${ours:>8,.0f} "
                        f"({pos_c/ours:.2f}x)  ${ours-pos_c:>8,.0f} of COGS missing (13wk)")

    # ---------- WHAT THE BOOK STILL DOES NOT REACH ----------
    # The audit lists what is WRONG. This lists what is ABSENT, which is the
    # bigger number and the one nobody was tracking: revenue whose product has no
    # costed recipe at all, so the P&L falls through to Lightspeed's figure —
    # and Lightspeed's figure runs about 0.28x of ours across every category.
    #
    # It is also the work queue. "Build recipes for these seven dishes" is a
    # sentence someone can act on; "coverage is 86%" is not.
    if sales:
        costed = set()
        for n, r in recipes.items():
            if r.get("is_prep") or money(r.get("our_cost")) <= 0:
                continue
            costed.add(_nrm_name(n))
            costed.add(_nrm_name(normalize_product(_VENUE_TAG.sub("", n))))
        # products_weekly collapses size variants, so the recipe side is
        # collapsed the same way before comparing — otherwise every tap beer
        # reads as uncovered when in fact all its sizes are costed.
        tot = defaultdict(float)
        cov = defaultdict(float)
        pw2 = list(csv.DictReader(PRODUCTS_WEEKLY.open(encoding="utf-8-sig")))
        wks2 = sorted({r["week_ending"] for r in pw2})
        cut2 = wks2[max(0, len(wks2) - 13)] if wks2 else ""
        gaps = defaultdict(float)
        for r in pw2:
            if r["week_ending"] < cut2:
                continue
            rev = money(r.get("sales_ex_gst"))
            if rev <= 0:
                continue
            tot[r["venue"]] += rev
            if _nrm_name(r["product_name"]) in costed:
                cov[r["venue"]] += rev
            else:
                gaps[(r["venue"], r["product_name"], r.get("reporting_group") or "")] += rev
        for ven in sorted(tot):
            pct = 100 * cov[ven] / tot[ven] if tot[ven] else 0.0
            add("INFO" if pct >= 85 else "WARN",
                "cost book coverage of revenue (the rest falls through to Lightspeed)",
                f"[{ven}] {pct:5.1f}% of ${tot[ven]:>10,.0f}  "
                f"— ${tot[ven] - cov[ven]:>9,.0f} uncosted (13wk)")
        for (ven, nm, g), rev in sorted(gaps.items(), key=lambda x: -x[1])[:25]:
            if rev < 500:
                break
            add("WARN", "sells well, has no costed recipe anywhere",
                f"[{ven}] ${rev:>8,.0f} (13wk)  {nm[:42]:<44} {g[:24]}")

    # ---------- COST BOOK ----------
    rows = list(csv.DictReader(COSTS.open(encoding="utf-8-sig")))
    for r in rows:
        if money(r.get("cost_per_unit")) == 0:
            add("WARN", "cost book row priced at $0",
                f"{r.get('ingredient')} {r.get('description')}")
    return F


def main():
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
