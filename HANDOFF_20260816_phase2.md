# Phase 2 complete — all three venues through the one-way door, staged

Supersedes `HANDOFF_20260816_phase2a.md` (kept: its findings are still the
reasoning behind half of this). Written 2026-08-16.

## Where it stands

| venue | products costing both ways | max delta | sum | staged file |
|---|---|---|---|---|
| Stowaway | **545 / 545** | $1.12 | $6.12 | `data/recipes/_staged/stowaway.yaml` |
| Marilyna's | **224 / 224** | $0.87 | $2.73 | `data/recipes/_staged/marilynas.yaml` |
| Harry Gatos | **58 / 59** | $0.39 | $0.77 | `data/recipes/_staged/harry_gatos.yaml` |

Zero refusals at Stowaway and Marilyna's, down from 22 apiece. The one Harry
Gatos exclusion is correct: `Dragon Soda` is a hand-authored **batch** (20,000 ml
yield, no sell price), and publishing a batch cost as a serve cost is the
$37.20-on-a-$9.00-drink bug a test already guards.

**Nothing has cut over.** `_staged/` is a subdirectory on purpose —
`data/recipes/*.yaml` is globbed non-recursively — and promotion is
`materialise_recipes.py --venue <v> --promote`, which IS the cutover.
`.github/workflows/shadow_diff.yml` diffs all three every morning at 07:15.

## The rule that did most of the work

> "it's always easier to measure in g if it's not a beverage" — Zak

Nearly every batch yield here had a unit **nobody measured**: the number was a
sum of ingredients recorded in grams, millilitres and sometimes *bunches*, and
somebody typed a unit on the end. Garlic Oil's "1,500 ml" is 1,000 g of garlic
plus 500 ml of oil.

The tempting fix is to declare a density. That is two mistakes — it invents a
number nobody measured, in order to reconcile a label that never meant anything.
The kitchen convention needs no density because it describes what the person
actually does. `modules/recipes/units.py` classifies each batch off the Sales
Product API's `reporting_group` and the yield follows.

Refinements that cost real debugging, each now a test:

- **It governs yields, not every line.** Two litres of milk in a cauliflower
  cheese stays 2,000 ml; the batch it makes is grams.
- **A relabel must not move a line's cost.** Nut Roast draws "1 ml" of a 7,304 g
  batch — relabelled to 1 g it silently lost $2.39, because one gram of nut roast
  is nothing. Anything moving >3× is frozen as a visible debt instead.
- **A bar prep borrowed by a dish is still a bar prep.** Guacamole uses 100 ml of
  Super Lime Juice; "reached by a dish too" turned super juice into grams.
- **Your choice wins.** The builder's g/ml/ea selector now writes
  `unit_confirmed: true` and the house rule steps aside for that batch,
  permanently. A default that reverts your fix is worse than no default.

## Findings worth more than the migration

1. **`resolve_yield` read pack labels as yields.** Seven preps, all wrong —
   jalapeño tequila 7.5×, brisket 6× ($8.53 on every Meatlovers). Three are now
   *proved*, not argued: 7,000 ml of tequila cannot leave the jar as 1,000.
   `scripts/audit_batch_yields.py` runs "a batch cannot yield more than it
   contains" over every prep.
2. **The group-wide price series was stale for 15 ingredients, 63 dishes.**
   Broccolini on a **2 January** price. Mostly stale *low* — understating cost,
   overstating GP. Fixed in `core/domain.py`; 12 of 1,225 ingredients moved,
   nothing became unresolvable.
3. **Two $34 bottles cost $0.00.** Sold as bought, so the scrape decomposes them
   into no lines, and an empty recipe is free. Now carried whole.
4. **"Has a yield" was never the test for a batch.** It cost BBQ Wings its serve
   cost and briefly made three sold products report 100% GP. `cogs_blend` now
   tests yield **and no sell price**, which its own comment always said.

## Still open

- **Weigh two batches.** `data/_worklist/yield_verification.html` (printable) is
  ranked by revenue at risk: **$6.05M across 68 unweighed yields**, and
  Pizza Sauce ($1.06M, 146 dishes) plus Pizza Dough ($849k, 97) are a third of
  it. Chefs log results in `data/measured_yields.yaml`, which outranks everything.
- **Promotion.** Watch the diff a few days. Decide deliberately whether to accept
  the residual — it is a *correction*, not noise, and every attributed cause
  moves cost **up**.
- **11 preps still make more than they contain**, and 2 have a cook loss over
  55%. Phase 3 accuracy work, no decisions needed.
- **Chimichurri** was hand-entered as 650 ml and is now 650 g. Relabelled, not
  converted — for a mostly-oil sauce that is a real ~8% question. It is on the
  weighing list.

## Traps added to the list

- **CI regenerates `lightspeed_recipes_costed.json`.** Testing against the
  committed copy gives a green that means nothing. Run the converter.
- **Node suites SKIP their real-data checks when the feeds are not built** and
  report 0 failures on nothing, exactly as SESSIONS.md warns. Build the feeds.
- **`arch_guard` catches feeds going stale behind a `costs.csv` rebuild** — same
  trap, one layer down.
- **`cost_on` is venue-scoped.** A probe built with a placeholder venue prices
  against a different series.
- **Claim boundaries bite mid-task.** This work spanned `cost-book`,
  `sales-pipeline` and `ops`. Plan for several claims, and expect one to block —
  mine did, transiently, while another session held `ops`.
