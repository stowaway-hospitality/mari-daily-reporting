# Buffer & spike-floor review — par model v2 (data to 2026-08-09)

**Question:** on the fresh live data, are the volatility buffer (1.12–1.30) and
the 3-month spike floor (worst window week × 1.10) too tight given the stockouts?

## What actually binds

For a par SKU the recommendation is the **greater** of

1. `forecast_wk × 1.0 wk × buffer`  (buffer ∈ 1.12–1.30, scaled by recent CV), and
2. `spike_floor = ceil(worst_of_last_13_weeks × floor_adj × 1.10)`.

The two protect different regimes:

- **Steady, higher-volume SKUs** (Rooster, house spirits, house wines): weekly
  demand is smooth, so the *buffer* binds and the floor sits below it. Here
  1.12–1.30 is appropriate — the recent CV lands most of these near 1.12–1.20 and
  a full week of cover plus that margin comfortably covers normal week-to-week
  wobble. **No change needed.**

- **Spiky, low-volume SKUs** (cans, seltzers, one-keg beers, single-vineyard
  wines): the *spike floor* binds, and the buffer is irrelevant because the peak
  week dwarfs `mean × buffer`. This is where stockout risk actually lives.

## The numbers (stow, most volatile movers, last 13 weeks)

| SKU | recent wk | peak wk | peak/mean | weeks >1.2×mean (of 13) |
|---|---|---|---|---|
| VB Tinnie | 0.2 | 5.0 | 20.0 | 6 |
| Hyoketsu Lemon Can | 1.0 | 9.0 | 9.0 | 6 |
| Better Beer Tin | 1.0 | 7.0 | 7.0 | 5 |
| Fellr Watermelon Seltzer Tin | 2.0 | 13.0 | 6.5 | 4 |
| Bundaberg Ginger Beer [750ml] | 0.9 | 4.0 | 4.6 | 4 |
| Peroni | 2.8 | 9.0 | 3.3 | 7 |
| Asahi 3.5% | 3.0 | 8.0 | 2.7 | 4 |

(HG is milder — worst is Tsingtao Longneck at peak/mean 3.1, 3/13 weeks over.)

For these SKUs **4–7 of the last 13 weeks already exceed 1.2× the recent mean.**
The floor sets par to only **10 % above the single worst week seen**. With a
weekly (coverage = 1.0) cycle and demand this bursty, a fresh spike ≥ the prior
peak — entirely plausible when a third of recent weeks are already >1.2×mean —
lands the venue 0–10 % short. That is exactly the thin-margin stockout pattern.

## Recommendation

- **Keep the 1.12–1.30 volatility buffer as-is.** It governs the steady SKUs it
  was designed for and the CV-scaling is doing the right thing there.
- **Raise the spike-floor multiplier from 1.10 → 1.20** (constant
  `SPIKE_FLOOR_MULT` in `modules/par/model.py`). A one-line change. It only lifts
  the SKUs where the floor is already the binding constraint — the spiky, cheap,
  easily-overstocked cans/seltzers/one-keg lines — and leaves every steady SKU
  untouched (their buffer still dominates). The downside is trivial holding cost
  on low-value stock; the upside is removing the 0–10 % shortfall on precisely
  the lines that spike.
- **Optional follow-up (not now):** make the floor multiplier CV-scaled the way
  the buffer is (e.g. `1.10 + 0.4·CV`, capped ~1.35) so the calmest SKUs keep
  1.10 and only the burstiest get the full lift. Worth a look once a few weeks of
  v2 recommendations vs. actual stockouts are on record.

No code change is shipped in this PR beyond the review itself; the multiplier bump
is a deliberate, reviewable one-liner left for sign-off.
