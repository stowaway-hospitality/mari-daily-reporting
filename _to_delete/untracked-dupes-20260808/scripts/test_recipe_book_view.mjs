/* The recipe book's table, under node.  Run: node scripts/test_recipe_book_view.mjs

   WHAT THIS IS FOR
   ----------------
   Three things this suite holds, all of which are invisible to every other
   check in this repo because they are display, not arithmetic:

   1. EVERY ROW IS A CONTROL. Clicking a recipe opens it in the builder. The row
      is a <tr>, which gets no keyboard behaviour for free, so it needs
      role="button", tabindex="0" and a name — and the product name has to be on
      the element in a form the binding can read back, through brackets,
      ampersands and accents.

   2. THE DELETIONS STAY DELETED. Zak asked for the five summary cards off the
      top of the book ("648 recipes costed", "648 fully on our book", "72% avg
      food GP (menu)", "60 GP alerts (<55%)", "3 under-costed (95%+)") and, twice,
      for the "our book" chip off every row. Both are gone. A deletion nobody
      guards is a deletion somebody re-adds in three months because the number
      "would be useful"; so it is asserted, with the exact strings.

   3. A MISSING VALUE IS NOT ZERO. An unpriced recipe has no GP and an unmatched
      one no sales. They sort LAST in both directions, or flipping a column
      fills the top of the screen with blanks.

   Run against the REAL costed book when one has been built. The feed is
   generated at build time and not committed, so a clean checkout skips that
   half rather than failing.
*/
import fs from 'fs'; import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const V = await import('file://' + path.join(ROOT, 'dashboard/_shared/recipe_book_view.js'));

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

const SALES = { 'american standard burger': { qty: 412, rev: 9100, group: 'Big Plates' } };
const BURGER = { sell_incl: 26.9, our_cost: 5.8403, gp_pct: 76.1, is_prep: false };
const PREP = { sell_incl: 2, our_cost: 41.45, gp_pct: null, is_prep: true };

// --- 1. every row is a control ---------------------------------------------
{
  const html = V.rowHtml('American Standard Burger', BURGER, SALES);
  ok('a row is focusable', /tabindex="0"/.test(html), html.slice(0, 120));
  ok('a row announces itself as a button', /role="button"/.test(html));
  ok('a row says what activating it does',
     /aria-label="Open American Standard Burger in the recipe builder"/.test(html));
  ok('a row carries the product name for the binding',
     /data-recipe="American Standard Burger"/.test(html));
  ok('the real numbers are on the row',
     html.includes('$26.90') && html.includes('$5.84') && html.includes('76.1%'),
     html);
  ok('13-week units are on the row', html.includes('412'));
}
{
  // The names that break naive attribute writing. A quote or a bracket in an
  // unescaped data- attribute closes the attribute and the row stops being
  // clickable at all — silently, for that dish only.
  for (const name of ['Regular Margherita [Dine-in]', 'Salt & Pepper Squid',
                      'Flor De Caña 4 Extra Seco', 'Jala Marg Duo (2) - PartyJar [6 serves]']) {
    const html = V.rowHtml(name, BURGER, {});
    const m = /data-recipe="([^"]*)"/.exec(html);
    const back = m && m[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<')
                          .replace(/&gt;/g, '>').replace(/&quot;/g, '"');
    ok(`"${name}" survives onto the row intact`, back === name, String(back));
  }
}
{
  // Column headings are the other control on this table and get the same
  // treatment, or sorting is mouse-only.
  const head = V.headHtml({ key: 'qty', dir: -1 });
  ok('every heading is a control',
     (head.match(/role="button"/g) || []).length === V.COLS.length
     && (head.match(/tabindex="0"/g) || []).length === V.COLS.length, head);
  ok('every heading names the column it sorts',
     V.COLS.every(c => head.includes(`data-sort="${c.k}"`)));
  ok('the sorted column shows its direction', head.includes('▼'));
  ok('...and flips', V.headHtml({ key: 'qty', dir: 1 }).includes('▲'));
}

// --- 2. the deletions stay deleted ------------------------------------------
{
  const rows = [['American Standard Burger', BURGER], ['Peking Sauce [6.75L]', PREP]];
  const table = V.tableHtml(rows, { key: 'qty', dir: -1 }, SALES);
  const all = table + V.statHtml(rows);
  const banned = ['recipes costed', 'fully on our book', 'avg food GP', 'GP alerts (',
                  'under-costed', 'our book', 'part LS', 'class="kpi"', '<div class="kpi'];
  for (const s of banned) {
    ok(`DELETED and stays deleted: "${s}"`, !all.toLowerCase().includes(s.toLowerCase()),
       all.slice(0, 200));
  }
  ok('the only count left is what is on screen', V.statHtml(rows) === '<b>2</b> shown',
     V.statHtml(rows));
  // The one tag that survives says something not true of every row.
  ok('a batch is still tagged prep', V.rowHtml('Peking Sauce [6.75L]', PREP, {}).includes('>prep<'));
  ok('...and a dish is not', !V.rowHtml('American Standard Burger', BURGER, {}).includes('>prep<'));
}

// --- 3. a missing value sorts last, both ways ------------------------------
{
  const rows = [['A', { sell_incl: 10, our_cost: 1, gp_pct: 90, is_prep: false }],
                ['B', { sell_incl: null, our_cost: 1, gp_pct: null, is_prep: false }],
                ['C', { sell_incl: 20, our_cost: 1, gp_pct: 50, is_prep: false }]];
  for (const dir of [-1, 1]) {
    const out = V.sortRows(rows, { key: 'gp', dir }, {}).map(r => r[0]);
    ok(`an unpriced recipe sorts LAST with dir ${dir}`, out[2] === 'B', out.join(','));
  }
  ok('descending GP puts the fat margin first',
     V.sortRows(rows, { key: 'gp', dir: -1 }, {})[0][0] === 'A');
  ok('ascending GP puts the thin one first',
     V.sortRows(rows, { key: 'gp', dir: 1 }, {})[0][0] === 'C');
}
{
  ok('clicking the same column flips it',
     V.nextSort({ key: 'gp', dir: -1 }, 'gp').dir === 1);
  ok('a new numeric column starts biggest-first',
     V.nextSort({ key: 'gp', dir: -1 }, 'qty').dir === -1);
  ok('a new name column starts A-Z',
     V.nextSort({ key: 'qty', dir: -1 }, 'name').dir === 1);
}

// --- the filters ------------------------------------------------------------
{
  const rows = [['Dish', { sell_incl: 20, our_cost: 5, gp_pct: 72, is_prep: false }],
                ['Thin', { sell_incl: 20, our_cost: 12, gp_pct: 34, is_prep: false }],
                ['Free', { sell_incl: 20, our_cost: 0.2, gp_pct: 98, is_prep: false }],
                ['Batch [1L]', { sell_incl: 2, our_cost: 40, gp_pct: null, is_prep: true }]];
  const only = (f) => V.filterRows(rows, { filter: f }).map(r => r[0]);
  ok('"menu" excludes batches', !only('menu').includes('Batch [1L]'), only('menu').join(','));
  ok('"alert" is GP under 55 only', only('alert').join() === 'Thin', only('alert').join());
  ok('"under" is GP at or over 95 — under-costing, not a win',
     only('under').join() === 'Free', only('under').join());
  ok('"batch" is the preps', only('batch').join() === 'Batch [1L]');
  ok('search is a substring, case-insensitively',
     V.filterRows(rows, { q: 'BAT' }).map(r => r[0]).join() === 'Batch [1L]');
}

// --- size and category ------------------------------------------------------
{
  ok('size is read from the name', V.sizeOf('Large Meatlovers').lab === 'Large');
  ok('...and ranked physically, not alphabetically',
     V.sizeOf('Kids Margherita Pizza').rank < V.sizeOf('Family Margherita').rank);
  ok('size options come out in physical order',
     V.sizeOptions(['Family P', 'Kids P', 'Large P', 'Regular P']).join()
       === 'Kids,Regular,Large,Family',
     V.sizeOptions(['Family P', 'Kids P', 'Large P', 'Regular P']).join());
  // The bracket is not decoration: three distinct POS products were merged into
  // one and reported the same 209 units against each.
  ok('[Dine-in] is a DIFFERENT product from the takeaway one',
     V.nrm('Regular Margherita') !== V.nrm('Regular Margherita [Dine-in]'));
}

// --- the 13-week sales fold -------------------------------------------------
{
  const roll = [{ products: [
    { name: 'Beef Cheek', reporting_group: 'Kitchen Specials',
      weekly: [{ we: '2026-08-02', qty: 10, sales_ex: 300 },
               { we: '2026-01-04', qty: 99, sales_ex: 9999 }] }] }];
  const idx = V.salesIndex(roll, '2026-05-01');
  ok('only weeks inside the window count', idx['beef cheek'].qty === 10, JSON.stringify(idx));
  ok('...and the reporting group comes with them',
     idx['beef cheek'].group === 'Kitchen Specials');
  ok('a product with no sales row reads as no sales, not as zero-priced',
     V.saleOf({}, 'Nothing').qty === 0 && V.saleOf({}, 'Nothing').group === '');
}

// --- against the REAL book ---------------------------------------------------
{
  const p = path.join(ROOT, 'data/lightspeed_recipes_costed.json');
  if (!fs.existsSync(p)) {
    console.log('  (skipped: data/lightspeed_recipes_costed.json not built)');
  } else {
    const recipes = JSON.parse(fs.readFileSync(p, 'utf8')).recipes;
    const entries = Object.entries(recipes);
    const rows = V.sortRows(V.filterRows(entries, {}), { key: 'qty', dir: -1 }, {});
    const html = V.tableHtml(rows, { key: 'qty', dir: -1 }, {});
    ok(`the real book draws (${entries.length} recipes)`, rows.length === entries.length);
    ok('every real row is focusable',
       (html.match(/tabindex="0"/g) || []).length >= entries.length, );
    ok('every real row carries its product name',
       (html.match(/data-recipe="/g) || []).length === entries.length,
       String((html.match(/data-recipe="/g) || []).length));
    ok('no "our book" chip survives on the real book',
       !/our book/i.test(html));
    ok('no summary card survives on the real book',
       !/recipes costed|avg food GP|under-costed/i.test(html));
    // The lettuce burger is the recipe this whole exercise keeps coming back to;
    // it must be openable from the book like anything else.
    ok('American Standard Burger is a clickable row',
       html.includes('data-recipe="American Standard Burger"'));
  }
}

// --- MUTATION CHECK ----------------------------------------------------------
// Prove the row assertions can fail: a row built the old way (no role, no
// tabindex, with the chip) must fail every one of them.
{
  const oldRow = '<tr class="main"><td>American Standard Burger'
    + '<span class="tag">our book</span></td></tr>';
  ok('MUTATION: the pre-merge row is not focusable', !/tabindex="0"/.test(oldRow));
  ok('MUTATION: the pre-merge row is not a button', !/role="button"/.test(oldRow));
  ok('MUTATION: the pre-merge row still carries the chip these tests ban',
     /our book/i.test(oldRow));
}

console.log(`\n${n} book-view assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
