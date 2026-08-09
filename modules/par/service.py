#!/usr/bin/env python3
"""
Par model v3 — the (R,S) service-level engine.

v2's primary driver was, in effect, "the worst week in the last 13 × 1.2". That
is not a stock policy; it is a superstition that happens to be conservative on
busy SKUs and wildly wrong on quiet ones (it sets a par off a single outlier
week, which for a premium spirit is one table ordering a round).

v3 uses the textbook periodic-review (R,S) order-up-to level:

    par = demand_over_exposure × (1 + shrinkage_fraction)
        + bookings_uplift                 (SHADOW ONLY — see modules/par/bookings.py)
        + z × σ_exposure

with
    demand_over_exposure = weekly forecast × exposure_day_units / 10
    σ_exposure           = σ_weekly × sqrt(exposure_day_units / 10)

Service levels (z), per SKU class
---------------------------------
    core / critical      95%   z = 1.645     the things that stop service
    standard             90%   z = 1.282
    long-tail / premium  85%   z = 1.036     the slow, expensive tail

Classified by volume/revenue decile within the venue, overridable per SKU in
`SERVICE_CLASS_OVERRIDES`. Higher service level ⇒ higher par, always.

Low movers
----------
Below `POISSON_THRESHOLD_WK` units a week the normal approximation is garbage —
it happily returns a fractional safety stock on a SKU that sells 0, 0, 0, 3. For
those we set the par at the **95th percentile of a Poisson** with λ = demand
over the exposure window. This is the correct distribution for rare independent
arrivals and it is what keeps the premium spirit tail from either stocking out
or carrying three bottles of everything.

Volatility
----------
σ comes from up to 52 weeks of DESEASONALISED demand, expressed as a cv and then
shrunk toward the reporting-group cv by observation count. v2's flat cv=0.5
fallback was doing real damage at Harry Gatos, where almost nothing had enough
history to measure and everything therefore got the same buffer.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

# ── service classes ─────────────────────────────────────────────────────────
Z = {"core": 1.645, "standard": 1.282, "tail": 1.036}
SERVICE_LEVEL = {"core": 0.95, "standard": 0.90, "tail": 0.85}

# Decile cuts on the within-venue volume rank (0 = quietest, 1 = busiest).
CORE_RANK = 0.70          # top 30% by volume  -> 95%
STANDARD_RANK = 0.30      # next 40%           -> 90%
                          # bottom 30%         -> 85%

# Per-SKU escapes. Anything named here ignores the decile. Keep it short and
# justified — this is a hand on the scale.
SERVICE_CLASS_OVERRIDES = {
    # sku name (exact, as it appears in the par book) -> class
    "Rooster Rojo Blanco Tequila [Bottle]": "core",   # margarita spine
    "Bombay Dry [Bottle]": "core",                    # house gin
}

POISSON_THRESHOLD_WK = 2.0    # mean weekly demand below this -> Poisson path
POISSON_SERVICE = 0.95        # 95th percentile of the Poisson
CV_SHRINK_K = 6.0             # weight n/(n+K) toward the SKU's own cv
CV_FLOOR, CV_CEIL = 0.15, 1.20   # a weekly cv above ~1.2 means the normal model
                                 # is the wrong model, not that the SKU needs
                                 # three times its demand on the shelf; those
                                 # SKUs are low movers and belong on the Poisson
                                 # path anyway.
VOL_WEEKS = 52


# ── volatility ──────────────────────────────────────────────────────────────
def _cv(series):
    vals = [v for v in series]
    if len(vals) < 4:
        return None, len(vals)
    m = sum(vals) / len(vals)
    if m <= 0:
        return None, len(vals)
    return statistics.stdev(vals) / m, len(vals)


class VolatilityBook:
    """Per-SKU cv, shrunk toward the reporting-group cv by observation count."""

    def __init__(self, deseasonalised_by_sku, rg_of_sku, weeks=VOL_WEEKS):
        self.raw = {}
        self.n = {}
        by_group = defaultdict(list)
        for sku, series in deseasonalised_by_sku.items():
            tail = series[-weeks:]
            nz = [v for v in tail if v > 0]
            cv, _n = _cv(tail)
            self.raw[sku] = cv
            self.n[sku] = len(nz)
            if cv is not None and len(nz) >= 8:
                by_group[rg_of_sku(sku) or "?"].append(cv)
        self.group = {rg: statistics.median(v) for rg, v in by_group.items() if v}
        allcv = [c for c in self.raw.values() if c is not None]
        self.overall = statistics.median(allcv) if allcv else 0.5

    def cv_for(self, sku, rg):
        own = self.raw.get(sku)
        n = self.n.get(sku, 0)
        prior = self.group.get(rg or "?", self.overall)
        if own is None:
            return max(CV_FLOOR, min(CV_CEIL, prior)), "group"
        w = n / (n + CV_SHRINK_K)
        cv = w * own + (1 - w) * prior
        return max(CV_FLOOR, min(CV_CEIL, cv)), ("sku" if w >= 0.5 else "sku+group")


# ── service class ───────────────────────────────────────────────────────────
def classify(consumption_by_sku, revenue_by_sku=None):
    """{sku: class}. Rank on volume, break ties toward revenue when we have it."""
    totals = {sku: sum(s) for sku, s in consumption_by_sku.items()}
    rev = revenue_by_sku or {}
    order = sorted(totals, key=lambda s: (totals[s], rev.get(s, 0.0)))
    n = len(order)
    out = {}
    for i, sku in enumerate(order):
        rank = (i / (n - 1)) if n > 1 else 1.0
        if rank >= CORE_RANK:
            cls = "core"
        elif rank >= STANDARD_RANK:
            cls = "standard"
        else:
            cls = "tail"
        out[sku] = cls
    for sku, cls in SERVICE_CLASS_OVERRIDES.items():
        if sku in out:
            out[sku] = cls
    return out


# ── Poisson ─────────────────────────────────────────────────────────────────
def poisson_quantile(lam: float, p: float = POISSON_SERVICE, kmax: int = 400) -> int:
    """Smallest k with P(X<=k) >= p for X~Poisson(lam). Exact, no scipy."""
    if lam <= 0:
        return 0
    # Sum the pmf term by term; log-space start keeps big lambdas finite.
    term = math.exp(-lam)
    cum = term
    k = 0
    while cum < p and k < kmax:
        k += 1
        term *= lam / k
        cum += term
    return k


def negbinom_quantile(mean: float, var: float, p: float = POISSON_SERVICE,
                      kmax: int = 2000) -> int:
    """Smallest k with P(X<=k) >= p for X~NegBinom with this mean and variance.

    Drinks do not arrive independently. A guest who orders a chu-hi orders three
    more; a table orders a round. That makes weekly demand OVER-DISPERSED —
    measured variance/mean runs 2.1–4.3 across this venue's range, where Poisson
    assumes exactly 1.0. Using Poisson on clumped demand under-states the par of
    every session product (Hyoketsu: Poisson 95th = 4, negative binomial = 7,
    observed burst = 9). Falls back to Poisson when the data is not
    over-dispersed (var <= mean).
    """
    if mean <= 0:
        return 0
    if var <= mean:
        return poisson_quantile(mean, p)
    r = mean * mean / (var - mean)      # dispersion
    q = r / (r + mean)                  # success probability
    term = q ** r                       # P(X=0)
    cum = term
    k = 0
    while cum < p and k < kmax:
        term *= (r + k) / (k + 1) * (1.0 - q)
        k += 1
        cum += term
    return k


# ── the order-up-to level ───────────────────────────────────────────────────
def order_up_to(forecast_wk, cv, exposure_units, normal_units, service_class,
                shrink_fraction=0.0, bookings_uplift=0.0,
                poisson_threshold=POISSON_THRESHOLD_WK, burst_floor=0.0):
    """Return (par, detail dict). Pure maths — no I/O, no rounding policy."""
    ratio = (exposure_units / normal_units) if normal_units else 1.0
    demand = forecast_wk * ratio
    z = Z[service_class]
    shrink_mult = 1.0 + max(0.0, shrink_fraction)

    if forecast_wk > 0 and forecast_wk < poisson_threshold:
        lam = demand * shrink_mult
        # cv here is the volatility estimate for this SKU; var = (cv*mean)^2.
        var = (cv * lam) ** 2 if cv else lam
        par = float(negbinom_quantile(lam, var, POISSON_SERVICE))
        path = "negbinom" if var > lam else "poisson"
        sigma_exp = math.sqrt(var)
        safety = max(0.0, par - lam)
    else:
        sigma_wk = cv * forecast_wk
        sigma_exp = sigma_wk * math.sqrt(ratio) if ratio > 0 else 0.0
        safety = z * sigma_exp
        par = demand * shrink_mult + safety
        path = "normal"

    par += max(0.0, bookings_uplift)

    # BURST / PRESENCE FLOOR — "you can't have 2 cans in the fridge."
    # A statistically-fine par can still be operationally useless: if a guest who
    # orders this product typically takes 3–4 back to back, holding 2 fails the
    # sale no matter what the weekly mean says. burst_floor is the 90th-percentile
    # of a week IN WHICH THE PRODUCT ACTUALLY SOLD, scaled to the exposure window,
    # so par is always enough to serve a realistic round.
    burst_applied = False
    if burst_floor and par < burst_floor:
        par = float(burst_floor)
        burst_applied = True

    return par, {
        "path": path,
        "burst_floor": round(burst_floor, 2) if burst_floor else 0.0,
        "burst_floored": burst_applied,
        "service_class": service_class,
        "service_level": SERVICE_LEVEL[service_class] if path == "normal" else POISSON_SERVICE,
        "z": round(z, 3) if path == "normal" else None,
        "exposure_ratio": round(ratio, 4),
        "demand_over_exposure": round(demand, 3),
        "shrink_multiplier": round(shrink_mult, 4),
        "cv": round(cv, 3),
        "sigma_exposure": round(sigma_exp, 3),
        "safety_stock": round(safety, 3),
    }
