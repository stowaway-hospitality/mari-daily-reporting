#!/usr/bin/env python3
"""
ESTIMATED vs ACTUAL COGS — purchases minus consumption, per venue, per week.

    python3 scripts/build_cogs_variance.py            # report + data/cogs_variance.json
    python3 scripts/build_cogs_variance.py --quiet     # feed only

THE NUMBER THIS PRODUCES. COGS_ARCHITECTURE.md has named it since the file was
written and it has never been built:

    Xero purchases      what you BOUGHT
    our recipe cost     what you actually USED
    the difference      stock movement + waste + theft + variance

Both feeds already exist and are already on the dashboard, side by side, never
differenced. This differences them.

WHY IT WOULD HAVE BEEN GARBAGE UNTIL NOW. Differencing two numbers only tells you
something when both are trustworthy. Before 2026-08-10 the cost book had ILG
repack lines running 6x UNDER and soft drinks 12-24x OVER (see
HANDOFF_20260810_pack_agreement.md), so purchases-minus-consumption would have
been dominated by arithmetic error and read as waste. That is presumably part of
why this is step 6 of a 6-step plan that stopped at 5.

FOUR TRAPS, all of which will silently produce a plausible-looking wrong number.

1. GST. Xero purchases are ex-GST; our cost book is inc-GST, because invoice
   lines are `cost_per_unit_incl_gst`. Dividing by 1.1 is WRONG and not by a
   little: most food is GST-FREE in Australia, so the effective GST content of
   stock purchases measures

       beverages  9.09%   (all of it GST-bearing, as alcohol must be)
       food       0.2-0.8% (almost none of it)
       Marilyna's 0.00%

   A flat 1/11 would invent a ~5% variance at Stowaway, which is the same order
   as the real waste number. So the rate is MEASURED per venue and per Xero
   category off the invoices' own `tax_treatment`, using the same
   account_map classifier that codes them into Xero in the first place — so both
   sides of the subtraction are split the same way by construction.

2. LUMPINESS. You buy a case and pour it over three weeks. A single week's
   variance is mostly delivery timing, not waste. The weekly figure is reported
   because you need to see the lumps, but the 4-week rolling and cumulative
   figures are the ones that mean anything.

3. COVERAGE. A variance is only a waste number to the extent consumption is
   real. Where a product has no recipe its cost falls back to Lightspeed, and
   where revenue has no cost at all consumption is understated and the whole
   shortfall lands in this variance looking like waste. Harry Gatos has ~31% of
   revenue with no cost behind it, so its variance is inflated BY CONSTRUCTION.
   Every row therefore carries its coverage, and `trustworthy` is false below
   COVERAGE_FLOOR. A number you cannot read is worse than no number.

4. WEEK ALIGNMENT. Xero's week_ending is a Sunday on all 37 weeks present, which
   matches the repo's Monday-Sunday week indexed by its Sunday. Asserted, not
   assumed — a silent off-by-one day would smear every delivery into the wrong
   week and there is nothing in the output that would look wrong.

WHAT THIS IS NOT. It is not a GP figure and it is not an accounting
reconciliation. Purchases legitimately differ from consumption whenever stock on
hand moves, and nothing here knows the stock level. Read a persistent gap as a
question, not a loss.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.invoices.account_map import BAR_SUPPLIES, BEVERAGE, FOOD  # noqa: E402

XERO = ROOT / "data" / "xero_cogs_weekly.csv"
INVOICES = ROOT / "data" / "invoices"
OUT = ROOT / "data" / "cogs_variance.json"

VENUES = {"stow": ("stowaway", "Stowaway Bar"),
          "hg": ("harry_gatos", "Harry Gatos"),
          "mari": ("marilynas", "Marilyna's")}
HISTORY = {v: ROOT / "data" / f"{v}_daily_history.csv" for v in VENUES}

# Below this, consumption is too incomplete for the residual to be read as
# waste. Not a tuned threshold — it is the point at which the uncosted share of
# revenue is bigger than any waste number worth chasing.
COVERAGE_FLOOR = 60.0
ROLL = 4                      # weeks in the rolling window


def _f(x, default=0.0):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return default


def week_ending(d: date) -> str:
    """The Sunday that closes d's Monday-Sunday week."""
    return (d + timedelta(days=6 - d.weekday())).isoformat()


def gst_rates() -> dict:
    """(venue, food|bev|other) -> effective GST fraction of stock purchases.

    Measured off the invoices' own tax_treatment, categorised with the same
    account_map codes that put them into Xero. Trap 1 in the module docstring.
    """
    tot, gst = defaultdict(float), defaultdict(float)
    for p in sorted(INVOICES.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        inv = payload.get("invoice", {})
        venue = inv.get("venue") or ""
        coding = {c.get("description"): c.get("account_code")
                  for c in payload.get("xero_coding", {}).get("lines", [])}
        for ln in inv.get("lines", []):
            if ln.get("line_class") != "stock":
                continue
            acct = coding.get(ln.get("description"))
            cat = ("food" if acct == FOOD
                   else "bev" if acct in (BEVERAGE, BAR_SUPPLIES) else "other")
            amount = _f(ln.get("line_total_incl"))
            tot[(venue, cat)] += amount
            if (ln.get("tax_treatment") or "").lower() in ("gst", "wet"):
                gst[(venue, cat)] += amount / 11
    return {k: (gst[k] / tot[k] if tot[k] else 0.0) for k in tot}


def coverage_by_week() -> tuple[dict, dict]:
    """(venue, week_ending) -> revenue-weighted coverage %, and the share of the
    week's revenue that coverage could be MEASURED on.

    From the per-day files, because the history CSV's recipe_coverage_pct column
    is blank on almost every row. Weighted by revenue, not averaged: a quiet
    Monday's coverage should not count the same as a Saturday's.

    UNKNOWN IS NOT ZERO, and telling them apart matters because averaging in a
    false zero drags a real 80% week down to nothing. `cogs_source` separates
    them, per cogs_blend's own contract:

      source "lightspeed"                 -> coverage genuinely 0. Every product
                                             priced off Lightspeed. Counts.
      source "recipe_blend", coverage > 0 -> a real measurement. Counts.
      source "recipe_blend", coverage 0.0 -> IMPOSSIBLE by that contract, which
                                             returns "lightspeed" whenever
                                             coverage <= 0. These are day files
                                             written before that guard landed, so
                                             the 0.0 is an artefact and not a
                                             fact. Excluded, and counted as
                                             unmeasured so the gap is visible
                                             instead of silently averaged in.

    `measured_share` is reported alongside so a coverage figure standing on two
    days out of seven cannot be read as if it stood on all of them.
    """
    num, den, meas, total = (defaultdict(float), defaultdict(float),
                             defaultdict(float), defaultdict(float))
    for v in VENUES:
        for p in (ROOT / "data").glob(f"{v}_daily_*.json"):
            try:
                sales = json.loads(p.read_text(encoding="utf-8-sig")).get("sales") or {}
            except Exception:
                continue
            rev = _f(sales.get("revenue_ex_gst"))
            if rev <= 0:
                continue
            try:
                d = date.fromisoformat(p.stem.rsplit("_", 1)[1])
            except Exception:
                continue
            k = (v, week_ending(d))
            total[k] += rev
            cov, src = sales.get("recipe_coverage_pct"), (sales.get("cogs_source") or "")
            if cov is None:
                continue
            if src == "recipe_blend" and _f(cov) <= 0:
                continue                      # the artefact — see docstring
            num[k] += _f(cov) * rev
            den[k] += rev
            meas[k] += rev
    covered = {k: num[k] / den[k] for k in num if den[k]}
    share = {k: (meas[k] / total[k] * 100 if total[k] else 0.0) for k in total}
    return covered, share


def build() -> dict:
    rates = gst_rates()
    cover, measured = coverage_by_week()

    actual = {}
    for r in csv.DictReader(XERO.open(encoding="utf-8-sig")):
        we = (r.get("week_ending") or "").strip()
        if we:
            # Trap 4: assert the alignment rather than trusting it.
            if date.fromisoformat(we).weekday() != 6:
                raise ValueError(f"xero_cogs_weekly week_ending {we} is not a Sunday — "
                                 f"the weekly join would smear deliveries across weeks")
            actual[(r.get("venue"), we)] = r

    out = {"generated": date.today().isoformat(), "coverage_floor": COVERAGE_FLOOR,
           "roll_weeks": ROLL, "gst_rates": {f"{k[0]}|{k[1]}": round(v, 6)
                                             for k, v in sorted(rates.items())},
           "venues": {}}

    for v, (vlong, label) in VENUES.items():
        est = defaultdict(lambda: {"cogs": 0.0, "food": 0.0, "bev": 0.0,
                                   "rev": 0.0, "days": 0})
        path = HISTORY[v]
        if path.exists():
            for r in csv.DictReader(path.open(encoding="utf-8-sig")):
                try:
                    d = date.fromisoformat((r.get("date") or "").strip())
                except ValueError:
                    continue
                e = est[week_ending(d)]
                e["cogs"] += _f(r.get("cogs_dollars"))
                e["food"] += _f(r.get("food_cogs"))
                e["bev"] += _f(r.get("bev_cogs"))
                e["rev"] += _f(r.get("revenue_ex_gst"))
                e["days"] += 1

        f_rate = rates.get((vlong, "food"), 0.0)
        b_rate = rates.get((vlong, "bev"), 0.0)

        weeks = []
        for we in sorted(w for w in est if (v, w) in actual):
            e, a = est[we], actual[(v, we)]
            # Convert OUR estimate to ex-GST per category, so it meets Xero on
            # Xero's basis. Fall back to the blended venue rate when the
            # department split is absent (Mari carries no beverage split).
            if e["food"] or e["bev"]:
                cons_ex = e["food"] / (1 + f_rate) + e["bev"] / (1 + b_rate)
            else:
                cons_ex = e["cogs"] / (1 + max(f_rate, b_rate))
            purch_ex = _f(a.get("actual_cogs_ex_gst"))
            cov = cover.get((v, we))
            share = measured.get((v, we))
            weeks.append({
                "week_ending": we, "days": e["days"],
                "revenue_ex_gst": round(e["rev"], 2),
                "purchases_ex_gst": round(purch_ex, 2),
                "consumption_inc_gst": round(e["cogs"], 2),
                "consumption_ex_gst": round(cons_ex, 2),
                "variance": round(purch_ex - cons_ex, 2),
                "variance_pct_of_revenue": (round((purch_ex - cons_ex) / e["rev"] * 100, 2)
                                            if e["rev"] else None),
                "purchases_food_ex_gst": round(_f(a.get("food_ex_gst")), 2),
                "purchases_bev_ex_gst": round(_f(a.get("bev_ex_gst")), 2),
                "recipe_coverage_pct": round(cov, 1) if cov is not None else None,
                "coverage_measured_on_pct_of_revenue": round(share, 1) if share is not None else None,
                "trustworthy": bool(cov is not None and cov >= COVERAGE_FLOOR
                                    and share is not None and share >= COVERAGE_FLOOR),
            })

        # Trap 2: the rolling window is the number that means something.
        for i, w in enumerate(weeks):
            win = weeks[max(0, i - ROLL + 1):i + 1]
            pv = sum(x["purchases_ex_gst"] for x in win)
            cv = sum(x["consumption_ex_gst"] for x in win)
            rv = sum(x["revenue_ex_gst"] for x in win)
            w["rolling_weeks"] = len(win)
            w["rolling_purchases_ex_gst"] = round(pv, 2)
            w["rolling_consumption_ex_gst"] = round(cv, 2)
            w["rolling_revenue_ex_gst"] = round(rv, 2)
            w["rolling_variance"] = round(pv - cv, 2)
            w["rolling_variance_pct_of_revenue"] = round((pv - cv) / rv * 100, 2) if rv else None

        trusted = [w for w in weeks if w["trustworthy"]]
        def _tot(rows, key):
            return round(sum(r[key] for r in rows), 2)

        out["venues"][v] = {
            "label": label,
            "gst_rate_food": round(f_rate, 6), "gst_rate_bev": round(b_rate, 6),
            "weeks": weeks,
            "cumulative": {
                "weeks": len(weeks),
                "purchases_ex_gst": _tot(weeks, "purchases_ex_gst"),
                "consumption_ex_gst": _tot(weeks, "consumption_ex_gst"),
                "variance": round(_tot(weeks, "purchases_ex_gst")
                                  - _tot(weeks, "consumption_ex_gst"), 2),
                "revenue_ex_gst": _tot(weeks, "revenue_ex_gst"),
            },
            "cumulative_trustworthy_only": {
                "weeks": len(trusted),
                "purchases_ex_gst": _tot(trusted, "purchases_ex_gst"),
                "consumption_ex_gst": _tot(trusted, "consumption_ex_gst"),
                "variance": round(_tot(trusted, "purchases_ex_gst")
                                  - _tot(trusted, "consumption_ex_gst"), 2),
                "revenue_ex_gst": _tot(trusted, "revenue_ex_gst"),
            },
        }
    return out


def report(feed: dict) -> None:
    print("ESTIMATED vs ACTUAL COGS — purchases (Xero, ex GST) minus consumption "
          "(our recipe cost)\n")
    print("effective GST content of stock purchases, measured off the invoices:")
    for k, v in feed["gst_rates"].items():
        if v:
            print(f"   {k:<22} {v * 100:5.2f}%")
    print("   (a flat 1/11 would be 9.09% — food is largely GST-free, so a flat "
          "rate would invent a variance)")

    for v, d in feed["venues"].items():
        print(f"\n=== {d['label']} ===")
        print(f"   {'week':<12} {'purchases':>10} {'consumption':>12} {'variance':>10} "
              f"{'%rev':>7} {'roll4':>10} {'cov':>6}")
        for w in d["weeks"][-8:]:
            cov = f"{w['recipe_coverage_pct']:.0f}%" if w["recipe_coverage_pct"] is not None else "  ?"
            flag = "" if w["trustworthy"] else "  <- low coverage, not a waste number"
            print(f"   {w['week_ending']:<12} {w['purchases_ex_gst']:>10,.0f} "
                  f"{w['consumption_ex_gst']:>12,.0f} {w['variance']:>10,.0f} "
                  f"{(w['variance_pct_of_revenue'] or 0):>6.1f}% "
                  f"{(w['rolling_variance'] or 0):>10,.0f} {cov:>6}{flag}")
        c, t = d["cumulative"], d["cumulative_trustworthy_only"]
        pct = (c["variance"] / c["revenue_ex_gst"] * 100) if c["revenue_ex_gst"] else 0
        print(f"   ALL {c['weeks']:>2} wks: bought ${c['purchases_ex_gst']:,.0f}  "
              f"used ${c['consumption_ex_gst']:,.0f}  variance ${c['variance']:,.0f} "
              f"= {pct:.1f}% of revenue")
        if not any(w["recipe_coverage_pct"] is not None for w in d["weeks"]):
            print("   COVERAGE UNKNOWN for every week above. The per-day files that carry")
            print("   recipe_coverage_pct start 2026-07-06 and Xero's purchases stop")
            print("   2026-08-02, so the two barely overlap — and only 3-4 of 35 day files")
            print("   carry a usable figure at all (the rest say cogs_source recipe_blend")
            print("   beside coverage 0.0, which cogs_blend's own contract makes")
            print("   impossible). So read the variance as a QUESTION, not a loss: part of")
            print("   it is waste and part is revenue we cannot cost. Today's uncosted")
            print("   share is ~11% at Stow, ~31% at HG, ~5% at Mari.")
        if t["weeks"]:
            tp = (t["variance"] / t["revenue_ex_gst"] * 100) if t["revenue_ex_gst"] else 0
            print(f"   {t['weeks']:>6} wks above {feed['coverage_floor']:.0f}% coverage: "
                  f"variance ${t['variance']:,.0f} = {tp:.1f}% of revenue  <- the readable one")
        else:
            print(f"        no week reaches {feed['coverage_floor']:.0f}% coverage — "
                  f"the variance cannot be read as waste yet")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="write the feed, print nothing")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    feed = build()
    OUT.write_text(json.dumps(feed, indent=2), encoding="utf-8")
    if not a.quiet:
        report(feed)
        print(f"\n-> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
