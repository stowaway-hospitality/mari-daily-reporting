# HANDOFF — COGS module, 2026-08-07

Continues `HANDOFF_20260806_audit.md`. **§0, §1, §2, §3 and §12 are done.**
Everything below is verified against the real data, not inferred.

**State:** all changes are in the working tree, **uncommitted**. Gate is green:
511 passed / 3 skipped, arch_guard, schema_guard, pipeline integration, mari
recovery 11/11, invoice battletest, all 3 node suites, build_site, cogs_blend.
`costs.csv` reproduces byte-identically. `audit_book` SEVERE 3, unchanged.

⚠️ **Commit these before the cron rebases the tree.** The Cowork mount cannot
clear `.git/index.lock`, so this must be done from the Mac.

---

## What moved

`data/costs.csv` **3,585 → 3,752 rows** (+167 new, 0 lost, 16 repriced).
The costed book: **126 of 892 recipes repriced — 84 GP down, 19 up.**
Every GP that fell was previously overstated.

| POS product | GP was | GP now |
|---|---|---|
| Btl Cri De Coeur PN D | 30.7 | **7.6** |
| Geppetto Pinot Noir D | 48.3 | 31.0 |
| Btl Ottelia Chardonnay D | 50.8 | 34.4 |
| Btl Padrillos Malbec D | 55.7 | 40.9 |
| $5 House Red | 60.2 | 46.9 |
| Cri De Couer Pinot Noir - Regular | 65.8 | 54.4 |
| Version Two Pinot Grigio - Regular | 87.9 | 83.8 |

**The `D` suffix is Marilyna's TAKEAWAY, not the bar.** Those SKUs are priced at
roughly half the dine-in bottle (bottle-shop vs restaurant pricing, deliberate).
Half-of-dine-in works on a $49 wine and collapses on a $127 one, because the cost
does not halve. Worth a pricing look — not a code fix:

- `Btl Veuve Clicquot D` $95 vs $80.76 cost → **6.5% GP**
- `Btl Cri De Coeur PN D` $74 vs $62.15 → **7.6%**
- `Btl Gueguen Chablis D` $64 vs $44.73 → 23.1%
- `Dom Pérignon Champagne - Bottle` $440 vs $332.55 → **16.9%** (this one is the bar)

---

## §1 — stated pack size beats a price basis  ✅

`seed_conv` kept whichever seed row came last in the file. `bo-seed` (2026-01-01)
states a size; `ls-recipe-seed` (2026-01-02) carries `basis=per_L`, which must
resolve to 1000 — so a 1000 nobody claimed overwrote every stated bottle size.
**96 ProductIDs**, not the 8 first counted, and it runs both ways: 0.15x on
150 ml bitters through 50x on a 50 L keg (where the magnitude guard then refused
the line outright, so keg invoices reached the book not at all).

`better_seed_pack()` decides by specificity, narrowed to the same base unit so a
`per_bottle` seed's countable `(1, "bottle")` cannot block a per_L row from
giving a product its per-ml basis. `seed_price` now comes from each row's own
divisor. Tests: `test_seed_pack_specificity.py` (11).

## §2 — recipe-bridge-seed prices divided twice  ✅

The row states its own pack and its price is already per that pack; resolve_pack
re-read the size out of the name. Heinz BBQ 4x under, Sunshine 3x, Milk 2x,
T2 Milk Bun 11.8x over — all four now exact.

`stated_pack_in_base_units()` believes a MEASURABLE pack (L, kg) and refuses a
CONTAINER ("box", "ea"), because there the price is per container and only the
description says what is inside — Barramundi states "1 box" and carries its 5 kg
in the name, so believing the container is 5000x wrong.

Recovered **80 observations** that were silently dropped because `seed_conv` held
`(1, "L")` and no ml invoice could ever match it. Also added kg↔g / L↔ml
conversion at the bridge. Tests: `test_recipe_bridge_seed_pack.py` (7).

**Found while doing this:** eight spirit bridge-seeds are contradicted by the BO
export by 6–10x (Jack Daniels seeded $6.55/L against a published $61.83/L). They
were invisible only because resolve_pack could not read them. `bridge_seed_is_misread()`
now refuses them, reusing `ls_seed_is_misread`'s band; `bo_stated_rates()` reads
`bo-ingredient-seed` too, which is why those eight had no second opinion before.

## §3 — wrong bridge + inert bridges  ✅

ILG `285-0409P` is Four Pillars **Bloody Shiraz** and carried rows for Olive Leaf
and Rare Dry as well. Corrected to `285-1480` and `285-0132P` — each product's
seed matches that code's invoice rate to four decimals. **This had stopped failing
closed** once §2 fixed the unit, so it was live before the correction.

`load_bridge()` now maps one code to MANY ProductIDs and emits onto all, which
kills the inert-bridge class. Added 10 sibling rows after verifying each pair's
seed rates agree within 10%. Grand Marnier 0.075414 → **0.097720** (22.8% under),
Rooster Rojo → 0.079701 across 19 recipes. Test: `test_bridge_reaches_the_recipes.py`.

**Held back — seeds disagree, needs §4 first:** Antica Formula (1.42x), Mr Black
(0.79x), Wolf Lane Navy (0.71x), Archie Rose (units disagree).

## §12 — coverage + the missing July week  ✅

`build_products_weekly.py` read the product name via an `or` chain of keys.
Lightspeed renamed the columns on 2026-07-13, so **every row of 11 older files**
was dropped by the footer guard. **$54,236 ex-GST recovered**; w/e 2026-07-12
Stow now reads $42,049 against $42,006 in the daily history (0.1%). Schemas are
now matched on the HEADER and an unknown one **raises**. A third shape (HG's
reporting-group export) is recognised and skipped deliberately.

`blend_reported_cogs` fixed on three counts: coverage could exceed 100% (a
discount row has negative rev and no recipe, so it shrank the denominator only);
coverage was published even when the blend was REJECTED; and **8 day files say
`recipe_blend` beside `recipe_coverage_pct: 0.0`** — summing per-product
Lightspeed costs is not a recipe blend. Numbers unchanged, labels now honest.

`data/insights_2026-07-11.csv` was a **ZIP archive under a .csv name** (c44c6cb).
It took out `build_site.py` AND the recipe build. Extracted the real CSV;
`ingest_insights_email.py` now unwraps a ZIP attachment by inspecting the bytes.
Guards added that no file in `data/` is an archive or carries a NUL.

---

## ALL FIFTEEN CLOSED — 2026-08-08

Commits on `recipes/liquor-pack-discriminator`: `23ea176`, `054db49`, `5c8a845`
(plus `2f32605` / `49d043d` for §0). Gate at the end: **586 passed, 3 skipped**,
every guard green, `costs.csv` / `ilg_pricebook.csv` / `lightspeed_recipes_costed.json`
all reproduce byte-identically, `audit_book` SEVERE 3 throughout.
costs.csv 3,585 → 3,880 rows.

Three things the audit did not know, found on the way:

1. **148 of 344 ILG lines** carried a unit price overstated by exactly the carton
   count — $21,950 inc-GST. One invoice booked Heaps Normal at **$64.08 a tin**
   (true $2.67), the exact number `validator.py`'s docstring names as the silent
   error it exists to catch.
2. **Aperol and Rooster Rojo were 30% UNDER** — the flattering direction, not in
   the audit at all. Aperol proved itself: the same day, a second invoice priced
   the same bottle 48% higher through a different path.
3. **`recipe_coverage_pct: 0.0` was stale, not broken.** The wiring landed in
   `527392f`; all 8 zero files predate it. Re-measured today: 93–95%.

Two judgement calls made deliberately and recorded in the code so they are not
re-litigated: the §8 **venue filter was measured and rejected** (it would delete
82 observations across 10 HG/Mari products and freeze each on a January seed —
one till, one catalogue), and §4 **refuses rather than guesses** where ILG's book
and its delivery note disagree (Corona, Heaps Normal).

---

## WHAT IS LEFT — none of it is code

1. **Decide on re-parsing the historical ILG invoices.** The §5 fix is
   forward-only; `cogs_list.csv` merges from stored JSON. Re-parsing would move
   **195 line prices** (Veuve $484.58 → $80.76). **Blocker first:** `raw_uom`
   describes the CASE while `qty` now counts BOTTLES, so `cost_per_base_unit`
   would read 6x low. Reconcile that before re-parsing anything.
2. **Back Office edits** (`audit_book` now reports these): Angostura HG
   `20747514` at 8.9% of ILG's book, feeding 4 HG cocktails; Plantation 3 Stars
   6.43x. Rooster Rojo HG and Alehouse Premium-carrying-Crisp cannot be settled
   from here — no HG-billed ILG invoice exists.
3. **A `suppliers.yaml` bounds entry** for White Light Vodka 20 L ($1,012.78) —
   6 Paramount invoices now flip PASS → REVIEW because the validator is finally
   running on them.
4. **Two bad seeds**, harmless today but wrong: `20467596` Garlic Bread seeded
   $59.81/ea against a Gulli case of 40 (~$1.43); `22873876` Pizza Box Inserts
   $11.055/ea against a case of 100.
5. **Lightspeed Produce data entry:** `Chicken Roast`'s protein line is
   `0.5` **ml** of a whole bird — 0.5 × a bird is the right intent, the unit is
   meaningless, and because our book prices that item per EACH the units never
   reconcile, so the line cannot track the B&E invoice. It is also missing the
   Yorkshire pudding and gravy the other three roasts carry.
6. **The kitchen questions below.**

---

## (superseded) original open list

1. **§13 sub-recipe $0 fallback.** 24 lines survive only on that gate, incl. BBQ
   Wings on all 23 Wings Deal recipes, while `resolved_pct` reads 100.
2. **§7 + §9 + §15 split/duplicate identities.** 53 split ids (25 different
   prices, 6 different UNITS); duplicate cogs_list rows double-weight
   `CostSeries.rolling()`; Angostura exists twice 11x apart.
3. **§6 override disables the plausibility guard.** Camembert $364.80/kg,
   black beans 6x, carrots 20x. An override should pin the PACK, not silence
   the sanity check.
4. **§4 + §11 pack size vs the ILG price book.** 15 bridged ProductIDs disagree
   with the book, 0.07x–6.67x. Unblocks the four bridges held back above.
5. **§5 + §10 ILG parser / Paramount validator.** `split("/")[-1]` reads the
   singles half, so "2/0" → 1; `raw_qty` never stored. Port
   `paramount.py::units_on_line`'s arithmetic proof.
6. **§8 + §14 dead cost guard, alias never fires.** `resolve.py` is imported only
   by its own tests — no BO cost guard, no venue filter (11 rows carry
   harry_gatos/marilynas). Also the lettuce line over-costing a burger $2.52.
7. **Cook loss.** Ask the kitchen — see below. Potentially the largest number here.

### Two bad seeds found, not yet fixed

Both are case prices recorded as "1 ea". They do no damage today (the guard is
deliberately not armed for container seeds) but they are wrong:

- `20467596` Garlic Bread — seeded **$59.81/ea**, Gulli invoice is a case of 40 (~$1.43)
- `22873876` Pizza Box Inserts — seeded **$11.055/ea**, case of 100 (~$0.11)

## §13 — the silent $0 line  ✅ (guarded; the 24 lines were already gone)

The audit's 24 lines, including BBQ Wings on the 23 Wings Deals, are **not in the
data any more** — checked before and after this session's changes, both read zero
sub-recipe lines at $0. But the PATH is unchanged (`else: eff = ls` with `ls` at
0), so the next product to land in it would be as quiet as the last.

`test_no_silent_zero_line.py` is the tripwire: no sub-recipe line may cost $0; the
list of deliberately-uncosted sub-cent garnishes may not grow silently; and a
recipe carrying a $0 line may not claim `fully_our_book`.

That last one was already false for three recipes (Frozen Marg's dehydrated lime,
Regular Little Italy ×2 rubbed oregano). `fully_our_book` is the flag the P&L and
the pricing page trust to mean "every line is invoice-priced", so a $0 line now
clears it. **867/892 fully costable, down from 870** — three flags corrected, no
dollars moved.

---

## §5 + §10 — ILG case/bottle discriminator, Paramount validator  ✅

Measured on the 54-PDF ILG corpus (49 parseable, 344 stock lines): **148 of 344
lines** carried a unit price overstated by exactly the carton count — x24 (78),
x6 (40), x12 (21), x8 (6) — **$21,950 inc-GST of purchasing**. Invoice 03694253
booked HEAPS NORMAL 24x375ML at **$64.08 a tin** (truth $2.67), which is the exact
number `validator.py`'s own sanity-bounds docstring names as the canonical silent
error.

`split("/")[-1]` reads the wrong half of BOTH Qty shapes ILG uses (`N` = N
cartons, `0/M` = M loose bottles). Ported Paramount's propose-then-prove pattern:
`cost x cases == total` holds 199/199 whole-carton lines; broken cartons carry an
ILG repack surcharge so the residual lands in [1.0243, 1.0261] on 141/141 lines.
ILG's own footer (`Cases & Repacks`) confirms the derived counts on 50/50
invoices. 4 lines stay unproved and take the fail-high reading (qty 1 at the full
line total) — the only reading that cannot UNDER-cost us. `raw_qty` is now stored.

§10: all **47** Paramount stock lines had `unit_price_incl=None` + `UNKNOWN`
basis across 19 invoices, so both per-line checks returned early — no Paramount
invoice had ever been checked. Now priced + `PER_UNIT` where the arithmetic
proved, and a new `LINE_UNPRICED` ERROR so an unpriced stock line is loud rather
than skipped. Test: `test_ilg_case_bottle_discriminator.py` (14).

**IMPORTANT — the fix is forward-only.** `cogs_list.csv` is byte-identical
because it merges from stored invoice JSON, which was not re-parsed. **Re-parsing
the historical ILG invoices would change 195 line prices** (Veuve $484.58 →
$80.76, Asahi $59.18 → $2.47, Heaps Normal $64.08 → $2.67). That re-extraction is
a deliberate decision, not a side effect — it is the single biggest remaining
correction available and it needs Zak's call.

**Also surfaced:** 6 Paramount invoices now flip PASS → REVIEW, all
`SANITY_BOUNDS` on WHITE LIGHT VODKA 20 L at $1,012.78 — a genuine drum
($50.64/L) exceeding the per_unit $500 ceiling. That is the validator engaging
correctly for the first time; it wants a bounds entry in `suppliers.yaml`.

**Cross-cutting, unfixed:** `raw_uom` on ILG/Paramount describes the CASE
(`6x700ML` → pack_qty 4.2 L) while `qty` now counts BOTTLES, so
`cost_per_base_unit` in cogs_list can read 6x low unless
`seed_matched_liquor_cost` catches it. Reconcile this with `build_costs.py`
before re-parsing anything.

---

## COOK LOSS — corrected, and smaller than the last handoff said

**The yield is not in the Lightspeed scrape and cannot be.** The scrape stores
`{name, qty, unit, cost}` per line — there is no yield, waste or loss concept in
it. (`yield_qty`/`yield_unit` in `recipes_full.json` are OURS and mean BATCH
yield — "Dragon Soda makes 20,000 ml" — and are `None` on every roast.) So the
220 g is the only number Lightspeed holds and nothing in the data says whether it
is raw or plated.

**The previous handoff's "$22,000 of revenue" was wrong**, and so was framing the
exposure as a percentage of it. Actual 52-week roast revenue is **$78,762 ex-GST**
($56,829 for the four named roasts). But the yield scales only the PROTEIN LINE,
not the dish, so if the 220 g is plated:

| yield | Pork | Lamb | Beef | Chicken | **total/yr** |
|---|---|---|---|---|---|
| 70% | 361 | 2,170 | 384 | 306 | **$3,221** |
| 65% | 454 | 2,726 | 483 | 384 | **$4,047** |
| 60% | 562 | 3,375 | 598 | 476 | **$5,010** |

≈**$4,000/yr**, not a share of $22k. **Lamb is two-thirds of it** (1,180 serves at
$19.50/kg) — so it is really one question, about lamb.

### Two roast problems that need no kitchen input

- **`Chicken Roast` protein line is `0.5` "ml" of `Chicken Whole Bird [No.8]`.**
  The dollars are right ($3.05 = half a $6.10 bird); the UNIT is wrong. Fix in
  Lightspeed Produce, not in code.
- **`Chicken Roast` is missing the Yorkshire Pudding and Gravy Prep** lines that
  Pork, Lamb and Beef all carry. Either the recipe is incomplete or the dish
  genuinely differs — the kitchen knows.
- `Meat Roast` needs nothing: it ran 2026-01-11 → 2026-06-07 and was split into
  the four named roasts (Beef Roast starts 2026-05-10). Retired, not missing.

### For the kitchen — ask only this

1. **The 220 g of lamb on the Lamb Roast — raw into the tray, or on the plate?**
   If plated, we are under ~$2,700/yr on lamb alone. Same question applies to
   pork, beef and chicken but they are ~$400 each.
2. `Cooked Beef Brisket [1Kg]` is charged at **$13.95/kg, the RAW price**, with no
   yield. What does 1 kg of raw brisket weigh after the slow cook? Same for
   `Achiote Chicken [15Kg]`. ≈$2,000/yr.
3. Still uncosted, no recipe: Shredded Beef, Miso, Shoyu, Unlimited BBQ, Chicken
   Karaage, BBQ Meat Platter, Sticky Chicken Wings, Edamame, Arancini Balls,
   Baked Camembert, Pie, Roast Turkey, Beef Cheek, plus add-ons.
