/**
 * The recipe BOOK as a PURE function: feeds in, HTML out.
 *
 * WHY IT IS PURE
 * --------------
 * The same split the sales dashboard makes between pnl.js (the model) and
 * render.js (the page), which scripts/arch_guard.py enforces there — R5, "the
 * model never touches the page". The book used to be 130 lines of filtering,
 * sorting and row-drawing inside dashboard/recipes-book/index.html, where the
 * only way to check that a row was clickable, or that a deleted summary card
 * had actually gone, was to open a browser and look.
 *
 * Everything that DECIDES anything is here. It touches no document, imports
 * nothing, returns strings — scripts/test_recipe_book_view.mjs runs it under
 * node against the real costed book.
 *
 * WHAT WAS DELETED, AND WHY IT MUST NOT COME BACK
 * ----------------------------------------------
 * 1. The five summary cards ("648 recipes costed", "648 fully on our book",
 *    "72% avg food GP (menu)", "60 GP alerts", "3 under-costed"). Four of the
 *    five were the same number said differently, and the two coverage cards
 *    read 648/648 — a statistic that has told nobody anything since the day it
 *    reached 100%. audit_book.py fails CI if coverage regresses; that is the
 *    thing that says so, not a card.
 * 2. The "our book" chip on every row. Coverage is 648/648, so it labelled
 *    every single row identically.
 * Both are asserted GONE by the test suite, because a deletion nobody guards is
 * a deletion somebody re-adds.
 *
 * EVERY ROW IS A BUTTON. Clicking a recipe opens it in the builder tab. It is
 * a <tr> and not an <a>, so it needs role, tabindex and a key handler or it is
 * mouse-only — the page is used on an iPad in a kitchen and by whoever is at a
 * desk, and half of them are on a keyboard.
 */

export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const money = (n) => '$' + Number(n || 0).toFixed(2);

/**
 * Match the POS product EXACTLY (bar case/punctuation). The bracket is not
 * decoration: 'Regular Margherita' is the takeaway/delivery product and
 * 'Regular Margherita [Dine-in]' is a different one that sells separately.
 * Stripping it merged three real products into one and reported the same 209
 * units against each.
 */
export const nrm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

/**
 * Size lives in the product NAME, so read it there and rank it physically
 * (Kids < Regular < Large < Family) rather than alphabetically, which would put
 * Family before Kids and make the column useless for comparing like with like.
 */
export const SIZES = [[/\bkids?\b/i, 'Kids', 1], [/\bgluten[- ]free\b/i, 'Gluten-free', 2],
                      [/\bregular\b/i, 'Regular', 3], [/\blarge\b/i, 'Large', 4],
                      [/\bfamily\b/i, 'Family', 5]];

export function sizeOf(name) {
  for (const [rx, lab, rank] of SIZES) if (rx.test(name)) return { lab, rank };
  return { lab: '', rank: 0 };
}

export const NO_SALE = { qty: 0, rev: 0, group: '' };
export const saleOf = (sales, name) => (sales && sales[nrm(name)]) || NO_SALE;

export const gpClass = (g) => g == null ? '' : (g < 55 ? 'gp bad' : g < 65 ? 'gp warn' : 'gp good');

export const COLS = [
  { k: 'name', label: 'Recipe',    cls: '' },
  { k: 'sell', label: 'Sell',      cls: 'r' },
  { k: 'cost', label: 'Our cost',  cls: 'r' },
  { k: 'gp',   label: 'Food GP',   cls: 'r' },
  { k: 'qty',  label: 'Sold 13wk', cls: 'r' },
  { k: 'size', label: 'Size',      cls: '' },
  { k: 'cat',  label: 'Category',  cls: '' },
];

/** The value a column sorts on. null means "no value", which sorts LAST. */
export function colValue(key, name, r, sales) {
  switch (key) {
    case 'name': return name.toLowerCase();
    case 'sell': return r.sell_incl;
    case 'cost': return r.our_cost;
    case 'gp':   return r.gp_pct;
    case 'qty':  return saleOf(sales, name).qty || null;
    case 'size': return sizeOf(name).rank || null;
    case 'cat':  return (saleOf(sales, name).group || '').toLowerCase();
    default:     return null;
  }
}

const isBatch = (r) => r.is_prep === true;

/**
 * The rows a search + the four selects leave standing.
 *
 * `filter` is the same five it always was. 'under' is not a win: a menu GP at
 * or above 95% is a missing ingredient or a bad quantity — Garlic Bread [Deal]
 * charges 0.025 of a garlic bread where the normal one charges 1. Under-costing
 * flatters GP, the direction that hurts, so it gets its own view.
 */
export function filterRows(entries, opts) {
  const o = opts || {};
  const q = String(o.q || '').trim().toLowerCase();
  const sales = o.sales || {};
  const menu = (e) => e[1].sell_incl != null && !e[1].is_prep;
  let rows = entries;
  if (o.filter === 'menu') rows = entries.filter(menu);
  else if (o.filter === 'alert') rows = entries.filter(e => menu(e) && e[1].gp_pct != null && e[1].gp_pct < 55);
  else if (o.filter === 'under') rows = entries.filter(e => menu(e) && e[1].gp_pct != null && e[1].gp_pct >= 95);
  else if (o.filter === 'batch') rows = entries.filter(e => isBatch(e[1]));
  if (q) rows = rows.filter(([n]) => n.toLowerCase().includes(q));
  if (o.cat && o.cat !== 'all') rows = rows.filter(([n]) => saleOf(sales, n).group === o.cat);
  if (o.size && o.size !== 'all') rows = rows.filter(([n]) => sizeOf(n).lab === o.size);
  return rows;
}

/**
 * Sort in place and return. A missing value is not "zero" — an unpriced recipe
 * has no GP and an unmatched one no sales. Park them LAST whichever way the
 * column is sorted, so flipping direction never fills the top with blanks.
 */
export function sortRows(rows, sort, sales) {
  const key = (sort && sort.key) || 'qty';
  const dir = (sort && sort.dir) || -1;
  return rows.slice().sort((a, b) => {
    const x = colValue(key, a[0], a[1], sales), y = colValue(key, b[0], b[1], sales);
    const bx = (x === null || x === undefined || x === ''), by = (y === null || y === undefined || y === '');
    if (bx || by) return bx && by ? a[0].localeCompare(b[0]) : (bx ? 1 : -1);
    if (typeof x === 'string') return dir * x.localeCompare(y) || a[0].localeCompare(b[0]);
    return dir * (x - y) || a[0].localeCompare(b[0]);
  });
}

/** The next sort state when a heading is clicked: same column flips, new column
 *  starts descending unless it reads as a name. */
export function nextSort(sort, key) {
  return (sort && sort.key === key)
    ? { key, dir: -sort.dir }
    : { key, dir: (key === 'name' || key === 'cat') ? 1 : -1 };
}

/**
 * One row. It is the click target for "open this recipe in the builder", so it
 * carries everything that makes a non-<a> element behave like a control:
 * role="button", tabindex="0", an aria-label that says what happens, and the
 * product name in data-recipe for the binding to read. The key handler lives in
 * recipe_book.js — this decides WHAT is clickable, that decides how.
 *
 * NO "our book" CHIP. See the header. The only tag left is `prep`, which
 * distinguishes a batch from a dish and is true of 265 of 913 rows rather than
 * all of them.
 */
export function rowHtml(name, r, sales) {
  const gp = r.gp_pct;
  const s = saleOf(sales, name);
  const tag = r.is_prep
    ? '<span class="tag prep">prep</span>' : '';
  return `<tr class="main" tabindex="0" role="button" data-recipe="${esc(name)}"`
    + ` aria-label="Open ${esc(name)} in the recipe builder">`
    + `<td>${esc(name)}${tag}</td>`
    + `<td class="r">${r.sell_incl ? money(r.sell_incl) : '—'}</td>`
    + `<td class="r">${money(r.our_cost)}</td>`
    + `<td class="r"><span class="${gpClass(gp)}">${gp == null ? '—' : gp + '%'}</span></td>`
    + `<td class="r soft">${s.qty ? Math.round(s.qty).toLocaleString() : '—'}</td>`
    + `<td class="soft">${esc(sizeOf(name).lab || '—')}</td>`
    + `<td class="soft">${esc(s.group || '—')}</td>`
    + `</tr>`;
}

export function headHtml(sort) {
  const key = (sort && sort.key) || 'qty', dir = (sort && sort.dir) || -1;
  return '<thead><tr>' + COLS.map(c =>
    `<th class="${c.cls} sortable${key === c.k ? ' on' : ''}" data-sort="${c.k}"`
    + ` role="button" tabindex="0">${c.label}`
    + (key === c.k ? (dir < 0 ? ' ▼' : ' ▲') : '') + '</th>').join('') + '</tr></thead>';
}

/** The whole table. Empty is a sentence, not an empty table. */
export function tableHtml(rows, sort, sales) {
  if (!rows.length) return '<div class="empty">No recipes match.</div>';
  return `<div class="pl"><table>${headHtml(sort)}<tbody>`
    + rows.map(([n, r]) => rowHtml(n, r, sales)).join('')
    + '</tbody></table></div>';
}

/** "N shown" — the ONLY count on this page, and it counts what is on screen. */
export const statHtml = (rows) => `<b>${rows.length}</b> shown`;

/** The distinct sizes present, ranked physically. */
export function sizeOptions(names) {
  const labs = [...new Set(names.map(n => sizeOf(n).lab).filter(Boolean))];
  return labs.sort((a, b) => (SIZES.find(x => x[1] === a)?.[2] || 0)
                           - (SIZES.find(x => x[1] === b)?.[2] || 0));
}

/** The distinct reporting groups the sales feed knows about. */
export function categoryOptions(sales) {
  return [...new Set(Object.values(sales || {}).map(x => x.group).filter(Boolean))].sort();
}

/**
 * Fold the Sales Product API rollups into { normalised name -> {qty, rev, group} }
 * over the last `days` days. Pure so the 13-week window is testable; the fetch
 * is recipe_book.js's problem.
 */
export function salesIndex(rollups, cutoff) {
  const out = {};
  for (const d of rollups || []) {
    for (const p of ((d && d.products) || [])) {
      let q = 0, rev = 0;
      for (const w of (p.weekly || [])) {
        if (w.we >= cutoff) { q += (+w.qty || 0); rev += (+w.sales_ex || 0); }
      }
      const k = nrm(p.name), cur = out[k] || { qty: 0, rev: 0, group: p.reporting_group || '' };
      out[k] = { qty: cur.qty + q, rev: cur.rev + rev, group: cur.group || p.reporting_group || '' };
    }
  }
  return out;
}
