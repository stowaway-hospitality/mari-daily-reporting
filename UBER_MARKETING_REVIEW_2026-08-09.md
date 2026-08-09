# Uber Eats marketing — first look at real numbers (2026-08-09)

Until today the fee split was modelled, so this question could not be asked.
`offers_inc_gst` is now the portal's actual Marketing line, per day, back to
2026-07-13, and `uber_fees_weekly.csv` carries the same figure weekly back to
2026-04-05. That's 19 weeks of real discretionary spend.

## The finding

Marketing has stepped up while sales have fallen.

| period | sales/wk | marketing/wk | marketing as % of sales |
|---|---|---|---|
| Apr 5 – Jul 12 (15 wks) | $5,726 | $524.59 | **9.2%** |
| Jul 13 – Aug 8 (4 wks) | $4,222 | $518.16 | **12.3%** |

Spend per week is essentially unchanged. Sales are down ~26%. So the *rate* went
from 9.2% to 12.3% — +3.1pp, about **$519 per four weeks, ~$6,700/yr** at current
volume — without the spend itself rising. The last two weeks ran 15.7% and 13.8%.

## Does the spend buy volume?

On this data, there is no sign that it does.

- Marketing correlates with sales at r = +0.77, but that is **mechanical, not
  causal**: funded offers are a percentage of order value, so they scale with
  revenue by construction. A high correlation here is the null result, not evidence.
- Ranking the 15 baseline weeks by marketing *intensity*: the 5 weeks with the
  **lowest** intensity (8.2% of sales) averaged **$6,457** in sales. The 5 with the
  **highest** (10.2%) averaged **$5,345**. The relationship runs the wrong way.
- Intensity is remarkably flat — 7.6% to 10.8%, standard deviation 0.92pp across
  15 weeks. That is the signature of a setting nobody is adjusting, not a lever
  being worked.

## What this does and does not establish

It does **not** show that marketing doesn't work. The data is observational, four
weeks deep at daily resolution, and confounded: quiet weeks may *cause* heavier
discounting rather than result from it, and the causality plausibly runs that way.

It does establish that **there is no measured evidence of incremental lift**, and
that the spend has been running on autopilot at ~9% of sales for five months.
For a cost of roughly **$27k/yr** at current volume, that is worth resolving
properly rather than assuming either way.

## The experiment that would settle it

Only one day in 27 carried zero marketing (2026-08-05), so there is no natural
control group. Turning funded offers off for a fortnight — ideally alternating
weeks, to hold seasonality roughly constant — would produce one. The daily feed
now has the resolution to read the result, which it did not have a week ago.

Uber Ads is the cheaper half to test first: it is pure incremental spend with no
discount attached, so switching it off costs nothing but reach, and any drop in
orders is attributable.
