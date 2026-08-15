# Christmas 2026 — the 14-day delivery gap

**Owner:** Zak · **Action window:** late November 2026 · **Status:** encoded in the
model (`data/par_calendar.json`, `modules/par/calendar.py`), supplier cutoffs PENDING.

---

## The problem in one line

**The stock that lands on Wednesday 23 December 2026 has to last until Wednesday
6 January 2027 — fourteen days, over the busiest fortnight of the year.**

## Why (the delivery chain, verified)

Stowaway orders Sunday; ILG's cutoff is 11:00 Tuesday, so a Sunday order makes
the **Wednesday** run. Two NSW rules bend that:

* a public holiday **Monday** slips the Wednesday run to **Friday**;
* a public holiday on the delivery day itself means **no delivery that week** —
  the goods slip to the next available run.

Applied to the 2026/27 calendar:

| Order (Sun) | Normal run | What actually happens | Delivery |
|---|---|---|---|
| 20 Dec 2026 | Wed 23 Dec | Mon 21 Dec is a normal day | **Wed 23 Dec** — last normal run |
| 27 Dec 2026 | Wed 30 Dec | Mon 28 Dec is Boxing Day (observed) → slips to **Fri 1 Jan**… which is New Year's Day | **NO DELIVERY** |
| 3 Jan 2027 | Wed 6 Jan | Mon 4 Jan is a normal day | **Wed 6 Jan** |

Public holidays in the window: Fri 25 Dec (Christmas Day), Sat 26 Dec (Boxing
Day), Mon 28 Dec (Boxing Day observed), Fri 1 Jan (New Year's Day).

## The number

Exposure is counted in weighted day-units (Fri/Sat/Sun and public holidays = 2,
other weekdays = 1). A normal Wed→Wed cycle is **10** day-units.

```
23 Dec Wed 1   24 Thu 1   25 Fri 2   26 Sat 2   27 Sun 2   28 Mon 2 (PH)   29 Tue 1
30 Dec Wed 1   31 Thu 1    1 Fri 2    2 Sat 2    3 Sun 2    4 Mon 1         5 Tue 1
                                                                    ------- 21
```

**21 day-units = 2.10× a normal cycle.** The order placed ~Sun 20 Dec must be
sized at roughly **2.1× a normal week**, not 1×, and not the old ×1.3 long-weekend
fudge.

The par model computes this automatically: run `scripts/build_par_model.py` in
the week of 20 Dec and `exposure.exposure_ratio` will read 2.1, every `rec_par`
will already carry it, and every SKU will be flagged `stretched_cycle`.

To see it right now, from any checkout:

```
python3 -c "import sys;sys.path.insert(0,'.');from modules.par import calendar as c;\
print(c.christmas_2026_exposure(c.load_calendar('data')))"
```

## What is NOT yet known — the November action

The 6 Jan resumption above assumes **ILG runs normally on 6 Jan and does not shut
down over the break**. That is the optimistic case. Suppliers publish their
Christmas cutoffs and shutdown windows in **early-to-mid December**, and they are
routinely worse than the public-holiday calendar implies (a last-order date in
the third week of December and a resumption in the second week of January is
common).

**Late November 2026, collect and record:**

1. Email/ring each supplier rep for their **last order date, last delivery date,
   and resumption date**: ILG, Paramount, Bacchus, Lion, Grifter, and any
   direct-to-venue wine suppliers.
2. Write the answers into `data/par_calendar.json → supplierShutdowns["2026-12"]`,
   replacing the `"PENDING — suppliers publish in December"` strings and setting
   `"confirmed": true`.
3. Re-run `scripts/build_par_model.py` and re-read the exposure ratio. If a
   supplier's real gap is longer than 14 days, the multiplier goes up with it.
4. Sanity-check cellar and cool-room **capacity** against the resulting pars —
   2.1× on beer and soft drink is a volume problem before it is a money problem.
   Where it will not physically fit, split the buy across the 13 Dec and 20 Dec
   deliveries rather than trying to land it all on 23 Dec.

## Reminder mechanics

A GitHub Actions cron cannot express "once, on 23 November 2026" cleanly — the
closest is a scheduled workflow with a date guard, which then sits in the repo
firing and no-opping every week for a year. Instead:

* the **weekly par build** (`.github/workflows/par_model.yml`, Sun 08:00 Sydney)
  prints the Christmas exposure line in its `SANITY` block on every run, so the
  gap is in front of whoever reads the build from now until it happens;
* every SKU in a stretched cycle carries the `stretched_cycle` flag in
  `data/par_recommendations_stowaway.json`;
* this note is the checklist. Put a calendar reminder for **Mon 23 Nov 2026** —
  "collect supplier Christmas cutoffs → data/par_calendar.json".

## Related

* `modules/par/calendar.py` — the delivery-chain logic and the day-unit weights.
* `data/par_calendar.json` — the holiday dates (copied from the reorder skill's
  `rules.json`, plus the Friday holidays that file never needed) and the
  `supplierShutdowns` slots.
* `tests/test_par_model.py` — `test_christmas_2026_gap_is_fourteen_days` locks
  the chain so a calendar edit cannot quietly break it.
