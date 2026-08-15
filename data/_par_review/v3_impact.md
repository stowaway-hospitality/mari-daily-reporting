# Par model v2 -> v3 — impact

_Generated 2026-08-09T20:34:08.177574+10:00 by `scripts/par_v3_impact.py`. Both engines run over the same committed inputs in one process._

## The finding that started this

The Lightspeed stock counts were being read as **net** variance, and the net variance lies. On **28 Jul 2026** the gross negative was **-$1,598** against a gross positive of **+$1,636** — a net of **+$37**, which reads as a clean count. It is not a clean count: $1,598 of stock left the building without a sale and an unrelated $1,636 of miscounts/mis-scans happened to cover it. v3 only ever takes the loss side, so a positive variance on one SKU can never pay for a loss on another.

Measured on that count: Rooster Rojo -5.82 btl (-18.8%), San Pellegrino -6 (-19.4%), Coke Zero Can -31 (-67%).

## Stowaway (`stow`)

Order Sun **2026-08-09** -> delivery **2026-08-12** -> next delivery **2026-08-19** — 7 days, 10.0 weighted day-units = **1.00x** a normal cycle (normal Wednesday run).

- SKUs compared: **218**
- v3 higher than v2: **78**   |   lower: **46**   |   unchanged: **94**
- service classes: core(95%) 66, standard(90%) 86, tail(85%) 66
- low movers on the Poisson path: **108**
- SKUs carrying a material measured shrinkage uplift: **58** (**5** hit the 50%-of-demand cap and are flagged `shrinkage_capped_investigate`)
- bookings: `unavailable: no STOWAWAY_BOOKINGS_TOKEN in environment (admin endpoints return 401)` — **shadow only**, not added to `rec_par`

### Top 20 movers (v2 -> v3)

| Δ | v2 | v3 | current | SKU | why |
|---:|---:|---:|---:|---|---|
| -8.8 | 10.8 | 2.0 | 3.9 | Hyoketsu Lemon Can | Poisson low-mover, seasonal x1.25 |
| +8.8 | 19.7 | 28.5 | 49 | Little Dragon Can | seasonal x1.25 |
| +6.8 | 9.0 | 15.8 | 21.2 | Monteith's Apple Cider Bottle | shrink +1.81/wk, seasonal x1.40 |
| +6.8 | 26.7 | 33.5 | 30.3 | Villa Fresco Sangiovese - Bottle | shrink +0.74/wk, seasonal x1.19 |
| +6.7 | 32.0 | 38.7 | 29.8 | Version Two Pinot Grigio - Bottle | shrink +0.57/wk, seasonal x1.09 |
| +6.2 | 31.2 | 37.4 | 43.4 | Corona | seasonal x1.25 |
| -5.3 | 36.4 | 31.1 | 33.2 | Heaps Normal Tin | shrink +0.22/wk, seasonal x1.09 |
| +5.1 | 14.2 | 19.3 | 24.8 | Geppetto Pinot Noir - Bottle | shrink +0.75/wk, seasonal x1.12 |
| +3.9 | 32.9 | 36.8 | 40 | Rooster Rojo Blanco Tequila [Bottle] | shrink +1.07/wk, seasonal x1.07, override reserve |
| +3.5 | 6.3 | 9.8 | 9.6 | Young Henrys Cloudy Cider Can | seasonal x1.23 |
| -3.3 | 6.3 | 3.0 | 8.9 | Better Beer Tin | Poisson low-mover, seasonal x1.25 |
| +2.9 | 15.0 | 17.9 | 30.5 | Kuku Sauvignon Blanc - Bottle | shrink +0.33/wk, seasonal x1.19 |
| +2.8 | 10.1 | 12.9 | 12 | Trentham Estate Rosé - Bottle | shrink +0.33/wk, seasonal x1.12 |
| -2.3 | 13.2 | 10.9 | 3.7 | Fellr Watermelon Seltzer Tin | seasonal x1.25 |
| -2.2 | 7.2 | 5.0 | 9.3 | Asahi 3.5% | Poisson low-mover, shrink +0.23/wk, sanity floor, seasonal x0.70 |
| +1.9 | 9.5 | 11.4 | 12.9 | Mother's Milk Shiraz - Bottle | shrink +0.37/wk, seasonal x0.86 |
| +1.8 | 4.6 | 6.4 | 6.6 | Aperol [Bottle] | shrink +0.02/wk, seasonal x1.08 |
| +1.8 | 1.2 | 3.0 | 1.9 | Bacardi Blanca [700ml] | Poisson low-mover, shrink +0.13/wk, seasonal x1.40 |
| +1.8 | 5.0 | 6.8 | 5.2 | Sailor Jerry [700ml] | Poisson low-mover, shrink +0.00/wk, seasonal x1.13, override reserve |
| +1.7 | 4.9 | 6.6 | 8.8 | Ottelia Cab Sav - Bottle | shrink +0.15/wk, seasonal x1.13 |

## Harry Gatos (`hg`)

Order Sun **2026-08-09** -> delivery **2026-08-12** -> next delivery **2026-08-19** — 7 days, 10.0 weighted day-units = **1.00x** a normal cycle (normal Wednesday run).

- SKUs compared: **80**
- v3 higher than v2: **21**   |   lower: **10**   |   unchanged: **49**
- service classes: core(95%) 25, standard(90%) 31, tail(85%) 24
- low movers on the Poisson path: **44**
- SKUs carrying a material measured shrinkage uplift: **0** (**0** hit the 50%-of-demand cap and are flagged `shrinkage_capped_investigate`)
- bookings: `unavailable: no STOWAWAY_BOOKINGS_TOKEN in environment (admin endpoints return 401)` — **shadow only**, not added to `rec_par`

### Top 20 movers (v2 -> v3)

| Δ | v2 | v3 | current | SKU | why |
|---:|---:|---:|---:|---|---|
| +3.4 | 5.4 | 8.8 | 5.5 | River Retreat Pinot Grigio - Bottle | forecast/volatility |
| -2.0 | 8.4 | 6.4 | 8.3 | Bintang | forecast/volatility |
| +1.7 | 4.4 | 6.1 | 3.7 | Angas & Bremer Grenache - Bottle | forecast/volatility |
| -1.2 | 5.2 | 4.0 | — | Kunizakari Umeshu [60ml] | Poisson low-mover |
| +1.0 | 1.0 | 2.0 | 0.8 | Aperol [Bottle] | Poisson low-mover |
| +0.8 | 0.2 | 1.0 | 0.3 | Monkey Shoulder Scotch [Bottle] | Poisson low-mover |
| +0.7 | 2.4 | 3.1 | 2.5 | Italicus Bergamot Liqueur [Bottle] | Poisson low-mover, override reserve |
| +0.6 | 2.4 | 3.0 | 11.4 | Asahi 3.5% | Poisson low-mover |
| +0.6 | 2.4 | 3.0 | 2.8 | Geppetto Chardonnay - Bottle | Poisson low-mover |
| +0.6 | 2.4 | 3.0 | 2.1 | Little Dragon Ginger Beer Can | Poisson low-mover |
| +0.5 | 0.5 | 1.0 | 0.4 | Buffalo Trace Bourbon [Bottle] | Poisson low-mover |
| +0.4 | 3.6 | 4.0 | 4.7 | Asahi Zero | Poisson low-mover |
| -0.4 | 3.4 | 3.0 | 3.5 | Azurescens Pinot Noir - Bottle | Poisson low-mover |
| +0.4 | 3.6 | 4.0 | 8.2 | Corona | Poisson low-mover |
| +0.4 | 2.6 | 3.0 | 2.8 | From Sundays Shiraz - Bottle | Poisson low-mover |
| +0.4 | 0.6 | 1.0 | 0.5 | Haku Vodka [Bottle] | Poisson low-mover |
| +0.4 | 3.6 | 4.0 | 6 | L'esprit Rosé - Bottle | Poisson low-mover |
| +0.4 | 0.6 | 1.0 | 0.7 | Sapporo [Keg] | Poisson low-mover |
| +0.3 | 6.0 | 6.3 | 4.2 | Tsingtao Longneck | forecast/volatility |
| -0.3 | 0.3 | 0.0 | 0.2 | Wa no Kokoro Yuzushu [Bottle] | Poisson low-mover |

## The named problem items (Stowaway)

| SKU | current par | v2 | v3 | Δ v2->v3 | v3 pre-override | shrink/wk | loss frac | path | note |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| Rooster Rojo Blanco Tequila [Bottle] | 40 | 32.9 | 36.8 | +3.9 | 15.8 | 1.07 | 13.1% | normal | hard `reserve` 21.0 pins it |
| San Pellegrino 500ml | 24.4 | 16.8 | 16.8 | +0.0 | 12.6 | 2.03 | 18.1% | normal | hard `min` 16.8 pins it |
| Coke Zero Can | 40.7 | 40.7 | 40.7 | +0.0 | 40.7 | 1.91 | 0.0% | normal | held at live par — no rung-up demand; **counts see it moving, the till mapping does not** |
| Bombay Dry [Bottle] | 9.4 | 8.8 | 8.7 | -0.1 | 5.3 | 0.33 | 13.4% | normal | hard `reserve` 3.4 pins it |
| Aperol [Bottle] | 6.6 | 4.6 | 6.4 | +1.8 | 6.4 | 0.02 | 5.8% | normal | core |
| Fellr Watermelon Seltzer Tin | 3.7 | 13.2 | 10.9 | -2.3 | 10.9 | 0.00 | 0.0% | normal | core |
| Hyoketsu Lemon Can | 3.9 | 10.8 | 2.0 | -8.8 | 2.0 | 0.00 | 0.0% | poisson | core |

**16 Stowaway SKUs are losing stock that the till never rang up at all** — the stock counts see the units leaving, `products_weekly.csv` shows no demand, so the par is held at the live value and the SKU is flagged `shrinkage_without_demand_mapping`. This is a POS naming / product-mapping job, not a par job: `4 Pines Kolsch Bottle D`, `Coke 1.25L`, `Coke Can`, `Coke Zero 1.25L`, `Coke Zero Can`, `Dragonfly Grenache - Bottle`, `Fever-Tree Mediterannean Tonic [Bottle]`, `Fever-Tree Naturally Light Tonic [Bottle]`, `Grifter Pale Ale Cans D`, `Little Dragon Ginger Beer [Keg]`, `Scout Pinot Gris - Bottle`, `Solo 1.25L`….

## Shrinkage (Stowaway)

- stock counts used: **11**, giving **10** measurable periods
- SKUs with a measurable loss: **62**
- median loss fraction (of modelled demand): **9.6%**
- capped at 50% of demand and flagged for investigation: **5** (a further 31 hit the cap on a sub-0.05-unit/week 'loss' — that is count rounding on a fractional bottle, not shrinkage)

### Top 8 by units lost per week

| SKU | loss/wk | loss fraction | periods | capped |
|---|---:|---:|---:|---|
| Coke Can | 3.05 | 0.0% | 10 | no |
| San Pellegrino 500ml | 2.03 | 18.1% | 9 | no |
| Coke Zero Can | 1.91 | 0.0% | 10 | no |
| Monteith's Apple Cider Bottle | 1.81 | 26.2% | 10 | no |
| Fever-Tree Naturally Light Tonic [Bottle] | 1.32 | 0.0% | 10 | no |
| Sprite Can | 1.26 | 0.0% | 10 | no |
| Rooster Rojo Blanco Tequila [Bottle] | 1.07 | 13.1% | 10 | no |
| Coke Zero 1.25L | 0.93 | 0.0% | 10 | no |

## Christmas 2026 — the 14-day gap

Order **Sun 20 Dec 2026** -> delivery **2026-12-23** -> next realistic delivery **2027-01-06**: **14 days**, **21.0 weighted day-units** = **2.10x** a normal cycle, over peak summer trade.

Chain: Mon 28 Dec is a public holiday, so the Wed 30 Dec ILG run slips to Fri 1 Jan; Fri 1 Jan is New Year's Day, so that delivery does not happen at all and the goods land on Wed 6 Jan. Full note: `data/_par_review/christmas_2026.md`.

Supplier Christmas shutdowns are **not yet known** — `data/par_calendar.json -> supplierShutdowns` carries PENDING entries to fill in once suppliers publish in December. Until then the 6 Jan resumption is the OPTIMISTIC case.

