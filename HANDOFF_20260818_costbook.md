# Cost book — where it stands, 18 Aug 2026

Supersedes `HANDOFF_20260816_phase2.md` for anything that changed since.
CI green. Cost book: SEVERE 0 on a fresh rebuild, shadow diffs boring, three venues staged.

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

## CLOSED 19 Aug — the Tandoori defect

It was three bugs wearing one symptom, and every one of them was a correction
that had been made and then quietly lost on the way to the number.

**What it looked like.** Six Tandoori products costing $100-$188 against $19.50
to $31.00 menu prices, -257% to -959% GP, $2,924 of quarterly revenue. The
audit ratchet went red on every commit — and *only* on a fresh rebuild, which
is why it kept looking clean locally: run against the committed artifact it
reported SEVERE 0, and CI rebuilds.

**The live book.** `Tandoori Chicken [2Kg]` draws 400 **ml** of a batch that
yields **grams**, so Lightspeed billed 400 batches: $2,940 against a $13 batch.
Our own book refused the line — `our_cost` is `None`, correctly — and then
`eff_cost` fell back to `ls`, the very number we had just refused. Falling back
to a datum you have judged unusable is not a fallback.

Fixed with an invariant that needs no unit, no yield and no density: **a draw
FROM a batch is capped at the batch.** Threshold 20x, and the threshold is the
design — at 1x the rule is wrong more often than right, because Super Lime
Juice really does draw three whole `Lime [ea]` batches and capping that would
under-cost it. Tandoori is 224x. Large Tandoori Chicken now costs $3.60 at
84.2% GP, between Hawaiian ($3.17) and BBQ Chicken ($3.97).

The cap is a BOUND, not a measurement — 400 g of a 1,116 g batch is a third of
it, so it still over-costs — and it says so: the line carries `capped_at_batch`,
`book_reconcile.capped_at_batch()` reports it, and it is HIGH on the Flags tab
addressed to the Kitchen. **Weigh a Tandoori batch and this stops being an
inference.**

**The staged book.** Renan typed the line free-hand: "Tandoori Sauce [Batch],
400, ml, $7.35". $7.35 is the whole batch's cost, not a rate. He was not wrong
about the recipe — 400 g of marinade on 1.7 kg of chicken is 24% by weight, and
1,700 + 400 against a 2,000 g yield is a 4.8% cooking loss — the LINE TYPE was
wrong. A manual line whose description names a recipe we hold is now a
sub-recipe draw, and the authored unit cost is dropped, so it tracks: the sauce
is $13.08 today, not $7.35.

**Two silent failures found underneath, both the same shape — a rule that was
running and reaching nothing:**

1. The declared fix in `batch_yield_units.yaml` was written against a record
   reading "1,000 g chicken + 1 ml sauce". Renan re-saved it as "1,700 g +
   400 ml". The fix keys on `from_qty`, so it stopped matching the moment the
   record moved — silently, still sitting in the file with its worked
   arithmetic, correcting nothing. **Treat every `from_qty` in that file as a
   claim about a record a chef can edit at any time.**
2. Declared line fixes never reached AUTHORED records at all: the materialiser
   applied them to the scrape, then the authored branch copied the builder
   block verbatim and `continue`d past them. That is the whole explanation for
   the previous session's note that declaring the relabel "does relabel the
   line but does not move the cost".

**Shadow diffs are now boring**, which is the promotion criterion:

| venue | max abs delta | sum abs delta |
|---|---|---|
| Marilyna's | $0.56 (was $184.37) | $3.61 (was $821.72) |
| Stowaway | $0.71 | $7.25 |
| Harry Gatos | $0.39 | $1.35 |

## Also closed 19 Aug — container sizes are declared, not parsed

The prerequisite for the standing instruction to stop trusting unit labels.
Labels are only droppable once every container-priced ingredient has a DECLARED
size; the other order turns a 30 ml pour into thirty bottles.

`data/container_sizes.csv` was 254 rows parsed out of product NAMES. It is now
507, sourced from the Back Office export, which distinguishes what Lightspeed
already knew and we were not reading:

    Archie Rose Signature Gin           InventoryType ''   Unit unit  DefaultSize 1     <- the POUR
    Archie Rose Signature Gin [Bottle]  InventoryType '1'  Unit ml    DefaultSize 700   <- the STOCK

126 recipe references point at sale items; 106 have a stock twin holding the
answer, and 98 rows now come through one (`source=back_office_twin`, twin named
in the evidence). The join is a name stem and is treated as the matcher it is:
it speaks only where every stock record sharing the stem agrees. Campari 750 vs
700, Pepperoni unit vs 3 kg, Lemon sliced/each/kg, Corn Chips 1000 g vs 214 g
are questions, not facts.

**BACK OFFICE'S NUMBER IS EVIDENCE; ITS UNIT LABEL IS NOT.** It files both
conventions under the same string —

    Tomato Ketchup 4L Heinz   Unit=l  DefaultSize=4      -> 4 litres
    Tomato Sauce Heinz [4L]   Unit=l  DefaultSize=4000   -> 4000 ml, mislabelled

— and my first cut believed the label, turning sriracha into 730,000 ml. The
same 1000x shape as every other unit failure in this book, arriving from a
source I had just called authoritative. `_resolve_bo()` now takes the number and
decides scale on evidence: the name if it states a size, else plausibility, else
REFUSE.

Three sizes corrected on the way, all the piece-vs-pack trap in reverse — the
name quoted the piece, Back Office knew the carton: schnitzel 150 g -> 6 kg,
nuggets 1 kg -> 6 kg, wagyu patties 150 g -> 3 kg.

Wattleseed also cleared SEVERE 1 -> 0: $0.385/g tripped the "dearer than
anything real" ceiling, but ground wattleseed genuinely retails $19-45 per
100 g, and code, description and invoice all say 100 g. It is simply an
expensive spice.

## What is still open

1. **Weighings, and the Tandoori is now on the list.** Pizza Sauce ($1.06M,
   146 dishes) and Pizza Dough ($849k) are a third of the $6.05M resting on
   unweighed yields. `data/_worklist/yield_verification.html`.
2. **~60 unreviewed identity bridges.** NEVER auto-apply.
3. **20 sale-only recipe references with no stock twin** — inventory tracking
   is simply switched off for them in Back Office. An ops gap, not a costing
   bug, but they cannot be sized until it is on.
4. **Corn Chips**: two stock records, 1000 g and 214 g. Which is the bag?
5. **40 stale-recipe lines** to clear out, per Zak: historical COGS comes from
   Xero, not this system.
6. **Mr Iceman is not in the ledger at all.**
7. **Promotion.** The diffs are boring now. `--promote` per venue is available
   whenever Zak wants it — that is the one-way door, so it is his call.
8. **The builder can still offer a unit the batch cannot supply.** Every defect
   above began there. Fixing it at the point of entry is worth more than any
   guard downstream.
