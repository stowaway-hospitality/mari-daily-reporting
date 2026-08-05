# Handoff — recipe/COGS deep audit, 2026-08-05 (afternoon)

Continues `HANDOFF_20260805.md`. **Run `python3 scripts/audit_book.py` first** — it
now reproduces every finding below with current numbers, including five NEW rules
that did not exist this morning.

Branch `recipes/liquor-pack-discriminator` @ `c9402d7` is **pushed**. Everything
after that is committed locally / staged and needs one push (see END).

---

## The one idea, this round

**Our cost book quietly deferred to Lightspeed whenever the two disagreed.**

`_trust_direct()` in `convert_lightspeed_recipes.py` uses "does our price agree
with Lightspeed's line?" as evidence that a recipe's *quantity* is believable.
That is sound — it is what stops Truffle Oil's "4 ml" (really 4 bottles) from
under-costing 250x.

But it has a blind spot: when **our** rate is right and **Lightspeed's** is wrong,
the disagreement condemns our price and we fall back to theirs. So the one moment
the book is adding value — being right where Lightspeed is wrong — is the moment
it surrendered. Hugo Spritz reported **92.9% GP** because of it.

The fix is narrow and evidence-based: only for ProductIDs where Lightspeed
*contradicts itself* (its BO export and its own recipe seed disagree >3x) may our
rate override its line. Truffle Oil and Rosemary Salted Fries — the two documented
regression guards — are provably untouched.

---

## What changed

| | |
|---|---|
| Liquor invoices reaching the book | +29 seed-validated bridges |
| Recipes now costed off real invoices | **76** (49 up, 27 down) |
| Hugo Spritz | $1.35 (92.9% GP) -> **$4.10 (78.5%)** |
| Patron Silver | $2.94 -> **$3.28** (76.0% GP) |
| New audit rules | **5** |
| Tests | 366 -> **371 pass** |

Both derived files still reproduce byte-identically (CI's hard gate).

### Two live cost errors fixed at the root
- **Massenez Elderflower**: BO export says $253/5L ($0.0506/ml, matching every
  Massenez sibling); a `ls-recipe-seed` dated ONE DAY LATER said $24.17/5L and won
  the as-of lookup forever. 10.5x undercost across 3 cocktails.
- **Bittermen's Tiki Bitters**: same class, 6.45x the other way.

Now guarded generally: `ls_seed_is_misread()` drops a recipe-seed that contradicts
the product's own BO export by >3x. The BO export is a *stated* cost; the LS
recipe cost is the computed number this project exists to escape.

---

## NEW audit rules (this is the durable part)

Zak's complaint was "I constantly find issues". These five turn each class of
issue found by hand into something the audit finds automatically, forever.

1. **combo costs the same as its base** — caught **20 Wings Deals** that are
   byte-identical to the plain pizza. The wings were never added, so each $30 item
   reports ~88% GP on a ~$5 cost.
2. **real recipe priced below cost (POS price looks wrong)** — caught
   **Pepperoni [Dine-in]: sells $2.00, costs $2.11**, with a full 5-line pizza
   recipe behind it while every dine-in sibling sells $15. Loses money on every
   order. It hid because the old rule ignored anything under $3.
3. **LARGE carries LESS than the REGULAR** — 19 lines (ham 55g vs 85g, spanish
   onion 20g vs 33g across 8 pizzas, chicken, mozzarella, pesto).
4. **ingredient in the REGULAR but missing from the LARGE** — 28 (+30 the other
   way, INFO). This is the "Hawaiian had no ham" class, generalised.
5. **batch uses far more input than the yield in its own name** — Jalapeño Tequila
   [1L] draws **7000 ml** of tequila; Coconut-washed Rooster Blanco [1L] draws
   4200 ml. You cannot get 1 L out of 7 L. (Cooked Beef Brisket [1Kg] at 10.9x
   also trips it — confirm whether "[1Kg]" is a yield or a pricing label.)

---

## What needs Zak — cannot be coded, must not be guessed

1. **The 20 Wings Deals** — which wings, and how many? The wings recipes exist
   (BBQ $3.14 / Buffalo $3.91). One number each and the audit goes quiet.
2. **Pepperoni [Dine-in] $2.00** — a POS price error, not a recipe error. Fix in
   Lightspeed.
3. **Jalapeño Tequila / Coconut-washed Rooster** — almost certainly 700 ml
   (1 bottle) written as 7000/4200. Confirm before anyone edits; changing it
   moves cost DOWN, the flattering and therefore dangerous direction.
4. **Large-pizza weights** — the 19 large<regular lines need the "large mains"
   weighed sheet, the same way `pizza_regular_grams.yaml` settled the regulars.
5. **Cocktails carrying only their spirit** — Mojito has rum and nothing else;
   Whisky Sour has no sour; 7 premium Old Fashioneds dropped the bitters line;
   7 branded Margaritas have no lime/salt/sugar though Classic Margarita does.
   These are Lightspeed Produce recipe gaps. A bar spec fixes them; inventing
   quantities would be exactly the wrong move.
6. **Kids Spag Bol / Sea Foam Pet Nat D** — unchanged from this morning
   (sauce choice / delist). Zak said ignore for now.

## Verified NOT errors (do not re-chase these)
- Carpano vermouth: $23.17/750 ml = $0.0309/ml is **correct** (an earlier pass
  called it a ÷1000 bug; it is not).
- Beaujolais + Terra Cotta: correctly costed from Zak's seeds ($34.58 / $21.89).
  The "no ingredient lines" warning is cosmetic — the cost arrives via the seed.
- The 680 vs 648 recipe gap: by design. The 32 extra are builder-authored
  recipes in `data/recipes/*.yaml`, a separate feed from the Lightspeed book.
  They cost fine (Southern Squid $2.28).
- Batch-yield backlog from the morning handoff: already cleared. Coverage is
  648/648 fully on our book.
- ~20 alarming-looking cost-book rates (10x onion, 24x Sprite, box prices never
  divided) are real defects but referenced by **zero** recipes — no live impact.

## END — the one thing left
Push the local commits on `recipes/liquor-pack-discriminator`, then open the PR.
