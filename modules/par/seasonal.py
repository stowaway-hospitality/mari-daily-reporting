#!/usr/bin/env python3
"""
Par model v3 — week-of-year seasonality.

v2 forecast off a trailing 13-week window and a crude YoY ratio. On a beachside
venue in Sydney that is structurally wrong in both directions: it under-orders
into December (the trailing window is November) and over-orders into May (the
trailing window is the Easter/Anzac run). It also made the "spike floor" a
trailing-13-week max, which is not a seasonal statement at all.

This module builds a **week-of-year index** from `data/products_weekly.csv`
(~95 weeks ≈ two Decembers), so the forecast becomes

    forecast = level (recent, DESEASONALISED) × seasonal_index(target week)

How the index is built
----------------------
1. Deseasonalise-by-ratio: each week's value is divided by a centred 13-week
   moving average, which strips the trend and leaves the seasonal ratio.
2. Those ratios are pooled by ISO week-of-year across ALL years, then smoothed
   ±`SMOOTH_WEEKS` weeks-of-year so a single big Saturday doesn't become "week 7
   is a peak".
3. Normalised to mean 1.0 and clamped to [`INDEX_LO`, `INDEX_HI`] — with two
   years of history the tails are thin and an unclamped index will happily
   invent a 4× December.

Per-SKU where there is enough history (`MIN_WEEKS_FOR_SKU` observed weeks and
`MIN_UNITS_FOR_SKU` units), otherwise the SKU's **reporting-group** index. The
group fallback is the whole point for anything launched in the last year: a gin
that started selling in March has no December of its own, but "Gin" does.

The seasonal window used for the sanity floor is the SAME weeks-of-year across
every year (target ±`WINDOW_HALF`), not the trailing 13 weeks.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date

SMOOTH_WEEKS = 2          # ± this many weeks-of-year in the smoothing kernel
MA_WEEKS = 13             # centred moving average used to detrend
INDEX_LO = 0.70
INDEX_HI = 1.40
MIN_WEEKS_FOR_SKU = 60    # need most of a two-year history to trust a SKU index
MIN_UNITS_FOR_SKU = 40.0  # ...and enough volume that the ratios aren't noise
WINDOW_HALF = 2           # seasonal window = target woy ± this, all years
FLOOR_PERCENTILE = 0.90   # sanity floor = 90th pct week of the seasonal window

# ── level-shift defences (learned the hard way, 2026-08-09) ─────────────────
# Harry Gatos roughly tripled in a single week in July 2026 (306 -> 940 units).
# A ratio-to-moving-average index reads that step as SEASON, not growth: the
# weeks straddling the step get ratios near 0.45, everything else normalises up
# against them, and the first cut of this module handed EVERY HG SKU a 1.60
# index — the clamp ceiling, which is always the tell that an estimator has run
# away. These four guards bound that failure:
RATIO_CAP = 2.0           # a single week can be at most 2x (or 1/2) its local level
MIN_OBS_PER_WOY = 2       # a week-of-year must be observed in >=2 years to count
DISAGREE_MAX = 2.0        # ...and those years must agree within 2x, or it is a
                          # level shift / one-off, not a season -> drop the week
MIN_WOY_SUPPORT = 20      # fewer than this many surviving weeks-of-year = no index


def week_of_year(week_ending: str) -> int:
    """ISO week number of a Mon–Sun week labelled by its Sunday."""
    return date.fromisoformat(week_ending).isocalendar()[1]


def _centred_ma(series, k=MA_WEEKS):
    """Centred moving average; asymmetric (truncated) at the two ends.

    Dropping the edges outright was tried first and is too expensive on ~2 years
    of history: a 13-week half-window costs 12 weeks, and the weeks it costs are
    the most recent ones — which is exactly the part of the year the next order
    is being built for. The level-shift protection therefore lives in the
    cross-year DISAGREE_MAX guard below, not in the moving average.
    """
    half = k // 2
    n = len(series)
    out = []
    for i in range(n):
        chunk = series[max(0, i - half):min(n, i + half + 1)]
        out.append(sum(chunk) / len(chunk) if len(chunk) >= half + 1 else None)
    return out


def build_index(series, weeks):
    """[value], [week_ending] -> {week_of_year: index}. Empty dict if unusable."""
    if not series or sum(series) <= 0:
        return {}
    ma = _centred_ma(series)
    ratios = defaultdict(list)
    for v, m, w in zip(series, ma, weeks):
        if m and m > 0:
            r = v / m
            ratios[week_of_year(w)].append(max(1.0 / RATIO_CAP, min(RATIO_CAP, r)))
    if not ratios:
        return {}

    raw = {}
    for woy, rs in ratios.items():
        if len(rs) < MIN_OBS_PER_WOY:
            continue                      # seen in only one year — not a season
        lo, hi = min(rs), max(rs)
        if lo <= 0 or (hi / lo) > DISAGREE_MAX:
            continue                      # the years disagree: growth, not season
        raw[woy] = statistics.median(rs)
    if len(raw) < MIN_WOY_SUPPORT:
        return {}

    # Smooth ±SMOOTH_WEEKS over the week-of-year circle (53 weeks, wrapping).
    smoothed = {}
    for woy in range(1, 54):
        vals = []
        for d in range(-SMOOTH_WEEKS, SMOOTH_WEEKS + 1):
            n = ((woy - 1 + d) % 53) + 1
            if n in raw:
                vals.append(raw[n])
        if vals:
            smoothed[woy] = sum(vals) / len(vals)
    if not smoothed:
        return {}

    mean = sum(smoothed.values()) / len(smoothed)
    if mean <= 0:
        return {}
    return {woy: max(INDEX_LO, min(INDEX_HI, v / mean))
            for woy, v in smoothed.items()}


class SeasonalBook:
    """Per-SKU and per-reporting-group week-of-year indices, with fallback."""

    def __init__(self, weeks, consumption_by_sku, rg_of_sku):
        self.weeks = weeks
        self.sku_index = {}
        self.group_index = {}
        self.source = {}

        grouped = defaultdict(lambda: [0.0] * len(weeks))
        for sku, series in consumption_by_sku.items():
            rg = rg_of_sku(sku) or "?"
            g = grouped[rg]
            for i, v in enumerate(series):
                g[i] += v
        for rg, series in grouped.items():
            idx = build_index(series, weeks)
            if idx:
                self.group_index[rg] = idx

        for sku, series in consumption_by_sku.items():
            observed = sum(1 for v in series if v > 0)
            if observed >= MIN_WEEKS_FOR_SKU and sum(series) >= MIN_UNITS_FOR_SKU:
                idx = build_index(series, weeks)
                if idx:
                    self.sku_index[sku] = idx
                    self.source[sku] = "sku"
        for sku in consumption_by_sku:
            if sku not in self.source:
                rg = rg_of_sku(sku) or "?"
                self.source[sku] = "group" if rg in self.group_index else "flat"

    def index_for(self, sku, rg, woy) -> float:
        idx = self.sku_index.get(sku)
        if idx and woy in idx:
            return idx[woy]
        gidx = self.group_index.get(rg or "?")
        if gidx and woy in gidx:
            return gidx[woy]
        return 1.0

    def source_for(self, sku) -> str:
        return self.source.get(sku, "flat")

    def deseasonalised(self, sku, rg, series):
        """Divide the observed series by its own seasonal index."""
        out = []
        for v, w in zip(series, self.weeks):
            i = self.index_for(sku, rg, week_of_year(w))
            out.append(v / i if i > 0 else v)
        return out


def seasonal_window_values(series, weeks, target_woy, half=WINDOW_HALF):
    """The SAME weeks-of-year across ALL years (target ± half), not a trailing
    window. This is what the sanity floor is allowed to look at."""
    keep = {((target_woy - 1 + d) % 53) + 1 for d in range(-half, half + 1)}
    return [v for v, w in zip(series, weeks) if week_of_year(w) in keep]


def percentile(values, p=FLOOR_PERCENTILE):
    """Simple nearest-rank percentile — no numpy in this repo's runtime deps."""
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return 0.0
    if len(vs) == 1:
        return vs[0]
    k = max(0, min(len(vs) - 1, int(round(p * (len(vs) - 1)))))
    return vs[k]
