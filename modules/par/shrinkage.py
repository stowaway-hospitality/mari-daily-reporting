#!/usr/bin/env python3
"""
Par model v3 — shrinkage engine (stock counts -> a real variance channel).

Why this exists
---------------
`products_weekly.csv` only knows what was RUNG UP. Everything that leaves the
building without a sale — over-pour, spillage, breakage, staff drinks, comps,
theft — is invisible to it, so v2 hard-coded `drivers.variance_wk = 0.0` and the
par was, by construction, short by exactly the amount that goes missing.

The Lightspeed Back Office stock counts in `data/stock_counts/` close that gap.
Each export carries, per ProductID:

    Qty      system-expected on hand (last count + receipts - sales)
    Counted  what was physically on the shelf
    Variance Counted - Qty       NEGATIVE = stock vanished beyond recorded sales

The trap this module is built around: **the net variance lies.** On 28 Jul 2026
the gross negative was -$1,598 and the gross positive +$1,636, so the net reads
+$37 and the count looks clean. It is not clean — $1,598 of stock went missing
and $1,636 of miscounts/mis-scans happened to offset it. So we only ever take
the LOSS side (`max(0, -Variance)`) and never let a positive variance on one SKU
pay for a loss on another.

Robustness (counts are noisy, and partial counts are normal)
------------------------------------------------------------
* A SKU that is ABSENT from a count file was not counted. It is skipped for that
  period — never read as a zero loss, which would drag its estimate down.
* A whole count file whose variances are all ~zero is a template/pre-apply
  export (2026-07-14a is exactly this) and is dropped: it carries no signal, and
  keeping it would create a zero-length period against the real 07-14 count.
* The per-SKU rate is the MEDIAN across periods, not a mean and never a single
  pair, so one disastrous count cannot set a par.
* Low-observation SKUs are shrunk toward their reporting-group median rate
  (James-Stein style, weight n/(n+K)), so a SKU seen in two counts does not get
  to shout.
* The uplift is CAPPED at `MAX_UPLIFT_FRACTION` of modelled demand. Anything
  that hits the cap is flagged `capped` / "investigate" rather than silently
  inflating a par — a 200%-of-demand shrinkage figure is a data or process
  problem, not a stock level.

Money note: this module counts UNITS, never currency, so the repo's Decimal-money
rule is satisfied trivially. (Cost columns are read only for reporting.)
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import date

# ── tunables ────────────────────────────────────────────────────────────────
MAX_UPLIFT_FRACTION = 0.50   # shrinkage uplift may never exceed 50% of demand
MATERIAL_LOSS_WK = 0.05      # below this a "loss" is count rounding on a
                             # fractional bottle, not shrinkage. Used only to
                             # decide whether hitting the cap is worth a human's
                             # attention — the cap itself always applies.
SHRINK_K = 2.0               # James-Stein weight: n/(n+K) own, K/(n+K) group
MIN_PERIOD_WEEKS = 0.5       # shorter than this = same-day recount, not a period
MAX_PERIOD_WEEKS = 30.0      # longer than this = a gap, not a countable period
DEGENERATE_VARIANCE_UNITS = 5.0   # a file with less gross |variance| than this
                                  # is a pre-apply template export, not a count
GROUP_MIN_OBS = 3            # group median needs this many SKU observations

_DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})([a-z]?)_export\.csv$")


# ── parsing ─────────────────────────────────────────────────────────────────
def _f(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def parse_count_file(path: str) -> dict:
    """One stock-count export -> {'date','tag','path','rows':{pid:{...}},...}."""
    m = _DATE_RE.search(os.path.basename(path))
    if not m:
        raise ValueError(f"stock count filename carries no date: {path}")
    rows = {}
    gross_loss = gross_gain = 0.0
    cost_loss = cost_gain = 0.0
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            pid = str(r.get("ID", "")).strip()
            if not pid:
                continue
            var = _f(r.get("Variance"))
            cvar = _f(r.get("CostVariance"))
            rows[pid] = {
                "product": (r.get("ProductName") or "").strip(),
                "qty": _f(r.get("Qty")),
                "counted": _f(r.get("Counted")),
                "variance": var,
                "cost_variance": cvar,
                "cost": _f(r.get("Cost")),
            }
            if var < 0:
                gross_loss += -var
            else:
                gross_gain += var
            if cvar < 0:
                cost_loss += -cvar
            else:
                cost_gain += cvar
    return {
        "date": date.fromisoformat(m.group(1)),
        "tag": m.group(2) or "",
        "path": path,
        "rows": rows,
        "gross_loss_units": round(gross_loss, 2),
        "gross_gain_units": round(gross_gain, 2),
        "gross_loss_cost": round(cost_loss, 2),
        "gross_gain_cost": round(cost_gain, 2),
        "net_cost": round(cost_gain - cost_loss, 2),
    }


def load_counts(data_dir: str = "data", venue: str = "stow") -> list:
    """Every usable stock count, oldest first, one per date.

    Only Stowaway counts exist today (`stock_count_stowaway_bar_*`). HG has no
    counts, so HG simply gets no shrinkage channel and behaves as it did in v2.
    """
    if venue != "stow":
        return []
    pat = os.path.join(data_dir, "stock_counts", "stock_count_stowaway_bar_*_export.csv")
    files = sorted(glob.glob(pat))
    parsed = [parse_count_file(p) for p in files]

    # Drop template/pre-apply exports (Counted == Qty for essentially every row).
    parsed = [c for c in parsed
              if (c["gross_loss_units"] + c["gross_gain_units"]) >= DEGENERATE_VARIANCE_UNITS]

    # One count per date: if a date still has two live exports, keep the one
    # carrying the most signal (the other is a partial/aborted pass).
    by_date = {}
    for c in parsed:
        prev = by_date.get(c["date"])
        if prev is None or (c["gross_loss_units"] + c["gross_gain_units"]) > (
                prev["gross_loss_units"] + prev["gross_gain_units"]):
            by_date[c["date"]] = c
    return [by_date[d] for d in sorted(by_date)]


def build_periods(counts: list) -> list:
    """Consecutive count pairs -> loss periods.

    A count's Variance is measured against a system Qty that was itself last
    trued-up at the previous count, so the variance recorded at d2 IS the loss
    over (d1, d2]. `losses` only contains SKUs that were actually counted at d2.
    """
    periods = []
    for prev, cur in zip(counts, counts[1:]):
        weeks = (cur["date"] - prev["date"]).days / 7.0
        if weeks < MIN_PERIOD_WEEKS or weeks > MAX_PERIOD_WEEKS:
            continue
        losses = {}
        for pid, row in cur["rows"].items():
            losses[pid] = max(0.0, -row["variance"])
        periods.append({
            "start": prev["date"],
            "end": cur["date"],
            "weeks": weeks,
            "losses": losses,
            "names": {pid: r["product"] for pid, r in cur["rows"].items()},
        })
    return periods


# ── estimation ──────────────────────────────────────────────────────────────
def _median(xs):
    return statistics.median(xs) if xs else 0.0


def estimate(
    periods: list,
    sku_of_pid,
    weeks,
    consumption_by_sku,
    rg_of_sku,
    max_uplift_fraction: float = MAX_UPLIFT_FRACTION,
):
    """Per-par-SKU shrinkage estimate.

    periods              from build_periods()
    sku_of_pid(pid)      Lightspeed ProductID -> par SKU name (or None)
    weeks                the ordered week_ending list the consumption series use
    consumption_by_sku   {sku: [units per week]} — the modelled (rung-up) demand
    rg_of_sku(sku)       par SKU -> reporting group, for the group prior

    Returns {sku: {...}} with `loss_per_week`, `loss_fraction`, `n_periods`,
    `method`, `capped`.
    """
    widx = {w: i for i, w in enumerate(weeks)}

    def consumed_between(sku, d1, d2):
        """Modelled consumption over (d1, d2] — weeks are labelled by the Sunday."""
        series = consumption_by_sku.get(sku)
        if not series:
            return 0.0
        tot = 0.0
        for w, i in widx.items():
            wd = date.fromisoformat(w)
            if d1 < wd <= d2:
                tot += series[i]
        return tot

    # Per-SKU observations: (loss units, period weeks, modelled consumption)
    obs = defaultdict(list)
    for p in periods:
        for pid, loss in p["losses"].items():
            sku = sku_of_pid(pid)
            if sku is None:
                continue
            used = consumed_between(sku, p["start"], p["end"])
            obs[sku].append((loss, p["weeks"], used))

    # Group priors, built from the per-SKU medians so one loud SKU can't set the
    # prior for its whole category.
    per_sku_rate, per_sku_frac = {}, {}
    for sku, rows in obs.items():
        per_sku_rate[sku] = _median([l / w for l, w, _u in rows if w > 0])
        fr = [l / u for l, _w, u in rows if u > 0]
        per_sku_frac[sku] = _median(fr) if fr else 0.0

    grp_rate, grp_frac = defaultdict(list), defaultdict(list)
    for sku in obs:
        rg = rg_of_sku(sku) or "?"
        grp_rate[rg].append(per_sku_rate[sku])
        grp_frac[rg].append(per_sku_frac[sku])
    grp_rate = {rg: _median(v) for rg, v in grp_rate.items() if len(v) >= GROUP_MIN_OBS}
    grp_frac = {rg: _median(v) for rg, v in grp_frac.items() if len(v) >= GROUP_MIN_OBS}
    all_rate = _median(list(per_sku_rate.values()))
    all_frac = _median(list(per_sku_frac.values()))

    out = {}
    for sku, rows in obs.items():
        n = len(rows)
        rg = rg_of_sku(sku) or "?"
        prior_rate = grp_rate.get(rg, all_rate)
        prior_frac = grp_frac.get(rg, all_frac)
        w_own = n / (n + SHRINK_K)
        rate = w_own * per_sku_rate[sku] + (1 - w_own) * prior_rate
        frac = w_own * per_sku_frac[sku] + (1 - w_own) * prior_frac

        # Modelled weekly demand over the counted span, for the cap.
        span_used = sum(u for _l, _w, u in rows)
        span_weeks = sum(w for _l, w, _u in rows)
        demand_wk = (span_used / span_weeks) if span_weeks > 0 else 0.0

        capped = False
        no_baseline = span_used <= 0
        if no_baseline:
            # Nothing was ever rung up for this SKU over the counted span, so
            # there is no denominator and the group's loss FRACTION means
            # nothing here — borrowing it just produces a fake "capped at 50% of
            # zero" flag. Report the rate, drop the fraction, say why.
            frac = 0.0
        cap = max_uplift_fraction * demand_wk
        if demand_wk > 0 and rate > cap:
            rate, capped = cap, True
        if frac > max_uplift_fraction:
            frac, capped = max_uplift_fraction, True

        out[sku] = {
            "product": sku,
            "reporting_group": rg,
            "loss_per_week": round(rate, 4),
            "loss_fraction": round(frac, 4),
            "raw_loss_per_week": round(per_sku_rate[sku], 4),
            "raw_loss_fraction": round(per_sku_frac[sku], 4),
            "modelled_demand_wk": round(demand_wk, 3),
            "n_periods": n,
            "shrink_weight_own": round(w_own, 3),
            "method": ("no demand baseline — rate only" if no_baseline else
                       ("median-of-periods + group shrink" if n < 4
                        else "median-of-periods")),
            "no_demand_baseline": no_baseline,
            "capped": capped,
            # Hitting the cap on a SKU that "loses" 0.02 of a bottle a week is
            # arithmetic, not a problem. Only ask a human to look when the raw
            # loss is material.
            "investigate": bool(capped and per_sku_rate[sku] >= MATERIAL_LOSS_WK),
        }
    return out


def build(data_dir: str, venue: str, weeks, consumption_by_sku, id2name,
          resolve_ingredient, rg_of_sku):
    """End-to-end: counts -> periods -> per-SKU estimate (+ the file summaries).

    `resolve_ingredient(bo_name)` is the par model's own name resolver, so a
    stock-count line lands on exactly the par SKU the model reasons about.
    """
    counts = load_counts(data_dir, venue)
    periods = build_periods(counts)

    def sku_of_pid(pid):
        bo_name = id2name.get(str(pid))
        if not bo_name:
            return None
        return resolve_ingredient(bo_name)

    est = estimate(periods, sku_of_pid, weeks, consumption_by_sku, rg_of_sku)
    summary = {
        "counts": [
            {
                "date": c["date"].isoformat(),
                "file": os.path.basename(c["path"]),
                "skus_counted": len(c["rows"]),
                "gross_loss_cost_ex_gst": c["gross_loss_cost"],
                "gross_gain_cost_ex_gst": c["gross_gain_cost"],
                "net_cost_ex_gst": c["net_cost"],
            }
            for c in counts
        ],
        "periods": [
            {"start": p["start"].isoformat(), "end": p["end"].isoformat(),
             "weeks": round(p["weeks"], 2), "skus": len(p["losses"])}
            for p in periods
        ],
    }
    return est, summary


def write_json(path: str, venue: str, est: dict, summary: dict, generated_at: str):
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "venue": venue,
        "source": "data/stock_counts/*.csv (Lightspeed Back Office stock counts)",
        "method": {
            "loss": "max(0, -Variance) per counted SKU per period — LOSSES ONLY; "
                    "positive variances never offset a loss (the net variance hides it)",
            "rate": "median across periods of loss_units / period_weeks",
            "fraction": "median across periods of loss_units / modelled consumption",
            "shrink": f"James-Stein toward the reporting-group median, weight n/(n+{SHRINK_K})",
            "cap": f"uplift capped at {MAX_UPLIFT_FRACTION:.0%} of modelled demand; "
                   f"capped SKUs are flagged investigate, not silently inflated",
            "absent": "a SKU missing from a count file was NOT counted — skipped, never zero",
        },
        "summary": {
            "n_counts": len(summary["counts"]),
            "n_periods": len(summary["periods"]),
            "n_skus_estimated": len(est),
            # "material" = at or above MATERIAL_LOSS_WK. Everything below that is
            # count rounding on a fractional bottle and would otherwise make the
            # headline read "218 SKUs are losing stock", which is not true.
            "material_loss_wk": MATERIAL_LOSS_WK,
            "n_skus_with_material_loss": sum(
                1 for v in est.values() if v["loss_per_week"] >= MATERIAL_LOSS_WK),
            "n_capped": sum(1 for v in est.values() if v["capped"]),
            "n_investigate": sum(1 for v in est.values() if v["investigate"]),
            "n_no_demand_baseline": sum(
                1 for v in est.values()
                if v["no_demand_baseline"] and v["loss_per_week"] >= MATERIAL_LOSS_WK),
            "median_loss_fraction": round(
                _median([v["loss_fraction"] for v in est.values()
                         if v["loss_fraction"] > 0
                         and v["loss_per_week"] >= MATERIAL_LOSS_WK]), 4),
        },
        "counts": summary["counts"],
        "periods": summary["periods"],
        "skus": [est[k] for k in sorted(est)],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return payload
