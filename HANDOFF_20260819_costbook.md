# Cost book — 19 Aug 2026

Supersedes `HANDOFF_20260818_costbook.md`. CI green, no claims held, everything
below verified on `origin/main`.

## Read this first: the pattern, not the list

**Four times in one session I found a correction that was already written down,
with a worked proof, that one reader was not consulting.**

| the declaration | who was reading it | who was not |
|---|---|---|
| Tandoori line relabel | staged materialiser | the LIVE converter |
| ILG account codes (`suppliers.yaml`) | nobody | the extractor, all 173 invoices |
| Back Office `DefaultSize` | nobody | `container_sizes.csv` |
| Garlic Oil / Mint Yoghurt yield relabels | staged materialiser | converter AND feed builder |

Each time I wired up the one reader and moved on. That is whack-a-mole, and Zak
called it: *"i feel like we're going around in circles."* He was right.

**THE STRUCTURAL PROBLEM.** There are ~8 declaration files
(`batch_yield_units`, `pack_overrides`, `pizza_portions`, `declared_purchases`,
`recipe_missing_lines`, `recipe_ingredient_swaps`, `product_map`,
`declared_conversions`) and ~6 readers (`convert_lightspeed_recipes`,
`materialise_recipes`, `build_recipe_feeds`, `build_costs`, `build_ingredients`,
`audit_book`). Nothing guarantees a reader consults a declaration. So every
session finds another disconnected pair, fixes that pair, and the next session
finds the next one.

**THE NEXT SESSION'S FIRST JOB — do this before anything else:**

1. Build the matrix: every declaration file × every reader. Which read it, which
   should, which do not.
2. Put each declaration behind ONE shared loader, the way
   `core.domain.prefer_cost_row` and `modules.recipes.units.apply_declared_yield_relabels`
   now are. Two rules already live this way; the rest do not.
3. Add a test that FAILS when a reader parses a declaration file directly
   instead of going through its loader. Without that, this recurs.

`scripts/check_declarations_bind.py` already answers "does this declaration
match anything?" It does not answer "does everyone who should read it, read it?"
That second question is the one that keeps costing days.

## Where the book stands

| | |
|---|---|
| SEVERE | **0**, pinned, on a FRESH rebuild (the run that matters) |
| WARN | 100 (was 116 this morning) |
| Lines costed LIVE | **94.8%** — 2,490 id + 474 sub-recipe of 3,125 |
| Lines FROZEN | 161 (a `manual` line is the feed giving up, not an import) |
| Coverage | mari 95.2% · stow 86.4% · hg 73.8% |
| Shadow diffs | mari $0.32 · stow $0.71 · hg $0.39 — boring |
| Declarations bound | 9 unbound, pinned; 3 of those are a real defect |
| Weighed pizza lines | 599 of 830 (72%) |

**STOWAWAY COVERAGE FELL 90.5% -> 86.4% AND THAT IS AN HONEST FALL.** Splitting
the size variants stopped "Version Two Pinot Grigio - Bottle" hiding inside a
merged name with "- Regular" and "- Large", which do have recipes. $24,392 of
uncosted Stowaway revenue is size variants, almost all WINE BOTTLES. They never
had recipes; the collapse was masking it. See the next section — this is the
cheapest large win left.

## Closed today

- **Pizza portions v2** — Zak weighed Regular/Large/Family. 805 lines carry a
  measurement, 142 products repriced. It also proved the morning's 0.716 "lift"
  WRONG: Produce's 20 g of Spanish onion was right and the old sheet's 33 g
  regular was the bad number. The lift is deleted, not adjusted.
- **Postmix** — Pepsi, lemonade and creaming soda now cost off the CUB BIB
  prices, $0.851 a 170 ml glass. Soft drinks were OVER-costed 54%.
- **`declared_purchases.yaml`** — a hand-entered invoice line for suppliers the
  mailbox never delivers (CUB, and Mr Iceman/HG-ILG next). Dated, evidenced,
  superseded automatically by a real invoice.
- **Freight rule** — same-day prices take the lower; the rule had been written
  out FIVE times and now lives in `core.domain.prefer_cost_row`.
- **Size variants stay separate** — a pint and a schooner are two products.
- **Container prices in the builder** — `$55.79 / 700ml`, not `$79.70/L`.
- **Circular recipes** — a recipe that is "one of itself" is flagged. Six found,
  four fixed (the postmix glasses), four wine glasses remain.
- **Stale prices labelled** — 558 of 1,255 ingredients are priced on something
  older than 90 days and now say so in the picker.
- **`recipe_as_of` tail-wins** — `max()` returned the FIRST maximal element, so
  an undated correction lost to the version it corrected.

## Open, in the order I would take them

1. **The declaration matrix** (above). Everything else recurs without it.
2. **Wine bottles: 27 products, $24,392 a quarter, no recipe.** A bottle sale is
   the simplest recipe in the book — one of the bottle, like Dom Pérignon
   already is. Highest value per hour of anything on this list.
3. **Harry Gatos: 73.8% coverage, $27,466 uncosted.** 255 recipe lines point at
   an HG product against 2,237 at Stowaway's. HG's problem was never prices.
4. **Three suppliers send nothing the mailbox can parse** — CUB, Mr Iceman, and
   HG's ILG account. CUB is declared; the other two are not.
5. **Re-extract 173 invoices** so ILG/Select Fresh/Gulli account codes land and
   Xero venue coding is corrected. HG's liquor spend is on Stowaway's books.
6. **161 frozen lines.** 87 are a live price disagreeing with the audited cost
   (a real safety valve, needs judgement per line); 58 are ten more batches
   whose yield unit and draw unit disagree — Gravy (12), Black Beans (8), Salsa
   Rosa, Chimichurri, Salted Caramel, Nut Roast, Cauliflower Cheese — each
   wanting its own proof, not a bulk relabel. Three yield in COUNT and are drawn
   in ml (Lime, Yorkshire Pudding, Brownie Prep): a different question.
7. **Weighings.** Pizza Sauce ($1.06M, 146 dishes) and Pizza Dough ($849k) are
   still estimates. The portion sheet leaves oregano (129 lines) and chilli (18)
   blank — two cells in a sheet Zak already has.
8. **The builder should stamp `effective_from`** on every save. Supabase deploy.
9. **Authored batch recipes that never reach the book** — "Avocado Verde" is a
   real chef-entered batch that nothing draws on, so it is silently dropped. I
   narrowed the insert to sold products deliberately; this wants its own pass.

## Traps that cost me time today

- **`on:` is a YAML 1.1 BOOLEAN.** `on: 2026-08-19` never arrives as a string,
  so every declared purchase came through undated — invisible to a cost book
  that orders by date. Use `purchased_on`.
- **A test that pins a DEFECT expires the day someone fixes it.** Happened three
  times: two of them were tests I wrote earlier the same day.
- **Do not chain a push off a piped pytest.** `pytest -q | tail -1` exits 0
  however many tests fail. That is how a red commit reached origin.
- **Use `edit_block`, not hand-rolled `str.replace` in a heredoc.** A single
  space of mismatch fails silently or applies half an edit. I lost a long
  stretch to this and Zak noticed before I did.
- **Rebuild the gitignored feeds before `arch_guard`** — it reports confident,
  specific, wrong failures against a stale feed and says so.

— Everything above is on `origin/main`. `python3 scripts/session.py status`
before touching anything.
