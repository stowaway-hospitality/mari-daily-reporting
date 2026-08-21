# One product, one price

**Zak, 2026-08-21: "we are going to merge all inventory into one, so all
pricing will be global."**

This file exists so the next change follows that ruling instead of deepening
the split. Nothing here has been implemented yet — it is the direction, the
measurement of how far away we are, and the traps found on the way.

## What the book does today

`data/costs.csv` carries a `venue` column and every rate is scoped to it:

| venue | cost rows |
|---|---|
| stowaway | 5,030 |
| harry_gatos | 1,200 |
| marilynas | 94 |

**139 products are priced at more than one venue. 82 of those disagree with
themselves.** Most of the disagreement is 1.3–2x, which is not a price
difference — the group buys on one account, from one supplier, on one invoice
run. It is the same purchase read twice.

## The venue split is not neutral. It hides errors.

The clearest case, found while writing this note:

    fresh-fruit-team:LMM15BX   Lettuce Mesculin
      harry_gatos   $0.017400/g
      stowaway     $17.400000/box      <- 1000x apart

One supplier code, one price, $17.40. Fresh Fruit Team's own UOM string says
`1.5kg box`. Harry Gatos read it as a KILO and had been costing mesculin **50%
too dear**; Stowaway read it as a box and had no gram rate at all, so it could
not cost a salad. Two venues, two readings, both wrong, in opposite directions,
and neither visible to the other. Now declared at 1500 g and both sit at
$0.0116/g.

That is the argument for the ruling in one row.

## What "global" has to mean, carefully

Going global is not "delete the venue column". Three things are genuinely
per-venue and must survive:

1. **Which venue bought it.** The invoice is billed to a venue and the P&L
   splits by venue. Provenance stays.
2. **Genuinely different products under one name.** `lightspeed:22995349` is
   bridged at Harry Gatos to `KITLMWASKG` — mesculin *washed and sanitised*, a
   prepped product at $18.00/kg — and at Stowaway to the unwashed box at
   $11.60/kg. Those are two products, not one price disagreeing. Collapsing
   them would be a 55% error on every Stowaway salad.
3. **The single-till model.** Marilyna's has no till of its own and its 94 rows
   are an attributed slice of Stowaway. Whatever global pricing does, it must
   not make Mari look like a third buyer.

So the rule is: **one PURCHASABLE, one rate.** Where two venues hold the same
supplier code, the price is the same and the newest invoice governs. Where they
hold different codes, they are different products and stay apart — and the job
is to notice which is which, not to average them.

## Where to start

The 82 disagreements are the worklist, and they sort themselves:

- **Same supplier code, different rate** — a defect every time. This is the
  LMM15BX shape: one purchase parsed two ways. Fix the parse or declare the
  pack; do not pick a side.
- **Different supplier codes** — check whether they are actually the same
  product before merging anything. `select-fresh:HCHI` at $3.00 (HG) against
  $2.00 (Stowaway) is the same chives at two market prices on two days, and the
  newest should win. `KITLMWASKG` against `LMM15BX` is not.

## The trap, stated once

While declaring bottle sizes the same day, the house convention said a
spirit/aperitif bottle is 700 ml. Antica Formula is billed by ILG as **1L** and
Back Office holds DefaultSize 1000 — it is a vermouth. Applying the convention
would have over-costed every Antica pour by 43%, and it would have looked
entirely reasonable next to four genuine 700s.

Global pricing has exactly this shape at scale: a rule that is right most of the
time, applied to a list nobody re-reads. Every merge wants the invoice in front
of it.
