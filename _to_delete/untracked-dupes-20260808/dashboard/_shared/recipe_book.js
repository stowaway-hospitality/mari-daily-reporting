/**
 * The Book tab of /recipes/ — the half that touches the page.
 *
 * Fetch the feeds, put HTML in the container, wire the controls. Every decision
 * about WHAT to draw is in recipe_book_view.js, which is pure and runs under
 * node in scripts/test_recipe_book_view.mjs. Same split as pnl.js/render.js,
 * which scripts/arch_guard.py enforces on the sales dashboard.
 *
 * CLICKING A ROW OPENS THAT RECIPE IN THE BUILDER. That is the whole point of
 * merging the two pages: the book is where you notice a dish is costed wrong,
 * and the builder is where you fix it, and until now getting from one to the
 * other meant a second page and a search box.
 *
 * The rows are <tr>, not <a>. A <tr> gets no keyboard behaviour for free, so:
 *   * recipe_book_view gives every row role="button" and tabindex="0"
 *   * this file handles click, Enter and Space (and preventDefault on Space,
 *     or the page scrolls under the person using it)
 *   * both go through ONE delegated listener on the container, so re-rendering
 *     640 rows on every keystroke in the search box does not leak 640 listeners
 * The column headings are sortable the same way, for the same reason.
 */

import { Feed } from '/_shared/feed.js';
import {
  categoryOptions, esc, filterRows, nextSort, salesIndex, sizeOptions,
  sortRows, statHtml, tableHtml,
} from '/_shared/recipe_book_view.js';

// 13 weeks, the window the book has always shown and the one audit_book.py
// reports coverage over. Anchored on today because the rollups are rebuilt
// daily (CLAUDE.md names them the authority for product questions).
const WINDOW_DAYS = 91;

const state = {
  recipes: {}, sales: {}, sort: { key: 'qty', dir: -1 }, onOpen: null, els: null,
};

const el = (id) => document.getElementById(id);

function render() {
  const rows = sortRows(
    filterRows(Object.entries(state.recipes), {
      q: el('q').value, filter: el('filter').value,
      cat: el('cat').value, size: el('size').value, sales: state.sales,
    }), state.sort, state.sales);
  el('stat').innerHTML = statHtml(rows);
  el('body').innerHTML = tableHtml(rows, state.sort, state.sales);
}

/** One delegated handler for the whole table: rows open, headings sort. */
function wireTable(box) {
  const act = (target) => {
    const th = target.closest('th[data-sort]');
    if (th) { state.sort = nextSort(state.sort, th.dataset.sort); render(); return true; }
    const tr = target.closest('tr[data-recipe]');
    if (tr && state.onOpen) { state.onOpen(tr.dataset.recipe); return true; }
    return false;
  };
  box.addEventListener('click', (e) => act(e.target));
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    // Space scrolls the page by default, which on a 640-row table means the
    // person who just "clicked" a recipe is now somewhere else entirely.
    if (act(e.target)) e.preventDefault();
  });
}

/**
 * Load the book and draw it. `onOpen(productName)` is called when a row is
 * activated; the page decides what that means (it switches to the Build tab and
 * loads the recipe).
 */
export async function mountBook({ onOpen } = {}) {
  state.onOpen = onOpen || null;
  const box = el('body');
  if (!box) return null;
  wireTable(box);

  const data = await Feed.load('/data/lightspeed_recipes_costed.json') || { recipes: {} };
  state.recipes = data.recipes || {};

  // Category and how it actually SELLS, straight from the Sales Product API
  // that already lives in this repo (built by scripts/build_products_api.py).
  const cut = new Date(Date.now() - WINDOW_DAYS * 864e5).toISOString().slice(0, 10);
  const rollups = await Promise.all(['stow', 'hg', 'mari'].map(
    v => Feed.load(`/sales/products/rollup_${v}.json`).catch(() => null)));
  state.sales = salesIndex(rollups.filter(Boolean), cut);

  el('cat').innerHTML = '<option value="all">All categories</option>'
    + categoryOptions(state.sales).map(c => `<option>${esc(c)}</option>`).join('');
  // Filter by size so a GP ranking compares like with like — a Regular and a
  // Large are different dishes, and mixing them makes the order meaningless.
  el('size').innerHTML = '<option value="all">All sizes</option>'
    + sizeOptions(Object.keys(state.recipes)).map(x => `<option>${esc(x)}</option>`).join('');

  for (const id of ['size', 'cat', 'filter']) el(id).addEventListener('change', render);
  el('q').addEventListener('input', render);
  render();
  return state.recipes;
}

/** Jump the book to one recipe — used when a flag names a dish. */
export function focusBook(name) {
  const q = el('q');
  if (!q) return;
  q.value = String(name || '');
  render();
}
