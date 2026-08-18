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
