# `data/functions_gp.json` — the contract

    data/function_tabs/*.json  ──►  modules/functions/pipeline  ──►  data/functions_gp.json  ──►  /functions/
    the comped tab, line by line        gross_profit()                    the feed                the screen

**Schema:** `data/schemas/functions_gp.schema.json` (`functions_gp/1`).
**Writes:** `modules/functions/pipeline/build_functions_gp.py`.
**Reads:** `dashboard/functions/` — the post-event outcome report.
**Additive only.** The page is deployed. Add fields, never rename them.

## What one entry is

Beverage gross profit for one function: revenue ex-GST, COGS ex-GST, gross
profit, GP%, drinks poured, drinks per head and per hour, COGS per head, the
menu value given away and per head, the margin foregone against the venue's
beverage run rate, and the ratio the function must out-earn displaced trade by
to be GP-neutral.

Every entry carries `caveats`, and that list is never empty. **A consumer that
renders `gp_pct` without them is misreporting the number** — `gpFigureHTML()`
in `dashboard/functions/functions.js` enforces this at the other end by drawing
a refusal instead of a percentage when the list is missing.

## The three soft spots, each its own field

**Mixer.** House spirits are costed in Lightspeed as the nip only; the mixer is
not in the recipe. A blend of $0.9483 a pour is added by assumption.
`mixer_est_ex_cents` is that estimate on its own line and `gp_pct_ex_mixer` is
the same sum without it — the other end of the range, worth 6.3 and 7.7 points
on the two nights measured. Not a footnote, a range.

**Uncosted lines.** A product with no recipe is not free. `uncosted_lines`
names and counts every one, so `total_cogs_ex_cents` can be read as the lower
bound it is and `gp_pct` as an upper one.

**Food.** Kitchen items are uncosted repo-wide and `cost_book_flags` warns that
food recipes price plated weight at raw purchase rate. So food revenue is taken
*off* the top line (`bev_revenue_inc_cents` < `revenue_inc_cents`) rather than
credited against a cost nobody has, `gp_basis` is always `beverage`, and the
`food_cogs_unknown` caveat says so.

## `cost_book_as_of` — read this before quoting a figure

`data/lightspeed_recipes_costed.json` carries no effective date. It answers
"what does a pour cost today", never "what did it cost in August". Between 8
and 20 August 2026 the live book moved Rooster Rojo Blanco Tequila [House] from
$1.9641 to $2.6065 and Lychee Martini from $3.9140 to $5.0328.

So a night is costed against `data/function_tabs/cost_book_<date>.json` when one
exists — a **closed** snapshot, where a product that is absent was uncosted on
the night and is reported as uncosted rather than as free. Otherwise the live
book is used and `cost_book_as_of` is `"live"`, which means the figure will
drift as recipes are recosted and is **not** reproducible.

## `booking_id` — the join, and the only one

The feed identifies a night by what the bar called the tab. The diary
identifies it by who booked. **"Dazzle drinks" is a tab name, not a customer**,
and 8 August 2026 carries *two* functions, so neither the name nor the date can
pair them. Either join would file one night's gross profit under the other
booking — nothing 404s, both figures are real and correctly caveated, and
Roman's 59.3% appears under Harry Baker.

So the id of the diary row is **recorded on the tab file by a human, against
the evidence**, copied onto the feed unchanged, and is the only thing
`dashboard/functions/functions.js::joinComputedReports()` matches on.
`booking_evidence` carries the reasoning with it, onto the screen: a pairing
matched on the money and one matched on a hunch look identical in a JSON file.

The two published nights, and what pairs them:

| tab | booking | the evidence |
| --- | --- | --- |
| `Dazzle drinks` | `e93280ad65d1` — Roman Bunting, 8 Aug 15:30, Old Stow, 40 covers | his is the only note of the four naming the **Razzle Dazzle** package; the ticket is **all beverage, no food line**, which is what "pizzas/wings through the night, everyone pays on arrival" means; the tab pours **cocktails**, which Razzle Dazzle includes and Soiree does not |
| `Harry` | `1878ce4a6350` — Harry Baker, 8 Aug 18:30, Main Hall, 25 covers | the money splits exactly — $80 × 19 heads = $1,520, of which **$380 food is $20 a head**, the "$20pp food" his note names, leaving **$60 a head of drinks, the Soiree price**; not one cocktail on the tab; 19 through the door sits inside his note's "15-20 pax" |

Knowing the booking is also what lets `booked_guests` be filled in at all —
40 for Roman, 25 for Harry, off the covers on their own diary rows — so the
report can say *"25 through the door, against 40 on the booking"* instead of
quietly dividing by the wrong number. It changes no money: nothing computes
from it.

It is **optional and stays optional** — a function with no booking joins to
nothing and the diary rightly goes on saying "no report yet". What is not
optional is that a *published* entry carry one: both suites assert it, so a new
tab that omits the id fails rather than shipping a report the screen can never
show.

Two entries claiming one booking attaches **neither**, and the card says so.
One of them belongs to a different night and picking either is the exact error
the id exists to make impossible.

## Two sources for one night

A function can hold a hand-recorded outcome on its brief *and* a computed one
here. **The computed one wins**, because it is re-derivable: the line items are
still in `data/function_tabs/`, priced by a book pinned to the night, so
anybody can run it again in a year and get the same answer. The hand-recorded
one is a summary somebody typed once and nothing can check it.

A disagreement is **named, not resolved** — it means one side is wrong about a
measured fact (heads, drinks, revenue, COGS) and that is worth an hour with the
POS. The clash notice lists those fields, both values, above the figure. It
never prints the hand-entered percentage: **one night gets one GP figure on the
screen**, and a second would be quoted later without its caveats and would be
the one nobody can reproduce.

## Input: `data/function_tabs/<date>_<slug>.json` (`function_tab/1`)

    { "schema": "function_tab/1", "name", "date", "venue", "package",
      "booking_id", "booking_evidence",
      "package_price_inc", "package_hours", "heads", "booked_guests",
      "food_revenue_inc", "pos_refs",
      "lines": [ { "product", "qty", "menu_value_inc" } ] }

All money is a **string** in these files, parsed straight to `Decimal`. A JSON
number here would be a float, and `539.00` is not a float.

`product` is the POS product name as the till prints it, not the recipe name.
The pipeline resolves it through `scripts/cogs_blend.book_cost`, which is the
same lookup the P&L uses — exact name first, then the category-word-stripped
form that settles `Fresh is Best Lager Pint` against the book's `Fresh is Best
Lager - Pint`.

## Where the line items come from — the open question

Today they are hand-entered, from the transaction sweep of 8 August 2026. That
is honest and it does not scale. What an ingestion would need, stated so the
next pass does not have to rediscover it:

1. **Transaction-level data, not the product mix.** `data/products_daily` is
   net of comps and cannot see these tabs. Verified: on 2026-08-08 the day's
   mix carries Beetle Juice 5.0 while the Dazzle drinks tab alone poured 45,
   and White Light Pure Vodka [House] 42.0 against 118 across the two
   functions. Every comped line is missing. The daily pull is the wrong source
   and no amount of filtering fixes it.
2. **A way to name the tab.** Answered, for the reading end: `booking_id` on
   the tab file pairs a night to its diary row and the screen joins on that and
   nothing else. What is still open is the *writing* end — an ingestion has to
   decide which booking a swept tab belongs to, and today a human does that and
   writes down why in `booking_evidence`. `pos_refs` is free text, so an
   ingestion either matches on it loosely and reports what it matched, or the
   field gets a machine part (receipt number, sale id) beside the prose.
3. **The comp itself as the marker.** A function tab is distinguished from
   ordinary trade by being discounted to $0.00, not by what is on it. A sweep
   that pulls "sales on this date with a 100% discount, grouped by tab" finds
   every function without needing to know a function happened.
4. **The date the tab closed**, so the right dated cost book is chosen — and a
   snapshot written for that date, or the figure is not reproducible.

Deliberately not built today: a Lightspeed scraper nobody can test from here.
The costing is the part that had to be right, and it is separable from where
the lines arrive from — `gross_profit()` takes a list and a callable and has no
opinion about either.
