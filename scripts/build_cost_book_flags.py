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
read, at /recipes-book/. The two are deliberately separate: the auditor's job is
to fail CI while a SEVERE stands; this one's job is to be a work queue that a
human can finish.

DERIVED FIRST, DECLARED ONLY WHERE DATA CANNOT KNOW
---------------------------------------------------
Five of the six flag families are computed from data/ on every build:

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
    merged: dict = defaultdict(lambda: [0.0, set()])
    for (ven, nm, group), rev in gaps.items():
        e = merged[(ven, nm)]
        e[0] += rev
        if group:
            e[1].add(group)
    pats = [(re.compile(e["match"], re.I), e["reason"]) for e in exempt]
    flags, skipped = [], []
    for (ven, nm), (rev, groups) in sorted(merged.items(), key=lambda x: -x[1][0]):
        group = " / ".join(sorted(groups))
        hit = next((reason for rx, reason in pats if rx.search(nm)), None)
        if hit:
            skipped.append({"subject": nm, "venue": ven,
                            "revenue_13wk": round(rev, 2), "reason": hit})
            continue
        if rev < min_rev:
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
    # The dollar figure is the revenue at stake, NOT an under-cost: how much of
    # that revenue's cost is missing is exactly what having no recipe means we
    # cannot say. Stating it as an impact would be the guess this file refuses.
    return flags, sorted(skipped, key=lambda x: -x["revenue_13wk"])


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
# 6. declared decisions — the numbers inside them still get looked up
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
    flags += declared_flags(spec.get("declared") or [])

    flags.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9),
                              -(f.get("impact_per_year") or 0),
                              -(f.get("revenue_13wk") or 0),
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
