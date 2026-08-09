#!/usr/bin/env python3
"""Build data/cost_book_flags.json — everything the cost book still needs from a
human, in one place.

    python3 scripts/build_cost_book_flags.py            # write the feed
    python3 scripts/build_cost_book_flags.py --print    # ...and read it here

WHY THIS EXISTS
---------------
The open questions in this cost book have always been real and have always been
invisible. They lived in HANDOFF_*.md files, in comment blocks inside
audit_book.py, and in chat. Nobody opens a handoff file. So a $2,700-a-year
question about lamb yield and a $59.81 seed row that an invoice fixed months ago
sat at exactly the same level of visibility: none.

audit_book.py answers "what in the book is WRONG". This answers a different
question — "what is BLOCKED, and on whom" — and it renders where the book is
read, on the Flags tab of /recipes/. The two are deliberately separate: the auditor's job is
to fail CI while a SEVERE stands; this one's job is to be a work queue that a
human can finish, on the Flags tab of /recipes/.

DERIVED FIRST, DECLARED ONLY WHERE DATA CANNOT KNOW
---------------------------------------------------
Eight of the nine flag families are computed from data/ on every build:

  no_recipe   revenue whose product has no costed recipe. Straight from
              audit_book.coverage() — the SAME function the auditor uses, so the
              panel and the audit can never disagree about what is costed.
  twin_price  one stock item held twice at materially different prices, from
              audit_book.twin_identity_conflicts().
  bad_seed    a seeded per-unit price a real invoice has since contradicted by
              3x or more (the seed is dead weight today, and wrong).
  validator   a recurring supplier line the validator cannot classify AND whose
              amount no sanity bound in suppliers.yaml admits — it will sit in
              review forever until the config learns the pack.
  cook_loss   the dollars behind a yield question: the protein line's own cost,
              times what the dish actually sells, times the loss the assumed
              yield implies.
  structure   a line that disagrees with the other 891 recipes rather than with
              arithmetic — a component every sibling carries at the same
              quantity, absent from one; a pack the book takes a twelfth of,
              taken whole. modules/recipes/book_reconcile.py, which holds the
              calibration and the two rules that were measured and dropped.
  batch_yield a batch whose stated inputs come to several times what its own name
              says it makes. One direction only: a batch can hold less than it
              makes (water) and can never hold more.
  price_conflict  our invoice-fed rate and Lightspeed's own rate for the same
              product, where they are 2x-50x apart and nobody has adjudicated it
              in data/product_map.csv.

Only the QUESTIONS and the DECISIONS are declared, in data/cost_book_flags.yaml,
plus the permanent exemptions (an "Open Price" key has no product behind it and
never will, so it must stop reading as work).

NEVER INVENT A NUMBER
---------------------
`impact_per_year` is null unless there is an arithmetic path to it from data on
disk, and `impact_basis` shows that path in words. A flag with a real question
and no dollar figure is honest; a flag with a plausible-looking guess is how a
work queue gets sorted wrong.

The one assumption in the file is the cook-loss yield, it is stated in the feed
under `assumptions`, it is applied to nothing but the sizing of its own
questions, and every flag that uses it says so in its own `impact_basis`.

NOT COMMITTED. The 13-week and 52-week windows move with the calendar, so a
committed copy would rot on whatever Tuesday a week rolled over — the same
reason data/ingredients.json is generated at build time. build_site.py runs this.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# The auditor owns the definitions of "costed" and "two identities for one stock
# item". Importing them is the point: a panel that had its own, looser idea of
# coverage would send someone to write a recipe that already exists, which is the
# failure mode audit_book's own docstring calls the worst one a work queue has.
from audit_book import (                                            # noqa: E402
    bo_product_names, cost_book_latest, coverage, money,
    twin_identity_conflicts,
)
# The internal-consistency rules. They are PURE and live under modules/ because
# they are the model, not the I/O: this file reads the book off disk, hands it
# over, and does nothing but word the answers and price them.
from modules.recipes import book_reconcile                          # noqa: E402
# The unit rules. Same shape and same reason: pure, under modules/, calibrated
# in its own docstring against the real feeds.
from modules.recipes import feed_defects                            # noqa: E402

DATA = ROOT / "data"
BOOK = DATA / "lightspeed_recipes_costed.json"
COSTS = DATA / "costs.csv"
COGS = DATA / "cogs_list.csv"
DECLARED = DATA / "cost_book_flags.yaml"
OUT = DATA / "cost_book_flags.json"
ROLLUPS = ROOT / "dashboard" / "sales" / "products"

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# A stock item held twice becomes a FLAG (someone must go and edit Back Office)
# rather than an audit line only when the gap is past anything buying explains.
# audit_book reports from 1.35x because that is where a finding starts; the six
# it lists today run 1.35x to 14.93x and the bottom four are two venues buying on
# separate ILG accounts a fortnight apart. 3x is not that. It is a keying error.
TWIN_FLAG_X = 3.0
# The same shape, one layer down: a seed row whose price a real invoice has since
# contradicted. Both live examples land on a whole pack count (100.0x and 40.0x),
# which is the signature of a case priced as one unit.
SEED_FLAG_X = 3.0
# The long tail of uncosted products. 299 of the 334 gaps sit under the $500
# single-product threshold and together they are $20,113 of 13-week revenue —
# every kitchen add-on, every 'Add Prawns', Sticky Chicken Wings at $41. One
# flag each would bury the panel; dropping them silently is how "the add-ons are
# uncosted" stayed true for a year. So the tail is ROLLED UP by reporting group,
# which is also the unit of work: one Produce recipe per add-on group, not 299.
TAIL_GROUP_MIN_REV = 500.0
TAIL_GROUP_MIN_N = 3
# A unit defect is ranked by the recipe cost that rides on the answer. It is NOT
# an under-cost — see feed_defect_flags — so it never enters known_impact.
FEED_DEFECT_HIGH_AT_STAKE = 250.0

# How many times an unclassifiable supplier line must recur before it is a
# CONFIG gap rather than a one-off oddity. Three deliveries is a standing order.
VALIDATOR_MIN_SEEN = 3


def _nrm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# --------------------------------------------------------------------------
# annual sales, from the Sales Product API (CLAUDE.md names it the authority)
# --------------------------------------------------------------------------

def annual_units() -> tuple[dict, str]:
    """-> (normalised POS product name -> units sold in the last 52 weeks, cutoff)

    Read from dashboard/sales/products/rollup_*.json rather than recomputed:
    that feed IS the answer to "how much of X have we sold" (CLAUDE.md), it is
    rebuilt daily, and re-deriving it here would give the flags panel a second
    opinion about sales volume that nothing keeps in step with the first.

    ANCHORED ON THE FEED'S OWN LATEST WEEK, NOT ON TODAY. A product's `weekly`
    array is SPARSE — Lamb Roast has 58 buckets spanning two years, with whole
    months absent — so "the last 52 entries" is not 52 weeks and would quietly
    reach back to 2024. And anchoring on date.today() would make the window
    shrink every day the products API has not rebuilt, moving a flag's dollar
    figure with nothing but the clock. Anchoring on max(week_ending) means the
    same feed always yields the same number.
    """
    weeks: list = []
    per_file: list = []
    for fn in ("rollup_stow.json", "rollup_hg.json", "rollup_mari.json"):
        f = ROLLUPS / fn
        if not f.exists():
            continue
        prods = json.loads(f.read_text(encoding="utf-8-sig")).get("products") or []
        per_file.append(prods)
        weeks += [w.get("we") or "" for p in prods for w in (p.get("weekly") or [])]
    if not weeks:
        return {}, ""
    last = max(weeks)
    cut = (date.fromisoformat(last) - timedelta(days=363)).isoformat()
    out: dict = defaultdict(float)
    for prods in per_file:
        for p in prods:
            k = _nrm(p.get("name") or "")
            if not k:
                continue
            out[k] += sum(money(w.get("qty")) for w in (p.get("weekly") or [])
                          if (w.get("we") or "") >= cut)
    return dict(out), f"{cut} to {last}"


# --------------------------------------------------------------------------
# 1. cook loss — the declared question, the derived dollars
# --------------------------------------------------------------------------

def cook_loss_flags(spec, recipes, sold, window="") -> list:
    """One flag per protein whose cook loss nobody has measured.

    THE ARITHMETIC, stated once here and repeated in each flag's own basis:

        under-cost per serve = what the line costs today x (1/yield - 1)
        under-cost per year  = that x units sold in the last 52 weeks

    It works on a plated portion and on a batch without being told which,
    because it never touches grams: it multiplies the line's OWN dollar
    contribution, which is already qty x rate in whatever unit that line uses.
    Lamb Roast's protein line is $4.29 of a 220 g plated portion; at a 65% yield
    the raw joint needed is 1/0.65 of that, so $2.31 a plate is missing. Over
    the last 52 weeks of Lamb Roasts that is the number below.

    A batch (Cooked Beef Brisket) is summed over every dish that draws on it, so
    the answer is the whole cost of not knowing the yield, not one dish's share.
    """
    yield_ = float(spec.get("assumed_yield") or 0)
    if not 0 < yield_ < 1:
        return []
    loss = (1.0 / yield_) - 1.0
    out = []
    for s in (spec.get("subjects") or []):
        uses = []                      # (dish, line $, units/yr)
        for dish, r in recipes.items():
            if r.get("is_prep"):
                continue               # a prep is not sold; its consumers are
            for ln in (r.get("ingredients") or []):
                hit = (s.get("ref") and ln.get("ref") == s["ref"]
                       and (not s.get("in_recipe") or dish == s["in_recipe"])) \
                      or (s.get("prep") and ln.get("kind") == "subrecipe"
                          and ln.get("ref") == s["prep"])
                if not hit:
                    continue
                eff = money(ln.get("eff_cost"))
                qty = sold.get(_nrm(dish), 0.0)
                if eff > 0:
                    uses.append((dish, eff, qty))
        if not uses:
            continue
        priced = sum(e * q for _d, e, q in uses)
        impact = round(priced * loss, 2) if priced > 0 else None
        served = sum(q for _d, _e, q in uses)
        detail = "; ".join(f"{d} ${e:,.2f}/serve x {q:,.0f}" for d, e, q in
                           sorted(uses, key=lambda x: -x[1] * x[2]))
        out.append({
            "id": s["id"],
            "category": "cook_loss",
            "severity": "high" if (impact or 0) >= 1000 else "medium",
            "subject": s["subject"],
            "subject_kind": "prep" if s.get("prep") else "ingredient",
            "what_is_wrong": "The recipe prices a cooked portion at the RAW "
                             "purchase rate — nothing in this system models cook loss.",
            "why_it_matters": "Every gram that cooks away is a gram we paid for "
                              "and never charged for, so the dish reads cheaper "
                              "than it is and its GP reads better than it is.",
            "question": s["question"],
            "impact_per_year": impact,
            "impact_basis": (
                f"${priced:,.2f} of this protein sold in the 52 weeks "
                f"{window} ({served:,.0f} serves), x (1/{yield_:g} - 1) = {loss:.3f}. "
                f"The {yield_:g} yield is an ASSUMPTION used only to size this "
                f"question — it is applied to no cost anywhere. Lines: {detail}."
            ) if impact is not None else None,
            "action": "Weigh it once, in and out. The answer goes in the recipe "
                      "as a yield; this flag then disappears on the next build.",
            "owner": s.get("owner") or "Kitchen",
            "evidence": [s["evidence"]] if s.get("evidence") else [],
            "derived": True,
            "source": "data/lightspeed_recipes_costed.json + dashboard/sales/products/"
                      " + data/cost_book_flags.yaml (the question only)",
        })
    return out


# --------------------------------------------------------------------------
# 2. dishes with no costed recipe — audit_book.coverage(), verbatim
# --------------------------------------------------------------------------

_VENUE_LABEL = {"stow": "Stowaway", "hg": "Harry Gatos", "mari": "Marilyna's"}


def no_recipe_flags(recipes, exempt, min_rev=500.0) -> tuple[list, list]:
    """-> (flags, exempted). One flag per uncosted product worth flagging.

    The list is NOT hardcoded and must never be: it is whatever
    audit_book.coverage() says is uncovered this week, so a recipe landing
    removes its own flag on the next build. That is the whole difference between
    this and the handoff files it replaces.
    """
    tot, cov, gaps = coverage(recipes)
    # ONE DISH IS ONE FLAG. coverage() keys on the reporting group as well as
    # the product, because a product can be re-filed and the audit lists both
    # rows. Harry Gatos' "Shredded Beef" is filed under two groups and so
    # arrived here twice — two flags, one id, one of them silently overwriting
    # the other in any map keyed by id. Merge on (venue, product) and keep the
    # groups as evidence, which is what they are.
    # Revenue is kept PER GROUP as well as summed, because the rollup below has
    # to file a product under ONE group and a re-filed product carries two. Its
    # biggest group is the one that owns it; splitting the revenue across both
    # would double-count it, and joining the names ("Harry Gatos Food / Kids")
    # invents a group that is nobody's queue.
    merged: dict = defaultdict(lambda: [0.0, defaultdict(float)])
    for (ven, nm, group), rev in gaps.items():
        e = merged[(ven, nm)]
        e[0] += rev
        if group:
            e[1][group] += rev
    pats = [(re.compile(e["match"], re.I), e["reason"]) for e in exempt]
    flags, skipped, tail = [], [], []
    for (ven, nm), (rev, groups) in sorted(merged.items(), key=lambda x: -x[1][0]):
        group = " / ".join(sorted(groups))
        primary = max(groups, key=lambda g: (groups[g], g)) if groups else ""
        hit = next((reason for rx, reason in pats if rx.search(nm)), None)
        if hit:
            skipped.append({"subject": nm, "venue": ven,
                            "revenue_13wk": round(rev, 2), "reason": hit})
            continue
        if rev < min_rev:
            # Not dropped — held for the rollup below. A product too small to
            # earn its own flag is not too small to be uncosted.
            tail.append((ven, primary, nm, rev))
            continue
        annual = rev * 4      # 13 weeks -> a year, stated as such below
        flags.append({
            "id": "no-recipe-" + re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-")
                  + f"-{ven}",
            "category": "no_recipe",
            "severity": "high" if rev >= 2000 else "medium",
            "subject": nm,
            "subject_kind": "product",
            "venue": ven,
            "what_is_wrong": f"{_VENUE_LABEL.get(ven, ven)} sells this and the "
                             f"cost book has no recipe under this name.",
            "why_it_matters": "With no recipe the P&L falls through to "
                              "Lightspeed's own cost, and where Lightspeed has "
                              "none either the revenue books at 100% gross "
                              "profit — a margin nobody investigates because it "
                              "looks like good news.",
            "impact_per_year": None,
            "impact_basis": None,
            "revenue_13wk": round(rev, 2),
            "revenue_annualised": round(annual, 2),
            "reporting_group": group,
            "action": "Build the recipe — or, if the book already holds this "
                      "dish under another name, pair them in "
                      "data/product_recipe_aliases.yaml (that fixes the P&L too, "
                      "because cogs_blend keys on the POS name).",
            "owner": "Kitchen (recipe) or Zak (if it is a renamed dish)",
            "evidence": [f"${rev:,.0f} ex-GST in the last 13 weeks, "
                         f"reporting group {group or 'none'}"],
            "derived": True,
            "source": "audit_book.coverage() over data/products_weekly.csv",
        })
    flags += _no_recipe_tail_flags(tail, min_rev)
    # The dollar figure is the revenue at stake, NOT an under-cost: how much of
    # that revenue's cost is missing is exactly what having no recipe means we
    # cannot say. Stating it as an impact would be the guess this file refuses.
    return flags, sorted(skipped, key=lambda x: -x["revenue_13wk"])


def _no_recipe_tail_flags(tail, min_rev) -> list:
    """The uncosted long tail, ROLLED UP by (venue, reporting group).

    299 of the 334 coverage gaps are under $500 of 13-week revenue each and
    $20,113 together: the kitchen add-ons, the pizza add-ons, Sticky Chicken
    Wings at $41.36. Emitting 299 flags would drown the eight that carry a
    measured dollar figure; emitting none is what left "the add-ons have no
    recipes" true and invisible.

    Rolling up by reporting GROUP is not a display convenience, it is the unit
    of work: 'Add-ons - Kitchen' at Harry Gatos is one sitting with the kitchen
    and one batch of Produce entries, not 47 separate decisions. Every member is
    listed in the evidence, so nothing is hidden behind the total — and it is
    DERIVED, so a group empties itself as the recipes land.
    """
    groups: dict = defaultdict(list)
    for ven, group, nm, rev in tail:
        groups[(ven, group or "no reporting group")].append((nm, rev))
    out = []
    for (ven, group), members in groups.items():
        rev = sum(r for _n, r in members)
        if rev < TAIL_GROUP_MIN_REV or len(members) < TAIL_GROUP_MIN_N:
            continue
        members.sort(key=lambda m: -m[1])
        out.append({
            "id": "no-recipe-tail-" + re.sub(r"[^a-z0-9]+", "-",
                                             f"{ven} {group}".lower()).strip("-"),
            "category": "no_recipe",
            "severity": "high" if rev >= 2000 else "medium",
            "subject": f"{group} — {len(members)} uncosted lines ({_VENUE_LABEL.get(ven, ven)})",
            "subject_kind": "product_group",
            "venue": ven,
            "what_is_wrong": f"{len(members)} products in this reporting group have "
                             f"no costed recipe. Each is under the ${min_rev:,.0f} "
                             f"a product needs to be flagged on its own; together "
                             f"they are ${rev:,.0f} of 13-week revenue.",
            "why_it_matters": "These are the lines nobody costs because each one "
                              "looks too small to matter — extra patty, side "
                              "aioli, add prawns. They are pure add-on revenue "
                              "booking at 100% GP, and they are ordered on top "
                              "of a dish that already carries its own margin.",
            "impact_per_year": None,
            "impact_basis": None,
            "revenue_13wk": round(rev, 2),
            "revenue_annualised": round(rev * 4, 2),
            "reporting_group": group,
            "action": "Cost the group in one pass — most of these are a single "
                      "ingredient at a stated portion. Anything that genuinely "
                      "has no food cost belongs in the exempt list in "
                      "data/cost_book_flags.yaml, not in silence.",
            "owner": "Kitchen (portions) then Dev (Produce entries)",
            "evidence": [f"{nm} — ${r:,.0f} in 13wk" for nm, r in members],
            "derived": True,
            "source": "audit_book.coverage() over data/products_weekly.csv,"
                      " rolled up by reporting group",
        })
    out.sort(key=lambda f: -f["revenue_13wk"])
    return out


# --------------------------------------------------------------------------
# 3. two identities for one stock item
# --------------------------------------------------------------------------

def twin_flags(recipes) -> list:
    uses: dict = defaultdict(int)
    for _n, r in recipes.items():
        for ln in (r.get("ingredients") or []):
            if ln.get("kind") == "id" and (ln.get("ref") or "").startswith("lightspeed:"):
                uses[ln["ref"]] += 1
    out = []
    for ratio, members in twin_identity_conflicts(cost_book_latest(), bo_product_names()):
        if ratio < TWIN_FLAG_X:
            continue
        members = sorted(members)                       # cheapest copy first
        cheap, dear = members[0], members[-1]
        cheap_uses = uses.get(cheap[1], 0)
        stem = re.sub(r"\s*[\[(].*", "", cheap[2]).strip()
        out.append({
            "id": "twin-" + re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-"),
            "category": "back_office",
            "severity": "high" if cheap_uses else "medium",
            "subject": stem,
            "subject_kind": "ingredient",
            "what_is_wrong": f"Back Office holds this one stock item twice at "
                             f"{ratio:.2f}x apart, and {cheap_uses} recipe(s) "
                             f"cost off the cheap copy.",
            "why_it_matters": "Both copies are internally consistent, so no rule "
                              "that checks a price against its own history can "
                              "see it. Whichever copy a venue's menu was built "
                              "from is the price its drinks carry.",
            "impact_per_year": None,
            "impact_basis": None,
            "action": "Correct the wrong copy in Lightspeed Back Office (or "
                      "retire it), then re-run the invoice bridge.",
            "owner": "Back office (Lightspeed) — Zak to say which copy is right",
            "evidence": [f"{nm} ({iid.split(':')[1]}) ${c:,.6f}/{u}, "
                         f"{uses.get(iid, 0)} recipe(s), seen {d}"
                         for c, iid, nm, d, u in members],
            "derived": True,
            "source": "audit_book.twin_identity_conflicts() over data/costs.csv"
                      " + data/bo_exports/",
        })
    return out


# --------------------------------------------------------------------------
# 4. seed rows a real invoice has since contradicted
# --------------------------------------------------------------------------

def _live_unit_costs() -> dict:
    """(ProductID id, unit) -> (date, cost, description) from real invoices only."""
    live: dict = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        iid = r["ingredient"]
        if not iid.startswith("lightspeed:") or "seed" in (r.get("source_invoice") or ""):
            continue
        c = money(r.get("cost_per_unit"))
        if c <= 0:
            continue
        k = (iid, r["unit"])
        if k not in live or r["observed_on"] >= live[k][0]:
            live[k] = (r["observed_on"], c, r.get("description") or "")
    return live


def bad_seed_flags() -> list:
    """A seeded per-EACH price that an invoice has since contradicted by >=3x.

    Harmless TODAY — costs.csv already carries the invoiced rate, so nothing is
    mispriced — and wrong all the same. The seed is what a new ProductID falls
    back to before its first invoice lands, so a $59.81 garlic bread is a loaded
    gun sitting in the book with the safety on. Both live cases land on an exact
    whole pack count, which names the defect instead of just flagging it.
    """
    live = _live_unit_costs()
    out = []
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        if (r.get("supplier") or "") != "Lightspeed":
            continue
        if "seed" not in (r.get("source_invoice") or ""):
            continue
        if (r.get("pack_unit") or "").strip().lower() != "ea":
            continue
        seed = money(r.get("cost_per_unit_incl_gst"))
        iid = "lightspeed:" + (r.get("supplier_code") or "").strip()
        got = live.get((iid, "ea"))
        if seed <= 0 or not got or got[1] <= 0:
            continue
        ratio = seed / got[1]
        if SEED_FLAG_X > ratio > 1 / SEED_FLAG_X:
            continue
        n = round(max(ratio, 1 / ratio))
        packish = (f" — exactly a pack of {n} priced as one unit"
                   if abs(max(ratio, 1 / ratio) - n) < 0.02 * n else "")
        nm = r.get("invoice_description") or iid
        out.append({
            "id": "seed-" + re.sub(r"[^a-z0-9]+", "-", nm.lower()).strip("-"),
            "category": "bad_seed",
            "severity": "low",
            "subject": nm,
            "subject_kind": "ingredient",
            "what_is_wrong": f"The seeded cost is ${seed:,.4f} each against an "
                             f"invoiced ${got[1]:,.4f} each — {ratio:,.1f}x"
                             f"{packish}.",
            "why_it_matters": "Nothing is mispriced today: the invoice supersedes "
                              "the seed in data/costs.csv. But the seed is the "
                              "fallback for any recipe that reaches this product "
                              "before an invoice does, and a 40x fallback is a "
                              "wrong number waiting for a quiet week.",
            "impact_per_year": None,
            "impact_basis": None,
            "action": f"Correct the seed row in data/cogs_list.csv ({iid}) to the "
                      f"per-unit rate, or confirm the pack in "
                      f"data/pack_overrides.yaml so it can never be read as one.",
            "owner": "Dev",
            "evidence": [f"seed ${seed:,.4f}/ea from {r.get('source_invoice')}",
                         f"live ${got[1]:,.4f}/ea from {got[2]} on {got[0]}"],
            "derived": True,
            "source": "data/cogs_list.csv seed rows vs data/costs.csv live rows",
        })
    return out


# --------------------------------------------------------------------------
# 5. a supplier line no sanity bound in suppliers.yaml admits
# --------------------------------------------------------------------------

def _bounds() -> dict:
    import yaml
    p = ROOT / "modules" / "invoices" / "suppliers.yaml"
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}).get("sanity_bounds") or {}


def validator_config_flags() -> list:
    """A recurring stock line the extractor could not give a cost basis, whose
    amount ALSO sits outside every bound suppliers.yaml declares.

    Both halves are needed. "Unclassified" alone is 33 line groups at Paramount
    and most are ordinary bottles the resolver will settle. "Outside the bounds"
    alone catches a genuine one-off case line. Together they say something
    narrower and actionable: this is a standing purchase whose pack size the
    CONFIG has no way to express, so it will keep going to review every time it
    is delivered until somebody adds the basis.
    """
    bounds = _bounds()
    ceiling = max((money(v.get("max")) for v in bounds.values()), default=0.0)
    if ceiling <= 0:
        return []
    groups: dict = defaultdict(list)
    for f in sorted((DATA / "invoices").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception:                                       # noqa: BLE001
            continue
        inv = d.get("invoice") or {}
        for ln in (inv.get("lines") or []):
            if (ln.get("cost_basis") or "") != "unknown":
                continue
            if (ln.get("line_class") or "") != "stock":
                continue
            if money(ln.get("line_total_incl")) <= ceiling:
                continue
            groups[(inv.get("supplier_key") or "?",
                    (ln.get("description") or "").strip())].append(
                (inv.get("invoice_ref"), inv.get("invoice_date"),
                 money(ln.get("line_total_incl")),
                 f"{ln.get('pack_qty')} {ln.get('pack_unit')}"))
    out = []
    for (sup, desc), seen in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(seen) < VALIDATOR_MIN_SEEN:
            continue
        amounts = sorted({s[2] for s in seen})
        pack = seen[-1][3]
        out.append({
            "id": "config-" + re.sub(r"[^a-z0-9]+", "-", f"{sup} {desc}".lower()).strip("-"),
            "category": "config",
            "severity": "medium",
            "subject": f"{desc} ({sup})",
            "subject_kind": "config",
            "what_is_wrong": f"{len(seen)} invoice lines carry no cost basis, at "
                             f"${amounts[-1]:,.2f} for a {pack} pack — above every "
                             f"sanity bound suppliers.yaml declares (the highest "
                             f"is ${ceiling:,.2f}).",
            "why_it_matters": "The bounds are the smoke alarm for a case total "
                              "landing in a per-unit field, so they are supposed "
                              "to be tight. A real pack this config cannot "
                              "express goes to review on every delivery — and a "
                              "review queue that always contains the same line is "
                              "a queue people stop reading.",
            "impact_per_year": None,
            "impact_basis": None,
            "action": f"Add a cost basis and bounds entry for this pack in "
                      f"modules/invoices/suppliers.yaml, citing one of the "
                      f"invoices below as the evidence — the convention every "
                      f"other rule in that file follows.",
            "owner": "Dev",
            "evidence": [f"{ref} {dt}: ${amt:,.2f} for {pk}"
                         for ref, dt, amt, pk in sorted(seen, key=lambda x: x[1] or "")],
            "derived": True,
            "source": "data/invoices/*.json vs modules/invoices/suppliers.yaml"
                      " sanity_bounds",
        })
    return out


# --------------------------------------------------------------------------
# 6. the book disagreeing with itself — modules/recipes/book_reconcile.py
# --------------------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _adjudicated_ids() -> set:
    """Every ProductID somebody has already reconciled against a real invoice,
    with the reasoning written down in data/product_map.csv. Havana Club is the
    live example: it sits at exactly 2.0000x and the file says why our figure is
    the right one. A queue that re-raises settled questions stops being read."""
    p = DATA / "product_map.csv"
    if not p.exists():
        return set()
    return {"lightspeed:" + (r.get("product_id") or "").strip()
            for r in csv.DictReader(p.open(encoding="utf-8-sig"))
            if (r.get("product_id") or "").strip()}


def structure_flags(recipes, sold, window="") -> list:
    """Missing standard components and whole-pack quantities.

    THE DOLLAR IS THE LINE'S OWN COST TIMES WHAT THE DISH SELLS, and nothing
    else. For a missing component that is exactly the under-cost: the siblings
    all carry it at one quantity, so the absent line is worth what theirs are
    worth. No assumption enters, which is why these carry a figure where
    `batch_yield` below carries none.
    """
    out = []
    for f in book_reconcile.missing_standard_component(recipes):
        per = f["per_serve_cost"]
        serves = sold.get(_nrm(f["recipe"]), 0.0)
        impact = round(per * serves, 2) if per > 0 and serves > 0 else None
        others = ", ".join(f["carriers"])
        out.append({
            "id": f"structure-missing-{_slug(f['recipe'])}-{_slug(f['ingredient'])}",
            "category": "structure",
            "severity": "high" if (impact or 0) >= 1000 else "medium",
            "subject": f"{f['recipe']} — no {f['ingredient']}",
            "subject_kind": "recipe",
            "what_is_wrong": (
                f"{f['carrier_count']} of the {f['family_size']} {f['family']}s carry "
                f"{f['ingredient']} at exactly {f['qty']} {f['unit']}. This one has no "
                f"such line at all."),
            "why_it_matters": "A recipe missing an ingredient under-costs the dish "
                              "and flatters its GP, which is the dangerous "
                              "direction — nobody investigates a margin that "
                              "looks good. The siblings agreeing on the quantity "
                              "to the decimal is what makes this an omission "
                              "rather than a different dish.",
            "question": f"Does {f['recipe']} get {f['ingredient']}? If it does, the "
                        f"quantity its siblings use is {f['qty']} {f['unit']}.",
            "impact_per_year": impact,
            "impact_basis": (
                f"${per:,.4f} — what this line costs on each of {others} — "
                f"x {serves:,.0f} serves of {f['recipe']} in the 52 weeks {window}. "
                f"No yield, waste or portion assumption enters it."
            ) if impact is not None else None,
            "action": "Confirm with the kitchen, then add the line in "
                      "data/recipe_missing_lines.yaml citing the siblings — or "
                      "say it does not belong on this dish and it stops being "
                      "asked.",
            "owner": "Kitchen (does the dish get it?) then Dev (one yaml line)",
            "evidence": [f"{c}: {f['qty']} {f['unit']} of {f['ingredient']}"
                         for c in f["carriers"]]
                        + [f"family '{f['family']}' agrees on "
                           f"{f['coherence']:.0%} of its ingredients"],
            "derived": True,
            "source": "modules/recipes/book_reconcile.missing_standard_component()"
                      " over data/lightspeed_recipes_costed.json",
        })
    for f in book_reconcile.whole_pack_outliers(recipes):
        serves = sold.get(_nrm(f["recipe"]), 0.0)
        extra = f["extra_per_serve"]
        impact = round(extra * serves, 2) if extra > 0 and serves > 0 else None
        out.append({
            "id": f"structure-wholepack-{_slug(f['recipe'])}-{_slug(f['ingredient'])}",
            "category": "structure",
            "severity": "high" if (impact or 0) >= 1000 else "medium",
            "subject": f"{f['recipe']} — a whole {f['ingredient']}",
            "subject_kind": "recipe",
            "what_is_wrong": (
                f"This takes {f['qty']:g} of {f['ingredient']}, {f['multiple']:g}x the "
                f"{f['peer_qty']:g} every other recipe takes. A quantity below 1 can "
                f"only be a share of a pack, so this line claims the whole pack."),
            "why_it_matters": "One pack on one plate is the defect that put $2.75 "
                              "of baby cos on an $8.20 burger and made lettuce "
                              "dearer than the wagyu. It over-costs the dish, "
                              "which sends the wrong signal to menu pricing.",
            "impact_per_year": impact,
            "impact_basis": (
                f"${f['line_cost']:,.4f} on this line against ${f['peer_cost']:,.4f} "
                f"on the same ingredient elsewhere = ${extra:,.4f} a serve, "
                f"x {serves:,.0f} serves in the 52 weeks {window}."
            ) if impact is not None else None,
            "action": "Weigh or count one serve and enter that fraction. The "
                      "builder at /recipes/ warns on this as it is typed.",
            "owner": "Kitchen",
            "evidence": [f"{p}: {f['peer_qty']:g}" for p in f["peers"]],
            "derived": True,
            "source": "modules/recipes/book_reconcile.whole_pack_outliers()"
                      " over data/lightspeed_recipes_costed.json",
        })
    return out


def batch_yield_flags(recipes) -> list:
    """A batch that states more than it makes.

    NO DOLLAR FIGURE, deliberately. Either the name is wrong or the quantity is,
    and the two readings have opposite consequences — if "Cooked Beef Brisket
    [1Kg]" really is 10 kg then its per-kilo rate is ten times too high, and if
    it really is 1 kg then the brisket line is. Picking one to size the flag
    would be the guess this whole feed refuses.
    """
    out = []
    for f in book_reconcile.batch_overflow(recipes):
        consumers = sorted({n for n, r in recipes.items()
                            for ln in (r.get("ingredients") or [])
                            if ln.get("kind") == "subrecipe" and ln.get("ref") == f["recipe"]})
        big = f["biggest_line"]
        out.append({
            "id": "batch-yield-" + _slug(f["recipe"]),
            "category": "batch_yield",
            "severity": "high" if f["multiple"] >= 5 else "medium",
            "subject": f["recipe"],
            "subject_kind": "prep",
            "what_is_wrong": (
                f"The name says it makes {f['declared']:,.0f} {f['declared_unit']}. "
                f"Its lines add up to {f['inputs']:,.0f} — {f['multiple']:g}x that. "
                f"A batch cannot hold more than it makes."),
            "why_it_matters": "Everything downstream divides by the declared "
                              "yield, so the per-unit rate this batch publishes "
                              "is out by the same multiple — and the book page "
                              "shows it as fact. Which of the two numbers is "
                              "wrong changes which way.",
            "question": f"Is {f['recipe']} a {f['declared']:,.0f} {f['declared_unit']} "
                        f"batch, or is the {big[2]} {big[3]} of {big[0]} wrong?",
            "impact_per_year": None,
            "impact_basis": None,
            "action": "Say which number is the kitchen's. If the yield is wrong, "
                      "rename the recipe in Produce; if a quantity is, fix it "
                      "there — or, where the unit is provably a typo, in "
                      "data/recipe_line_unit_fixes.yaml with the arithmetic.",
            "owner": "Kitchen",
            "evidence": [f"largest line: {big[2]} {big[3]} of {big[0]}",
                         f"batch costs ${f['batch_cost']:,.2f} as it stands"]
                        + ([f"drawn on by {', '.join(consumers)}"] if consumers else
                           ["nothing draws on it as a sub-recipe"]),
            "derived": True,
            "source": "modules/recipes/book_reconcile.batch_overflow()"
                      " over data/lightspeed_recipes_costed.json",
        })
    return out


def price_conflict_flags(recipes, sold, window="") -> list:
    """Our rate and Lightspeed's rate for one product, 2x-50x apart.

    `impact_per_year` IS NULL ON EVERY ONE OF THESE, on purpose, and the spread
    goes in the evidence instead. A spread is not an under-cost: we do not know
    which of the two prices is wrong, only that the recipes cost off ours, so
    the figure says how much recipe cost rides on the answer. The panel's
    headline reads "$X a year of under-cost that has actually been measured";
    adding a number that might move either way would make that sentence false.
    It still sorts by money, because the spread sets the severity.
    """
    twins = {m[1] for _ratio, members in
             twin_identity_conflicts(cost_book_latest(), bo_product_names())
             for m in members}
    out = []
    for f in book_reconcile.price_conflicts(recipes, _adjudicated_ids(), twins):
        per_year, parts = 0.0, []
        for name, r in recipes.items():
            for ln in (r.get("ingredients") or []):
                if ln.get("ref") != f["ref"] or ln.get("kind") != "id":
                    continue
                try:
                    gap = abs(float(ln.get("eff_cost") or 0) - float(ln.get("ls_cost") or 0))
                except (TypeError, ValueError):
                    continue
                q = sold.get(_nrm(name), 0.0)
                if gap > 0 and q > 0:
                    per_year += gap * q
                    parts.append(f"{name} ${gap:,.4f}/serve x {q:,.0f}")
        spread = round(per_year, 2) if per_year > 0 else None
        dearer = "above" if f["ours_is_dearer"] else "below"
        out.append({
            "id": "price-conflict-" + _slug(f["ingredient"]),
            "category": "price_conflict",
            "severity": "high" if (spread or 0) >= 500 else "medium",
            "subject": f["ingredient"],
            "subject_kind": "ingredient",
            "what_is_wrong": (
                f"Our book holds this at ${f['our_rate']:,.6f}/{f['unit']}, "
                f"{f['ratio']:g}x {dearer} the ${f['ls_rate']:,.6f} Lightspeed's own "
                f"recipe cost implies. Both cannot be this product's price."),
            "why_it_matters": "Every recipe costs off ours, so if ours is the "
                              "wrong one the error is already in the P&L. The "
                              "median ingredient in this book agrees with "
                              "Lightspeed to 0.1% and 380 of 461 agree within "
                              "10%, so a 2x gap is not a price that moved.",
            "question": "Which of the two prices is this product's real price?",
            "impact_per_year": None,
            "impact_basis": None,
            "action": "Check one invoice for this product. Then correct whichever"
                      "side is wrong — Back Office if Lightspeed's, "
                      "data/pack_overrides.yaml if ours read the pack wrong — and "
                      "record the answer in data/product_map.csv so it is never "
                      "asked again.",
            "owner": "Zak (which price is real) then Dev",
            "evidence": ([f"${spread:,.0f} a year of recipe cost rides on the "
                          f"answer — a SPREAD, not a loss, which is why this flag "
                          f"states no impact: {'; '.join(parts)}, over the 52 "
                          f"weeks {window}"] if spread else [])
                        + [f"used by {', '.join(f['recipes'])}",
                           f"those lines cost ${f['our_line_total']:,.2f} on our book "
                           f"and ${f['ls_line_total']:,.2f} on Lightspeed's"],
            "derived": True,
            "source": "modules/recipes/book_reconcile.price_conflicts() over"
                      " data/lightspeed_recipes_costed.json, minus every"
                      " ProductID already adjudicated in data/product_map.csv",
        })
    return out


# --------------------------------------------------------------------------
# 7. units the feed cannot mean — modules/recipes/feed_defects.py
# --------------------------------------------------------------------------

def _ingredients() -> list:
    """data/ingredients.json, or [] if this build has not generated it yet.

    build_site.py runs build_ingredients.py BEFORE this script for exactly that
    reason. Missing is not an error: the family simply does not appear, the same
    way a missing rollup means no annual window. Half a feed is worse than an
    absent one.
    """
    f = DATA / "ingredients.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8-sig")).get("ingredients") or []


def _cost_riding_on(recipes, ref, sold) -> tuple:
    """-> (dollars of recipe cost a year that flow through this ingredient, how).

    NOT an under-cost and never reported as one: it is what the recipes charge
    today through a record whose unit nobody can read. It sizes the question —
    which is the whole reason the panel can rank these — without asserting that
    a dollar of it is lost. Same discipline as price_conflict.
    """
    total, parts = 0.0, []
    for name, r in recipes.items():
        if r.get("is_prep"):
            continue          # a prep is not sold; whatever draws on it is
        for ln in (r.get("ingredients") or []):
            if ln.get("kind") != "id" or ln.get("ref") != ref:
                continue
            eff = money(ln.get("eff_cost"))
            q = sold.get(_nrm(name), 0.0)
            if eff > 0 and q > 0:
                total += eff * q
                parts.append(f"{name} ${eff:,.4f}/serve x {q:,.0f}")
    return round(total, 2), parts


# A g/mL recipe line against a by-the-piece purchase has two readings, and only
# one of them is ever sane. "0.083 ml" of a twin pack IS a fraction of a pack, so
# "did you mean 0.083 packs?" is a fair question. "900 g" of shallots is not 900
# bunches — nobody puts 900 bunches in a broth — and asking it that way makes the
# panel look like it cannot count, while the real gap (how much IS a bunch?) goes
# unasked. So offer the swap only when the number survives being read as a count;
# otherwise ask for the conversion, which is the thing actually missing.
COUNT_SWAP_MAX = 12


def _short_desc(desc: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", str(desc or "")).strip() or str(desc or "")


def _swap_is_sane(worst) -> bool:
    try:
        q = float(str(worst.get("qty")))
    except (TypeError, ValueError):
        return False
    return 0 < q <= COUNT_SWAP_MAX


def _unit_question(f, worst) -> str:
    if _swap_is_sane(worst):
        return (f"In {worst['recipe']}, is \u201c{worst['qty']} {worst['unit']}\u201d "
                f"meant to be {worst['qty']} {f['pack_unit']}?")
    return (f"One {f['pack_unit']} of {_short_desc(f['description'])} is how many "
            f"{worst['unit']}? ({worst['recipe']} takes {worst['qty']} "
            f"{worst['unit']}, bought at ${f['rate']:,.2f} a {f['pack_unit']}.)")


def _unit_action(f, worst) -> str:
    if _swap_is_sane(worst):
        return ("Correct the unit on these lines in Lightspeed Produce (the "
                "quantity stays as it is), or, where the arithmetic proves the "
                "unit is a typo, add the entry to "
                "data/recipe_line_unit_fixes.yaml with the proof.")
    return (f"Weigh one {f['pack_unit']} and record it as the pack size, so the "
            f"g/mL lines cost off a real conversion instead of an assumed one. "
            f"Correcting the unit in Produce is NOT the fix here — the lines "
            f"genuinely are in {worst['unit']}.")


def _named_rate_plausible(f) -> bool:
    """Is the rate sane for the unit the NAME declares?

    "can" is Lightspeed's default pack unit, not a fact about the product: a
    cauliflower at $3.20, a Turkish bread at $1.50, potato at $3.60/kg are
    obviously priced per each / per kg and the word "can" is just a label nobody
    changed. Asking a human "is one can of Cauliflower one ea?" is noise — the
    number already answers it.

    So judge the number against the bounds suppliers.yaml already declares (not a
    threshold invented here). Inside the band -> the label is cosmetic, fix it in
    Back Office, do not ask. Outside it -> the rate is NOT the unit the name
    claims (Pears at $0.65/kg is under any real kg price; Ponzu at $0.0153 is a
    per-mL rate on a 360 mL bottle) and that is worth a human's judgement.
    """
    b = _bounds()
    u = (f.get("name_unit") or "").strip().lower()
    band = b.get("per_kg" if u in ("kg", "g") else "per_unit") or {}
    lo, hi = money(band.get("min")), money(band.get("max"))
    r = float(f.get("rate") or 0)
    if not (lo or hi):
        return False
    return (not lo or r >= lo) and (not hi or r <= hi)


def feed_defect_flags(recipes, sold, window="") -> list:
    """One flag per record (or per line group) whose UNIT cannot be that
    product's unit.

    `impact_per_year` IS NULL ON EVERY ONE, and that is not timidity: we do not
    know which side of the contradiction is wrong, so we cannot say what it
    costs. What we can say exactly is how much recipe cost is drawn through the
    bad record each year, and that goes in `cost_at_stake_per_year` — a
    separate field with a separate word on the panel, so it can never be added
    to the headline "under-cost that has actually been measured".
    """
    ings = _ingredients()
    if not ings:
        return []
    out = []

    for f in feed_defects.pack_unit_contradicts_name(ings):
        stake, parts = _cost_riding_on(recipes, f["id"], sold)
        plausible = _named_rate_plausible(f)
        container = f["kind"] == "container"
        out.append({
            "id": "feed-unit-" + _slug(f["description"]),
            "category": "feed_defect",
            "severity": "low" if plausible else
                        ("high" if stake >= FEED_DEFECT_HIGH_AT_STAKE else "medium"),
            "subject": f["description"],
            "subject_kind": "ingredient",
            "what_is_wrong": (
                f"Label only: the pack unit reads \u201c{f['pack_unit']}\u201d because "
                f"that is Lightspeed's default, not because it comes in one. "
                f"${f['rate']:,.4f} is a sane price for one {f['name_unit']}, so the "
                f"cost is right and only the word is wrong."
                if plausible else
                f"The name says this is sold by the {f['name_unit']} and the cost "
                f"book prices it per {f['pack_unit']}, at ${f['rate']:,.4f} per "
                f"{f['pack_unit']}."
                + (" A cauliflower, a loaf of bread and an egg do not come in a "
                   "can \u2014 this is Lightspeed's default pack unit sitting on a "
                   "produce line." if container else
                   " Those are not the same kind of measurement, so the rate is "
                   "in a unit the product does not have.")),
            "why_it_matters": "Every recipe line and every builder rate drawn off "
                              "this record inherits the unit. It is the same "
                              "family as the whole chicken logged as '0.5 ml' and "
                              "the $10,530 Peking Sauce batch — the dollar figure "
                              "can look right for years while the unit makes it "
                              "impossible to check.",
            "question": None if plausible else (
                f"${f['rate']:,.4f} is not a real price for one {f['name_unit']} of "
                f"\u201c{f['description']}\u201d. Is this rate per "
                f"{f['name_unit']}, or for a whole box?"),
            "impact_per_year": None,
            "impact_basis": None,
            "cost_at_stake_per_year": None if plausible else (stake or None),
            "cost_at_stake_basis": (
                None if plausible else
                (f"${stake:,.2f} of recipe cost a year is drawn through this record "
                 f"over the 52 weeks {window}. It is what rides on the answer, NOT "
                 f"a loss — the quantity may well be right. Lines: "
                 f"{'; '.join(parts)}." if stake else None)),
            "action": f"Set the pack unit on {f['id']} in Lightspeed Back Office to "
                      f"the unit the invoice states, then re-run the invoice "
                      f"bridge. If the pack really does hold several, record the "
                      f"count in data/pack_overrides.yaml instead.",
            "owner": ("Dev / Back office tidy \u2014 no decision needed" if plausible
                      else "Zak (is this per unit or per box?) then Back office"),
            "evidence": [f"{f['id']} ({f['supplier']}): ${f['rate']:,.4f} per "
                         f"{f['pack_unit']}, name declares [{f['name_unit']}]"]
                        + ([f"used by {len(parts)} sold recipe(s)"] if parts else
                           ["no sold recipe draws on it today"]),
            "derived": True,
            "source": "modules/recipes/feed_defects.pack_unit_contradicts_name()"
                      " over data/ingredients.json",
        })

    for f in feed_defects.product_priced_in_two_worlds(ings):
        stake = 0.0
        parts: list = []
        for m in f["members"]:
            st, pt = _cost_riding_on(recipes, m["id"], sold)
            stake += st
            parts += pt
        hi, lo = f["members"][0], f["members"][-1]
        two_dims = f["kind"] == "two_dimensions"
        out.append({
            "id": "feed-two-worlds-" + _slug(f["stem"]),
            "category": "feed_defect",
            "severity": "high" if (two_dims or stake >= FEED_DEFECT_HIGH_AT_STAKE)
                        else "medium",
            "subject": hi["description"],
            "subject_kind": "ingredient",
            "what_is_wrong": (
                f"The cost book holds this product at ${hi['rate']:,.4f} per "
                f"{hi['pack_unit']} and at ${lo['rate']:,.4f} per {lo['pack_unit']} "
                f"— {f['ratio']:,.1f}x apart."
                + (" One is a weight and the other a volume, and it cannot be both."
                   if two_dims else
                   " One prices the container and the other prices the piece, so "
                   "anything reaching the first charges a whole pack for one of "
                   "them.")),
            "why_it_matters": "Both records are internally consistent, so no rule "
                              "that checks a price against its own history can see "
                              "it. Which one a recipe reaches is decided by which "
                              "ProductID somebody happened to pick.",
            "question": "Which of these two records is this product, and what "
                        "happens to the other one?",
            "impact_per_year": None,
            "impact_basis": None,
            "cost_at_stake_per_year": round(stake, 2) or None,
            "cost_at_stake_basis": (
                f"${stake:,.2f} of recipe cost a year flows through these records "
                f"over the 52 weeks {window} — what rides on the answer, not a "
                f"loss. Lines: {'; '.join(parts)}." if stake else None),
            "action": "Check one invoice. Retire or correct the wrong record, and "
                      "record the answer in data/product_map.csv so it is never "
                      "asked again.",
            "owner": "Zak (which record is real) then Dev",
            "evidence": [f"{m['id']} ({m['supplier']}): ${m['rate']:,.4f} per "
                         f"{m['pack_unit']} \u2014 \u201c{m['description']}\u201d"
                         for m in f["members"]],
            "derived": True,
            "source": "modules/recipes/feed_defects.product_priced_in_two_worlds()"
                      " over data/ingredients.json",
        })

    for f in feed_defects.line_unit_contradicts_pack(recipes, ings):
        stake, parts = _cost_riding_on(recipes, f["id"], sold)
        worst = f["lines"][0]
        # A LABEL problem is not a decision. When the quantity reads as a sane
        # fraction of a pack (0.083 of a twin pack, 0.22 of a bunch) the cost is
        # already right and the only thing wrong is the unit word — there is
        # nothing for Zak to judge, and dressing it with "$767/yr at stake" makes
        # a tidy-up look like a loss. Those drop to low, lose the dollar figure
        # (nothing is at stake — the cost is not in dispute) and say so plainly.
        # What STAYS prominent is the genuinely unanswerable half: nobody has
        # said what a bunch weighs, so a g/mL line cannot be costed honestly.
        cosmetic = _swap_is_sane(worst)
        out.append({
            "id": "feed-line-unit-" + _slug(f["description"]),
            "category": "feed_defect",
            "severity": "low" if cosmetic
                        else ("high" if stake >= FEED_DEFECT_HIGH_AT_STAKE else "medium"),
            "subject": f"{f['description']} \u2014 {f['line_count']} line(s) in the wrong unit",
            "subject_kind": "ingredient",
            "what_is_wrong": (
                f"Label only, nothing at stake: {f['line_count']} line(s) measure "
                f"this in {worst['unit']} when it is bought by the {f['pack_unit']}. "
                f"The quantity and the cost are already right ({worst['recipe']} "
                f"takes {worst['qty']} of a {f['pack_unit']}) — the unit word is "
                f"the only thing wrong."
                if cosmetic else
                f"This is bought by the {f['pack_unit']} at ${f['rate']:,.4f}, and "
                f"{f['line_count']} recipe line(s) measure it in g or mL — "
                f"{worst['recipe']} takes \u201c{worst['qty']} {worst['unit']}\u201d of it."),
            "why_it_matters": "The QUANTITY is usually right and the unit is "
                              "meaningless, which is the worst combination: the "
                              "cost is correct, so nothing fails, and the line "
                              "reads as an error to every human who sees it and "
                              "gets raised again. American Standard Burger's "
                              "lettuce is 0.083 of a twin pack ($0.23, exactly "
                              "what the book charges) labelled 'ml'.",
            "question": None if cosmetic else _unit_question(f, worst),
            "impact_per_year": None,
            "impact_basis": None,
            "cost_at_stake_per_year": None if cosmetic else (stake or None),
            "cost_at_stake_basis": (
                None if cosmetic else
                (f"${stake:,.2f} of recipe cost a year runs through these lines over "
                 f"the 52 weeks {window}. The cost is not disputed — the unit is. "
                 f"Lines: {'; '.join(parts)}." if stake else None)),
            "action": _unit_action(f, worst),
            "owner": ("Dev / Produce tidy \u2014 no decision needed" if cosmetic
                      else "Kitchen \u2014 weigh one and record the pack size"),
            "evidence": [f"{l['recipe']}: {l['qty']} {l['unit']} = ${l['eff_cost']:,.4f}"
                         + (" (batch)" if l["is_prep"] else "")
                         for l in f["lines"]],
            "derived": True,
            "source": "modules/recipes/feed_defects.line_unit_contradicts_pack()"
                      " over data/lightspeed_recipes_costed.json"
                      " + data/ingredients.json",
        })
    return out


# --------------------------------------------------------------------------
# 8. declared decisions — the numbers inside them still get looked up
# --------------------------------------------------------------------------

def _latest_any() -> dict:
    """id -> (observed_on, cost_per_unit, unit) for EVERY id in the cost book.

    audit_book.cost_book_latest() answers the same question for Lightspeed
    ProductIDs only, because that is all its twin rule compares. A declared flag
    quotes supplier ids too — "ILG bill the Premium keg at $212.44" is the whole
    force of the Alehouse question — and looking those up as Lightspeed ids
    returned nothing, which rendered as "not in the cost book today" beside a
    price the file was asserting. Wrong, and quietly so.
    """
    out: dict = {}
    for r in csv.DictReader(COSTS.open(encoding="utf-8-sig")):
        c = money(r.get("cost_per_unit"))
        if c <= 0:
            continue
        iid, d = r["ingredient"], r["observed_on"]
        if iid not in out or d >= out[iid][0]:
            out[iid] = (d, c, r.get("unit") or "")
    return out


def _pack_sizes() -> dict:
    """lightspeed:<id> -> (pack_qty, pack_unit), so a per-ml rate can be quoted
    as the whole keg or bottle a human recognises."""
    out: dict = {}
    for r in csv.DictReader(COGS.open(encoding="utf-8-sig")):
        if (r.get("supplier") or "") != "Lightspeed":
            continue
        q, u = money(r.get("pack_qty")), (r.get("pack_unit") or "").strip().lower()
        if q > 1 and u:
            out.setdefault("lightspeed:" + (r.get("supplier_code") or "").strip(), (q, u))
    return out


def declared_flags(items) -> list:
    """Static flags from the yaml, with their quoted prices re-read from disk.

    `verify` names cost-book ids the flag talks about. Their CURRENT rate is
    attached as evidence, so a decision note can never go on quoting a price
    that has moved since somebody typed it — the exact way a handoff file rots.
    """
    latest = _latest_any()
    packs = _pack_sizes()
    out = []
    for s in items:
        ev = []
        for iid in (s.get("verify") or []):
            got = latest.get(iid)
            if not got:
                ev.append(f"{iid}: not in the cost book today")
                continue
            whole = ""
            pk = packs.get(iid)
            if pk and pk[1] == got[2]:
                whole = f" = ${got[1] * pk[0]:,.2f} per {pk[0]:g} {pk[1]}"
            ev.append(f"{iid}: ${got[1]:,.4f}/{got[2]}{whole} as at {got[0]}")
        if s.get("evidence_note"):
            ev.append(s["evidence_note"])
        out.append({
            "id": s["id"],
            "category": s.get("category") or "decision",
            "severity": s.get("severity") or "medium",
            "subject": s["subject"],
            "subject_kind": "config",
            "what_is_wrong": s["what_is_wrong"],
            "why_it_matters": s["why_it_matters"],
            "impact_per_year": None,
            "impact_basis": None,
            "action": s["action"],
            "owner": s.get("owner") or "Zak",
            "evidence": ev,
            "derived": False,
            "source": "data/cost_book_flags.yaml (declared — no data can settle it)"
                      + ("; prices re-read from data/costs.csv" if s.get("verify") else ""),
        })
    return out


# --------------------------------------------------------------------------

CATEGORIES = [
    {"key": "cook_loss", "title": "Yields we have never measured",
     "why": "Every one of these prices a cooked portion at the raw rate, so the "
            "dish under-costs. A kitchen scale closes them."},
    {"key": "structure", "title": "Lines that disagree with the rest of the book",
     "why": "Not arithmetic — internal consistency. A component every sibling "
            "carries at the same quantity, missing from one; a pack the book "
            "takes a twelfth of, taken whole. This is the class Zak has twice "
            "caught by eye and no other check sees."},
    {"key": "batch_yield", "title": "Batches that hold more than they make",
     "why": "The name declares a yield and the lines add up to several times it. "
            "Everything downstream divides by that yield."},
    {"key": "price_conflict", "title": "One product, two prices",
     "why": "Our invoice-fed rate and Lightspeed's own rate for the same stock "
            "item, 2x-50x apart, with nothing in data/product_map.csv settling "
            "it. The recipes cost off ours."},
    {"key": "feed_defect", "title": "Units the feed cannot mean",
     "why": "A lemon priced per millilitre, a cauliflower priced per can, a "
            "burger line that takes '0.083 ml' of a twin pack. The dollar figure "
            "can be right while the unit is nonsense — which is why these get "
            "re-raised by eye every few weeks and never close."},
    {"key": "no_recipe", "title": "Sold, but no costed recipe",
     "why": "The P&L falls through to Lightspeed for these, and where Lightspeed "
            "has no cost either the revenue books at 100% GP."},
    {"key": "back_office", "title": "Back Office edits",
     "why": "Lightspeed holds one stock item twice at two prices. A recipe costs "
            "off whichever copy its venue was built from."},
    {"key": "bad_seed", "title": "Seed rows an invoice has overtaken",
     "why": "Harmless today because the invoice wins — and a wrong fallback for "
            "the next recipe that reaches the product before an invoice does."},
    {"key": "config", "title": "Config the pipeline is waiting on",
     "why": "A real pack the invoice rules cannot express, so it goes to review "
            "on every delivery."},
    {"key": "decision", "title": "Decisions pending",
     "why": "No data in this repo can settle these. They need a person."},
]


def build() -> dict:
    import yaml
    spec = yaml.safe_load(DECLARED.read_text(encoding="utf-8-sig")) or {}
    recipes = json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]
    sold, window = annual_units()

    flags: list = []
    flags += cook_loss_flags(spec.get("cook_loss") or {}, recipes, sold, window)
    gaps, exempted = no_recipe_flags(recipes, spec.get("exempt") or [])
    flags += gaps
    flags += twin_flags(recipes)
    flags += bad_seed_flags()
    flags += validator_config_flags()
    flags += structure_flags(recipes, sold, window)
    flags += batch_yield_flags(recipes)
    flags += price_conflict_flags(recipes, sold, window)
    flags += feed_defect_flags(recipes, sold, window)
    flags += declared_flags(spec.get("declared") or [])

    flags.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9),
                              -(f.get("impact_per_year") or 0),
                              -(f.get("revenue_13wk") or 0),
                              -(f.get("cost_at_stake_per_year") or 0),
                              f["id"]))

    known = [f["impact_per_year"] for f in flags if f.get("impact_per_year")]
    by_cat: dict = defaultdict(int)
    by_sev: dict = defaultdict(int)
    for f in flags:
        by_cat[f["category"]] += 1
        by_sev[f["severity"]] += 1

    return {
        "generated_at": date.today().isoformat(),
        "generated_by": "scripts/build_cost_book_flags.py",
        "note": "What the cost book still needs from a human. Derived from data/ "
                "on every build; only the questions, the decisions and the "
                "permanent exemptions are declared, in data/cost_book_flags.yaml.",
        "assumptions": {
            "cook_loss_yield": (spec.get("cook_loss") or {}).get("assumed_yield"),
            "cook_loss_yield_note":
                "Used ONLY to size the yield questions. It is applied to no cost "
                "in this system — every recipe still prices its raw weight.",
            "annual_window": window or "no Sales Product API rollup found",
            "coverage_window": "13 weeks of data/products_weekly.csv",
        },
        "counts": {
            "total": len(flags),
            "by_severity": dict(sorted(by_sev.items(),
                                       key=lambda x: SEVERITY_RANK.get(x[0], 9))),
            "by_category": {c["key"]: by_cat.get(c["key"], 0) for c in CATEGORIES},
            "with_a_dollar_figure": len(known),
        },
        "known_impact_per_year": round(sum(known), 2) if known else 0.0,
        "categories": CATEGORIES,
        "flags": flags,
        "exempt": exempted,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="also print the flags")
    args = ap.parse_args()

    payload = build()
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"{payload['counts']['total']} flags "
          f"({', '.join(f'{k} {v}' for k, v in payload['counts']['by_severity'].items())})"
          f" -> {OUT.relative_to(ROOT)}")
    if payload["known_impact_per_year"]:
        print(f"  ${payload['known_impact_per_year']:,.0f}/yr of measurable "
              f"under-cost across {payload['counts']['with_a_dollar_figure']} of them")
    if args.print:
        for f in payload["flags"]:
            imp = (f"${f['impact_per_year']:,.0f}/yr" if f.get("impact_per_year")
                   else (f"${f['revenue_13wk']:,.0f} 13wk rev" if f.get("revenue_13wk")
                         else "impact unknown"))
            print(f"\n  [{f['severity']:6}] {f['subject']}  ({imp})")
            print(f"      {f['what_is_wrong']}")
            print(f"      -> {f['action']}  [{f['owner']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
