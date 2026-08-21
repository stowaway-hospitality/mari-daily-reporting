# Handoff — HG "Non-Alcoholic" bar builds, 2026-08-21

**Status: done, verified locally, NOT pushed — this session had no GitHub write
credential (see "Blocker" below). A patch is attached; someone with push access
needs to apply it.**

## The job

Zak, in chat, closed the "Non-Alcoholic" reporting group at Harry Gatos (the six
items on the kitchen worklist owned "Bar — write the builds"):

> "yuzu orange soda: Single serve -30ml Adjusted OJ -7.25ml Sugar Syrup -2ml
> Yuzu Juice -120ml Soda / diet coke is now coke zero / vinada replaced with
> the one from stowaway, draws from stow stock / ginger beer = stow's
> bundaberg ginger beer / carafe sparkling water is free / asahi zero is
> bought from CUB"

## What landed

1. **Yuzu Orange Soda** — new recipe, `data/recipes/harry_gatos.yaml`. 30 ml
   East Coast OJ (the "Adjusted OJ" convention already used for Stowaway's
   Virgin Margy — costs the juice, not an invented acid ratio), 7.25 ml Sugar
   Syrup (subrecipe), 2 ml Yuzu Juice (`lightspeed:22874517`). The 120 ml soda
   is free, same reasoning as the water in Virgin Margy. Landed at
   $0.2461/serve, sell $9.00, GP 97.0%.
2. **Diet Coke Can -> Coke Zero Can** — `data/product_recipe_aliases.yaml`.
   The can HG pours is physically Coke Zero now; aliased to Stowaway's
   already-costed, ILG-bridged `Coke Zero Can` (`lightspeed:20459561`).
   $1.8898/can, sell $4.00, GP 48.0%.
3. **Vinada Sparkling Wine -> Vinada Sparkling Rose** — same file. HG's
   separate "Vinada Sparkling Wine" listing (not the already-aliased Rosé
   twin) now draws Stowaway's bottle, `lightspeed:20492687`, invoice-verified
   2026-08-20 at $0.020313/ml. $3.047/150ml pour, sell $11.00, GP 69.5%.
4. **Ginger Beer Glass [HG] -> Bundaberg Ginger Beer** — same file. Pours from
   the same bottle Stowaway sells neat, `lightspeed:20485266`. $1.0384/200ml
   glass, sell $4.00, GP 71.4%.
5. **Carafe Sparkling Water** — exempted in `data/cost_book_flags.yaml`
   (`^carafe sparkling water$`). Free, no ingredient behind it.
6. **Asahi Zero — NOT DONE.** Zak said it's bought from CUB, but CUB has no
   invoice or pricebook feed anywhere in this repo (`build_cogs_list.py`
   states CUB sends us nothing automated — the five existing CUB lines in
   `data/cogs_list.csv`, the postmix BIBs, are all hand-declared from a cart
   total). There is no number to write down without guessing. Left uncosted;
   documented in `data/product_recipe_aliases.yaml`. **Needs a CUB invoice, a
   cart total, or a price from Zak**, declared the same way the five postmix
   lines were (`source=declared:CUB` in `data/cogs_list.csv`).

Net: the "Non-Alcoholic" group flag (`no-recipe-tail-hg-non-alcoholic`,
$2,330/yr) is fully closed — the remaining Asahi Zero revenue ($201/13wk) is
below the $500 single-flag threshold, so it sits silently in the tail rather
than reappearing as a flag. That is correct behaviour, not a gap: it is real
unfinished work, just not big enough to earn its own line.

## Verified locally (this session's sandbox — python3.10, not the pinned
3.11/3.12, but sufficient to catch logic errors)

- `scripts/convert_lightspeed_recipes.py` — all four new/aliased products
  resolved at 100%, no refusals.
- `scripts/materialise_recipes.py --venue {stowaway,harry_gatos,marilynas}` —
  clean, "Yuzu Orange Soda" lands in Harry Gatos' authored count (39, up
  from 36).
- `modules/recipes/pipeline/build_ingredients.py`,
  `modules/recipes/pipeline/build_recipe_feeds.py`,
  `scripts/build_cost_book_flags.py` — clean rebuild chain.
- `python3 -m pytest -q` — full suite green (no failures).
- `scripts/arch_guard.py` — ok after feeds rebuilt.
- `scripts/schema_guard.py` — ok.
- `scripts/check_pack_agreement.py --strict` — ok.
- `scripts/check_pack_as_rate.py --strict` — ok.
- `scripts/check_declarations_bind.py --strict` — 9 unbound, matches pinned
  baseline (no regression from this change).
- `scripts/check_declaration_readers.py --strict --quiet` — 0 unconnected
  pairs.
- `scripts/check_wine_pours.py --strict` — ok.
- `scripts/check_yield_conflicts.py --strict` — pre-existing conflicts only,
  none touched by this change.
- `scripts/check_invoice_coverage.py --strict` — pre-existing gaps only.
- `scripts/build_cogs_variance.py --quiet` — clean.
- `scripts/test_mari_recovery.py` — 14/14 passed.

Flags: production was at 50 (`generated_at: 2026-08-21`, pulled from
`app.stowawaybar.com/data/cost_book_flags.json` at the start of this
session). Local rebuild after this change: 49 — exactly the one group flag
closed, everything else unchanged.

## Blocker — could not push

This session's GitHub access was the `mcp__.../create_or_update_file` /
`push_files` connector, authenticated as `zakstowaway` for read, but every
write attempt returned `403 Resource not accessible by integration` — a
GitHub App permission gap (Contents: Read only), not a branch-protection or
path issue (confirmed against both `ops/session_claims.json` and a throwaway
probe file). No `GH_TOKEN`/`.secrets/github_pat_v2.txt` was available in this
sandbox either, so `scripts/session.py` could not claim `cost-book` on main —
the claim in this handoff is informal, not recorded on `main`.

**A patch bundling every change in this handoff (including this file) is
attached as `hg-bar-builds-2026-08-21.patch`.** To land it:

    cd ~/Documents/STOW/Sales\ Reports/Daily\ Reporting   # or a fresh /tmp clone
    git checkout main && git pull
    git apply /path/to/hg-bar-builds-2026-08-21.patch
    git add -A
    git commit -m "HG Non-Alcoholic: Yuzu Orange Soda, Coke Zero, Vinada, Ginger Beer, Carafe Water"
    git push

Then run `python3 scripts/session.py end cost-book` if a claim was made, and
`python3 scripts/session.py verify data/recipes/harry_gatos.yaml
data/product_recipe_aliases.yaml data/cost_book_flags.yaml` to confirm it
landed on `origin/main`.
