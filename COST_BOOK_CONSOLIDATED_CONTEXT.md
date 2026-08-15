# Cost Book — Consolidated Context

**Compiled:** 15 August 2026
**Sources:** 27 Cowork transcripts + live audit of `zakstowaway/mari-daily-reporting` @ `main` = `ed05e4a`
**Purpose:** one document that replaces ten handoff docs and twenty-seven chat threads.
**Companion:** `COST_BOOK_ARCHITECTURE_PLAN.md`

---

## 0. The one-paragraph diagnosis

The cost book is not a book. It is a **correction layer bolted onto a foreign scrape**. Lightspeed Produce is scraped into `data/lightspeed_recipes.json` (15,160 lines, 895 recipes); a single 2,465-line script applies **17 sequential correction stages** drawing on **nine hand-maintained YAML files (~1,380 lines)** to produce `data/lightspeed_recipes_costed.json`, which is what the P&L actually costs off. Meanwhile the *architecturally intended* book — `data/recipes/{venue}.yaml`, the thing `COGS_ARCHITECTURE.md` calls "what the whole project is for" — holds **41 products**, under 5% of the range, and is reached by a **second, separate costing engine** (`modules/recipes/cost.py`). Every recurring inconsistency being re-solved is a symptom of this shape: corrections accumulate *outside* the thing they correct, so they can never converge, and each new defect needs a new override rather than a fix.

Zak's standing instruction from an earlier session already names the fix:

> *"do everything you can to unlink lightspeed dependencies - they shouldnt exist, it should be completely our own book running off invoices and weightings that have been MIRRORED from [Lightspeed]."*

That has not happened. The dependency has been *reduced* (down to ~553 of 3,055 lines at one measurement) but never *cut*, so every session re-enters the same maze.

---

## 1. Measured current state (verified 2026-08-15, not quoted from a handoff)

| Metric | Value |
|---|---|
| Recipes in the costed book | **895** |
| Fully costable on our own book | 879 / 895 |
| Ingredient refs resolved | 100% (1,542 by id + 383 sub-recipe) |
| `data/costs.csv` | 4,281 rows — **rebuilds byte-identically** ✅ |
| `data/cogs_list.csv` | 4,532 rows |
| Open flags on `/recipes/#flags` | **67** (18 high / 42 medium / 7 low) |
| Measurable under-cost across 5 flags | **$11,518/yr** |
| `audit_book.py` | **SEVERE 7 · WARN 116 · INFO 180** (exit 1) |
| Revenue coverage, 13wk — **mari** | 95.5% of $139,272 ($6,318 uncosted) |
| Revenue coverage, 13wk — **stow** | 91.5% of $573,632 ($48,608 uncosted) |
| Revenue coverage, 13wk — **hg** | **75.5%** of $100,982 ($24,735 uncosted) ⚠ |
| Products with no recipe at all | 25 products, ~$39,500 revenue |

**The queue is growing.** `HANDOFF_20260814.md` claims flags 65 / WARN 112. A day later it is 67 / 116. Defects arrive faster than they drain — exactly what a correction-layer architecture predicts.

---

## 2. What actually exists (many documented paths are wrong)

| Transcripts / handoffs say | Reality |
|---|---|
| `scripts/build_costs.py` | `modules/recipes/pipeline/build_costs.py` (1,247 L) |
| `scripts/build_ingredients.py` | `modules/recipes/pipeline/build_ingredients.py` (922 L) — its own docstring still cites the old path |
| `scripts/book_reconcile.py` | `modules/recipes/book_reconcile.py` (549 L) |
| `scripts/resolve.py` | `modules/invoices/resolve.py` (191 L) |
| `scripts/pack_confirmation_worklist.py` | **Does not exist anywhere.** Capability scattered across `core/pack_overrides.py`, the `needs_pack_review` flag, `check_pack_agreement.py`, `check_pack_as_rate.py`, and 168 INFO findings in `audit_book.py` |
| `data/ingredients.json` | **Gitignored** — generated at build time, never committed |
| `data/suppliers.yaml` | `modules/invoices/suppliers.yaml` (929 L) |
| `data/product_dept_map.json` | `scripts/product_dept_map.json` — a 118 KB data file living in `scripts/` |
| `modules/inventory/` | **Never created**, though `session.py::AREAS` claims it |
| `apps/_shared/` (per MODULES.md) | Landed as `dashboard/_shared/` |
| a `cogs` module (per MODULES.md) | **Never created.** `cogs_blend.py` sits in `scripts/` |

### The real engine

`scripts/convert_lightspeed_recipes.py` — **2,465 lines**. A single run prints 17 correction stages:

```
304 unit normalisations
 13 scraped recipes replaced by our book
 19 lines rescaled where the unit was typed into the name
 47 wheat-dough lines removed from gluten-free recipes
 71 omitted lines restored
242 pizza quantities from Zak's weighed sheet
 44 dine-in recipes mirrored
160 packaging lines
 47 sold-as-bought products
 88 pack-count quantities restated
```

It lives in `scripts/`, outside `modules/recipes/`, so `MODULES.md`'s module rules don't reach it — and `session.py` names it by path as a special case.

---

## 3. Sources of truth — the actual count

### Ingredient cost: **seven**

1. `data/cogs_list.csv` (4,532 rows) — validated invoice lines, the fact table
2. `data/costs.csv` (4,281 rows) — derived per-base-unit series
3. `data/pack_overrides.yaml` (770 L) — chef confirmations that change the divisor on 1 & 2
4. `data/ilg_pricebook.csv` (6,157 rows) — supplier catalogue read directly by `build_costs.py`
5. `data/bo_exports/*products*.csv` — **gitignored** Lightspeed Back Office costs, read by three separate scripts
6. `data/beverage_seed.csv` (430 rows) + `data/recipe_ingredient_seed.csv` — January seed prices, still live wherever no invoice exists
7. `data/adjudicated_prices.yaml` (220 L) — tie-breaker layer for "two-worlds" records

Plus, outside the repo entirely: `~/Documents/STOW/price-history.csv`, written by the `dext-lightspeed-invoices` skill and read by the monthly/quarterly price reviews. **The price reviews do not read the cost book.**

### Recipe quantity: **three, in an inverted hierarchy**

1. `data/lightspeed_recipes.json` — the Produce scrape, **895 recipes**. *This is what the P&L costs off.*
2. `data/recipes/{venue}.yaml` — the authored book. **41 products** (25 stow / 10 hg / 6 mari). `CODEMAP.md` calls this the "Authored" tier.
3. Nine correction YAMLs applied on top of (1) inside `convert_lightspeed_recipes.py`:
   `recipe_line_unit_fixes` (248 L) · `recipe_missing_lines` (275 L) · `recipe_yields` (140 L) · `prep_yields` (340 L) · `cook_yields` (38 L) · `pizza_regular_grams` (135 L) · `recipe_ingredient_swaps` (37 L) · `recipe_venue_mirrors` (87 L) · `product_recipe_aliases` (81 L)

**Two costing engines exist, and the authoritative one is the one the docs call the fallback.** `test_chicken_roast_is_a_roast.py` documents this in the repo, verbatim: *"Authoring a fresh serve recipe in `data/recipes/stowaway.yaml` would NOT have… Only `cogs_blend._load_our_costs` would have seen it."*

### Recipe quantity, outside the repo

- **Harry Gatos**: `HG 2026 Recipe.xlsm` — an Excel workbook uploaded by hand. Not in the repo. HG's 75.5% coverage is a direct consequence.
- **Stowaway cocktail batches**: raw text pasted into chat, tagged `[STOW]`, batch-named "1/2 DRUM 5L". Not in any system.
- **Marilyna's**: Lightspeed Produce (being retired) + `pizza_regular_grams.yaml`.

---

## 4. The recurring inconsistencies — why they keep coming back

These have been solved more than once. Each is a *class*, not an incident.

### 4.1 Unit-vs-count (the master defect class)
Produce writes a **pack count** and labels it with a **volume unit**. `"1 ml"` yoghurt = one 1 kg tub. `"3 ml"` black beans = three tins. `"4 ml"` flour = four bags. Detector found **181 instances**; 89 auto-restated, 94 unresolved (sub-recipe refs with no per-gram rate to divide by).

**Same class, different faces:** lettuce twin-pack billed as a whole pack ($2.75 vs $0.228); T2 Milk Bun displayed at **$980.00/L**; `0.077ml`/`0.025ml` that were actually batch fractions (1/13, 1/40); Rosemary Salted Fries collapsing to $0.0019 when 0.35 kg was read as 0.35 g; Kids Spag Bol issued a pizza box.

**Root cause it always returns to:** no single declared base unit per item, so every read is a guess about which unit the writer meant.

### 4.2 Case-vs-single (the money class)
ILG prices some lines per case and some per bottle **with no field distinguishing which**.
- 148 of 344 ILG lines overstated by exactly the carton count — **$21,950 inc-GST** of purchasing
- Veuve at $484.58 against a true $80.76; Asahi $59.18 vs $2.47; Heaps Normal $64.08/tin vs $2.67
- Foodlink camembert billed **both ways on one code** — $3.80/piece and $45.60/CTN-12. A per-code pack override cannot be right for both.
- Spanish onion 10× over-cost: FFT code `OS10BG` is a 10 kg bag read as 1 kg
- Plant-based patty at $936/kg because a "125G" substring in the *description* overrode the invoiced "1 box"

**The trap that ate a session:** feeding ILG's own `pack_qty`/`pack_unit` into the resolver looks like a one-line win (1,152 of 1,154 rejected lines carry one). It dropped Patron Silver from $2.94 → $0.61/pour — 4.8× under. Caught by a test, reverted. The correct discriminator (test both readings against the seed rate) was specified but never implemented.

### 4.3 Two derivations of the same number
`ingredients.json` and `costs.csv` **independently re-derive pack logic**. On Foodlink black beans (`foodlink:100175`) they disagreed by exactly **6×** — `build_costs.py` applied the CTN-6 multiplier, `build_ingredients.py` divided a carton price by a single tin's weight. This broke CI on 15 Aug (`c0767e6`).

### 4.4 Identity collisions
- Bracket-stripping made `"Pepperoni [3kg]"` (a bulk bag) resolve to the recipe `"Pepperoni [Dine-in]"` (a whole pizza) — **71 lines** wrongly resolved
- `"Pizza Dough"` and `"Pizza Dough [Recipe]"` existed as two ingredients for one thing (split 4/42 pizzas)
- Nine ingredient names carried a stray literal `" kg"` suffix, duplicating the same ProductID
- Campari was in `product_map` under ILG's pricebook spelling `395-102-1` while invoices write `395-1021` — the row existed and never matched
- `"mL"` ≠ `"ml"` excluded an entire HG scrape
- Angostura sits 11× apart between venues, feeding 4 HG cocktails; carrot 20× apart
- Kikurage = "Black mushroom" — resolved once in a chat, nowhere else

### 4.5 Ordering bugs
Corrections applied in the wrong order silently undo each other.
- `cost_of()` matched the ingredient unit **before** `_restate_pack_quantities()` corrected it — `our_cost` was decided against the pre-restate unit and never revisited. **47 GF pizza base lines = 23% of all Lightspeed-dependent dollar exposure.**
- Dine-in-mirrors-takeaway ran **before** the gram-correction pass, so dine-in copied stale numbers and "drifted straight back" every run

### 4.6 Silent zeros and empty joins
- Two wine "- Bottle" recipes had **zero ingredient lines** in Produce, so $73/$110 bottles cost $0 — infinite margin, the flattering kind of error
- Spiced Sour Cream, Chimichurri, Minced Garlic Prep were costed in our own book but the converter resolved the same names to **empty Lightspeed stubs** and silently costed $0
- A volume check read the field `weeks` instead of `weekly` and silently returned 0 for everything
- `core/pack_overrides.py` used a bare `read_text()` and swallowed `UnicodeDecodeError` in `except Exception: return {}` — on any non-UTF-8 machine **322 cost observations vanished with no error and no log line**

### 4.7 Guards that cannot fail
- **The `costs.csv` determinism gate is vacuous.** `test_pipeline_integration.py` rebuilds `data/costs.csv` and leaves it rebuilt; in `tests.yml` that runs *before* "Deterministic feeds must rebuild identically". The gate diffs a rebuild against a rebuild. A controlled restore-then-rebuild produced **48 diff lines** that CI's ordering masks.
- **`audit_book.py` runs in no workflow.** Its own docstring says it exits 1 on SEVERE so "CI can hold the line". CI never calls it. SEVERE 7 has stood since at least 14 Aug.
- **The derived feeds are gitignored** (`ingredients.json`, `recipes_full.json`, `recipes_index.json`, `cost_book_flags.json`). This is *why* `audit_book` reported SEVERE 3 for a week when the truth was 7. `arch_guard.py` R0 was added on 15 Aug after two debugging passes were lost to a stale feed producing "a specific, plausible, completely fictional regression."
- An encoding ratchet guard counted the whole repo instead of the PR-merged tree, breaking CI on a difference that lived on `main`.

### 4.8 Venue attribution
The recipe feed **hard-coded `"venue": "stowaway"`** for every recipe. Result: 666 stow / 8 hg / 6 mari — **144 of Marilyna's pizza recipes mis-filed under Stowaway**. Fixed by sourcing venue from `rollup_mari`/`rollup_stow`. Post-fix: 455/214/11.

Separately and **intentionally**, two RG classification sets disagree and must stay disagreeing:
- `weekly-report`/`stowaway-data` skill's `reporting-groups.md` = "Marilynas-strict" (incrementality: *would we lose this if Mari closed?*) — excludes Dine-in Pizza, Delivery Alcohol, Delivery Cocktails
- `daily_aggregator.py::MARILYNAS_RGS` = P&L attribution (*whose till-line is this?*) — includes Dine-in Pizza and Delivery Alcohol

Zak's ruling: *"dine in pizza and delivery alcohol is mari. delivery cocktails is stow."* Documented in-code so nobody "helpfully" reconciles them. **A cost book consuming RG classification must declare which question it is asking.**

---

## 5. Rules and conventions that ARE settled

Do not re-litigate these.

**Structural**
- Dine-in = the Regular recipe, minus packaging (box + insert). Regular takeaway always carries an **11" box + insert**. Packaging counts as **whole units**, never scaled by a size ratio.
- Size variants (Regular / Large / GF / Dine-in / Takeaway) are separate recipe records; sum across them when asked about "the product".
- `[Recipe]` / `[Batch]` / `[Prep]` suffixes denote reusable sub-recipes.
- `D` suffix = the delivery SKU (Marilyna's takeaway).
- Uber Eats marketplace commission = **30%**, verified. Break-even uplift to hold margin: 15%→+18%, 25%→+33%, 30%→+43%, 35%→+54%. Actual median dine-in→delivery uplift is **+3.5%**. Three SKUs lose money outright.

**Costing**
- Cost is normalised to base units at load ($/kg→$/g, $/L→$/ml); countable units pass through.
- g↔mL conversion at density 1.0 is permitted **only for our own prep/batch recipes**.
- Yield = **batch cost ÷ known per-unit rate** ("the cost proves the weight"), never sum-of-quantities × 0.9. That old rule rotted 9 preps — Queso Dip read $58.94/L for months; true $12.41/kg.
- Where cost and stated quantity conflict, **cost wins**.
- A price is only matched against the delivery that produced it (`source_invoice` on every `costs.csv` row).
- **Never invent a yield factor.** Lamb is the only measured one: 2.7 kg raw → 2.3 kg cooked = **85.19%**.
- The re-derive fence: *"a re-derive may raise a cost freely and may not quietly lower one."*
- Alehouse kegs are **49.5 L**, per ILG's own truncated `raw_uom`, not a round 50 L. Pinned in tests. (S&W, Kirin, Guinness, Coopers = 49.5 L; Sapporo, Philter, Grifter Konvoy = 50 L.)
- Cost prices in Lightspeed are **GST-inclusive**; sales-side revenue is **ex-GST**. Two conventions, both live.
- **Venue-cost ruling (Zak, 15 Aug 2026): the whole group pays the same cost per ingredient.** One group-wide cost series; venue on a `cogs_list` row is purchase provenance, never a cost dimension.

**Process**
- Never create a product with $0.00 cost intending to fix later.
- Never create products inside Produce via the "Create X" shortcut — they get $0.00 cost.
- Stock items (non-zero cost, blank category, `BuyAccount=1`) are the only valid recipe ingredients. POS items ($0 cost, has category) produce garbage costing.
- Invoice number must be set **before** "Receive order" — the View order page is read-only afterward, permanently.
- Reconciliation gate: PO lines must match the Dext invoice total within **$0.50**, else stop.
- ProductIDs are **venue-specific**. A cost CSV built from one venue silently does nothing in the other.
- Lightspeed's CSV import success counter has lied before — always re-verify by refetching.

---

## 6. Target conflicts — five incompatible GP systems in play

| Source | Target |
|---|---|
| Dashboard (v15, Zak's call) | **22% COGS / 78% GP flat**, all venues, ±2pp band |
| `dext-lightspeed-invoices` skill, Appendix K | **≥75% GP** wines/spirits/cocktails/non-keg beer/food; **≥70% GP** keg pours; premium imports exempt |
| Harry Gatos ramen (Zak's call) | **20–25% food cost** (≈75–80% GP) |
| Marilyna's pizza COGS workbook | green ≥80% / amber 70–80 / **red <70%** GP |
| Recipe builder (`recipe_builder.js`) | >85% warn / <65% warn / <55% bad |
| Marilyna's pizza repricing (actual) | competitor-anchored **$/in²** off Mad Toppings — no GP target at all |

HG's 20–25% FC would sit in Marilyna's "amber". The 22% group COGS target and the 75% category GP target imply different things about mix. **There is no single target table anywhere in the repo.**

---

## 7. Documentation debt

Ten handoff documents at repo root, none consolidated. **No handoff exists for 15 August — the single busiest day on `main` (30+ commits).** That day's knowledge existed only in chat until this document.

Stale statements still in load-bearing docs:
- `ARCHITECTURE.md` "Known gaps": *"No `as_of` anywhere yet"* — `CostSeries.as_of` shipped.
- `ARCHITECTURE.md`: *"dashboard/index.html is 134KB, one file"* — long since split.
- `MODULES.md` promises a `cogs` module and `apps/_shared` — neither exists under those names.
- `build_ingredients.py` docstring cites a path that hasn't existed since the module move.
- `INVENTORY_ARCHITECTURE.md` says recipe coverage was measured ~0% on 2026-08-09 — it is 91.5% stow today; the doc's build order was sequenced off that wrong number and had to be reversed mid-build.

---

## 8. Governance — the collision problem

- **Every PR shows `merged: false`.** All 9. `main` is pushed directly, including by bots.
- **PR #5** is closed unmerged, yet three August handoffs reference it as the live branch. The work reached `main` by other means.
- **PR #9** (persist daily product mix) is the only open PR and the stated prerequisite in `INVENTORY_ARCHITECTURE.md`.
- Branch protection is **Tier 1 only**. Tier 2 (PR-required) is blocked on a personal-repo limitation (`422` on adding GitHub Actions as bypass actor). ~18 workflows push via `GITHUB_TOKEN`. Recorded workarounds: move to a free GitHub org, SSH deploy key, or stay at Tier 1.
- `SESSIONS.md` + `scripts/session.py` work (path-computed claims, 12 h expiry, CAS writes to `main`) but enforcement is honour-system.
- The guarded-against failure already happened: a concurrent session's rebase **silently dropped the `target_date` fix** while keeping the other hunk of the same commit.

---

## 9. Housekeeping debt

- `_to_delete/untracked-dupes-20260808/` contains full duplicate copies of `build_cost_book_flags.py`, `feed_defects.py` and 8 recipe tests — they match on grep and mislead every search.
- `_pending_restatements.patch` — 125 KB uncommitted-work patch, checked into git.
- `Recipe cost coverage - confirm list.xlsx` at repo root.
- `data/_recipe_migration/` staging area still live inside the contract directory.
- The Cowork mount **cannot `unlink` files** — `git checkout`/`reset`/`merge` fail on the mounted tree. Workaround: `ops/git_on_the_mount.sh`. Always clone to `/tmp`.

---

## 10. The single most important missing artifact

**`data/ingredient_map.csv` is empty — 47 bytes, header only.**

`ARCHITECTURE.md` Decision 1 defines the two-layer identity model: **Purchasable** (`supplier:CODE`) → **Ingredient** (what a recipe consumes). It was never populated. Recipes reference `supplier:CODE` and `lightspeed:<PID>` directly. Change supplier and every recipe using that item breaks — the exact hole the doc says Lightspeed is in.

Everything patching around this absence: `canonical_purchasable()` re-keying, twin-price flags, `recipe_venue_mirrors.yaml`, `product_recipe_aliases.yaml`, `recipe_ingredient_swaps.yaml`, `adjudicated_prices.yaml`, `data/ingredient_aliases.json` (the chef self-service merge map), the `one_stock_one_cost` / `twin_identity_audit` test family. **That is what a missing identity map looks like after three months of patching around it.**

---

## 11. Open work, consolidated and deduplicated

### Blocking / structural
1. `data/ingredient_map.csv` empty — Purchasable→Ingredient layer never built
2. `audit_book.py` in no workflow; SEVERE 7 standing
3. `costs.csv` determinism gate vacuous (48 real diff lines masked)
4. Derived feeds gitignored → audits read stale trees
5. Two costing engines; the authoritative one undocumented as such
6. `convert_lightspeed_recipes.py` — 2,465 L, 17 order-dependent stages, outside its module

### Cost accuracy
7. ILG per-case/per-bottle discriminator — specified, never implemented. 340 ingredients still on pre-February seeds
8. CTN-N magnitude reading — Foodlink camembert bills both ways on one code
9. Coke No Sugar 1.25L and the FFT king brown mushroom punnet — genuinely ambiguous, need Zak's call
10. `be-foods` frozen goods invoiced as "1 box" with no count — ~$14,600 across 6 SKUs needing pack confirmation
11. Imbibo — 74 invoices, $28,774, zero ingested. Franc About Wine — Dext trading name still unidentified
12. ~19 products bought since June never reached the book (unbridged supplier codes)
13. 21 packaged drinks consumed as both `each` and `ml` — need declared can/bottle volumes
14. Twin/venue price splits: Angostura 11×, black beans 6×, carrot 20×
15. `suppliers.yaml` bounds entry needed for the 20 L vodka drum

### Recipe accuracy
16. `cook_yields.yaml` has ONE row. Cook loss unmodelled for pork, beef, brisket, chashu, achiote chicken
17. 25 products with no recipe, ~$39,500 revenue (`RECIPES_TO_WRITE.md`)
18. 12 Produce batch yields unset; 19 HG batch yields unread
19. 47 GF pizza weights are noise (0.43×–2.00×, median 1.059) — pending a weighed sheet
20. 8 pairs where a Large pizza carries less of an ingredient than its Regular
21. 32 regular-pizza ingredients uncovered by the weighed sheet — oregano (42 of 46 regulars) highest-value
22. Cooked Beef Brisket 11,454 g declared yield; Mango-Chilli Puree quantity back-computed from a dollar figure
23. Massenez Elderflower 10.47× dearer than Lightspeed's, $839/yr riding on it
24. No post-mix or soda water costed anywhere — LLB, Pink Lemonade, Stow Soda show 95%+ GP
25. Lemon at $0.375/ml; Cauliflower/Turkish Bread/Avocado wrong pack units
26. **~2,300 of 3,041 recipe lines have never been checked by anything except the person who typed them.** The accuracy ceiling, not a bug list.

### Venue / access
27. **Harry Gatos on a separate Lightspeed account** — blocks backfill, blocks monthly GP review of HG SKUs, causes the 75.5% coverage
28. HG recipe source of truth is `HG 2026 Recipe.xlsm`, not in the repo
29. HG liquor SKU population 0 / 144

### Downstream
30. Doshii per-platform override prices don't auto-update — ~96 manual modal edits per repricing round
31. "Same price on all apps" toggle inconsistently applied in Doshii
32. GF BBQ Chicken Pizza (PID 20467972) silently refused a price change across five attempts — needs Lightspeed support
33. Uber Eats "Garlic Cheese" at $9.00 vs POS $13.50 — netting ~$5.73 ex-GST after 30% commission. Flagged, never fixed
34. me&u still on two blanket upsell sets; approved GP-anchored rules never implemented
35. Philter XPA keg divisor: Appendix K says 30 L, Lightspeed config implies 50 L — measure the physical keg
36. Havana 3yr and Fellr Watermelon Seltzer Tin have $0 cost in Back Office
37. Ottelia range at 65–72% GP, flagged every month, never actioned

### Process
38. `price-history.csv` at `~/Documents/STOW/` isn't reliably mounted for scheduled runs
39. Invoices skill logs to CSV in practice but SKILL.md documents Apple Notes + JSON (Zak: "we don't care about Apple Notes, just the CSV")
40. Invoices skill periodically trips a content-safety classifier on embedded DOM-automation JS
41. Cowork ships only `SKILL.md` for plugin-path skills — forced a 91,845-byte single-file consolidation

---

## 12. Zak's standing rules (verbatim)

> *"do everything you can to unlink lightspeed dependencies - they shouldnt exist, it should be completely our own book running off invoices and weightings that have been MIRRORED from [Lightspeed]."*

> *"NEVER guess, assume, or estimate recipe ingredients, quantities, or portion sizes. Every recipe must be based on weighed data from Zak or documented specs."*

> *"Do not invent a yield factor."*

> *"wait why are you rounding numbers? I JUST GAVE YOU EXACT WEIGHTS."* — derived numbers must not be dressed up as measurements

> *"lets just keep a visible log of flags that are needed for our cost book… put this on the recipe book module"* — the flags list, not a markdown doc, is the backlog

> *"is this update live?"* — reported ≠ deployed

> *"Always confirm before writing… Never auto-create products. Never assume par levels."* / *"Batch all par-level questions into one message."*

> *"we do need a human to mark off stock physically received and correct anything that's missing or wrong."*

> *"we will be using our own code to do stocktakes, so we will have complete control over the schema"*

> *"just leave the rent as per olly's BEP file"*

> *"dine in pizza and delivery alcohol is mari. delivery cocktails is stow."*

> *"the whole group pays the same costs per ingredient"* (15 Aug 2026)

> Sell prices for GP: use realized prices from the daily ingested sales reports — happy hours and comps mean list price overstates GP. Lightspeed product sheet = list price, updated rarely. (15 Aug 2026)

> *"DONT HIJACK MY TAB."*

---

## 13. The honest assessment

The engineering is good. The tests are serious — 56 cost-book test files, mutation testing, parser regression corpora, encoding pins, a re-derive fence that only permits costs to rise. The defect discovery is excellent; the audit tooling finds real money.

The problem is not quality of work. It is **shape**. A correction pipeline runs against a source already slated for retirement, with corrections stored in nine files outside the thing they correct, validated by guards that cannot fail, against derived data that isn't committed, coordinated by an honour-system protocol, documented across ten handoff files and twenty-seven chat threads.

Each choice is individually defensible. Together they produce the reported experience exactly: *"everything so far has been extremely fragmented and I continually am trying to solve the same inconsistencies."*

The way out is in `COST_BOOK_ARCHITECTURE_PLAN.md`.
