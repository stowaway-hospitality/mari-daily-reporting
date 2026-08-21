# The functions module publishes two feeds

`data/functions_gp.json` — what a package function that has already happened
actually made. Documented first, below.

`data/functions_pipeline.json` — every enquiry that has not happened yet (and a
good many that never will). Documented second, at the end of this file.

They are separate feeds because they answer separate questions at separate
ends of the same customer, and nothing joins them: the GP feed is keyed on a
booking, the pipeline feed on a monday row, and an enquiry that becomes a
booking passes through a brief in between.

---

# `data/functions_gp.json` — the contract

    data/function_tabs/*.json  ──►  modules/functions/pipeline  ──►  data/functions_gp.json  ──►  /functions/
    the comped tab, line by line        gross_profit()                    the feed                the screen

**Schema:** `data/schemas/functions_gp.schema.json` (`functions_gp/1`).
**Writes:** `modules/functions/pipeline/build_functions_gp.py`.
**Reads:** `dashboard/functions/` — the post-event outcome report.
**Additive only.** The page is deployed. Add fields, never rename them.

ONE FIELD HAS BEEN RENAMED SINCE, and the exception is recorded here rather
than left to be discovered. `drinks_per_hour` became `drinks_per_hour_room`,
and `drinks_per_head_per_hour` was added as the figure that should have been
published all along. Keeping the old name would have kept the ambiguity that
caused the error — it read as a drinking pace and carried the room's
throughput — so renaming it was the correction, not a side effect of one. It
was safe to do because this feed has exactly one consumer,
`dashboard/functions/functions.js`, which ships from this repo in the same
commit, and because `?v=` on the module was bumped so no cached copy reads for
a field the new feed no longer has. That is the whole of the licence: a rename
is allowed when the only reader is in this commit and the cache is busted. It
is not licence for the next one.

## What one entry is

Beverage gross profit for one function: revenue ex-GST, COGS ex-GST, gross
profit, GP%, drinks poured, drinks per head, the drinking pace per head per
hour and the room's throughput per hour beside it, COGS per head, the
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

`package_hours` is **how long the drinks ran**, and it is optional: omit it and
`DEFAULT_PACKAGE_HOURS` — two, per the brochure's "2-HOUR DRINKS PACKAGES" —
applies. It is **not the room hold**. The hold is how long the party has the
space, it is usually longer (both 8 August bookings were held four hours on a
two-hour package), and it lives in the booking engine as
`functions.DEFAULT_DURATION_HOURS` where it prices the peak window. Both
fixture tabs carried the hold here once and every pace figure came out a third
too low. Set this field only where a package genuinely ran to something other
than two hours.

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

---

# `data/functions_pipeline.json` — the contract

    monday board 5027645686  ──►  data/functions_monday_raw.json  ──►  build_functions_pipeline.py  ──►  data/functions_pipeline.json  ──►  /functions/ Pipeline
    FUNCTIONS ENQUIRY TRACKER     the capture, dated and committed        modules/functions/enquiries.py        the feed                        the tab

**Schema:** `data/schemas/functions_pipeline.schema.json` (`functions_pipeline/1`).
**Writes:** `modules/functions/pipeline/build_functions_pipeline.py`.
**Reads:** `dashboard/functions/` — the Pipeline tab, and nothing else.
**Additive only.** The page is deployed and a stale browser tab is still
reading the old field names.

## Why it exists

The Pipeline tab read *briefs* — rows in the booking engine's
`function_briefs` table. That table has been empty since the day it was
created, so the tab said **"Pipeline 0"** while **sixty** live enquiries sat on
the monday.com FUNCTIONS ENQUIRY TRACKER with a dated history of every reply,
every chase and every silence on them. Zak asked three times why it was empty.

Nothing was broken and nothing could have caught it: an empty list is a
perfectly correct rendering of an empty table. It was answering a question
nobody was asking.

The briefs did not go away and must not. **A brief exists so a deposit link can
be minted and a room held** — that is the only thing it is for. So the screen
now has three tabs: the diary (rooms held), the pipeline (enquiries), and
briefs (the paperwork). "Take a deposit" on an enquiry is what creates one.

## What one entry is

One row of the board, with nothing dropped. Per enquiry: the monday item id and
its url, the name as typed, the group, whether it is archived, the event date,
follow-up date, group size, occasion, stage, outcome, lost reason, contact
email and phone, start time, area, source, the notes **in full**, and the seven
columns added in August 2026 — Drinks (`color_mm6dn3ba`), Bar tab covers
(`text_mm6dgpr2`), Food (`text_mm6dvkw9`), Deposit (`color_mm6dj4yx`), Music
(`color_mm6d7ar4`), Settling up (`text_mm6dgnbc`) and Min spend
(`numeric_mm6dnjnp`). Every column id was confirmed against `get_board_info`
rather than taken on trust; a monday column id is opaque, so the wrong one
reads a plausible value from the wrong column and nothing errors.

On top of the board's own columns it carries four derivations, and each of the
four is documented in `modules/functions/enquiries.py` at the code that makes
it: `whose_move`, `outstanding`, `flags`, `brief_prefill`.

## Every enquiry, including the archive

Zak: *"the pipeline should reflect all enquiries, whether or not they've been
replied to."* Nothing is filtered.

    Stowaway Bar   16
    Harry Gatos     8
    archive        36
    ─────────────────
                   60

The archive is **more than half the board**. A pipeline that dropped it would
report 24 and look exactly like the bug it replaced. It is drawn last in the
rail, not first, and not hidden.

Nineteen rows have no date, no headcount and no notes at all. They are
published too. They are real enquiries somebody typed a name into and never
came back to, and a list that hides them is how they stay forgotten.

## `whose_move` — the thing the board knows that nothing else does

The Notes column carries a dated running log, written by the autodraft
automation on the venue's Mac:

    [2026-08-19 auto] Awaiting customer reply — nothing back since our 11 Aug email (8 days)
    [2026-08-15 auto] Customer replied 14 Aug: "This looks great! We are super keen!!"
    [2026-08-16 auto] Steph replied 15 Aug 2:07pm — ...

It is read off the **last dated entry and nothing else**, because the last
entry is the only one describing the current state; an older "awaiting customer
reply" is a fact about July. Four answers:

| verdict | what it means | how many |
|---|---|---|
| `us` | the entry names something for us — an unanswered question, a chase, an outcome left for a human, a correction, or a fresh enquiry with no reply logged after it | 23 |
| `them` | the entry says in words that we are waiting on the customer | 12 |
| `nobody` | there are no notes on the row at all | 19 |
| `unclear` | anything else | 6 |

**`unclear` is a real answer and is drawn as one.** The six are: two logs cut
off mid-sentence by monday's character cap, two notes that are not dated logs
at all, and two bare "Customer replied…" entries with nothing after them saying
whether the reply was answered. Guessing on those would put a confident verdict
where the evidence does not support one, and the wrong guess is expensive in
one direction only: a false "waiting on them" is indistinguishable from
"nothing to do".

**`us` beats `them` when one entry says both.** *"Awaiting customer reply — no
reply since our first reply on 14 Jul (23 days). Large enquiry (100 pax,
28 Nov) — worth a chase."* says both. The chase wins, because "awaiting a
reply" states what last happened while "worth a chase" names the next action,
and the next action is ours. Four rows turn on this.

Whatever the verdict, **`whose_move_evidence` carries that entry verbatim** and
`whose_move_since` its date. The screen draws them inside the same block as the
verdict, at body size — never a tooltip, never a `<details>` — so the judgement
can be checked against the sentence it was read off. `whose_move_why` names the
phrase that decided it.

This is prose matching, and prose matching rots silently: a wording change in
the autodraft flips a dozen rows, nothing errors, and the screen starts saying
there is nothing to do. `modules/functions/tests/test_functions_pipeline.py`
pins the whole distribution and a dozen named rows against the sentences they
were read off, so the rot fails a test instead of shipping.

## `flags` — surfaced, never resolved

| code | what it is |
|---|---|
| `date_conflict` | the row TITLE and the Event date column disagree |
| `date_shared` | two live enquiries at the same venue want the same date |
| `notes_truncated` | the note is at monday's 2000-character cap |
| `no_floor_plan` | Harry Gatos: no floor plan in the engine, so no room can be held |
| `no_contact` | no email and no phone — nobody to chase |

Nothing here picks a side. **"Marcus - 10th Oct"** has a form submission saying
3 October, flagged unresolved since 22 July; the feed publishes `2026-10-03`
because that is what the column says, and publishes the disagreement beside it.
Choosing silently is how the wrong Saturday gets held.

`date_shared` needs **both rows live and both at the same venue**. Keying on the
date alone reported thirty of these — 19 September is Heather at Stowaway and
Ruth at Harry Gatos, which is two rooms in two buildings — and a flag that is
usually noise is a flag people stop reading. It also says out loud that two
functions *can* share a night in different rooms, because 8 August 2026 was
two.

## `brief_prefill` — the deposit hand-off

The body that would be `POST`ed to `/api/admin/functions` if somebody presses
**Take a deposit**, and nothing creates it until they do. Sixty enquiries are
not sixty briefs; a brief is minted when there is money to take.

`source_ref` is **`monday:<item id>`**. `create_brief` upserts on `source_ref`,
so a second press — or a future recurring sync — converges on the same brief
instead of littering the table with duplicates.

Values are mapped into the engine's own vocabulary, once, here rather than in
the page:

* the board's `SOIRÈE $60pp` is the engine's `SOIRÈE`. `functions.validate()`
  checks `drink` against `DRINK_CHOICES` because **a package name is a price**,
  and it rejects one it has never heard of.
* `Whole venue` and `Not sure yet` are real board answers and neither is an
  area a brief may name — `validate()` rejects `Whole venue` outright. They are
  sent as **nothing** rather than as a guess, so the panel shows the room as
  unanswered, which is true.
* the page applies a second filter at click time (`depositPrefill`), because
  `accepted_areas` comes off `/api/admin/functions/config` and only the browser
  has it. Offering a room the save refuses is worse than offering none.

`brief_prefill` is **null for every Harry Gatos row**. There is no floor plan
for that venue in the booking engine, so a brief there could never hold a room,
and a button that mints a deposit link against a room nobody can hold is worse
than no button. Those eight rows are tracked here and booked by hand.

## The capture, and why there are two files

`data/functions_monday_raw.json` (`functions_monday_raw/1`) is the board as it
was read, committed. The feed is derived from it with no clock and no network.

Two files rather than one because of two separate obligations:

1. **MODULES.md rule 4** — a derived file that no longer reproduces from its
   source is a fossil. With the capture committed, `--check` rebuilds and
   byte-compares on every pytest run, the same contract `data/costs.csv` lives
   under. If the only source were the live board, CI could never check the feed
   at all: it has no token, and the board changes hourly.
2. **`captured_at` is dated evidence.** It rides onto the feed and onto the
   screen, so the age of every fact below it is visible.

## Staleness — say the age or say nothing

`captured_at` is **when the board was read**, not when the file was written.
The Pipeline tab draws it above the list, at body size, in words: *"Read from
the board today (2026-08-21)."* Past two days it turns into a warning that says
the enquiries have moved since, and it always links to the board, which is
always current.

A stale feed presented as live is worse than an empty one — the empty screen at
least told the truth.

## To make it refresh by itself

**One secret is needed, and this pass could not set it:**

    Settings → Secrets and variables → Actions → New repository secret
    Name:  MONDAY_API_TOKEN
    Value: a monday.com personal API token with read access to board 5027645686
           (monday.com → avatar → Developers → My Access Tokens)

With it present:

    MONDAY_API_TOKEN=... python3 modules/functions/pipeline/build_functions_pipeline.py --fetch

re-reads the board over GraphQL, rewrites the capture with a fresh
`captured_at`, and rebuilds the feed. Without it the same command exits 2 and
says so; the plain command still rebuilds from the committed capture.

A workflow to run that on a schedule is **not** included: `.github/workflows/`
is `ops`-owned (SESSIONS.md rule 7) and this pass held `bookings`. The step it
needs is the one line above plus a commit of both files, and it should run
after the autodraft has written the morning's log entries.

## Reading the board directly

`--fetch` asks for `column_values { id text }`, which gives the label for a
status, the ISO date for a date, and the raw string for everything else — the
same shape the MCP capture holds. One known difference: monday's phone column
comes back over GraphQL with a trailing ISO country code (`+61411642774 AU`),
which `enquiries._phone` strips, so both routes produce the same feed. It pages
at 100 with a cursor, because `items_page` will not return sixty rows and a
silent first page is exactly the failure this feed exists to end.
