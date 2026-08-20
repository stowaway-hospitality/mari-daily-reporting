# Sales dashboard — module map & invariants

`app.stowawaybar.com/sales/` used to be one 194KB `index.html` with all the
business logic inline. That is why it kept regressing: a UI tweak sat inches from
the P&L maths, nothing was tested, and every change was eyeballed. It is now a
thin shell that loads four layered modules, and an **enforced** guard keeps it
that way.

## Layers (all classic `<script src>`, loaded before the inline bootstrap)

| File | Responsibility | Touches DOM? | Tested by |
|------|----------------|--------------|-----------|
| `pnl.js`    | Pure P&L maths — `pnlWindow`, overheads, delivery, wage/leave, rollups | No | `scripts/test_pnl_model.mjs` (conservation, group = Σvenues) |
| `util.js`   | Helpers, formatting, data transforms (`fmtDollars`, `synthesizeGroupHistory`, …) | No | `scripts/test_dashboard_units.mjs` |
| `data.js`   | Async loaders that fetch feeds into `STATE` | fetch only | via render test |
| `render.js` | All DOM rendering + UI event handlers | Yes | `scripts/test_dashboard_render.mjs` (drives `render()` over venue × timeframe) |

### The recipe module (`/recipes/`) also lives here

`/recipes-book/` (the costed book) and `/recipes/` (the builder) used to be two
pages. They are **one page with four tabs** — Book, Build, Prep timer, Flags —
and `dashboard/recipes-book/index.html` is now a redirect stub, because a URL is
a contract. Same layering, same reason:

| File | Responsibility | Touches DOM? | Tested by |
|------|----------------|--------------|-----------|
| `recipe_tabs.js` | Pure routing: which tab a URL opens, and where `/recipes-book/` redirects to. Every historical URL form is a case. | No | `scripts/test_recipe_tabs.mjs` |
| `recipe_book_view.js` | Pure: the book's filtering, sorting and row HTML. Every row is a control (`role="button"`, `tabindex="0"`) because clicking one opens it in Build. | No | `scripts/test_recipe_book_view.mjs` |
| `recipe_book.js` | The Book tab's DOM: fetch the feeds, draw, one delegated click/keydown listener for 913 rows. | Yes | via the view test + the shell test |
| `recipe_builder.js` | Build + Prep timer, moved verbatim out of the old `index.html`. Owns the save path. | Yes | `scripts/test_recipe_builder_load.mjs` (drives it over the real feeds) |
| `recipe_line_guard.js` | Pure plausibility rules for one line of the **builder** — a whole twin-pack of cos on a burger, a bun priced per litre. Returns warnings; the page draws them. Never blocks a save. | No | `scripts/test_recipe_line_guard.mjs` (named cases + re-calibration against the real book on every run) |
| `flags_view.js` | Pure: the cost-book work queue as HTML. Computes no money — every figure is read verbatim from `data/cost_book_flags.json`. | No | `scripts/test_recipe_book_flags.mjs`, `scripts/test_recipe_flags_families.mjs` |
| `flags.js` | The Flags tab's DOM: fetch the feed, draw it, wire the one filter. | Yes | via the two flag suites |
| `recipes_page.js` | The page: one auth gate, four tabs, the deep links. `modules/recipes/app/index.html` calls `start()` and nothing else. | Yes | `scripts/test_recipes_page_shell.mjs` |

The shell test is the one that makes the merge safe: it reads every
`getElementById` out of all four DOM modules and proves the merged
`index.html` still has each id, and proves both old pages' controls are still
there by id. A merge breaks by returning `null` from `getElementById`, which
throws nothing and 404s nothing.

`index.html` holds only: the markup shell, the config objects (`VENUE_CONFIG`,
`ROLE_CONFIG`, `CARD_DEFS`, targets), the shared `STATE`, and the single
`bootstrap()` call. **No business logic lives in `index.html`.**

Every top-level binding is a global (functions are auto-global; config is `var`),
so cross-module references resolve exactly as when everything was inline. `STATE`
is the one shared object the model reads; the model never writes the page.

## The rule, and why it can't be broken

`scripts/arch_guard.py` runs in **`tests.yml` (every push/PR)** and gates
**`deploy_dashboard.yml` (every deploy)**. It fails the build on:

1. any function declaration inside `index.html` (logic creeping back onto the HTML)
2. `index.html` past the shell size cap
3. a missing module, or any module failing `node --check`
4. a DOM token in `pnl.js` (the model touching the page)
5. a function defined in two modules
6. a missing behaviour marker (day scrubber, leave toggle, delivery KPI, …)
7. any of the **twelve** JS test suites failing — P&L conservation, render
   layer, pure helpers, the recipe builder's plausibility guard (re-calibrated
   against the real cost book on every run), the six that hold the merged
   recipe module (tab routing, book view, page shell, builder load, the flags
   panel's wording, and whether every family of open question is actually on
   the panel), the /functions/ enquiry-to-deposit screen, and
   `data/functions_gp.json` drawn by that screen's own module — the one that
   catches a gross profit computed in Python and rendered in JavaScript
   drifting apart in the middle

Drift doesn't get a warning — it goes red and never ships. To add a feature: maths
in `pnl.js`/`util.js`, fetch in `data.js`, DOM in `render.js`, and add/extend a
test. If the guard is unhappy, the code is in the wrong layer.

## Data-side guard

`scripts/schema_guard.py` (daily pull + `tests.yml`) blocks the *data* regressions
that also bit us: a dropped history column, lost dates (truncation), or a column
going dark (e.g. the `leave_dollars` wipe). Fix data at source — never by
hand-editing a generated `*_daily_history.csv`, because the next rebuild
overwrites it.
