# HANDOFF — COGS module audit, 2026-08-06

Four independent audits of the costing module. Every finding below was verified
against a second source before being written down. Ranked by dollar impact.

**Branch:** `recipes/liquor-pack-discriminator` (PR #5).
**State:** all of tonight's work is pushed except the encoding fix (see §0).
**Gate at time of writing:** 466 tests pass, all seams hold, SEVERE 3,
costs.csv reproduces byte-identically. Coverage: stow 91.0%, mari 96.2%, hg 74.2%.

---

## 0. FIXED TONIGHT — needs committing only

`data/costs.csv` and `data/cogs_list.csv` were written with `newline=""` and **no
`encoding=`**, so the write used `locale.getpreferredencoding()`. Every *read* in
the codebase correctly passes `encoding="utf-8-sig"`; only the two writes were
locale-dependent.

Both files carry UTF-8 (`Dom Pérignon`, `Don Julio 1942 Añejo`, `Flor De Caña`,
`Whispering Angel Rosé`, `Germano Caetano Cachaça` — 35 lines in costs.csv, 117
in cogs_list.csv). Under a C or latin-1 locale:

```
$ LC_ALL=C python3 modules/recipes/pipeline/build_costs.py
UnicodeEncodeError: 'ascii' codec can't encode character '\xe9'
```

The write is **not atomic**, so it leaves the fact table truncated mid-row.
Everything below the cut then reads as `MissingCost` or falls back to a stale
`as_of` — silent under-costing, on the file the whole system derives from. It
has not bitten because the Mac and CI are both UTF-8.

Fixed at `modules/recipes/pipeline/build_costs.py:460` and
`modules/invoices/build_cogs_list.py:140`. Verified: `LC_ALL=C` now reproduces
costs.csv byte-identically (md5 `9c813fed…` before and after).

```
bash "_archive/claude-commit-10.sh"
```

---

## 1. Wine by the glass is under-costed 25% — ≈$8,430/yr — THE BIG ONE

`modules/recipes/pipeline/build_costs.py:289` and `:439-442`.

`seed_conv[pid]` does double duty: it is both "the seed's unit" and "the bottle
size to divide a whole-unit invoice price by". Any ProductID with an
`ls-recipe-seed` row (basis `per_L`, dated 2026-01-02) resolves to `(1000,"ml")`,
which **overwrites** the `bo-seed` row's correct `(750,"ml")` (dated 2026-01-01,
so it loses the file-order race). Line 442 then does `bper = pack_cost / 1000`
on a 750 ml bottle.

The ratio is exactly **0.75**, which sits comfortably inside the magnitude guard's
0.1–10 band at line 452, so nothing refuses it.

Evidence, `data/cogs_list.csv`:
```
2026-01-01  20655236  Geppetto Pinot Noir 750ML   $17.5608  basis ''      pq 750 ml  bo-seed-stowaway
2026-01-02  20655236  Geppetto Pinot Noir-Bottle  $23.4000  basis 'per_L' pq 1 ml    ls-recipe-seed
```
`data/costs.csv` records `0.017560/ml`. True rate is `17.56 / 750 = 0.023413/ml`.

| Wine | book /ml | true /ml | 52-wk qty | annual under-cost |
|---|---|---|---|---|
| Version Two Pinot Grigio | 0.008833 | 0.011778 | 3,417 | **$1,571** |
| Geppetto Pinot Noir | 0.017560 | 0.023414 | 1,352 | **$1,515** |
| Villa Fresco Sangiovese | 0.012062 | 0.016080 | 2,208 | **$1,477** |
| Mother's Milk Shiraz | 0.016082 | 0.021442 | 1,466 | **$1,467** |
| Ottelia Chardonnay | 0.017298 | 0.023053 | 1,133 | **$1,236** |
| Cri De Couer Pinot Noir | 0.062152 | 0.082869 | 124 | **$618** |
| Padrillos Malbec | 0.019332 | 0.025777 | 353 | **$477** |
| Carpano Classico (Negroni, Stow Vermouth Blend) | 0.023170 | 0.030893 | 1,216 | ~$71 |

Published GPs are inflated — `Version Two Pinot Grigio - Regular` reads 87.9%,
true ≈ 83.8%.

**Fix direction:** a real stated pack size from a `bo-seed` row must beat a
`per_L`-derived 1000. Prefer the most specific evidence, not the latest row.
Careful: many products are genuinely 1 L bottles and 1000 is right for them.

---

## 2. `recipe-bridge-seed` prices divided a second time

`build_costs.py:357` calls `resolve_pack` on the **description** and ignores the
row's own `pack_qty`/`pack_unit`. Rows stating `pack_qty=1, pack_unit=L` with a
price **already per litre** get divided again by a pack read out of the name.

| ProductID | stated | recorded | error |
|---|---|---|---|
| 22989447 Heinz BBQ Sauce [4L] | $3.475/L | $0.87/L | **4× UNDER** |
| 22989451 Sunshine Smokey BBQ [3L] | $4.1667/L | $1.39/L | **3× UNDER** (7 recipes, live) |
| 22888650 Milk Full Cream 2L | $1.75/L | $0.875/L | **2× UNDER** |
| 22995947 T2 Milk Bun [85g] | $11.53/kg | $135.64/kg | **11.8× OVER** |

Live today: `Large BBQ Chicken Pizza` books 61 ml of BBQ sauce at $0.085 instead
of $0.254 (507 sold/yr); `Regular` 32 g at $0.044 instead of $0.133 (417/yr).
≈$135/yr, and it is why that pizza publishes GP 82.8% with `fully_our_book:true`.
The Heinz 4× and the bun 11.8× are currently masked by a downstream unit mismatch
(`our_cost: null` → falls back to Lightspeed) — latent, not live, but wrong rows
in the fact table.

**Same root cause, second symptom:** `build_costs.py:298-303` sets
`seed_conv[pid] = (1, pack_unit)` from these rows, clobbering the real
`(700,"ml")` / `(1000,"g")`. Because that unit can never match an invoice-side
`ml`/`g`, **118 invoice observations are silently dropped** (instrumented run,
lines 443/452). Worst offenders — every one frozen on its January seed forever:

```
ilg:305-1949P → Buffalo Trace [House]    18 dropped, 12 recipes
ilg:345-5638P → Sailor Jerry [House]     13 dropped,  9 recipes
b-e:14580     → Pizza Tomato Sauce       22 dropped
fresh-fruit-team:HCBCH → Herb Chives     13 dropped
foodlink:101636 → Milk                    8 dropped
b-e:17530     → Kewpie Mayo               8 dropped
```

---

## 3. A wrong-product bridge, and 26 inert ones

`data/product_map.csv:46-47`:
```
ILG,285-0409P,20445814,Four Pillars Olive Leaf,,,,,recipe-bridge,,
ILG,285-0409P,20445815,Four Pillars Rare Dry,,,,,recipe-bridge,,
```
ILG **285-0409P is Four Pillars *Bloody Shiraz Gin***, confirmed twice: every
`cogs_list.csv` line for that code says so, and `data/ilg_pricebook.csv` code
`285-040-9` is Bloody Shiraz. Correct codes are `285-1480` (Olive Leaf) and
`285-0132P` (Rare Dry) — both already in the file pointing elsewhere.
`load_bridge()` is a dict, so **last row wins silently** and the code lands on
Rare Dry. `seed_matched_liquor_cost` computes $0.09380/ml for Bloody Shiraz and
offers it to Rare Dry (true $0.098343); only the accidental `(1,'L')` clobber
from §2 stops it landing. **Fails closed today by luck, not design.** Nine
purchasable IDs have duplicate product_map rows; three map to 3 different
ProductIDs.

**Inert bridges:** 26 of 179 bridges write to a ProductID **no recipe
references**, while the sibling the recipes DO use stays frozen on its January
seed. This is the Appleton `[Bottle]`/`[House]` pattern, unfixed for 9 more:

| bridge target (0 recipes) | ID recipes use | frozen at | invoice says | error |
|---|---|---|---|---|
| 20487225 Grand Marnier [Bottle] | 20445871 (2 recipes) | 0.075414 | 0.097720 | **22.8% under** |
| 20744462 Monkey Shoulder | 20445855 (2) | 0.080257 | 0.088671 | 9.5% under |
| 20483410 Rooster Rojo [Bottle] | 20445833 (**19 recipes**) | 0.078375 | 0.079701 | 1.7% → **$535/yr** |
| + Buffalo Trace, Sailor Jerry, Aperol, Wolf Lane, Corona, Mr Black, Archie Rose, Yamazaki, Lagavulin, Herradura, 1800 Coconut, Brookie's, Macallan, Antica, Four Pillars ×3 | | | | |

Rooster Rojo alone spans 6,068 Classic Margaritas, 1,180 Coconut, 845 Tommy's,
788 Scorched As.

**Worth doing generally:** bridge to the identity the *recipes* resolve, or emit
onto both — plus an audit rule that reports an inert bridge, so this class can
never hide again.

---

## 4. Antica Formula — a per-bottle invoice divided by a size the price book contradicts

`build_costs.py:439-442`. `lightspeed:20484285` is named "Antica Formula Rosso
Vermouth **700ML**" in the BO export, so `seed_conv = (700,'ml')`. But ILG invoice
`03729959` (basis `per_bottle`, note `0/1 repack`) is for a **1 L** bottle — every
other delivery's note reads `6x1LT`, and `ilg_pricebook.csv` code `175-042-0`
states `size_ml = 1000`. `64.27/700 = 0.091814/ml` is written as the live cost:
**43% OVER**; correct is 0.064270.

Systematically: **15 ILG-bridged ProductIDs have a seed pack size that disagrees
with the price book's stated size**, 0.07× to 6.67×. Only Antica has received a
`per_bottle` line so far, and Antica reaches no recipe — so ~$0 today, but it is
one invoice away from hitting De Bortoli 15 L (0.07×) or Fee Bros bitters (6.67×).

---

## 5. ILG parser destroys the case/bottle discriminator

`modules/invoices/parsers/ilg.py:96-99`:
```python
qty = _m(qraw.split("/")[-1]) if "/" in qraw else _m(qraw)   # "0/1" repack -> 1
if qty is None or qty == 0: qty = Decimal("1")
```
ILG's Qty cell is cases/singles, same as Paramount's. `split("/")[-1]` reads the
*singles* half, so **"2/0" → 0 → forced to 1**: two cartons priced as one unit.
`raw_qty` is never stored, so the signal is gone before anything downstream could
recover it. This is the exact bug already fixed in
`parsers/paramount.py::units_on_line` — **read that function; it has the
arithmetic-proof pattern** (`base × units / per_carton == net`, refuse if it
doesn't prove).

Real lines from `data/invoices/2026-06-11_ilg_03712630.json`:
```
BOMBAY DRY GIN               qty 2  unit_price_incl  50.8500  pack 6x700ML  <- per bottle
ROOSTER ROJO TEQUILA BLANCO  qty 1  unit_price_incl 346.3900  pack 6x700ML  <- a CASE of 6
```
Same code, alternating basis, no marker. `per_unit` sanity bounds are $0.10–$500
so **$346.39 for one "unit" passes the validator**.

**306 `cogs_list.csv` rows** have a parser-stated pack disagreeing with the pack
actually used, e.g. `ilg:460-1639 COKE NO SUGAR 1.25L` → $35.46/L (12× over),
`ilg:460-3254 SPRITE 375ML 24 CUBE` → $121.79/L (24× over). Those sit under
`ilg:<code>` identities no recipe references, so **no live COGS impact** — but
they are the fuel for the `/pricing` page's phantom "+2300%" rises, and one
product_map row away from reaching the book.

---

## 6. A chef-confirmed pack override DISABLES the plausibility guard

`build_costs.py:378-381` unconditionally sets `bad = ""`; mirrored at
`build_ingredients.py:486-489`. A per-carton line divided by a per-piece override
produces an absurd rate that no longer gets flagged:

| ingredient | window | recorded | correct | error |
|---|---|---|---|---|
| `foodlink:100487` Camembert (`SI4467596`, `UOM CTN-12`) | 16–22 Jul | **$364.80/kg** | $30.40/kg | **12× OVER** |
| `foodlink:100175` Black Beans A10 (`SI4341099`, `CTN-6`) | 8 May–23 Jul (77 days) | **$0.0174/g** | $0.0029/g | **6× OVER** |
| `fresh-fruit-team:CL20KGBX` Carrot Large (`INB00102377`, per_kg on a 20 kg box) | 1 May–19 Jul | **$27.50/kg** | $1.32/kg | **20× OVER** |

The camembert case is *documented* as a known trade-off in
`data/pack_overrides.yaml:318-329`; black beans and carrots are not. All three
recompute wrong for any historic day in those windows — the answer is stable, it
is just wrong. **An override should pin the PACK, not silence the sanity check.**

---

## 7. 53 split ingredient identities

`build_costs.py:343` uses the raw `supplier_code`. `build_ingredients.py:110` has
`normalize_code()` to strip a unit word bled into the code from the PDF parse —
and applies it only at merge time (`:574`), never to the cost key. So:

```
fresh-fruit-team:AH20T        2026-07-25  $30.80/tray  (n=7)
fresh-fruit-team:AH20T TRAY   2026-08-04  $26.40/tray  (n=14)
```

**53 split identities; 25 hold different latest prices; 6 hold different UNITS:**
```
EGL7BX    $0.266667/ea  vs  $56.00/box
LRW15BG   $17.60/box    vs  $0.011733/g
MSHB2     $0.008250/g   vs  $33.00/box
TGL10BX   $34.16/box    vs  $0.004256/g
POTCOBX   $0.002475/g   vs  $49.50/box
```
Half the price history is invisible to `as_of` on whichever id a recipe holds.
Directly contradicts the comment at `build_ingredients.py:465` ("THE ID MUST BE
THE SAME KEY THE COST ENGINE USES").

---

## 8. `modules/invoices/resolve.py` is dead code — the cost guard never runs

The only importers of `Resolver` / `is_suspect` are its own tests. Nothing in the
invoice→cost path uses it. `build_costs.load_bridge()` (`:58-77`) reads
`product_map.csv` directly and applies:
- **no** Back-Office cost guard (the whole Alehouse Crisp/Premium story),
- **no venue filter** (`Resolver.__init__` has one; `load_bridge` does not — 11
  rows carry `harry_gatos`/`marilynas` and bridge unconditionally),
- **last row wins** on duplicate keys, silently (§3).

Either wire the guard in or delete the module and move its guarantees into
`load_bridge`. The venue filter and duplicate-key loudness must exist somewhere.

---

## 9. Duplicate rows in `cogs_list.csv` double-weight the rolling average

Two rows each for Paramount `5441124` Carpano, De Bortoli `44583`, Sprite `98541`
— identical price, differing only in the `note`. `build_cogs_list._key()`
(`:57-59`) would have deduped them, so a second writer bypassed it; the file is
not idempotent as documented at `:13-16`. `data/costs.csv` carries the duplicate
through. `as_of` is unaffected, but **`CostSeries.rolling()` (`core/domain.py:196-202`)
counts the duplicated observation twice** in the trailing-30-day mean — the live
menu-costing path.

---

## 10. Paramount lines are exempt from both per-line validator checks

`parsers/paramount.py:153-155` sets `unit_price_incl=None` and
`cost_basis=UNKNOWN`. `validator._check_line_arithmetic` returns early on `None`
(`:134`); `_check_sanity_bounds` returns early on `UNKNOWN` (`:280`). So **no
Paramount invoice ever gets the silent-error net** its own docstring promises.
Only the invoice-level reconcile runs. Now that `units_on_line` computes a real
unit count, populate `unit_price_incl` and a real `cost_basis` where the
arithmetic proved, so the validator engages.

---

## 11. `data/ilg_pricebook.csv` drops the column that makes its price interpretable

`scripts/build_ilg_pricebook.py:69` captures the "Case" count as group(6) but
`FIELDS` (`:76-77`) never stores it. `book_price_unit` is per *selling unit*,
which for **1,349 of 6,156 rows (22%)** is a 4-pack or 6-pack, not an item:
```
110-668-0  4 Pines Hazy Pale Cans 4pk  ctn=24  case $79.67  unit $13.28  (= /6, a 4-pack)
115-3762   Corona Mexican 6pk          ctn=24  case $51.55  unit $12.89  (= /4, a 6-pack)
```
7 bridged products affected. `audit_book.py:940` only compares `unit == "ml"`
rows so there is no live wrong number — but the denominator is unrecoverable
from the committed CSV, and that file is the API contract. The PDF lives only on
the Mac at `data/invoice_corpus/ilg_pricebook.pdf`; the script already no-ops
when it is absent.

---

## 12. Reported-coverage defects (verify each before fixing)

- `recipe_coverage_pct` can **exceed 100%** — Marilyna's hit 102.3% on a day with
  a discount row.
- It reads **0.0% on days that were ~97% recipe-costed**.
- It is **published unchanged when the blend is rejected**.
- Every history row currently reads `0.0%` coverage with COGS identical to
  Lightspeed's, **while the JSON labels the source `recipe_blend`** — the
  dashboard is claiming provenance it does not have. Make the label tell the truth.
- `scripts/build_products_weekly.py` reads `"Product Name"` / `"$ Sales"`, but 12
  July exports use the older `"Product"` / `"Sale Amount"` schema, so every row is
  skipped as a footer. Week ending 2026-07-12 shows **$9,183** for Stowaway against
  **$42,006** in the daily history — **$51,044 missing**. Support both schemas and
  **fail loudly** on an unrecognised one rather than silently emitting an empty week.

---

## 13. The sub-recipe $0.00 silent fallback — root cause found

In `scripts/convert_lightspeed_recipes.py`, a sub-recipe line silently costs $0
when the referenced product isn't separately sold on the till (`sell_of` returns
`None`), there is no scraped cost, and the last branch returns zero — while
`resolved_pct` still reads 100. **24 lines currently survive only on that gate**,
including the BBQ Wings line on all 23 Wings Deal recipes.

Make a zero from that path impossible to publish silently: either cost it
properly, or mark the recipe as not fully costed and let the audit shout. (This
is the same defect that made Choc Brownie and the pizza menu-average contribute
$0 to a $45 deal while the recipe still read as complete — the deal builder now
resolves components at build time and skips a deal with any zero component, but
the general path is unfixed.)

---

## 14. Confirmed alias never fires; a lettuce line over-costs a burger

`apply_product_aliases` skips when `pos_name in out`, and `Beef Burger D` already
existed as an entry — so Zak's confirmed alias (Beef Burger D **is** the American
Standard Burger) did not apply through that path. A confirmed alias should win
over an existing entry with no real cost, but never over a genuinely costed
Produce recipe.

Separately: a lettuce line on that burger reads "1 whole twin-pack" where it
should be ~0.083, **over-costing $2.52 a burger**. Verify and fix.

---

## 15. Duplicate product identities at materially different prices

- **Angostura Bitters exists twice, 11× apart.** The cheap copy is 9% of ILG's own
  book price and feeds four Harry Gatos cocktails.
- **Rooster Rojo exists twice, 20% apart.**
- **Harry Gatos' Alehouse Premium keg carries the Crisp keg price.**

Worth an audit rule: "two identities for one stock item at materially different
prices".

---

## DELIBERATELY NOT FIXED — needs the kitchen, not code

**Cook loss is not modelled.** `Cooked Beef Brisket [1Kg]` charges $13.95/kg —
the **raw** price — with no yield factor. Slow-cooked brisket yields 55–60%.
Same shape on `Achiote Chicken [15Kg]`. ≈**$2,000/yr under**.

And the roasts may have it too: if the 220 g on Pork/Lamb/Beef/Chicken Roast is
the **plated** weight rather than raw, they are under by 35–40% across **$22,000**
of revenue. **This is worth more than everything else on this list and only the
kitchen can answer it.** Do not invent a yield factor.

**Dishes with no recipe.** Do not invent recipes. Still outstanding: Shredded
Beef, Miso, Shoyu, Unlimited BBQ, Chicken Karaage, BBQ Meat Platter, Sticky
Chicken Wings, Edamame, Arancini Balls, Baked Camembert, Pie, Roast Turkey, Beef
Cheek, plus the kitchen add-ons (Add Grilled Chicken/Fish, Extra Yorkshire
Pudding, Extra Veg, Side Guac, Side Spicy Salsa, Extra Dumpling). `Open Price`
and `Open Food` cannot be costed by anyone.

**Note on that list:** it was wrong four times tonight. `audit_book.py` no longer
asserts "has no recipe" — it says "no costed recipe UNDER THIS NAME" and attaches
the nearest candidates. Confirmed pairs go in `data/product_recipe_aliases.yaml`,
which fixes the P&L too, because `cogs_blend` keys on the POS name. Four are
confirmed so far: Outback Prawn Toast → Devon's Prawn Toast, Beef Burger D →
American Standard Burger, Petits Detours Rosé Mediterranee - Bottle → Petits
Detours Rosé D, Katsu Curry → Chicken Katsu Curry.

---

## VERIFIED CLEAN (so the coverage is known)

- **Determinism under UTF-8** — costs.csv reproduces byte-identically. No clock
  read, no dict-order dependence, no float formatting in the output path.
- **Zero / null prices** — 0 rows in costs.csv and 0 in cogs_list.csv with cost ≤ 0.
  `build_cogs_list.py:87` refuses a zero qty; `seed_matched_liquor_cost:216` and
  `ls_seed_is_misread:175` both refuse non-positive inputs; `cogs_blend.py` drops
  `our_cost == 0` rather than publishing 100% GP.
- **Invoice reconciliation** — all **502** stored invoices reconcile to within
  $0.50. No dropped or hallucinated lines.
- **The liquor case-vs-single discriminator works.** 33 bridged spirits
  cross-checked against `ilg_pricebook.csv`: all land 0.91×–1.37× of book, median
  ≈1.12× (ILG's broken-carton premium). No 2×/6×/12×/24× outlier survived into a
  `lightspeed:` identity.
- **Paramount's `units_on_line` fix is correct.** Rooster Rojo reads qty 12 →
  $60.82/bottle; Fever Tree reads qty 8 → $4.31/bottle. Fail-closed path is real.
- **`cost.py` unit guard** — converts kg↔g and L↔ml only, refuses pack/each↔base.
- **GST/WET arithmetic** — `GST_DIVISOR = 11` is right for WET-bearing lines. No
  live path found putting an ex-GST figure into `cost_per_unit_incl_gst`.
- **The `P`/non-`P` twin bridge extension** — requires identical descriptions on
  the same supplier; fired once, correctly.
- **Test suite** — 466 pass. **None of the defects above is covered by a test**,
  which is the other half of the work.

---

## WORKING NOTES

- **Pushing.** The cloud sandbox's git proxy refuses this repo (`not in this
  session's authorized repository set`, 403). The GitHub MCP connector *is*
  authenticated as zakstowaway and can push, but takes file contents inline, so
  the 914 KB `lightspeed_recipes_costed.json` and `compare.json` can't go through
  it. Practical shape: push sources via the connector, rebuild derived data where
  it is generated. **Worth setting up properly** — it removes the patch-file dance
  entirely.
- **The Cowork mount cannot unlink files**, so every git command that writes the
  index leaves a `.git/index.lock` it cannot clear, and `git add`/`git commit`
  never land there. `git apply` works. `mv` works, so a stale lock can be moved
  aside. That is why the commit scripts all start with `rm -f .git/index.lock`.
- **Do NOT `git stash -u` on the Mac** — it sweeps the patch files and the cron's
  working-tree output.
- **Several data CSVs are CRLF** (27 of 154, including costs.csv and
  cogs_list.csv). Appending in Python text mode silently converts the whole file;
  `modules/recipes/tests/test_data_line_endings.py` guards the mixed case. Append
  in binary.
- **Full gate:**
  ```
  python3 -m pytest
  python3 scripts/arch_guard.py
  python3 scripts/schema_guard.py
  python3 scripts/test_pipeline_integration.py
  python3 modules/invoices/tests/battletest_pipeline.py --offline
  python3 scripts/test_mari_recovery.py
  python3 scripts/build_site.py
  node scripts/test_pnl_model.mjs && node scripts/test_dashboard_render.mjs && node scripts/test_dashboard_units.mjs
  cp data/costs.csv /tmp/before.csv && python3 modules/recipes/pipeline/build_costs.py >/dev/null && diff -q /tmp/before.csv data/costs.csv
  python3 scripts/audit_book.py | tail -1
  ```
- **Rebuild order after a data change:** `build_costs.py` → `build_ingredients.py`
  → `build_price_compare.py` → `convert_lightspeed_recipes.py`.

---

## SUGGESTED ORDER

1. Commit the encoding fix (§0) — already done, just needs the script run.
2. **§1 wine 750/1000** — the money, and self-contained.
3. **§12 coverage + the missing July week** — the dashboard is currently claiming
   provenance it does not have, which is a trust problem more than a dollar one.
4. §2 + §3 together — same root cause, and §3's inert-bridge rule stops the class
   recurring.
5. §13 sub-recipe zero — it is the shape of defect that has bitten this project
   most often.
6. Ask the kitchen about cook loss. Potentially the largest single number here.
7. The rest, in the order above.

Each one wants a regression test that fails without the fix. None of the 15 is
covered today.
