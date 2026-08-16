# Phase 2a — Marilyna's through the one-way door (staged, not cut over)

Written 2026-08-16, continuing `HANDOFF_20260816_full.md` §3a. Read
`COST_BOOK_ARCHITECTURE_PLAN.md` Part V (T1, T2, T4) first; this is what
executing it actually turned up.

## What exists now

`data/recipes/_staged/marilynas.yaml` — **224 products + 13 sub-recipes, 1,511
lines**, every line carrying `source`:

| source | lines | what it means |
|---|---|---|
| derived | 715 | scaled from another size, or a measured cook yield |
| scrape | 525 | **nobody has ever checked this.** The backlog, now countable |
| weighed | 238 | Zak's regular-pizza grams |
| mirrored | 22 | put back from the same pizza in another size |
| authored | 9 | typed into the recipe builder by a person |
| rule | 2 | a written rule with arithmetic proof corrected it |

Built by `scripts/materialise_recipes.py`, diffed by
`scripts/shadow_diff_recipes.py`, run daily by
`.github/workflows/shadow_diff.yml` at 07:15 Sydney, pinned by
`modules/recipes/tests/test_materialised_mari_book.py`.

**It is STAGED. Nothing has cut over.** `_staged/` is a subdirectory on purpose:
`data/recipes/*.yaml` is globbed non-recursively by the converter and by
`test_saved_recipe_log_is_unambiguous`, so a staged file cannot be picked up by
accident. Promotion is `materialise_recipes.py --promote`, and it IS the
cutover — `cogs_blend._load_our_costs` prefers the builder book over the costed
book, so the P&L switches engines the moment the file lands at the real path.

## The first diff, fully attributed

**217 of 224 products cost both ways. Max |delta| $0.1034, sum $0.53.**

Every dollar is attributed to one of three causes. Nothing is unexplained.

1. **Sub-cent rounding on Wings Deals** (≤ $0.0011 each) — from freezing a
   per-unit cost at six decimal places on `manual` lines.
2. **Sub-recipe lines the old engine never costed from our book at all.** Large
   Garlic Cheese Pizza is the worst at +10c: the scrape carries
   `our_cost: None, eff_cost = ls_cost`, so the P&L has been publishing
   Lightspeed's 6c for 43 g of garlic oil — **$1.40/kg against our invoice-fed
   $3.80/kg**. This is the migration working. It moves cost UP, the safe
   direction. It is deliberately *not* frozen into a manual line: that would
   have held the diff at $0.0011 by embedding Lightspeed's number in the new
   book permanently, which is the thing this migration exists to end.
3. **The Jimmy Jury family, down ~0.75c** — the one deliberate correction (see
   `data/batch_yield_units.yaml`).

## Three findings worth more than the migration

### 1. `Cooked Beef Brisket [1Kg]` was costing 6× — $8.53 a pizza

`build_recipe_feeds.resolve_yield` prefers a bracket in the name over
`prep_yields.yaml`, on the stated grounds that "a MEASURED yield in the name
always beats an estimate". For this record the bracket is a **Lightspeed pack
label, not a yield**: `prep_yields.yaml` says 6,000 g on a worked cook-loss
basis (10,000 g raw × 60%, corroborated by Lightspeed's own $25.00/kg seed
implying 58.5%). Believing the name put $8.53 on every Meatlovers and Sanchez.

Same shape as the black-bean 6× the plan says a second derivation keeps
producing. Pinned in a test. **`resolve_yield` is used by `build_recipe_feeds`
too — someone should check whether any other bracket is a pack label.** That is
the highest-value loose end here.

### 2. Spanish Onion carries a Marilyna's-specific cost, against T6

T6 (Zak, 15 Aug) says the group pays one cost per ingredient and a venue-split
cost is a defect. Spanish Onion still has one: $0.0726/g for Mari against
$0.0798 group-wide, so the two engines disagree on 48 lines. Materialised as a
`manual` line reproducing today's figure rather than silently repricing 139
products. **This belongs in Phase 1 cleanup, not here.**

### 3. Two batch yields had no unit at all

`prep_yields.yaml` states its own basis as "sum of ingredients ×1" — and that
sum adds grams to millilitres, and in one case to *bunches*:

- Garlic Oil: 1000 g garlic + 500 ml oil = "1500 ml"
- Mint Yoghurt: 1000 g yoghurt + 100 ml lime + 2 bunch = "1102 ml"

A number built that way has no unit; the `ml` is a label somebody picked. All 16
drawing lines are in g. Reading the label off the dominating component removes
an assumption rather than adding one — no density was declared. The arithmetic
is in `data/batch_yield_units.yaml`.

Chimichurri is the opposite case: its yield really is a volume, and the
mislabelled thing is the drawing line (scrape says 60 g, the hand-authored
"Jimmy Jury Aioli" says 60 ml — a human wrote ml).

**These yields are still estimates and still wrong in the flattering direction
by roughly the oil fraction (~2.7% on Garlic Oil). WEIGH THE THREE BATCHES.**

## Second pass, same day — "fix everything"

Three of the four open items are fixed at source. The fourth is deliberately not.

### FIXED — `resolve_yield` read a pack label as a yield (all venues)

Not a Mari problem. `build_recipe_feeds.resolve_yield` preferred the size
bracket in a prep's name over `prep_yields.yaml`. **Seven preps conflict, and in
all seven the bracket is wrong** — worst is Jalapeno Tequila at 7.5x, then
brisket at 6x. The bracket is Lightspeed's pack/nominal naming (a 1 L bottle you
decant a 7.5 L batch into; a 15 kg raw joint that leaves the oven at 10.5 kg).

This was also a **consistency** bug: the P&L costs off the converter's prep
rates, which have always read `prep_yields.yaml`. Only this path read the
bracket, so the builder and the published cost disagreed about the same batch.
Precedence flipped; the bracket still answers when nothing is written down.
A ratchet test fails if an eighth conflict ever appears.

### FIXED — the batch-unit correction is now a rule, not a hand-list

Fires only when three things agree: `prep_yields` states the basis as a "sum of
ingredients" (so the number is a mixed-unit sum and its label is arbitrary),
every drawing line uses one unit that is not the declared one, and the batch's
own dominant component is in that same unit. **No density is ever applied.**

It reproduces both hand-declared cases and found three more —
`Black Beans Prep` and `Cauliflower Cheese Prep` were mislabelled identically —
and it correctly **refuses** Tandoori Sauce, where the two readings disagree.

### FIXED — Mulled Wine PartyJar, by arithmetic

Its "3.89 ml" of a drink is not a quantity of anything, but $8.127473 / $2.0908
a serve = 3.8872. The magnitude counts **serves**; the unit is junk.

**Mari refusals 22 -> 6. Products costing 202 -> 218 of 224.**

### NOT FIXED, on purpose — two things that are not mine to change

**1. Tandoori (6 products).** `Tandoori Chicken [2Kg]` draws "1 ml" of a batch
yielding 1,116 g. The "1 that is really a 1kg tub" pattern applies, but *which*
reading — 1 kg of sauce, or the whole 1.116 kg batch? The scrape's $7.35 line
cost is exactly 1,000 g of Greek yoghurt, which hints at the tub, but a hint is
not evidence and the readings differ by 12% on six products. The derived rule
deliberately holds rather than picking. **Needs a human.**

**2. The group-wide price series is stale for 16 ingredients.** Spanish Onion
turned out not to be a venue-split defect at all: `CostSeries.as_of(venue=None)`
does not see venue-tagged observations, so it returns July's $0.002420 while the
1 August Marilyna's invoice says $0.002200. Sixteen of 1,365 ingredients are
affected, some badly — `lightspeed:22995320` is on a 2 January price of $4.61
against 12 August's $3.17, and `fresh-fruit-team:LMM15BX` shows $17.40 against
$0.0174, which is a 1000x unit defect on top.

Under T6 ("the whole group pays the same costs per ingredient", venue is
provenance only) the group-wide lookup should consider every row and take the
latest. **The fix is in `core/domain.py`, which belongs to no area and is read
by the P&L, par and invoices alike** — so per SESSIONS.md rule 3 it is flagged,
not widened into silently. It is probably the highest-value item left in the
whole cost book.

## Next, in order

1. **`CostSeries.as_of` group-wide staleness** — 16 ingredients, needs an
   `ops`-ish decision because `core/domain.py` is read by everything.
2. **Tandoori: 1 kg or the whole batch?** One answer unblocks 6 products.
3. **Watch the daily diff for a week.** Cut over when it is boringly
   *attributed* — not necessarily zero, because cause 2 above is a correction
   we want to keep. Decide explicitly whether cutover accepts the +$0.53.
4. **Then `--promote`**, and only then archive the scrape and converter.
5. Stow (2b) will be harder: 612 products, and its builder book is much larger,
   so the authored-overlap path gets a real workout for the first time.

## Traps this session added to the list

- **`cost_on` is venue-scoped.** `Recipe.venue` selects the price series, so a
  single-line probe built with a placeholder venue silently prices against a
  different series. Cost me an hour chasing a $0.0073 phantom that was only
  Spanish Onion.
- **A top-level YAML file cannot mix a sequence and a mapping.** Obvious in
  hindsight; `_load_yaml` returned something the caller mis-shaped and the
  materialiser failed *after* the diff had already printed a stale result, which
  read as "the change did nothing".
- **Claim boundaries bite mid-task.** `.github/workflows/` is `ops`, not
  `cost-book`, so the workflow needed a separate claim and a separate push. Plan
  for two claims when a task ships both code and a job.
