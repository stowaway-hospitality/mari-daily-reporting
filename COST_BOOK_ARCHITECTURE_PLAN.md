# Cost Book — Target Architecture & Migration Plan

**Companion to:** `COST_BOOK_CONSOLIDATED_CONTEXT.md`
**Written:** 15 August 2026 · against `main` = `ed05e4a` · Part V revised same day after Zak's rulings

---

## Part I — The design

### 1. The single decision everything else follows from

**Stop correcting the scrape. Become the book.**

Today: `lightspeed_recipes.json` (scrape) → 17 correction stages → `lightspeed_recipes_costed.json` → P&L. The corrections live in nine YAML files. The scrape is regenerated. The corrections must be re-applied, in order, forever.

Target: `data/recipes/{venue}.yaml` **is** the book — all 895 recipes, not 41. Lightspeed is a **downstream consumer**, not an upstream source. The nine correction YAMLs cease to exist because their content has been *applied and absorbed* into the recipe records themselves.

This is a **one-way door**, and that is the point. A correction layer can never converge because the thing it corrects keeps regenerating. A book converges because every fix is permanent.

**What this kills outright:** the entire ordering-bug class; stale-scrape drift; 1,380 lines of override YAML; `convert_lightspeed_recipes.py` (becomes a one-off migration script); the two-engine problem — `modules/recipes/cost.py` becomes the only engine, as the docs always said it should be.

**What it costs:** one careful migration, and the ability to pick up recipe edits made in Produce. The second is a feature: Produce is retired, and a book that silently absorbs untracked edits is not a book.

---

### 2. Four layers, one direction

```
   PURCHASABLE          what a supplier sells you
   supplier:CODE        ilg:395-1021, foodlink:100175, be-foods:FZ-CHIP-CC
        |
        |  ingredient_map.csv        <-- THE MISSING LAYER
        v
   INGREDIENT           what a kitchen consumes, one canonical base unit
   ing:black-beans      base_unit: g
        |
        |  costs.csv (effective-dated series, per base unit, GROUP-WIDE)
        v
   RECIPE LINE          qty + unit + provenance
   recipes/{venue}.yaml
        |
        |  cost.py  (the ONLY costing engine)
        v
   COSTED PRODUCT  ->  P&L (cogs_blend)  ->  variance vs stocktake
                   ->  GP review (realized + list)  ->  price decisions
                   ->  Lightspeed / Doshii / me&u  (export, one-way OUT)
```

**No arrows point back up.** Lightspeed, Produce, me&u and Doshii sit below the last line. They receive; they never inform.

---

### 3. Layer 1 — Identity. Build `ingredient_map.csv`.

Currently 47 bytes. One row per **(purchasable, basis)** — because Foodlink genuinely bills camembert both per-piece and per-CTN-12 on one code, and a per-code override cannot be right for both:

```csv
purchasable_id,ingredient_id,base_qty,base_unit,basis,confirmed_by,confirmed_on,evidence
foodlink:100175,ing:black-beans,2850,g,CTN-6,zak,2026-08-12,INV-88213
foodlink:100175,ing:black-beans,475,g,EA,zak,2026-08-12,INV-88101
ilg:395-1021,ing:campari,700,ml,BOTTLE,zak,2026-08-09,ILG-44120
ilg:395-102-1,ing:campari,700,ml,BOTTLE,alias,2026-08-09,pricebook-spelling
ffteam:OS10BG,ing:spanish-onion,10000,g,BAG,zak,2026-07-30,FFT-2211
```

The resolver reads `basis` off the invoice line; if it can't determine one, it **refuses** — loudly — into the confirmation worklist.

This single file retires: `adjudicated_prices.yaml`, `recipe_ingredient_swaps.yaml`, `product_recipe_aliases.yaml`, `recipe_venue_mirrors.yaml`, the twin-price flag families, and the Campari/Angostura/onion/case-vs-bottle defect classes. It must also absorb or read `data/ingredient_aliases.json` (the chef self-service merge map — see T4.4).

**Rules:** one base unit per ingredient (`g`/`ml`/`each`); everything else a declared conversion with named evidence; refusal over guessing.

---

### 4. Layer 2 — Cost. One ladder, one derivation.

`costs.csv` stays the effective-dated series. It already rebuilds byte-identically. Three changes:

**(a) One precedence ladder, written down and tested:**
```
1. ingredient_map.csv basis row      (human/document-confirmed pack)
2. invoice line's own parsed pack    (only where unambiguous)
3. cost book history                 (costs.csv matched on source_invoice)
4. supplier pricebook                (ilg_pricebook.csv)
5. seed                              (beverage_seed.csv — flagged bad_seed)
6. REFUSE                            -> confirmation worklist, never a guess
```
Back Office cost drops **out** of the cost path — it is Lightspeed's opinion, and Lightspeed is downstream. It survives only as a reconciliation flag (ours vs theirs >20% apart → flag).

**(b) Kill the second derivation.** `build_ingredients.py` becomes a **view** over `costs.csv` — a projection, not a computation. The black-bean 6× class of failure becomes impossible by construction.

**(c) Commit the derived feeds.** `ingredients.json`, `recipes_full.json`, `recipes_index.json`, `cost_book_flags.json` come out of `.gitignore`.

The re-derive fence stays exactly as written: **a re-derive may raise a cost freely and may not quietly lower one.**

---

### 5. Layer 3 — Recipe. Provenance per line.

Every line in `data/recipes/{venue}.yaml` carries `source` (`weighed | invoice | derived | mirrored | rule | authored | scrape`), `confirmed_by`, `confirmed_on`, `evidence`.

**`source` is the whole idea.** "~2,300 of 3,041 lines never checked by anyone but their author" stops being a paragraph in a handoff and becomes a dashboard number — revenue depending on `source: scrape` lines — that shrinks as things are weighed and can never silently regrow, because there is no scrape.

Provenance ranking is also the conflict resolver: `weighed` beats `derived` beats `mirrored` beats `scrape`. Not order — rank.

The nine correction YAMLs are absorbed: unit fixes → corrected `unit` on the line; missing lines → real lines with `source: mirrored`; weighed grams → `source: weighed`; yields → `yield:` on the recipe; cook loss → `cook_yield:` (a process fact, kept separate — and **assumed yields must be visibly distinct from measured ones**, since lamb proved the 0.65 assumption ~20 points pessimistic).

---

### 6. Layer 4 — Consumption. One target table.

`data/gp_targets.yaml`, read by every consumer (dashboard, builder, audit_book, monthly review, quarterly review):

```yaml
default: {gp_min: 0.75}
by_category:
  keg_beer:        {gp_min: 0.70}
  ramen_hg:        {fc_max: 0.25}       # Zak's explicit HG call
by_channel:
  ubereats:        {commission: 0.30, uplift_required: 0.43}
exemptions:
  - {product: "Don Julio 1942", reason: premium_import}
group_rollup:
  cogs_target: 0.22    # dashboard headline; a MIX outcome, not a line-item rule
```

The 22% group COGS number and the 75% category GP floors are different kinds of thing — one is a mix outcome, one a line-item floor. Saying so in one file stops the five current target systems contradicting each other.

---

### 7. Where the code lives

```
modules/recipes/    cost.py (THE engine) · book.py · flags.py (<- scripts/build_cost_book_flags.py)
                    audit.py (<- scripts/audit_book.py) · reconcile.py
                    pipeline/{build_costs, build_ingredients (view), build_recipe_feeds}
                    export/{lightspeed.py, doshii.py}   # one-way OUT
                    tests/  (56 files already here)
modules/identity/   purchasable.py (<- core/domain) · ingredient_map.py
                    pack_overrides.py (<- core/) · worklist.py (the missing tool)
data/               ingredient_map.csv · costs.csv · cogs_list.csv · gp_targets.yaml
                    recipes/{stowaway,harry_gatos,marilynas}.yaml · cook_yields.yaml
```

`convert_lightspeed_recipes.py` → `_archive/` after migration. One module, one owner, one claim area.

---

## Part II — Migration

### Phase 0 — Make CI honest (1 session)
- Fix the vacuous determinism gate (`git checkout -- data/costs.csv` before the `cp`, or make `test_pipeline_integration.py` restore what it touches). Verify it can fail.
- Add `audit_book.py` to `tests.yml` as an **ID-pinned ratchet** (see T5): new finding ID fails CI; resolving one lowers the pin.
- Un-gitignore the four derived feeds. Commit them.
- Delete `_to_delete/untracked-dupes-20260808/`, `_pending_restatements.patch`, root xlsx.

**Exit criterion:** you can break the cost book and CI tells you.

### Phase 1 — Identity layer (2–3 sessions)
- Generate `ingredient_map.csv` from what already exists scattered across `cogs_list.csv`, `pack_overrides.yaml`, `product_map.csv`, `adjudicated_prices.yaml`, `product_recipe_aliases.yaml`, `recipe_ingredient_swaps.yaml`, `recipe_venue_mirrors.yaml`, `ingredient_aliases.json`.
- One canonical `ingredient_id` + one base unit per ingredient. **Group-wide cost series (T6 ruling).**
- Build `modules/identity/worklist.py`; work it with Zak in one batched pass (be-foods ~$14,600, 21 dual-unit drinks, Coke 1.25L, king brown punnet).
- Implement the ILG case-vs-bottle discriminator (**Patron Silver regression test first**), CTN-N by magnitude.
- Ingest Imbibo ($28,774); identify Franc About's Dext name.

**Exit criterion:** every ingredient one id, one base unit; every purchasable resolves or is worklisted.

### Phase 2 — The one-way door (per venue: Mari → Stow → HG)
⚠️ Exclusive `cost-book` claim. Tag `main` first. Shadow-run before cutting (T2).
- Final converter run; **materialise** output into `data/recipes/{venue}.yaml` with `source` derived from which correction stage produced each line (untouched lines = `scrape`, the honest label).
- Collapse the append-log recipe files to one live record per product (tail-wins), keep history.
- Shadow-run: daily CI job costs every product both ways; cut over after N consecutive zero-diff days. **Equivalence includes known bugs** — migration and correction are separate steps.
- Point `cogs_blend` at `cost.py` for everything. Archive the scrape + converter. Delete the absorbed YAMLs.

**Exit criteria:** one quantity source, one engine, byte-identical costs — **and book-first intake** (see T4).

### Phase 3 — Accuracy (ongoing, dollar-ranked)
- Dashboard panel: revenue-at-risk by `source`. This replaces the handoff genre as the backlog.
- Weighing as a standing weekly routine (10 lines/week), ranked by the panel, later by variance. Known top items: oregano (42/46 regulars), 47 GF pizza weights, 8 Large<Regular pairs, cook yields (pork/beef/brisket/chashu/achiote), 12+19 batch yields, 25 missing recipes (~$39,500), post-mix.
- **HG priority:** fix the Lightspeed access problem; import `HG 2026 Recipe.xlsm` (frozen chashu 60 g, $28 ramen, kikurage="Black mushroom"); import the [STOW] batch-cocktail texts.

### Phase 4 — The ledger (the actual prize)
- Merge PR #9. Build `modules/inventory/` — append-only movements (`receive/sale/production/waste/transfer/count`), effective-dated by `recipe_as_of`.
- `count` consumes whatever schema Zak's stocktake app emits — design to receive, not dictate.
- **Variance = counted − theoretical = waste + theft + portioning drift + recipe error.** Persistent negative variance = wrong quantity, found statistically — Phase 3's self-correction.
- Human-in-the-loop receive stays. Non-negotiable.

### Phase 5 — Governance & downstream
- Org move (unlocks Tier 2 protection without killing bot pushes) — **preferably pulled forward to Phase 0** (T3).
- `export/lightspeed.py` + `export/doshii.py`; reconciliation flags; fix GF BBQ Chicken PID 20467972, Uber Garlic Cheese $9.00, Philter keg (measure it), me&u upsells.
- Point monthly/quarterly reviews at the book (realized-price GP, T7). Move `price-history.csv` into the repo as a derived output.
- Update skills: `stowaway-new-product` + invoices skill target the book/export layer; retire `produce-recipe-builder`. Fix stale docs (ARCHITECTURE.md `as_of`, MODULES.md promises, INVENTORY_ARCHITECTURE.md's ~0% coverage claim).
- Archive all ten `HANDOFF_*.md` → `_archive/handoffs/`.

---

## Part III — Sequencing and risk

| Phase | Sessions | Risk if skipped |
|---|---|---|
| 0 — Honest CI | 1 | can't tell whether later phases worked |
| 1 — Identity | 2–3 | Phase 2 bakes today's identity chaos in permanently |
| 2 — Door (per venue) | 1–2 each | the same inconsistencies forever |
| 3 — Accuracy | ongoing | variance unreadable (waste vs recipe error) |
| 4 — Ledger | 3–4 | book stays unverifiable except by hand |
| 5 — Governance | 1–2 | collisions and stale docs tax every session |

**0 → 1 → 2 order is not negotiable.** Named risks: non-zero migration diff (stop, investigate — the cord stays attached); Produce edits after cutover vanish (confirm kitchens are on the builder first); weighing stalls (variance partially substitutes — argument for starting Phase 4 early); concurrent-chat breakage during Phase 2 (exclusive claim, tag, single sitting, ideally post-org-move).

---

## Part IV — The three things that matter most

1. **Build `ingredient_map.csv`.** 47 bytes today; six files exist to patch around it; its content is already in the repo, scattered.
2. **Walk through the one-way door.** Decided months ago, never executed. One session per venue once identity is done; permanently ends the correction-layer treadmill.
3. **Make CI able to fail.** Ratchet audit_book, fix the vacuous gate, commit the feeds. Excellent tests currently guard a system whose two most important checks structurally cannot fire.

---

## Part V — Tightenings (second pass, stress-testing the plan)

These supersede the earlier text where they conflict.

### T1. Don't big-bang the one-way door — pilot it on Marilyna's first
Mari: highest coverage (95.5%), smallest ingredient set, most weighed data, cleanest venue boundary. A design flaw surfaces on ~240 pizza products in two sessions, not mid-cutover on 895 recipes. Stow next, HG last (needs the access fix anyway). The converter already splits by venue.

### T2. Replace one-shot equivalence with a shadow run
Run both engines in parallel in CI; a daily job diffs them; cut over after N consecutive clean days. The scrape stays attached (and reversible) until the diff has been boringly zero for a week. Equivalence **deliberately carries known-wrong numbers** — migration and correction are separate steps.

### T3. Move the org migration into Phase 0
Cheap, prerequisite for real branch protection, and Phase 2 is exactly when collisions must be impossible rather than discouraged. Order: org move + Tier 2 → honest CI → identity → door.

### T4. Intake path — VERIFIED: the builder already is book-first intake *(revised after inspecting the code)*

Zak's correction, confirmed in the source. The recipe builder (`dashboard/_shared/recipe_builder.js` + the `shg-auth` Supabase edge function) is already the right shape:

- **Picker sources from live invoices** — it loads `data/ingredients.json` (built from `cogs_list.csv`/`costs.csv`), shows `cost_per_base_unit`, and flags `needs_pack_review` items with a "confirm pack" prompt whose answer is **append-committed to `data/pack_overrides.yaml`** so the ingredient is costed for everyone from the next build.
- **Saves commit to the book** — `POST /shg-auth/recipes` append-commits a YAML block to `data/recipes/{venue}.yaml` via the GitHub Contents API, **authored as the actual person**, with body-dedup idempotence (the Romesco double-save lesson is already encoded in the worker).
- **Prep labour** is captured (`/prep` → `data/prep_sessions/{venue}.yaml`, last-4 average) and batches carry `cost_per_yield_unit_with_prep`.
- **Chefs already self-service identity merges** — `POST /alias` (admin/bigchef only) writes merge/unmerge pairs to `data/ingredient_aliases.json`.

What remains:

1. **The saved YAML freezes a cost snapshot into the recipe.** `buildYaml()` writes `unit_cost_incl` per line — a cost frozen at authoring time, inside a quantity record. Under the layered design a recipe line stores `id + qty + unit` only and cost is joined live from `costs.csv` at costing time. Keep the snapshot as provenance ("cost when authored") if wanted, but it must never be the number the P&L uses.
2. **The recipe files are append-only logs, not canonical state.** Last-block-wins. Phase 2's materialisation must collapse each file to one live record per product (keeping history), and the feed builder must be explicit that tail-wins is the rule.
3. **Provenance exists only as comments.** Promote `# entered by {name} on {date}` to fields, and add per-line `source:` so builder-authored lines land as `source: authored`.
4. **`data/ingredient_aliases.json` is an eighth identity artifact.** The chef-merge map must fold into (or be read by) `ingredient_map.csv` in Phase 1, or the map and the aliases will disagree about which ingredients are the same thing.
5. **The skills still write the wrong way.** `stowaway-new-product` and `produce-recipe-builder` build recipes in Produce; the invoices skill sets costs in Back Office. Repoint at the builder/book + export layer; retire `produce-recipe-builder`.
6. **The builder has its own GP colour scheme** (>85% warn, <65% warn, <55% bad) — a fifth target system. It should read `gp_targets.yaml` like everyone else.

### T5. Ratchet on finding IDs, not counts
A count ratchet (SEVERE ≤ 7) passes when one finding is fixed and a new one appears. Pin the **set of finding IDs**: any new ID fails CI; resolving one lowers the pin automatically.

### T6. Venue-cost ruling — DECIDED (Zak, 15 Aug 2026)
**"The whole group pays the same costs per ingredient."** One ingredient, one group-wide cost series. Venue is a property of the *purchase* (which account bought it), recorded on the `cogs_list` row for provenance — never a cost dimension. Venue-split costs (Angostura 11×, black beans 6×, carrot 20×) are by definition defects, not facts to model. The `venue` column on `costs.csv` becomes provenance only; `ingredient_map.csv` carries no venue axis. Genuinely different products per venue = two ingredient IDs on purpose.

### T7. Two GP numbers, not one — realized price from the daily sales ingest *(revised per Zak, 15 Aug 2026)*

Zak's correction: list prices lie. Happy hours, comps and discounts mean the Lightspeed product-sheet price is not what the till collects, so GP off list price overstates reality. The data to do better is **already ingested daily** (`insights_{venue}_{date}.csv`, plus the full product mix once PR #9 merges).

- **`realized_price`** = revenue_ex ÷ qty, per product per day/week, from the daily ingest. No new ingestion, no browser session. The primary GP basis.
- **`list_price`** = the Lightspeed product sheet. Changes rarely. Snapshotted occasionally via BO export — used for pricing *decisions* and theoretical GP.
- **`discount drag`** = theoretical GP − achieved GP, per product/venue/day-of-week. Happy hour, comps and EatClub become a visible line item. Nearly free to produce; nobody has ever seen this number.

Consequences: the monthly GP review becomes a report over repo data (HG stops being skipped); the Philter-XPA class of divisor argument disappears for achieved GP; recipe-only spirits get GP through the cocktails that sell them, at realized prices.

### T8. Define "bomb-proof" as a scoreboard, wired to the health monitor

| Metric | Now | Done |
|---|---|---|
| SEVERE findings | 7 | **0, enforced in CI** |
| New-defect ratchet | none | ID-pinned, CI-enforced |
| Revenue coverage per venue | 95.5 / 91.5 / 75.5 | **≥97% all three** |
| Lines at `source: scrape` on top-80%-revenue products | unqueryable | **0** |
| Cost sources of truth | 7 | **2** (cogs_list facts + costs.csv series) |
| Recipe quantity sources | 3 + 9 YAMLs | **1** |
| Costing engines | 2 | **1** |
| Variance loop | none | live, weekly, per venue |
| GP computable without a browser | no | yes, per channel, realized + list |

Add the cost book to `health_monitor.py`: feed freshness, SEVERE count, coverage floor. The pipeline pages on stale sales data but stays silent while the book rots — the August PAT outage showed how long "silent" lasts.

### Smaller items
- Weighing is a routine (10 lines/week), not a campaign; variance ranks it once live.
- Phase 2 rollback window: full reversibility (tag + archived scrape) until the first post-cutover recipe edit; then forward-only.
- Prefer mailbox/pipeline ingestion over browser automation wherever an invoice can arrive by email.

### Revised sequencing

```
Phase 0   org move + Tier 2 protection + honest CI (T3, T5, T8)
Phase 1   identity layer, venue-cost ruling baked in first (T6)
Phase 2a  Mari through the door, shadow-run, book-first intake confirmed (T1, T2, T4)
Phase 2b  Stowaway through the door
Phase 2c  HG: Lightspeed access fix -> xlsm import -> door
Phase 3   accuracy as weekly routine + realized-price GP (T7)
Phase 4   ledger + variance
Phase 5   skills update, handoff-genre retirement, doc repair (T4 remainder)
```
