# `[HG]` pars — a question for Zak, not a change

**Status: NOTHING HAS BEEN CHANGED OR ZEROED. This is a question.**

Generated alongside the `[HG]` suffix rule (par-model-v3). Source of the par
values: `data/_scrape_hg_20260809.json` → `nonzero_pars`.

## The rule we just implemented

Your words, 2026-08-10:

> "same as the wines. pretty much any SKU labelled with [HG] is a harrys SKU
> that draws from stowaway stock"

The par model now applies that automatically. Any Harry Gatos till line carrying
the `[HG]` suffix has the suffix stripped, the remainder matched against
**Stowaway's** par universe, and its consumption routed to that **Stowaway** par
SKU. No file edit is needed when a new `[HG]` product goes on sale. Where the
stripped name matches no Stowaway par SKU the model refuses to guess and lists
the line as `hg_suffix_unresolved` so someone can map it by hand.

## The second-order question

If the stock behind an `[HG]` item is really Stowaway's — held, counted and
ordered on a **Stowaway** par — then a **Harry Gatos** par against the same
physical stock is counting it twice. You would be ordering to a Stowaway par
*and* to an HG par for one pile of bottles.

Four `[HG]`-suffixed SKUs currently hold a non-zero **Harry Gatos** par:

| HG par SKU | HG par | The Stowaway SKU the name resolves to | Stowaway live par |
|---|---:|---|---:|
| `Hyoketsu Lemon [HG]` | **2.4** | `Hyoketsu Lemon Can` | 3.9 |
| `Trutta Streamside Shiraz - Bottle [HG]` | **1.9** | `Trutta Streamside Shiraz [Chilled] - Bottle` | 2.5 |
| `Kaiju Hazy Pale [HG]` | **1.4** | *(no Stowaway Kaiju SKU exists at all)* | — |
| `Two Tonne Riesling - Bottle [HG]` | **0.8** | `Two Tonne Riesling - Bottle` | 2.2 |

**These HG pars may now be redundant. Please confirm before anyone zeroes them.**

Three specific things worth your eye:

1. **Trutta and Two Tonne are already double-plumbed.** `data/par_aliases.json`
   maps HG's by-the-glass till lines (`Trutta Streamside Shiraz`,
   `Two Tonne Riesling`) onto these HG `[HG]` par SKUs, *and* Stowaway holds its
   own par for the same wine. If the bottles come out of Stowaway's rack, those
   two aliases should become `stow:` targets and the HG pars should go to zero.
   If Harry Gatos genuinely holds its own bottles of these wines, everything is
   already correct and nothing should change. **Only you know which.**

2. **Kaiju Hazy Pale has no Stowaway counterpart.** There is no Kaiju SKU
   anywhere in Stowaway's par universe or catalogue. So either the `[HG]` label
   on it is the exception to "pretty much any", or Stowaway needs the stock item
   created. Its HG par of 1.4 is the only thing ordering this beer today —
   **do not zero it** until that is resolved.

3. **Hyoketsu is a naming mess independent of this.** Stowaway carries both
   `Hyoketsu Lemon Can` (live par 3.9) and a catalogue-only `Hyoketsu Lemon
   [Keg]` (no live par). The rule breaks that tie on the live par, which is the
   Can. Worth confirming that is the right physical item.

## What is NOT in this list

The `[HG]`-suffixed **till lines** that actually sell are a different set from
the `[HG]`-suffixed **par SKUs** above — the suffix is used in both catalogues
and they do not overlap. Of the 13 `[HG]` till lines selling at Harry Gatos, the
rule resolved two to Stowaway pars on its own:

| `[HG]` till line | qty/wk | → Stowaway par SKU |
|---|---:|---|
| `Grifter Big Sur IPA Can [HG]` | 0.77 | `Grifter Big Sur IPA Tin` |
| `Monteith's Apple Cider [HG]` | 0.15 | `Monteith's Apple Cider Bottle` |

Both were previously in `_unmapped_investigate` as "HG's Purchase module needs
the stock item created". Under your rule they need no such thing — the stock is
Stowaway's, and both now attribute there.

Five more `[HG]` till lines still reach no par SKU anywhere, because nothing in
Stowaway's par book matches the stripped name. Each needs either an explicit
alias or a new stock item — the model will not guess:

| `[HG]` till line | qty/wk | stripped name it looked for | why it found nothing |
|---|---:|---|---|
| `Two Bays GF Rice Lager Can [HG]` | 0.54 | `Two Bays GF Rice Lager Can` | no Two Bays SKU at Stowaway either |
| `Zagara Orange [HG]` | 0.46 | `Zagara Orange` | Stowaway calls it `Year Wines 'Zagara' Orange - Bottle` — same wine, different name. An explicit alias would fix this today. |
| `Fries [HG]` | 0.38 | `Fries` | a kitchen line, not stock-bearing; the gate ignores it |
| `Philter Old Ale [HG]` | 0.23 | `Philter Old Ale` | no Philter Old Ale at Stowaway |
| `Two Bays GF Pale Can [HG]` | 0.08 | `Two Bays GF Pale Can` | no Two Bays SKU at Stowaway either |

The remaining four (`Vodka Martini [HG]`, `Cosmo [HG]`, `Manhattan - Dry [HG]`,
`Long Island Iced Tea [HG]`) are cocktails and resolve through the recipe book
as they always did, plus `Mulled Wine [HG]` and `Ginger Beer Glass [HG]` which
are already declared intentionally unattributed. None of those needs anything.
