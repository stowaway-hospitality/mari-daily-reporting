# Handoff — recipe/COGS deep audit, 2026-08-05 (afternoon)

Continues `HANDOFF_20260805.md`. **Run `python3 scripts/audit_book.py` first** — it
reproduces every finding below with current numbers, including six rules that did
not exist this morning.

Branch `recipes/liquor-pack-discriminator` — **PR #5 open**, 7 commits, all pushed.

---

## The one idea, this round

**The P&L was not using the cost book at all, and the book was deferring to
Lightspeed wherever the two disagreed.**

Two independent failures pointing the same way. Every published day carried
`recipe_coverage_pct: 0.0` and a `cogs_source: recipe_blend` that had blended
nothing — `_load_our_costs` read only the 35 hand-authored builder recipes, whose
names do not match POS product names, so COGS fell through to Lightspeed's stale
Average-Cost figure for 100% of revenue while 648 invoice-fed recipes sat unused.

And inside the book, `_trust_direct()` used agreement-with-Lightspeed as evidence
that a recipe *quantity* was believable. Sound in general — it is what stops
Truffle Oil's "4 ml" (really 4 bottles) under-costing 250x. But when **our** rate
is right and **Lightspeed's** is wrong, the disagreement condemned our price and we
fell back to theirs. The one moment the book adds value was the moment it
surrendered.

Both are fixed. Recipe coverage of revenue: **0.0% → 49.2%** (Mari 91.5%,
Stow 45.1%, HG 4.3%).

---

## What changed

| | |
|---|---|
| P&L recipe coverage | 0.0% → **49.2%** of revenue |
| Builder recipes actually costing | 25 → **35** (all of them) |
| Liquor bridges | **+29**, seed-validated |
| Recipes costed off real invoices | **76** (49 up, 27 down) |
| Hugo Spritz | $1.35 (92.9% GP) → **$4.10 (78.5%)** |
| New audit rules | **6** |
| Tests | 366 → **371** |

Both derived files still reproduce byte-identically (CI's hard gate); `build_site`
and `arch_guard` clean.

### Root-cause fixes
- **`cost_on` was never passed `recipes`**, so every sub-recipe resolved against an
  empty list: *"sub-recipe 'Sugar Syrup' has no version in force"*. 9 of 21
  Stowaway builder recipes — every one using a syrup, jam or batch — silently fell
  back to Lightspeed each morning. The message printed every run and read like a
  data gap, which is why it survived.
- **Massenez Elderflower**: BO export says $253/5L ($0.0506/ml, matching every
  Massenez sibling); a `ls-recipe-seed` dated ONE DAY LATER said $24.17/5L and won
  the as-of lookup forever. 10.5x undercost, 3 cocktails.
- **Bittermen's Tiki Bitters**: same class, 6.45x the other way.
- **Liquor invoices never reaching the book**: `resolve_pack` refuses a sizeless
  description ("BOMBAY DRY GIN"). The naive fix (divide by `pack_qty`) is wrong —
  ILG records the CASE but prices *some* lines per BOTTLE, under-costing Patron 6x.
  `seed_matched_liquor_cost()` forms both readings and keeps whichever agrees with
  the product's own seed, else skips.

---

## The six audit rules (the durable part)

Zak's brief was "I constantly find issues". Each rule turns a class of issue found
by hand today into one the audit finds by itself, forever.

1. **combo costs the same as its base** → 20 Wings Deals contain no wings
2. **real recipe priced below cost** → Pepperoni [Dine-in] sells $2.00, costs $2.11
   (hid under the old $3 floor)
3. **LARGE carries LESS than the REGULAR** → 19 lines
4. **ingredient in the REGULAR but missing from the LARGE** → 28 (the "Hawaiian had
   no ham" class, generalised)
5. **batch uses more input than its own name's yield** → Jalapeño Tequila [1L] draws
   7000 ml of tequila
6. **POS cost column far below our book** → reported per venue + category

---

## A correction kept in the history

Commit `f82c51e` diagnosed tap beer as poured at 139 ml. **Wrong.** Zak: *"ALL
schooners are 425ml… how did you get those wrong numbers when everything on
lightspeed was correct when you scraped it"*. He was right — the same shortfall
appears in every category (pizza 0.29x, classic cocktails 0.24x), so it was never a
pour, and the Harry Gatos "control group" was two low-volume products I over-read.
`24b0c2e` supersedes it. Both commits kept so the wrong diagnosis cannot outlive
its correction.

Related: the "$54k missing from the P&L" claim was also wrong. That 6.7% figure
came from `products_weekly.csv`, whose `cost` column is incomplete (the Looker
backfill has null costs). **The daily P&L reports 16.8–22.7% COGS and is sane.**
That feed drives the products API, not the P&L.

---

## What needs Zak — cannot be coded, must not be guessed

1. **Harry Gatos: 95.7% of revenue has no recipe anywhere** — not in our book and
   not in the Lightspeed scrape. Unlimited Dumplings alone is $18,936/13wk. These
   have to be built in Lightspeed Produce before any code can cost them. **This is
   the largest remaining gap by a wide margin.**
2. **The 20 Wings Deals** — which wings, how many? The wings recipes exist
   (BBQ $3.14 / Buffalo $3.91). One number each and the audit goes quiet.
   (Small money: 65 sold in 13 weeks, ~$16–20/wk.)
3. **Pepperoni [Dine-in] $2.00** — a POS price error. Note: 0 units sold in 13
   weeks, so it is a dormant SKU, not an active loss.
4. **Jalapeño Tequila / Coconut-washed Rooster** — almost certainly 700 ml written
   as 7000/4200. Confirm before editing: the change moves cost DOWN, the flattering
   and therefore dangerous direction.
5. **Large-pizza weights** — the 19 large<regular lines need a weighed "large mains"
   sheet, the way `pizza_regular_grams.yaml` settled the regulars.
6. **Cocktails carrying only their spirit** — Mojito has rum and nothing else;
   Whisky Sour has no sour; 7 premium Old Fashioneds dropped the bitters; 7 branded
   Margaritas have no lime/salt/sugar though Classic Margarita does. Produce recipe
   gaps; a bar spec fixes them.

## Verified NOT errors — do not re-chase
- **Carpano vermouth**: $23.17/750 ml = $0.0309/ml is correct.
- **Beaujolais + Terra Cotta**: correctly costed from Zak's seeds ($34.58/$21.89).
  The "no ingredient lines" warning is cosmetic — cost arrives via the seed.
- **680 vs 648 recipes**: by design. The 32 extra are builder-authored recipes in
  `data/recipes/*.yaml`, a separate feed. They cost fine (Southern Squid $2.28).
- **Batch-yield backlog** from the morning handoff: cleared; 648/648 on our book.
- **~20 alarming cost-book rates** (10x onion, 24x Sprite, undivided box prices):
  real defects, but referenced by **zero** recipes — no live impact.
- **Tap beer pours**: see the correction above. Not a pour problem.

## Known limitation, stated not hidden
The costed book carries no effective date, so `_load_book_costs` answers "what does
this cost now", not "what did it cost in June" — right for the daily 6am pull,
approximate for a historical backfill. It never overrides a dated source (builder
recipes win) and only ever displaces Lightspeed's stale figure. **Making the book
effective-dated is the clean follow-up** and would close the last as-of gap.
