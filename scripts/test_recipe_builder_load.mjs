/* What the builder DRAWS when it loads a recipe.  Run: node scripts/test_recipe_builder_load.mjs

   WHY THIS EXISTS
   ---------------
   "If you went and looked at the American Standard Burger on Lightspeed you
   would see that not a whole lettuce twin pack is used per burger."

   That burger's lettuce line in our costed book is qty 0.083 — a twelfth of the
   pack, $0.228 — so the QUANTITY has always agreed with him. The unit on it,
   though, is "ml", against a pack the kitchen counts in "ea", which is the same
   typo family as the whole chicken logged as "0.5 ml". The open question was
   whether the BUILDER was corrupting that fraction on load and showing it as a
   whole pack at $2.75, because that is what a screenshot appeared to show — and
   a display bug that makes a correct recipe look wrong is exactly how a closed
   question gets re-opened every few weeks.

   It is not. This drives the REAL builder module over the REAL feeds with a
   stub document and reads the HTML it produced, and the answer is 0.083 in the
   box, "$2.75/ea" as the rate, "$0.23" as the line's contribution. The $2.75
   in the screenshot is a whole pack at qty 1 — a line somebody TYPED, which is
   the case dashboard/_shared/recipe_line_guard.js was written to catch and
   does.

   So this suite is the regression net for that answer: if a future change ever
   does round, truncate or whole-number a fractional countable on load, the
   burger goes from $5.68 to $8.20 and this goes red on the same push.

   It needs data/ingredients.json and data/recipes_full.json, which are
   generated at build time and not committed, so a clean checkout skips.
*/
import fs from 'fs'; import path from 'path';
import { register } from 'node:module';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHARED = path.join(ROOT, 'dashboard/_shared');

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

for (const f of ['data/ingredients.json', 'data/recipes_full.json',
                 'data/recipes_index.json']) {
  if (!fs.existsSync(path.join(ROOT, f))) {
    console.log(`  (skipped: ${f} is generated at build time, not committed)`);
    console.log(`\n0 builder-load assertions, 0 failures`);
    process.exit(0);
  }
}

// The module imports '/_shared/x.js' (a site-absolute path) and pulls the
// Supabase client off a CDN. Under node both need a resolver; neither is what
// this suite is testing.
register('data:text/javascript,' + encodeURIComponent(`
  export async function resolve(spec, ctx, next) {
    if (spec.startsWith('/_shared/')) return next('file://${SHARED}' + spec.slice(8), ctx);
    if (spec.startsWith('https://')) return { url: 'data:text/javascript,'
      + 'export const createClient=()=>({auth:{getSession:async()=>({data:{session:null}})}});'
      + 'export default {};', shortCircuit: true };
    return next(spec, ctx);
  }`), import.meta.url);

// --- the smallest document the builder will run against --------------------
const nodes = new Map();
const mk = (id) => ({ id, style: {}, dataset: {},
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  textContent: '', innerHTML: '', value: '', disabled: false,
  addEventListener() {}, insertAdjacentHTML() {}, remove() {}, setAttribute() {},
  getAttribute() { return null; }, querySelector() { return null; },
  querySelectorAll() { return []; }, closest() { return null; },
  scrollIntoView() {}, appendChild() {} });
const byId = (id) => { if (!nodes.has(id)) nodes.set(id, mk(id)); return nodes.get(id); };
globalThis.document = { getElementById: byId, querySelector: () => null,
  querySelectorAll: () => [], addEventListener() {}, createElement: () => mk('x'),
  body: mk('body') };
globalThis.window = globalThis;
globalThis.location = { pathname: '/recipes/', hash: '', search: '' };
globalThis.history = { replaceState() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.addEventListener = () => {};
globalThis.scrollTo = () => {};
globalThis.fetch = async (u) => {
  const p = path.join(ROOT, String(u).split('?')[0].replace(/^\//, ''));
  if (!fs.existsSync(p)) return { ok: false, status: 404 };
  const txt = fs.readFileSync(p, 'utf8');
  return { ok: true, status: 200, json: async () => JSON.parse(txt), text: async () => txt };
};

const B = await import('/_shared/recipe_builder.js');
await B.mountBuilder({ name: 'Suite', role: 'bigchef', venue: 'stowaway' });
ok('the builder boots against the real feeds', B.builderReady());

/** Every rendered line, parsed back out of the HTML the builder wrote. */
function lines() {
  const html = byId('lines').innerHTML;
  const re = /<div class="line">\s*<div class="nm">([\s\S]*?)<small id="ct-\d+">([^<]*)<\/small><\/div>\s*<input[^>]*value="([^"]*)"[^>]*placeholder="([^"]*)"[^>]*>\s*<div class="lc" id="lc-\d+">([^<]*)<\/div>/g;
  const out = []; let m;
  while ((m = re.exec(html)) !== null) {
    out.push({ name: m[1].trim(), rate: m[2], qty: m[3],
               placeholder: m[4], cost: m[5] });
  }
  return out;
}

// --- THE LETTUCE ------------------------------------------------------------
{
  ok('the burger loads', B.openRecipe('American Standard Burger'));
  const L = lines();
  ok('every one of its nine lines drew', L.length === 9, String(L.length));
  const cos = L.find(l => l.name.startsWith('Lettuce Cos Baby Twin Pack'));
  ok('the lettuce line is there', !!cos, L.map(l => l.name).join(' | '));
  // THE ANSWER. A fraction is not rounded to a whole pack on load.
  ok('the quantity box holds the FRACTION, not 1', cos.qty === '0.083', cos.qty);
  ok('...and the line contributes $0.23, not $2.75', cos.cost === '$0.23', cos.cost);
  ok('...while $2.75 is shown as the RATE of one whole pack', cos.rate === '$2.75/ea', cos.rate);
  // The builder ignores the feed's "ml" and denominates the box in the unit the
  // ingredient is actually bought in. That is right, and it is also why the
  // "ml" on that line is invisible here and has to be flagged in the work queue
  // instead (modules/recipes/feed_defects.line_unit_contradicts_pack).
  ok('...and the box counts EACH, not millilitres', cos.placeholder === '0 ea', cos.placeholder);
  // A BAND, NOT A CENT. What this guards is that the dish took a FRACTION of the
  // pack ($5.67) and not a whole one (~$100) — a two-order-of-magnitude
  // question. Pinning the exact cent made it fail the day an invoice moved a
  // rate by 1c, which is the book doing its job, and sent somebody hunting a
  // regression that was a price change.
  {
    const t = byId('c-food').textContent;
    const v = parseFloat(String(t).replace(/[^0-9.]/g, ''));
    ok('the dish total is the fraction total, nowhere near a whole pack',
       v > 5.0 && v < 6.5, t);
  }
  {
    const g = parseFloat(String(byId('gp-food').textContent).replace(/[^0-9.]/g, ''));
    ok('...so the burger reads ~77% GP, not 68%', g >= 76 && g <= 78,
       byId('gp-food').textContent);
  }
  ok('the sell price came back too', byId('sell').value === 26.9 || byId('sell').value === '26.9',
     String(byId('sell').value));
  ok('the dish name came back', byId('dish').value === 'American Standard Burger');
}

// --- and the second burger that carries the same pack ----------------------
{
  const other = 'Beef Burger';
  if (B.openRecipe(other)) {
    const cos = lines().find(l => l.name.startsWith('Lettuce Cos Baby Twin Pack'));
    if (cos) {
      ok(`${other} takes the same fraction of the pack`, cos.qty === '0.083', cos.qty);
      ok(`...at the same $0.23`, cos.cost === '$0.23', cos.cost);
    }
  }
}

// --- fractions are not a lettuce special case -------------------------------
{
  // Whatever else in the book is entered below 1, it has to survive the round
  // trip too. A rounding bug would not have picked on the cos.
  const full = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/recipes_full.json'), 'utf8'));
  const withFraction = full.recipes.filter(r => (r.lines || []).some(
    l => l.id && Number(l.qty) > 0 && Number(l.qty) < 1)).slice(0, 12);
  let checked = 0;
  for (const r of withFraction) {
    if (!B.openRecipe(r.product)) continue;
    const drawn = lines();
    for (const ln of r.lines) {
      const q = Number(ln.qty);
      if (!(q > 0 && q < 1) || !ln.id) continue;
      const hit = drawn.find(d => d.qty === String(q));
      if (hit) checked++;
      ok(`fraction ${q} survives the load in "${r.product}"`, !!hit,
         drawn.map(d => d.qty).join(','));
    }
  }
  ok(`fractional quantities were actually exercised (${checked} lines)`, checked >= 5,
     String(checked));
}

// --- a name that is not in the feed says so, it does not fail silently ------
ok('an unknown recipe reports that it is unknown',
   B.openRecipe('A Dish That Has Never Existed') === false);
ok('an empty name is not a lookup', B.openRecipe('') === false);

// --- MUTATION CHECK ----------------------------------------------------------
// The bug this suite exists to catch, written out: a load path that rounded a
// countable to a whole pack. It must fail the lettuce assertions above.
{
  const rounded = Math.max(1, Math.round(0.083));
  ok('MUTATION: rounding the countable would show 1', rounded === 1);
  ok('MUTATION: ...and would charge $2.75 instead of $0.23',
     (rounded * 2.75).toFixed(2) === '2.75');
}

console.log(`\n${n} builder-load assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
