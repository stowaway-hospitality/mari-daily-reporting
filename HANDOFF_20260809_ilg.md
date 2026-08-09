# Start here — ILG historical re-parse

Written 2026-08-09. One job, stated fully. Paste-and-go.

## The job

Re-parse the 54 historical ILG invoices so the cost book carries per-BOTTLE prices
instead of per-CASE ones. It moves **195 line prices** — Veuve Clicquot currently
sits at **$484.58** where the truth is **$80.76** — and it is worth more than
anything left in code.

**It is blocked on one thing, and that thing must be fixed FIRST.** See below.

## Why it is not done yet — the blocker, precisely

`modules/invoices/parsers/ilg.py::units_on_line()` was fixed to read ILG's Qty
cell correctly: a bare "1" against a Pack of "6x700ML" is ONE CARTON = **6 bottles**,
not 1. That fix is **forward-only** — `cogs_list.csv` merges from the stored JSON in
`data/invoices/`, so the history still carries the old reading.

The trap on re-parsing:

    a stored ILG line today:
      qty            1          <- old reading: "one thing"
      raw_uom        "6x700ML"  <- the CASE
      pack_qty       4.2        <- the CASE, in litres
      pack_unit      "L"
      unit_price_incl 310.51    <- the whole carton

    after a re-parse:
      qty            6          <- CORRECT: six bottles
      unit_price_incl 51.75     <- CORRECT: per bottle
      pack_qty       4.2        <- STILL THE CASE. This is the bug.

`cost_per_base_unit` is derived as price ÷ pack (see
`modules/invoices/build_cogs_list.py`, the `pack_qty`/`pack_unit` block around
l.121-148). With a per-BOTTLE price over a CASE pack you get

    $51.75 / 4200 mL = $0.0123/mL      instead of      $51.75 / 700 mL = $0.0739/mL

— exactly **6x low**, i.e. a cost that UNDER-states, which FLATTERS GP. That is the
direction this repo treats as dangerous, and nothing in the 652-test suite catches
it, because every guard still passes on a self-consistent wrong number.

**So: make `pack_qty`/`pack_unit` describe ONE unit whenever `qty` counts units,
prove it, and only then re-parse.** `modules/recipes/pipeline/build_costs.py`
already carries ILG case-vs-bottle reasoning (`seed_matched_liquor_cost`, ~l.720-790,
and the notes at ~l.900-950) — read it before changing anything, it explains why
some ILG lines are priced per bottle and some per case.

## What "done" looks like

1. `pack_qty` reconciled with `qty` (the blocker above), with a test.
2. All 54 ILG invoices in `data/invoice_corpus/ilg/` re-parsed.
3. **Per-line validation, not just a green suite.** Every moved price checked
   against its expected per-bottle figure. Veuve must land at **$80.76**. Any line
   that moves by ~6x in the WRONG direction is the bug reappearing.
4. Full gate green (below), and `audit_book` still SEVERE 3.

## The gate (all of it, every time)

    python3 -m pytest -q                      # 652 passed  — NOTE: use python3, NOT
                                              #   /opt/homebrew/bin/python3.12 (no pytest there)
    node scripts/test_recipe_book_flags.mjs
    node scripts/test_recipe_flags_families.mjs
    python3 scripts/arch_guard.py
    python3 scripts/schema_guard.py
    python3 scripts/build_site.py
    # and costs.csv must reproduce byte-identically:
    cp data/costs.csv /tmp/before && python3 modules/recipes/pipeline/build_costs.py \
      && diff -q /tmp/before data/costs.csv

Rebuild order when anything upstream changes:
`build_costs.py` → `build_ingredients.py` → `modules/invoices/build_price_compare.py`
→ `scripts/convert_lightspeed_recipes.py` → `scripts/build_cost_book_flags.py`

## Two traps this repo has already sprung (do not repeat them)

* **A green CI is not proof when a feed fails to build.** `build_cost_book_flags.py`
  crashing means the node suites SKIP their real-data checks and report 0 failures.
  On 2026-08-09 that shipped a broken builder. Always confirm the feed actually
  built (`test -f data/cost_book_flags.json`) and that the families test prints
  `(real feed: N flags ...)`.
* **Never pin a COUNT in a test.** Several tests asserted "the four batches", "the
  two burrito findings", ">= 10 reconcile findings". Every one went red when a
  finding was legitimately SETTLED. Assert the shape/family, not the total.

When a fix closes a flag, the test that asserted the DEFECT must be rewritten to
guard the FIX ("re-break it and this reds"), not deleted.

## How to work here

* **The mount cannot delete files.** `add`/`commit`/`push`/`fetch` work;
  `checkout`/`reset`/`merge`/`stash` cannot. Use `. ops/git_on_the_mount.sh`
  (`unlock`, `g`, `gpush`, `sandbox_merge`). Anything that removes files happens
  off the mount.
* **Work in a `/tmp` clone**, never the mounted tree, and never run builders
  against it. Push with the PAT at `.secrets/github_pat_v2.txt`.
* Money is `Decimal`. `data/` facts are append-only. Schema changes additive-only.
  No business logic in `dashboard/*/index.html` (`arch_guard.py` fails CI + deploy).
* **Fail toward review.** Errors that flatter — low cost, high GP — are the
  dangerous ones. Where two sources disagree, refuse and say so.

## What was finished on 2026-08-09 (do not redo)

* **Angostura under-cost fixed.** HG's Back-Office seed had a 200 mL bottle at
  $1.34 against an ILG invoice of $17.305 (13x low); four HG cocktails were costing
  off it. Seed corrected; 11 recipes repriced upward.
* **Red Chilli 10x under-cost fixed.** Book had $1.1667/kg, seeded circularly from
  Lightspeed's own recipe lines. Invoices say $12.10–$16.00/kg; set to the invoiced
  $16.00/kg. This also dissolved the "8,569 g of chilli" batch finding — at the
  right rate that $10.00 is 625 g.
* **Real batch yields captured** into `data/recipe_yields.yaml` (34 of them) and
  wired into `declared_yield()`. The scrape had NEVER captured a yield — 0 of 852 —
  so the number in the NAME was being read as the yield. It was wrong by 7.5x on
  Jalapeño Tequila (a "[1L]" batch that makes 7,500 mL) and 10.5x on Cooked Beef
  Brisket. **The batch_yield family is now empty — all four findings settled.**
* **Fish Burrito** genuinely had no lime; added at source in Produce
  (Lime [ea], 0.125 Units). **Cauliflower Burrito's "missing cheese" was a false
  positive** — it carries Vegan Shredded Cheese at the same 55 g — so
  `missing_standard_component` now treats a same-slot substitution as not-missing.
* Flags panel: rows lead with the ASK and fold the rest; questions that could not
  be meant (900 g of shallots read as "900 bunches") now ask for the conversion;
  the by-the-piece "can" family is judged against `suppliers.yaml` bounds instead
  of asking about all ten. Flags 96 → 89.
* Sales pipeline self-heals a missed day (ingest look-back 2 → 8 days, re-dispatch
  when a day's data never landed) + a "Sales completeness" health check.
* Xero approvals poller can no longer wedge on a network blip (socket timeout).

## Still open besides ILG

1. **19 Harry Gatos batch yields.** Blocked: HG is a separate COMPANY in
   Lightspeed, and the Back Office company switcher does not respond to
   automation (driven six ways). Zak switches it, then re-run the harvester —
   ~90 seconds for all of them.
2. **~19 products bought since June never bridged** to a ProductID. Needs fuzzy
   matching, so calibrate flagged/true/false before shipping it.
3. **12 batches have no Expected yield set in Produce at all** — listed under
   `no_yield_set_in_produce` in `data/recipe_yields.yaml`. A real ask for whoever
   owns those recipes; they cannot be costed per serve until it is filled in.
4. **Five kitchen weighings** for cook loss: lamb, pork and beef roast (raw joint
   in → plated portions out), plus the 10 kg brisket and 15 kg achiote chicken
   batches (raw in → cooked out). Chicken Roast is CLOSED — half a bird is plated
   whole, so nothing is lost between buying and serving.

## Product sales questions

Always fetch
`app.stowawaybar.com/sales/products/{index,latest,rollup_stow,rollup_hg,rollup_mari}.json`.
Never the sales-lookup skill, never Drive. Revenue is ex-GST; weeks are Mon–Sun
labelled by the Sunday. Quote `generated_at` on the first answer.
