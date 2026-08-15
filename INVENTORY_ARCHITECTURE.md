# Owning inventory

Design note. Zak, 2026-08-15: *"i want to start building our own inventory
tracker ... it will eventually replace us using lightspeed to track inventory ...
the only thing i'm really thinking about is how to track deductions to stock."*

The stocktake app is ours and lands in this repo, so counts stop being a CSV
imported into someone else's database. This note says what the rest of the system
has to be, what it costs, and which single thing decides whether it works.

## We already own four of the five pieces

| Piece | Where | Status |
|---|---|---|
| Stock IN | `modules/invoices/` | DONE — 15 suppliers, cent-accurate, supplier code → ProductID bridge |
| Recipes | `data/recipes/*.yaml`, `data/lightspeed_recipes.json` | DONE — 852 recipes, effective-dated (`recipe_as_of`) |
| Sales | daily Insights pull | DONE — but TRUNCATED, see Prerequisite |
| Counts | the stocktake app | DONE — moving into this repo |
| **The ledger tying them together** | — | **This is the build.** |

## The model: one append-only movement ledger

Every change to stock is the same shape:

    (ts, venue, item_id, qty_base_units, direction, reason, source_ref, actor)

Six reasons cover the whole business:

| reason | written by | meaning |
|---|---|---|
| `receive` | invoice pipeline | a supplier delivered |
| `sale` | daily aggregation | recipe × units sold |
| `production` | prep/batch | consumes ingredients, YIELDS a prep item |
| `waste` | a human, in the stocktake app | spill, spoil, comp, staff feed |
| `transfer` | a human | venue → venue (HG food rings through the Stow till) |
| `count` | stocktake | sets truth and BOOKS the difference as its own row |

Then, and this is the whole point:

    theoretical on-hand = Σ movements
    counted on-hand     = last count
    VARIANCE            = counted − theoretical      ( = waste + theft + portioning drift + recipe error )

`data/` is append-only (ARCHITECTURE.md). A correction is a new row, never an
edit — so "what did we think last Tuesday" stays answerable, which is the same
property the restatement ledger gives the P&L.

## Deductions: the question that prompted this

Both routes compute the deduction from OUR recipes. Lightspeed never says "you
used 30 ml of gin" — it says a Jalapeño Marg was sold, and our book turns that
into ingredients. So the paid API changes only WHEN sales arrive, not WHETHER we
can deduct:

* **Daily batch (free, already running).** Yesterday's mix, deducted overnight.
  Sufficient for ordering, par levels, variance and COGS.
* **GET API (paid).** Sales during service, so depletion is live.

The only decision that needs intraday is *"will we run out of X tonight?"*
Everything else is a daily-cycle question. **Build on the daily feed first.** It
is free, it already works, and it exercises the entire model. Buy the API later
to shorten the loop, not to make the thing possible.

**Do NOT sync stock levels FROM Lightspeed.** That is the system being replaced,
and its numbers are already fiction — `stowaway-stocktake` exists because counts
are trued up by hand. We compute on-hand ourselves and treat LS as a second
opinion at most.

## PREREQUISITE: persist the full daily product mix — DONE 2026-08-15

`daily_aggregator.py` kept `product_breakdown[:20]`. The Insights CSV has all
~300 lines. Deducting from a truncated mix under-deducts silently and forever —
the stock would drift down slower than reality and every variance would be wrong
in the flattering direction. Fix this before writing a single ledger row.

What was built:

| Thing | Where |
|---|---|
| Per-day fact, untruncated | `data/product_mix/<prefix>_<date>.json` |
| Rollup the ledger reads | `data/products_daily/<year>.csv` (`scripts/build_products_daily.py`) |
| History rebuild | `scripts/backfill_product_mix.py` |
| Guard | `tests/test_product_mix.py` |

`top_products` in the daily record stays 20 on purpose — it is the dashboard
panel and it ships to every open browser tab. The mix is a separate file, so
widening one cannot bloat the other and pointing the ledger at the wrong one is
a visible mistake rather than a silent 10x under-deduction.

Every mix carries its own reconciliation: the lines must sum to that day's
revenue, on the same ex-GST basis the day settled on, or the file is written
`reconciled: false` and says so. 110 venue-days backfilled (2026-07-06 →
2026-08-14, the full daily-grain history we hold — the Looker backfill behind
`products_weekly.csv` is weekly-only, so it cannot feed a daily mix). All 110
tie to the cent.

**It found something on the way in.** Nine Harry Gatos days —
07-12, 07-14, 07-19, 07-21, 07-26, 07-28, 08-04, 08-09, 08-11 — have the
REPORTING-GROUP report committed under the product report's filename. Revenue
sums correctly off those files, so the P&L never complained; there is simply no
product detail in them, and `top_products` has been `[]` on those days all
along. They are refused rather than written as an empty mix, because a zero-line
mix reads as "nothing sold" and would deduct nothing while reporting a clean
variance. Nine days is 23% of HG's history — fix the export before the ledger
goes live or HG's variance is measured on three weeks in four.

## THE THING THAT DECIDES IF THIS WORKS: recipe coverage

### RE-MEASURED 2026-08-15 — this gate is mostly already passed

The ~0% below was measured against the truncated top-20-BY-REVENUE mix, before
the 648-recipe costed book landed. With the full mix now persisted, the real
number, over 610 trading days and ranked properly by VOLUME
(`scripts/recipe_coverage_worklist.py`):

| scope | covered by our own costed recipes |
|---|---|
| top 25 by units | **92.3% of units, 100% of revenue** |
| top 50 by units | **88.2% of units, 95.3% of revenue** |
| all 1,670 products | 72.3% of units |

Ranking by volume rather than revenue is the point: a $4 side of chips outsells
a $95 bottle many times over and empties far more shelf. Revenue ranking is what
made this look like a crisis.

**Seven of the top 50 have no recipe, and only four are real work:**

| units | rev ex | product | |
|---:|---:|---|---|
| 7,333 | $668 | Aioli Dipping Sauce | needs a recipe |
| 3,050 | $25,933 | Bombay Dry [House] | needs a recipe |
| 2,694 | $7,786 | Fresh Lime Soda | needs a recipe |
| 2,675 | $182 | Tomato Dipping Sauce | needs a recipe |
| 4,037 | $0 | On The Rocks | a modifier, not a dish |
| 2,691 | $12,313 | Staff Dinner $5 | staff feed, not a recipe |
| 3,176 | $67,819 | Fancy Pants Parmy | discontinued — last sold 2026-03-06 |

So recipe coverage is NOT what is blocking this build. Stock IN is. See
"Step 2" below.

### The original measurement, kept for the record

Measured 2026-08-09: recipe coverage of the TOP SELLERS was ~0% of
revenue — the recipes that exist are the tiki/sake/cocktail program, not the
beers, wines, burgers and classic cocktails that carry the volume.

An inventory system deducting from recipes it does not have reports confident
nonsense. So the order is:

    recipes for the top 50 sellers by VOLUME  →  sale-deduction  →  variance

Nothing else on this page matters more than that list.

## Seven traps, every one already paid for in this repo

1. **Unit identity. The big one.** In one day we found a CTN-6 read as one tin
   (**6x**), ILG cases read as bottles (**6x**), Red Chilli (**10x**), Angostura
   (**13x**). In COSTING a bad unit is one wrong dish. **In INVENTORY it is wrong
   on every movement, forever, and it compounds.** One canonical base unit per
   item (g / ml / each); everything else is a DECLARED conversion with evidence;
   an unprovable conversion REFUSES rather than guesses.
2. **Batches are first-class.** `production` consumes ingredients and yields a
   prep item at its REAL Lightspeed yield — `data/recipe_yields.yaml`. The name
   is a label, not a yield: Jalapeño Tequila "[1L]" makes 7,500 mL. 12 batches
   still have no yield set in Produce; 19 Harry Gatos ones are unread.
3. **Modifiers.** Extra shot, no cheese. Insights lists them separately: deduct
   them, and do not double-count the base dish.
4. **Venue attribution.** Mari has no till and HG food rings through Stow.
   `daily_aggregator.classify_product` already solves this — INHERIT it, never
   re-derive it (`scripts/eatclub/config.py` is the cautionary example of a
   second copy).
5. **Fractional units.** Half a keg, an open bottle. Track base units and accept
   decimals. Do not round to whole containers.
6. **Effective-dated recipes.** A sale on the 8th deducts using the recipe in
   force on the 8th, whenever it is computed. `recipe_as_of` already does this.
7. **Waste needs a human path.** If waste can only be inferred, every variance is
   "waste or theft or error, unknown". Put it in the stocktake app as a first-
   class entry.

## What makes it better than what you can buy

* **Variance ranked by dollars**, per item per period, with the periods and the
  count dates it is measured between.
* **Confidence on every line.** Where recipe coverage is thin, say "not
  measurable yet" instead of printing a number. Fail toward review — the same
  rule the cost book runs on, and the reason its flags are trusted.
* **Close the loop already built here:** depletion → par levels
  (`par-management`) → drafted POs (`lightspeed-reorder`) → invoice receipt →
  back into the ledger. Purchase to plate, one system, no re-keying.
* **The waste number.** Nobody in this business has it today. It is the output
  that justifies the whole build.

## Build order

1. ~~Persist the FULL daily product mix~~ — DONE 2026-08-15 (prerequisite above)
2. Ledger schema + `receive` + `count` — **schema DONE, `receive` PARTIAL,
   `count` has no source yet.** See "Step 2, and the gate it hit" below.
3. Recipes for the top 50 sellers by volume (the gate)
4. `sale` + `production` movements
5. Variance report, with confidence
6. Only then: consider paying for the API

Steps 1–2 are useful on their own. That is deliberate: if the project stalls
after step 2 you still have a working stock ledger fed by real invoices and real
counts, which is more than exists today.

## Step 2, and the gate it hit — 2026-08-15

Built: `scripts/ledger.py` (the append-only movement ledger, `data/ledger/
movements_<year>.csv`), `build_item_base_units.py`, `build_receive_movements.py`,
`tests/test_ledger.py`.

The schema is the one at the top of this page, and it enforces itself: base unit
must be g/ml/each, quantity may not be negative (direction carries the sign),
`item_id` must be namespaced exactly as the recipe book does it
(`lightspeed:21999746`) so the two cannot drift apart, and every row needs a
`source_ref`. `on_hand()` measures FORWARD FROM THE LAST COUNT — a count is
truth, not an adjustment, or every stocktake gets added to the stock it was
measuring.

**Unit identity, per trap 1.** `data/item_base_units.csv` derives one canonical
base unit per item from how recipes actually consume it. 546 of 578 items
resolve. The other 32 are refused rather than guessed, and the refusals are
informative:

* **21 packaged drinks — Peroni, Corona, VB Tinnie, the cans, Coke 1.25L — are
  consumed in BOTH `each` and `ml`.** One id, two ideas of what the item is.
  Declaring a can's volume fixes it; guessing which recipes meant what does not.
* 7 items exist only in `bunch` or `tray` (coriander, mint, parsley, thyme,
  broccolini, radish, Lime [Tray]). No provable gram weight.
* `Lemon [Sliced]`, `Avocado [Tray]` and `Sunshine Smokey BBQ Sauce [3L]` are
  consumed in both g and ml.

**RECEIVE: 2,572 of 3,501 invoice stock lines book — 73.5%, across 712 distinct
items.**

The first cut of this managed 12.5%, because it insisted every line resolve to a
`lightspeed:<id>` through `product_map.csv`. That was a self-inflicted wound:
this repo's identity model already treats a supplier code as a first-class key.
`core.domain.purchasable_id()` builds it, and recipes already reference
`foodlink:102689` and `fresh-fruit-team:AH20T` next to `lightspeed:` ids. So the
rule is now: use the Lightspeed id where `product_map.csv` has it — that unifies
one item bought from two suppliers — and otherwise let the supplier key stand.
A line with NO supplier code still refuses, because falling back to the
description is how ALEHOUSE CRISP KEG becomes the wrong $27.50 keg.

Pack sizes are inherited too. `data/pack_overrides.yaml` is exactly the
declared-conversion-with-evidence table trap 1 asks for — a human writes what a
pack holds, with their name and the date — and 208 lines book today because
somebody did that.

**What still refuses, ranked by money** (`scripts/pack_confirmation_worklist.py`):

| why | lines | $ incl |
|---|---:|---:|
| pack unit `box` — no confirmation yet | 404 | $34,412 |
| item delivered in two dimensions — refused, not averaged | 281 | $3,980 |
| unit clash: recipes use ml, line delivers each | 65 | $15,730 |
| unit clash: recipes use g, line delivers each | 45 | $1,884 |
| pack unit `bunch` | 38 | $429 |
| no supplier code at all | 33 | $5,643 |
| the rest | 63 | $3,503 |

**$57,581 of deliveries sit behind 147 items, and the top 12 confirmations
release $34,239 of it.** That is a short afternoon with a box cutter, and it is
the single highest-leverage job left in this build:

    be-foods:15555   FZ CHIPS - CRISPY COATED    $4,070 blocked
    be-foods:16952   FZ CHICKEN BREAST           $2,784
    be-foods:19957   FZ CHIPS - ULTRA SWEET      $2,338

Two of the top three blockers are the kegs — `ALEHOUSE PREMIUM KEG` ($12,201)
and `ALEHOUSE CRISP KEG` ($3,073) — delivered as `each` while recipes pour them
in `ml`. A keg's volume is a declared fact; confirm it once and both clear.

The worklist prints ready-to-fill `pack_overrides.yaml` stubs (`--stubs`) and
flags where the supplier PRINTED the size and the parser could not use it
(`BARRAMUNDI FILLETS ... S/OFF 5KG` arrives on a UOM token of `box`). Those are
shown as evidence for the person confirming, never applied — the description is
free text that wraps, truncates and carries substitution notes.

**`count` has no source in this repo yet.** The stocktake app is still outside
it. The ledger accepts count rows and the supersede logic is tested against a
worked example; nothing writes them.

So step 2 does NOT yet give a trustworthy on-hand — nothing counts yet, so there
is nothing to be right against. But the plumbing is real, three quarters of
stock IN books, and what remains is a list with dollars against each line.

### What actually blocks this build now

Not the API, and — re-measured — not recipes either.

1. **Counts.** No source in this repo. Everything else is a way of predicting a
   number that only a count can confirm.
2. **~12 pack confirmations**, worth $34k of currently-unbookable deliveries.
   Someone opens a box and writes down what is in it.
3. **Harry Gatos' own till** is on a separate Lightspeed account, so HG has no
   daily-grain history and one still-wrong published day (2026-08-10).
4. Four real recipes: Aioli, Bombay Dry [House], Fresh Lime Soda, Tomato
   Dipping Sauce.

## Standing rules that apply here

Money is `Decimal`. `data/` is append-only. Schema changes additive-only. No
business logic in `dashboard/*/index.html`. Every derived number carries a guard
whose test holds real measured values. **Errors that flatter — low cost, high GP,
stock that lasts longer than it should — are the dangerous ones.**

_Written 2026-08-15._
