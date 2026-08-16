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

## Next, in order

1. **7 products still refuse.** `Tandoori Sauce [Batch]` yields g against a 1 ml
   line (6 products); `Mulled Wine` declares no yield at all and is carried as
   1 ea. Same shape as the three above, same treatment: read the basis, do not
   assume a density.
2. **Watch the daily diff for a week.** Cut over when it is boringly
   *attributed* — not necessarily zero, because cause 2 above is a correction
   we want to keep. Decide explicitly whether cutover accepts the +$0.53.
3. **Tier 2 branch protection** (`HANDOFF_20260816_full.md` §3b) before
   promotion, not after.
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
