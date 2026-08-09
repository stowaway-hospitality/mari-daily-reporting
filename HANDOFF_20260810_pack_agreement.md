# Start here — what is left after the ILG re-parse

Written 2026-08-10. Supersedes `HANDOFF_20260809_ilg.md`, whose one job is DONE.

## What was finished (do not redo)

**The ILG re-parse landed.** `063d9b7` then `a38ec33`.

`units_on_line` had fixed the COUNT and said nothing about the PACK, and
`cost_per_base_unit` is price ÷ pack — one division, so both halves have to
describe the same thing. `ilg.py::one_unit_pack()` now reads the inner size off
the Pack cell and sets `pack_qty`/`pack_unit` whenever the count PROVED. Veuve
Clicquot lands at **$80.76 a bottle / $107.68 per L**.

49 of 55 ILG invoices re-parsed, 331 cogs rows re-derived, per-line validated:
**138 repack lines RAISED** (they were 6x low — a per-bottle price over a case
pack, live in the book), 239 held constant, 14 packs became measurable, **no ILG
cost fell**. 14 `costs.csv` rows fell, all soft drinks whose case price was being
divided by one bottle's size from the description (Sprite $1.90/375ml).

**The merge no longer makes fixes forward-only.** `build_cogs_list` skipped every
identity it already had, which is why the parser fix could not reach 344 old
lines. A row derived from a validated invoice now tracks its source.
`lightspeed_product` / `basis` / `pack_size` are a human's judgement and are
never re-derived.

**And it is fenced.** A re-derive may raise a cost freely and may not quietly
lower one, because a cost that comes out too high gets looked at and one that
comes out too low flatters GP and nothing asks. Three of the first four rows it
unblocked outside ILG were wrong, all cheaper, and are HELD and reported on every
run. All three are correct as they stand — verified, no data change needed:

| row | held move | why the STORED value is right |
|---|---|---|
| Y&R Villa Fresco Sangiovese 24 | $12.06 → $6.03 | "24" is the VINTAGE read as a case count; `raw_uom` C750 is a case of 12, so 2 cartons is 24 bottles not 48. Lightspeed's BO export states $12.06 for the ProductID. |
| Foodlink FLOUR TORTILLAS 12X91GM | $33.60 → $5.60 | one CTN-6 split into 6. Foodlink's OWN deterministic parser reads code 101113 as $33.60 for one CTN-6 on a real corpus PDF, matching the "confirmed bridge from Foodlink 101113" seed. |
| B&E CHICKEN BREAST (F) SLICE | $61.00 → $12.20 | both readings are individually RIGHT ($61.00/per_unit against a "5KG BAG" description is $0.0122/g; $12.20/per_kg is also $0.0122/g). The mixture is not: `basis` is JUDGED, so a re-derived price under a pinned basis lets the description's 5 kg divide a per-kg price → $2.44/kg. |

**The class, not the instance.** `scripts/check_pack_agreement.py` looks for a
price/pack disagreement everywhere. A pack misread does not move a cost by a
plausible amount — it moves it by a WHOLE PACK FACTOR, and real price movement
never lands on 6.000. Each code is its own control via the MEDIAN of its
deliveries (median, not mean: when the ILG history was wrong it was wrong
TOGETHER). It found 7 lines, every one UNDER-costed; six were the un-reachable
ILG invoices below and the seventh was new:

* **Fresh Fruit Team MKB500PUNN** — King Brown mushrooms at **$7.56/kg** against
  the $30.25/kg every other delivery of the same code states. FFT prints the unit
  in its own column ("200g punnet") and the money row lands BETWEEN the two rows
  that column wraps across, so the line reached the book with no stated unit and
  `pack_size` scavenged the description — which had also wrapped, to
  `"Punnet) 8 x 100g packs supplied for"`. That "8 x 100g" is the supplier
  describing the whole FOUR-punnet line (4 × 200 g = 800 g = 8 × 100 g). Fixed by
  leading with the UOM, which is what `pack_size` already claimed to do
  everywhere else. **The price ($6.05) had been identical to its neighbours the
  whole time** — only the pack was wrong, which is exactly why nothing caught it.

## The six unreadable invoices — CLOSED

`03739295`, `03739296`, `03739297`, `03741446`, `03741447`, `03741448` (31 Jul +
4 Aug) were the last ILG invoices still carrying a per-BOTTLE price against a
CASE pack, reading ~6x UNDER (Buffalo Trace $12.65/L against a $76.36 median;
Bacardi 12x). Their PDFs were not in `data/invoice_corpus/ilg`.

They were in the **accounts@stowawaybar.com mailbox** the whole time.
`modules/invoices/graph_auth.py` authenticates app-only from
`~/Documents/STOW/.graph_app_secret.json` and reaches any mailbox as
`/users/{address}` — no delegation needed, and nothing has to read the secret
itself. A read-only search of `/messages?$search=<ref>` found all six as
`Fw: Invoice <ref> (member NNNN)` with the PDF attached.

**Every recovered PDF's sha256 matched the `source_pdf` its own invoice JSON had
recorded months earlier** — the same bytes the pipeline first ingested, not a
lookalike. All six re-parsed PASS with 0 findings; 38 rows re-derived, every one
RAISING a cost (Buffalo Trace $12.87 -> $77.24/L, Mr Black $13.47 -> $80.83/L).

Result: `check_pack_agreement.py` reports **ok**, `audit_book` is back to
**SEVERE 3**, WARN 136 -> 118, and `UNREACHABLE` in
`modules/recipes/tests/test_pack_agreement.py` is now empty.

Four `costs.csv` rows fell, all the soft drinks those invoices had been
OVER-costing 8-12x (Coke Zero now $3.69/1.25L, Bundaberg $3.72/750ml, Solo
$3.08/1.25L — all in line with every other delivery). Those two were the extra
SEVEREs.

**If an invoice is ever unreadable again:** it is almost certainly in accounts@.
Search the mailbox by invoice ref, check the attachment's sha256 against the
`source_pdf` in `data/invoices/*.json`, drop it in `data/invoice_corpus/ilg/`
and re-run `run.py`. Do NOT run `pull_mailbox.main()` for this — it moves
messages to Processed and commits.

## Still open besides that

1. **15 products bought since June never reached the cost book** (pack
   unreadable) — `audit_book` WARN. Needs fuzzy matching, so calibrate
   flagged/true/false before shipping it. Note one real mis-bridge found on the
   way: `recipe-bridge-seed` maps Foodlink **101113** (the 12-INCH tortilla) to
   Lightspeed *"Flour Tortillas 6" Plain Mission [24x12]"*. The corpus shows
   101115 is the 6-inch. Worth checking before trusting that bridge.
2. **19 Harry Gatos batch yields.** Still blocked: HG is a separate COMPANY in
   Lightspeed and the Back Office company switcher does not respond to
   automation. Zak switches it, then re-run the harvester — ~90 seconds.
3. **12 batches have no Expected yield set in Produce at all** —
   `no_yield_set_in_produce` in `data/recipe_yields.yaml`. A real ask for whoever
   owns those recipes.
4. **Five kitchen weighings** for cook loss: lamb, pork and beef roast, plus the
   10 kg brisket and 15 kg achiote chicken batches. Chicken Roast is CLOSED.

## The gate (all of it, every time)

    python3 -m pytest -q                      # 798 passed, 3 skipped
    node scripts/test_recipe_book_flags.mjs
    node scripts/test_recipe_flags_families.mjs
    python3 scripts/arch_guard.py
    python3 scripts/schema_guard.py
    python3 scripts/build_site.py
    python3 scripts/check_pack_agreement.py   # new — price/pack disagreement
    cp data/costs.csv /tmp/before && python3 modules/recipes/pipeline/build_costs.py \
      && diff -q /tmp/before data/costs.csv

Rebuild order when anything upstream changes:
`build_costs.py` → `build_ingredients.py` → `modules/invoices/build_price_compare.py`
→ `scripts/convert_lightspeed_recipes.py` → `scripts/build_cost_book_flags.py`

## Three traps this repo has now sprung (do not repeat them)

* **A green CI is not proof when a feed fails to build.** Confirm the feed built
  (`test -f data/cost_book_flags.json`) and that the families test prints
  `(real feed: N flags ...)`.
* **Never pin a COUNT in a test.** Assert the shape/family. When a fix closes a
  flag, rewrite the test that asserted the DEFECT to guard the FIX
  ("re-break it and this reds") rather than deleting it.
* **NEW: reverting code does not revert data.** An experiment that briefly moved
  `basis` into the re-derived set wrote `per_unit` over the hand-set
  `per_bottle` on invoice 03729959's 14 rows. The code was reverted; the CSV was
  not, and two tests went red pointing at ILG codes that had silently dropped out
  of the cost book. `git checkout -- data/` and rebuild after ANY experiment that
  runs a builder. The whole point of a `/tmp` clone is that this is cheap.

## How to work here

* **The mount cannot delete files.** `add`/`commit`/`push`/`fetch` work;
  `checkout`/`reset`/`merge`/`stash` cannot. Use `. ops/git_on_the_mount.sh`.
  Anything that removes files happens off the mount.
* **Work in a `/tmp` clone**, never the mounted tree. Note the mounted tree runs
  ~10 commits behind origin/main; clone from GitHub, not from the mount, or
  rebase before pushing. Push with the PAT at `.secrets/github_pat_v2.txt`.
* `data/invoice_corpus/` is **gitignored** — copy it in from the mount before
  re-parsing anything.
* Money is `Decimal` (`pytest.approx` takes a float `rel` and will `TypeError` on
  a Decimal — quantize and compare exactly). `data/` facts are append-only.
  Schema changes additive-only. No business logic in `dashboard/*/index.html`.
* **Fail toward review.** Errors that flatter — low cost, high GP — are the
  dangerous ones. Where two sources disagree, refuse and say so.
