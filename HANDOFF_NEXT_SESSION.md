# Start here — handoff for a new Cowork chat

Written 2026-08-09 at the end of the COGS audit session. Paste-and-go context.

## Where things stand

- Branch `main`, live at **app.stowawaybar.com**. The Mac's tree is on `main` (it
  spent Saturday on a feature branch, which starved the dashboard — if the home
  page's system-status card mentions the working tree again, that is what it means).
- Gate: **654 passed, 3 skipped**. `audit_book` **SEVERE 3**. `costs.csv`
  reproduces byte-identically. Keep all three true.
- `HANDOFF_20260806_audit.md` (the original 15-finding audit) is **fully closed**.
  `HANDOFF_20260807_cogs.md` has the detail of how each was fixed.

## What changed, in one paragraph

Every finding in the audit, plus four the audit never knew about: **148 of 344 ILG
invoice lines carried a unit price overstated by exactly the carton count**
($21,950 inc-GST — one invoice booked Heaps Normal at $64.08 a tin against a true
$2.67); **Aperol and Rooster Rojo were 30% UNDER**; `data/insights_2026-07-11.csv`
was a **ZIP committed under a .csv name** breaking two builds; and a Lightspeed
column rename on 2026-07-13 **silently voided 11 export files** ($54,236 ex-GST
missing from the Products view). `costs.csv` went 3,585 → 3,880 rows and 126+
recipes repriced, overwhelmingly downward — every GP that fell had been overstated.

## The open list is IN THE APP, not in a file

`/recipes/#flags` — **96 flags in ten families**, derived from the data, ranked by
dollars. That is the backlog. Do not rebuild it; add to it
(`scripts/build_cost_book_flags.py`).

Highest value first:

1. **Re-parse the historical ILG invoices.** The parser fix is FORWARD-ONLY;
   `cogs_list.csv` merges from stored JSON. Re-parsing moves **195 line prices**
   (Veuve $484.58 → $80.76). **BLOCKER:** `raw_uom` describes the CASE while `qty`
   now counts BOTTLES, so `cost_per_base_unit` would read 6x low. Reconcile that
   with `build_costs.py` FIRST. This is worth more than anything left in code.
2. **~19 products bought since June never reached the book** — supplier code never
   bridged to a ProductID. Bridging them would test our rate against a real invoice
   on lines that currently have no second opinion at all. Needs fuzzy name
   matching, so calibrate it before shipping (see "how to add a detector" below).
3. **Yields.** Cook loss is not modelled anywhere. Zak has confirmed the Lamb Roast
   220 g is PLATED. ~$2,726/yr on lamb alone at a 65% yield; ~$4,000 across the
   four roasts; ~$2,300 on brisket. **Do not invent a factor** — the number has to
   come from the kitchen. `Cooked Beef Brisket [1Kg]` is 10 kg raw + aromatics
   spread over the RAW weight; we need the cooked batch weight.
4. Back Office edits (Angostura 11x apart feeding 4 HG cocktails), the
   `suppliers.yaml` bounds entry for the 20 L vodka drum, two case-price-as-each
   seeds, and the feed defects (Lemon at $0.375/ml).

## The honest limit on accuracy

There are two kinds of error and only one is detectable. **~700 of 3,041 lines**
have a peer to compare against (sibling recipes, ingredients used in ≥3 dishes,
declared batch yields) — that is how Chicken Roast and the lettuce were found. The
other **~2,300 are single-use quantities with nothing to compare to**: a cocktail
using one spirit no other recipe touches. If that pour is 30 ml and the recipe says
20, every check passes. Most of the drinks book is in that category. Nobody has
verified those and no code can.

## HOW TO WORK HERE — read before touching git

**This mount cannot delete files.** `/Users/Shared/ClaudeShared/...` allows create
and rename but never `unlink`. Therefore:

- `add` / `commit` / `push` / `fetch` **work**
- `checkout` / `reset` / `merge` / `stash` **cannot** — they must remove files
- git cannot clear its own `*.lock`, so the NEXT command dies with *"Another git
  process seems to be running"*. **It is not another process. It is the mount.**

```
. ops/git_on_the_mount.sh    # then: unlock | g <args> | gpush | sandbox_merge
```

**Never rename a lock in place** — it leaves junk inside `.git/refs/` that git
reads as a ref, and every later fetch dies with `fatal: bad object`. 43 of those
accumulated on 2026-08-08. `unlock` quarantines to `.git/_lockjunk/`.

Anything that removes files happens off the mount: `sandbox_merge` clones to
`/tmp`, merges, gates, pushes. A merge attempted here half-writes files and leaves
them untracked, which then blocks the next attempt — do not retry it in a loop.

Other standing rules: work in a `/tmp` clone and never run builders against the
mounted tree; money is `Decimal`; `data/` facts are append-only; schema changes are
additive-only; **no business logic in `dashboard/*/index.html`** (`arch_guard.py`
fails CI and the deploy).

Rebuild order: `build_costs.py` → `build_ingredients.py` →
`modules/invoices/build_price_compare.py` → `scripts/convert_lightspeed_recipes.py`.

## The two habits that made this session work

**Fail toward review.** Errors that flatter you — low cost, high GP — are the
dangerous ones. Where two sources disagree, refuse rather than guess and say so out
loud. Several fixes here deliberately publish nothing rather than a plausible
number.

**Calibrate every detector before shipping it.** A rule that cries wolf trains
people to ignore the warning, which is worse than no rule. Measure flagged / true /
false against the real book and DROP the ones that fail. Dropped this session: "one
ingredient >30% of dish cost" (380 flagged, a single-spirit cocktail is legitimately
100%), magnitude outlier (13 flagged, 0 true), portion share (4 flagged, 0 true).
Shipped: 4 detectors at 10 flagged / 10 true.

## Product sales questions

Always fetch `app.stowawaybar.com/sales/products/{index,latest,rollup_stow,rollup_hg,rollup_mari}.json`.
Never the sales-lookup skill, never Drive. Revenue is ex-GST; weeks are Mon–Sun
labelled by the Sunday. Quote `generated_at` on the first answer.
