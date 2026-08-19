# Cost book — where it stands, 18 Aug 2026

Supersedes `HANDOFF_20260816_phase2.md` for anything that changed since.
CI green, no claims held.

## The scoreboard

| metric | was | now |
|---|---|---|
| SEVERE findings | 7 | **0, pinned in CI** |
| Manual lines (book can't price) | 286 | **160** |
| Venues staged | 1 | **3** |
| Products costing both ways | — | Stow 545/545 · Mari 224/224 · HG 58/59 |

## What changed today

- **T6 landed.** `CostSeries.as_of` is venue-blind on PRICE — Stowaway was paying
  20 July's onion price because the 1 Aug invoice hit Marilyna's account. It is
  NOT venue-blind on unit: the named venue still fixes that, because taking the
  newest row outright picked a per-BUNCH price for something drawn in CANS.
- **Single-serve containers count.** `can/tin/bottle/stubby/jar/punnet/bunch` now
  pair with `ea`. One `ea` of a beer and one `can` of it are the same drink.
  `box/carton/crate/case/pack/tray` deliberately still refuse — they hold many,
  and that is the $11,400/serve guard.
- **Our invoice beats a Back Office seed**, asymmetrically: any RISE is ours; a
  FALL past a third of today's figure is a unit problem wearing a price and stays
  frozen. Both halves were learned by getting them wrong first.
- **Name brackets are never a yield.** `[1L]` was wrong every time it disagreed.
- **24 identity bridges**, judged one at a time. In `data/product_map.csv`, which
  is the source — `ingredient_map.csv` is GENERATED and hand-edits are wiped.
- **Realized-price GP** (`scripts/build_realized_gp.py`): Stowaway's discount
  drag annualises to ~$81k; Marilyna's *uplifts* $7,459 rather than discounting.

## Still open

1. **Weighings.** `data/_worklist/yield_verification.html` — $6.05M rests on
   unweighed yields; Pizza Sauce ($1.06M, 146 dishes) and Pizza Dough ($849k)
   are a third of it.
2. **~60 unreviewed identity bridges.** Regenerate the sheet with `/tmp/rev.py`
   logic; the method is proven on 23. NEVER auto-apply: a matcher offered
   "Hahn Super Dry GF -> ASAHI SUPER DRY", Coke Zero -> Better Beer, and apple
   cider -> apple cider VINEGAR.
3. **40 stale-recipe lines** to clear out (no recent sales AND no recent
   invoice), per Zak: historical COGS comes from Xero, not this system.
4. **Mr Iceman is not in the ledger at all** — that supplier's invoices are not
   being ingested. Real fix is the mailbox pipeline.
5. **Promotion.** `--promote` per venue once the daily diff is boring.

## The pattern worth remembering

Four SEVERE pack misreads today (mousse ×3, finger lime, spring rolls) were ONE
bug: the parser takes a size out of the product DESCRIPTION and treats it as the
pack. Every one was solvable from the product name alone — the tell is a
description carrying both a piece size and a pack count ("15gm 96'S", "12/10
pcs", "45GM"). None needed a catalogue.

---

## OPEN DEFECT, found 19 Aug — Tandoori Chicken in the STAGED book

`Regular Tandoori Chicken` costs **$187.16** in `data/recipes/_staged/marilynas.yaml`
against $1.90 in the live book. A 100x error.

**It is not in production.** Nothing is cut over; the P&L still reads the costed
book. The shadow diff caught it, which is exactly the job the cord exists to do —
and it is the reason promotion waits for a boring diff rather than a green suite.

What is known:

* `Tandoori Chicken [2Kg]` is HAND-AUTHORED (not scraped): 1,700 g chicken +
  400 **ml** of `Tandoori Sauce [Batch]`, declaring a 2,000 g yield.
* That sauce batch yields 1,116 **g**. A gram batch drawn in millilitres should
  REFUSE in `modules/recipes/cost.py`; instead the line costs $2,940, i.e.
  $7.35/ml — which is the yoghurt line's whole cost. Something is resolving the
  sub-recipe to a per-unit rate rather than dividing by the yield.
* Read as GRAMS the authored record is coherent: 1,700 + 400 = 2,100 g in
  against 2,000 g out, a 5% loss, normal for a marinated batch that is cooked.
  So the record's quantities are right and its UNIT LABEL is wrong.
* Declaring the ml->g line fix and applying declared fixes to authored records
  DOES relabel the line (confirmed in `unit_relabels`) but does NOT move the
  cost — so the $2,940 comes from further up the chain, not from this line.
  That is where the next session should start.

Also note this supersedes the earlier `line_qty_unit_fixes` entry that reads the
SCRAPE's "1 ml" as the whole 1,116 g batch: the authored record wins, and a
person has since written down what actually goes in. That entry is now dead
weight and should be removed once the real cause is found.

**Do not promote Marilyna's until this is closed.**

---

## URGENT — main is RED and the cause is a chef's recipe, not a code change

**`Large Tandoori Chicken` costs $135.31 against a $25.00 menu price. −495% GP,
68 sold in 13 weeks. `Regular Tandoori Chicken` is $187.78 against $19.50.**

### Why CI is red on every run

The COMMITTED `data/lightspeed_recipes_costed.json` is fine ($19.55 for the
batch). A FRESH rebuild is not — it explodes to $2,960 — so the SEVERE ratchet
fails in CI on every commit, including ones that touch nothing related.
`SEVERE 17` against a pinned baseline of 0.

### The cause

`92d59c2b — Recipe: Tandoori Chicken [2Kg] (marilynas) — renan@stowawaybar.com`

Renan saved, through the builder: **1,700 g chicken + 400 ML of
`Tandoori Sauce [Batch]`**, declaring a 2,000 g yield. That batch yields
**grams**.

The quantities are RIGHT. Read as grams: 1,700 + 400 = 2,100 g in against 2,000 g
out — a 5% loss, exactly what a marinade that is then cooked should show. Only
the unit label is wrong.

Read as millilitres the units never meet, and instead of refusing, the line
resolved to **$7.35/ml** — which is the yoghurt line's entire cost — giving
$2,940 of sauce and a $2,960 batch.

### What I tried, and why each failed

1. `data/batch_yield_units.yaml` line fix + applying declared fixes to authored
   records — the relabel IS recorded in `unit_relabels`, but the cost did not
   move. Affects the materialiser only.
2. `data/recipe_line_unit_fixes.yaml` — the converter runs `apply_unit_fixes()`
   on the RAW SCRAPE at the top of `main()`, and the authored recipe is merged
   in *afterwards* by `load_our_book_lines()`. The fix cannot reach it.
3. Appending a corrected block to `data/recipes/marilynas.yaml` (append-only,
   tail-wins) with `unit: g` — the converter still produced $2,960, so its
   authored-recipe pickup is not tail-wins, or it reads elsewhere. **This is the
   thread to pull.**

All three are reverted. The tree is clean and matches main.

### The two real bugs behind it

* **A millilitre draw on a gram batch should REFUSE.** `modules/recipes/cost.py`
  does exactly that. The converter's own prep resolution produces a number
  instead — and a number nobody can defend is worse than a refusal.
* **The builder offered a unit the batch cannot supply.** A chef picked ml from
  a dropdown for a batch measured in grams and got a −495% GP product. That is
  not a data-entry mistake to blame him for; it is the UI letting it happen.

### Do this first

Find where `convert_lightspeed_recipes.py` resolves an authored sub-recipe line
whose unit does not match the batch's yield unit, and make it refuse. That fixes
the class, not the instance. Then correct Renan's record.

**Marilyna's must not be promoted until this is closed.**

---

## Disregarding unit labels — tried, measured, and what it actually needs

Zak, 19 Aug: *"the unit label needs to ALWAYS be disregarded... they constantly
trip you up. we just need yields to be estimated until legitimately recorded."*

He is right about the diagnosis. Every costing bug in this session was a LABEL
disagreeing with a quantity that was itself correct: "6 ml" of Asahi meaning six
cans, "1 ml" of a sauce meaning a whole 1,116 g batch, "2 ml" of a [4L] pack
meaning two packs, "0.077 ml" meaning 0.077 OF a batch, and a chef picking ml for
a batch measured in grams.

**Implemented and measured. It takes manual lines 160 -> 66 and refusals to
almost zero. It also produces $1,862.40 of gin.**

    Archie Rose Signature Gin   30 ml  ->  $1,862.40   (price is $63.92 per BOTTLE)
    Flor De Cana 4 Extra Seco   30 ml  ->  $1,450.50
    Stowaway diff  max $1.99 -> max $1,859.57

Why: "30" of a spirit priced per bottle means 30 ml OUT OF a 700 ml bottle. With
the label gone there is nothing left to distinguish that from 30 bottles. The
label was carrying the only signal that the quantity was a base unit rather than
a count.

Normalising the RATE to base units first (kg->g, L->ml) does not help — a bottle
is not a mass or a volume, it is a container of unstated size.

### So the rule is right and the prerequisite is missing

Labels become genuinely disregardable the moment every container-priced
ingredient has a DECLARED SIZE. `data/declared_conversions.yaml` already does
exactly this for 11 items — 7 wine bottles at 750 ml, Aperol 700 ml, Alehouse
2x49,500 ml, Grifter 50,000 ml — and the plan's acceptance test for one is that
supplier rate / declared qty lands on the book's independent rate.

**The order is: declare the sizes, THEN drop the labels.** Doing it the other way
turns a 30 ml pour into thirty bottles.

Roughly how much is left to declare: every ingredient whose price unit is
bottle/jar/punnet/bunch/tray and which a recipe draws in a base-unit quantity.
That is a finite, listable set and it is the single highest-value piece of work
left in the cost book — it closes ~94 manual lines AND makes the label rule safe.

The change itself is reverted; this section is the map for redoing it in the
right order.
