# HANDOFF — full data-integrity audit

**For a fresh Cowork chat. Investigation brief, not a change list.**
Written 17 Aug 2026 after two weeks of sales-data errors surfaced. Every
candidate below was found by scanning the repo's own committed data; none has
been confirmed as a real error yet. **That distinction is the point of this
document.**

## How to run this

1. **Investigate only.** Report findings with evidence and let Zak choose what
   gets fixed. The session that wrote this made things worse twice by changing
   things mid-investigation.
2. **Reproduce every claim with the repo's own code** (`daily_aggregator.py`,
   `build_*`, the test suites), not a throwaway script. Two false alarms came
   from ad-hoc sums; the pipeline disagreed and the pipeline was right.
3. **A number Zak recognises beats a number you computed.** "Stowaway's till
   isn't $9.4k on a Tuesday" ended an investigation that was heading the wrong
   way. Ask him before concluding.
4. **Dry-run any guard over 60+ days and report the false-positive count**
   before proposing it.
5. Claim the area: `python3 scripts/session.py start sales-pipeline --who "audit"`.

---

## 1. Sales exports — PARTLY CONFIRMED, see HANDOFF_20260817.md

Root cause, verified: `ingest_insights_email.py::target_date()` dates a report
by **when the email arrived**, and the product export contains no date column.
A re-send is therefore filed under the wrong day.

Still needing genuine re-exports: **HG 3 + 10 Aug**, **Mari 3 + 10 Aug**,
**Mari 11 + 13 Aug**, **Mari 13 Jul**. Open question on **Stow 11 Aug**.

    python3 -c "import sys;sys.path.insert(0,'scripts');from export_guards import *;..."
    # the guards are in scripts/export_guards.py; tests in test_export_guards.py

---

## 2. COGS percentages — STRONGEST UNEXAMINED SIGNAL

A scan of `*_daily_history.csv` for `cogs_pct` outside 5–45%:

| venue | days out of range | examples |
|---|---|---|
| Stowaway | 7 | **4,106%** (8 Sep 25), **4,928%** (13 Oct 25, 15 Dec 25), 71.2% (11 Aug 26) |
| Harry Gatos | 23 | 3.8%, 4.4%, 4.8% (too low), 59.0% |
| **Marilyna's** | **339** | 427%, 346%, 124.7%, 109.3% |

**Marilyna's has 339 implausible days.** That is not an outlier list, that is a
systematic problem, and Mari's COGS feeds the group GP% on the dashboard.

Where to look: a four-thousand-percent COGS means cost was read against a tiny
or zero revenue — check whether those days are delivery-only (Uber revenue
recorded elsewhere), whether `cogs_lightspeed_dollars` is being divided by a
revenue that excludes delivery, and whether Mari's near-zero revenue days (e.g.
$7.27, $10.92) are real. Start:

    python3 -c "
    import csv
    for r in csv.DictReader(open('data/mari_daily_history.csv')):
        p=(r.get('cogs_pct') or '')
        if p and float(p)>100: print(r['date'], r.get('revenue_ex_gst'), r.get('cogs_dollars'), p)" | tail -30

---

## 3. Days with revenue but nobody paid

| venue | days | notes |
|---|---|---|
| Stowaway | 14 | all Nov 2024 — probably pre-Deputy, confirm and exclude |
| Harry Gatos | 3 | Nov 2024 ×2, **2 Aug 2026** |
| Marilyna's | 23 | all Nov 2024 |

**2 Aug 2026 is confirmed real** — a function settled as one Open Price line,
staff rostered under Stowaway. The Nov 2024 cluster is almost certainly the
period before Deputy data existed; confirm the wages feed's start date and, if
so, this class is closed.

The reverse — **wages paid, no revenue** — has exactly one hit:
**Stowaway 13 Jan 2025, $1,088.96 of wages, no sales.** That looks like a
sales day that was never recovered. Worth a look; it is 19 months old.

---

## 4. Business-day boundary (post-midnight settlements)

Zak: *"if a function was paid on the Sunday, those sales actually happened
Saturday night."* Confirmed on HG 2 Aug (a $2,400 function, order type
"Unspecified", no dine-in, on a day with no wages).

Measured on Stowaway's hourly exports (which carry `Sale Closed Hour`):

    Mon–Sat  $0.00 after midnight
    Sunday   $347.60 over four weeks (0.6% of Sunday revenue)

So it is immaterial for Stowaway's ordinary trade and total for a late function.
**Open policy question for Zak, not a bug:** should a tab settled after midnight
be attributed to the trading night? It changes weekend reporting and any
weekday-pattern logic that keys off it.

---

## 5. Venue classification / cross-till

All three venues ring through the Stowaway till; `classify_product()` splits
them, and rows move both ways (`hgf` = HG food on the Stow till, `stf` = Stow
food on HG's, `m` = Marilyna's). Measured on 10 Aug: $1,216 of cross-venue rows
excluded from Stowaway; on 15 Aug only $116.96 pulled into HG.

Unaudited: whether `reporting_group_mapping.csv` (described in the aggregator as
"a HISTORICAL aggregate") still matches how products are grouped today. A
product that moved reporting group would be attributed to the wrong venue
silently — and that shifts revenue between P&Ls without changing the total.

    grep -n "reporting_group_mapping" scripts/daily_aggregator.py   # start here

---

## 6. Delivery platforms

- `uber_daily.csv`: 61 rows, and a naive (date, venue) key scan showed **26
  repeated keys** — but the venue column name may differ, so **verify the key
  columns before believing that**. If real, Uber revenue may be double-counted.
- `uber_direct_daily.csv`: 36 rows, no duplicates.
- EatClub give-aways are deducted from revenue (e.g. −$195.09 on Stow 10 Aug).
  Confirm those deductions are not also netted off elsewhere.

---

## 7. Missing days

Gap scan of the daily histories: Stowaway 49, HG 63, Mari 62 gaps. Most will be
legitimate closed days (Stow Mondays historically, HG Tue/Sun, Mari's closed
days). The new `_missing_sales_days` check in `health_monitor.py` now uses
wages to tell a closed day from a lost one — run it over history rather than
eyeballing the gap list:

    python3 -c "import sys;sys.path.insert(0,'scripts');import health_monitor as h;print(h._missing_sales_days(lookback=60))"

---

## 8. Wages — largely unaudited

Known rules that could silently mis-state a venue:
- **Deputy id 24 (Oliver) must never be mapped**; only Oliver + Bryony go to
  corp payroll (`_corp_payroll_only`), never on a venue wage line.
- **Monday reallocation**: `MONDAY_REALLOCATED_OU = "Stow Kitchen"` flips to
  Harry Gatos on Mondays (`core/venues.py`).
- Unapproved shifts are costed at the person's own rate until Deputy approves —
  so an unapproved week understates wages.
- `reconcile_wages.py` claims every wage dollar ties to Xero and runs in CI;
  confirm it is actually covering the current period, not just old months.

---

## 9. Estimated vs actual COGS

`build_cogs_variance.py` differences Xero purchases against recipe cost. Two
things worth testing: that the comparison only runs on **closed** months with
full daily coverage, and that HG's ~31% of revenue with no recipe behind it is
still being excluded rather than counted as waste.

---

## 10. Publication

The repo can be right while the screen is wrong. `deploy_dashboard.yml` now
gates on Tests, and that gate broke publishing twice on 17 Aug (a missing run
reads as `null`, and **cancelled is not failed**). If figures look stale, check
the deploy runs before doubting the data.

---

## Suggested order

1. **Marilyna's COGS%** — 339 days, feeds group GP, nobody has looked.
2. **Uber duplicate keys** — verify the key columns; double-counted revenue if real.
3. **Stow 11 Aug / the till reference** — one Insights lookup settles it.
4. **The five venue-days needing re-exports** — mechanical once #3 is settled.
5. **Reporting-group drift** — slow, but it silently moves money between venues.
6. **Stow 13 Jan 2025** — one orphan day, cheap to close out.
