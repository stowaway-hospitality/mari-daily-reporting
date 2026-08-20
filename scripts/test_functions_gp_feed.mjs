/* data/functions_gp.json, rendered by the real /functions/ module.
   Run: node scripts/test_functions_gp_feed.mjs

   WHAT THIS IS FOR
   ----------------
   Two halves of one number live in two languages. `modules/functions` computes
   a function's gross profit in Python, with Decimal, off the comped tab's own
   line items. `dashboard/functions/functions.js` draws it, and it draws it
   under one rule: a GP percentage NEVER appears without the caveats that
   qualify it — gpFigureHTML() puts the number inside the guard that requires a
   non-empty caveat list, so an outcome carrying a figure and no qualifications
   renders a refusal where the figure would have been.

   Nothing else in this repo can see those two halves disagree. A pytest proves
   the arithmetic; a node suite proves the page renders; neither notices when
   the feed publishes `gp_percent` and the page reads `gp_pct`, or when the
   pipeline emits a caveat code the page has never heard of. Both ship green
   and the screen is wrong — silently, and in the flattering direction, because
   a missing caveat does not remove the percentage, it removes the doubt.

   So this drives the REAL module over the REAL feed and reads what it drew.

   Hermetic: no network, no clock. Skips cleanly (exit 0) when the feed has not
   been built, per the arch_guard R0 convention — an absent feed is normal on a
   clean checkout, a WRONG one is not.
*/
import fs from 'fs';
import path from 'path';
import { register } from 'node:module';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHARED = path.join(ROOT, 'dashboard/_shared');
const PAGE = path.join(ROOT, 'dashboard/functions');
const FEED = path.join(ROOT, 'data/functions_gp.json');
const SCHEMA = path.join(ROOT, 'data/schemas/functions_gp.schema.json');

if (!fs.existsSync(FEED)) {
  console.log('data/functions_gp.json not built — skipping (0 assertions)');
  process.exit(0);
}

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

const feed = JSON.parse(fs.readFileSync(FEED, 'utf8'));
const schema = JSON.parse(fs.readFileSync(SCHEMA, 'utf8'));

// ------------------------------------------------ the contract, before the page
{
  ok('the feed declares the schema the file in data/schemas describes',
     feed.schema === schema.title, `${feed.schema} vs ${schema.title}`);
  ok('it carries the venue run rate a function is judged against',
     feed.benchmark_gp_pct === 76.4, String(feed.benchmark_gp_pct));
  ok('there is at least one function on it', (feed.functions || []).length > 0);

  const req = schema.$defs.outcome.required;
  for (const o of feed.functions) {
    const missing = req.filter((k) => !(k in o));
    ok(`${o.id}: every required field is present`, missing.length === 0,
       missing.join(', '));
    ok(`${o.id}: money is whole cents, never a float`,
       Object.entries(o).filter(([k]) => k.endsWith('_cents'))
         .every(([, v]) => v === null || Number.isInteger(v)),
       Object.entries(o).filter(([k, v]) => k.endsWith('_cents')
         && v !== null && !Number.isInteger(v)).map(([k]) => k).join(', '));
    ok(`${o.id}: the id is stable and dated`, /^\d{4}-\d{2}-\d{2}_/.test(o.id), o.id);
    ok(`${o.id}: it says which cost book priced it`,
       typeof o.cost_book_as_of === 'string' && o.cost_book_as_of.length > 0);
  }
}

// ------------------------------ every caveat code the feed emits, the page knows
// Not "handles gracefully" — KNOWS. The fallback that prints an unknown code
// verbatim exists so a caveat is never swallowed, not so the page can ship
// `uncosted_lines` as a heading at a director.
const CODES = new Set(schema.$defs.caveat.properties.code.enum);
{
  for (const o of feed.functions) {
    ok(`${o.id}: has caveats at all — without them the page draws a refusal`,
       Array.isArray(o.caveats) && o.caveats.length > 0);
    for (const c of o.caveats)
      ok(`${o.id}: caveat '${c.code}' is one the schema declares`, CODES.has(c.code));
    ok(`${o.id}: the package SKU caveat is on every function, because it is`,
       o.caveats.some((c) => c.code === 'package_sku_uncosted'));
  }
}

// ---------------------------------------- load the real page module under node
register('data:text/javascript,' + encodeURIComponent(`
  export async function resolve(spec, ctx, next) {
    if (spec.startsWith('/_shared/')) return next('file://${SHARED}' + spec.slice(8), ctx);
    if (spec.startsWith('https://')) return { url: 'data:text/javascript,'
      + 'export const createClient=()=>({auth:{getSession:async()=>({data:{session:null}})},'
      + 'from:()=>({select:()=>({eq:()=>({maybeSingle:async()=>({data:null})})})})});'
      + 'export default {};', shortCircuit: true };
    return next(spec, ctx);
  }`), import.meta.url);

const mk = (id) => ({
  id, style: {}, dataset: {}, value: '', textContent: '', innerHTML: '',
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, setAttribute() {}, getAttribute: () => null,
  appendChild() {}, focus() {}, querySelector: () => mk('stub'),
  querySelectorAll: () => [], closest: () => null,
});
const nodes = new Map();
const byId = (i) => { if (!nodes.has(i)) nodes.set(i, mk(i)); return nodes.get(i); };
globalThis.document = { getElementById: byId, querySelector: () => mk('stub'),
  querySelectorAll: () => [], addEventListener() {}, createElement: () => mk('x'),
  body: mk('body'), activeElement: null };
globalThis.window = globalThis;
globalThis.location = { pathname: '/functions/', hash: '', search: '', origin: 'https://app.stowawaybar.com' };
globalThis.history = { replaceState() {} };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const F = await import('file://' + path.join(PAGE, 'functions.js'));
ok('the page module loads without a browser', typeof F.gpFigureHTML === 'function');

{
  for (const code of CODES)
    ok(`the page has a human heading for '${code}'`,
       Object.prototype.hasOwnProperty.call(F.CAVEAT_TITLES, code),
       `add it to CAVEAT_TITLES in dashboard/functions/functions.js`);
}

// ------------------------------------------ the feed, drawn by the real module
{
  for (const o of feed.functions) {
    const gp = F.gpFigureHTML(o);
    ok(`${o.id}: the percentage is drawn`,
       gp.includes(`${o.gp_pct.toFixed(1)}%`), gp.slice(0, 160));
    ok(`${o.id}: ...and it is NOT the refusal branch`,
       !gp.includes('GP withheld') && !gp.includes('nobody has counted'),
       gp.slice(0, 160));
    ok(`${o.id}: every caveat's own words travel with it`,
       o.caveats.every((c) => gp.includes(F.esc(c.note))));
    ok(`${o.id}: the mixer-free end of the range is drawn as a range`,
       o.gp_pct_ex_mixer == null
         || (gp.includes(`${o.gp_pct_ex_mixer.toFixed(1)}%`)
             && gp.includes('somewhere between')), gp.slice(0, 400));
    ok(`${o.id}: the basis says beverage, so nobody reads it as a blended GP`,
       gp.includes('beverage'), gp.slice(0, 200));

    // The rule, tested against THIS feed's entry rather than a fixture: strip
    // the caveats off a real, published outcome and the number must vanish.
    const bare = F.gpFigureHTML({ ...o, caveats: [] });
    ok(`${o.id}: with its caveats removed the number refuses to appear`,
       bare.includes('GP withheld')
         && !bare.includes(`${o.gp_pct.toFixed(1)}%`), bare.slice(0, 200));

    const earn = F.outEarnSentence(o);
    ok(`${o.id}: the displaced-trade sentence quotes the run rate`,
       earn.includes('76.4%'), earn);
    ok(`${o.id}: ...and does not restate the GP a second time, away from its caveats`,
       !earn.includes(`${o.gp_pct.toFixed(1)}%`), earn);

    const mets = F.outcomeMetricsHTML(o);
    ok(`${o.id}: the drinks count and rate are on the report`,
       mets.includes(String(o.drinks_poured))
         && mets.includes(o.drinks_per_head.toFixed(2)), mets.slice(0, 300));
    ok(`${o.id}: no percentage escapes onto the metrics row`,
       !/\d\.\d%/.test(mets), (mets.match(/\d\.\d%/) || [''])[0]);
  }
}

// -------------------------------------- the two nights, as they were verified
// Named here rather than left implicit: these are the only two functions
// anybody has costed, both hand-verified off the POS before this module
// existed. If the feed ever stops carrying them, the regression fixture is
// gone and every future number is unanchored.
{
  const by = Object.fromEntries(feed.functions.map((o) => [o.id, o]));
  const A = by['2026-08-08_dazzle_drinks'];
  const B = by['2026-08-08_harry'];
  ok('8 Aug 2026, Dazzle drinks: 25 heads, 239 drinks, 59.3% on $739.28 of cost',
     A && A.actual_heads === 25 && A.drinks_poured === 239
       && A.total_cogs_ex_cents === 73928 && A.gp_pct === 59.34
       && A.gp_pct_ex_mixer === 65.6 && A.drinks_per_head === 9.56,
     A && JSON.stringify({ h: A.actual_heads, d: A.drinks_poured,
       c: A.total_cogs_ex_cents, gp: A.gp_pct }));
  ok('...and it names the three drinks it could not cost',
     A && A.uncosted_drinks === 4
       && A.uncosted_lines.map((u) => u.product).sort().join('|')
          === 'Better Beer Tin|Corona|Fresh Lime Soda',
     A && JSON.stringify(A.uncosted_lines));
  ok('8 Aug 2026, Harry: 19 heads, 149 drinks, 60.2% — on the DRINKS half only',
     B && B.actual_heads === 19 && B.drinks_poured === 149
       && B.total_cogs_ex_cents === 41222 && B.gp_pct === 60.22
       && B.gp_pct_ex_mixer === 67.91 && B.drinks_per_head === 7.84,
     B && JSON.stringify({ h: B.actual_heads, d: B.drinks_poured,
       c: B.total_cogs_ex_cents, gp: B.gp_pct }));
  ok('...with the $380 of food taken OFF the top line, not costed at zero',
     B && B.revenue_inc_cents === 152000 && B.food_revenue_inc_cents === 38000
       && B.bev_revenue_inc_cents === 114000 && B.gp_basis === 'beverage'
       && B.caveats.some((c) => c.code === 'food_cogs_unknown'));
  ok('both were priced against the dated book, not the live one',
     A && B && A.cost_book_as_of === '2026-08-08' && B.cost_book_as_of === '2026-08-08',
     A && `${A.cost_book_as_of} / ${B.cost_book_as_of}`);
}

// ----------------------------------------------------- registered to ship
{
  const guard = fs.readFileSync(path.join(ROOT, 'scripts/arch_guard.py'), 'utf8');
  ok('arch_guard runs this suite, so a regression cannot deploy',
     guard.includes('scripts/test_functions_gp_feed.mjs'));
}

console.log(`\n${n} functions-GP-feed assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
