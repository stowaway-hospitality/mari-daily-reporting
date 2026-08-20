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

// -------------------------------------- the join key, without which it is mute
// The feed identifies a night by tab name; the diary identifies it by booking.
// "Dazzle drinks" is not a customer, and 8 August 2026 carries TWO functions,
// so neither the name nor the date can pair them. `booking_id` is the whole
// join, and an entry without one is a report the screen can never show.
//
// This is asserted rather than left to the schema, which allows null on
// purpose: a function with no booking is a legitimate thing to cost one day.
// What is not legitimate is PUBLISHING one and nobody noticing it went
// nowhere, so a new tab that omits the id goes red here and gets a decision
// made about it.
{
  const ids = feed.functions.map((o) => o.booking_id);
  for (const o of feed.functions) {
    ok(`${o.id}: carries the booking it belongs to`,
       typeof o.booking_id === 'string' && o.booking_id.length > 0,
       String(o.booking_id));
    ok(`${o.id}: ...and says on what evidence, because a pairing matched on the `
       + `money and one matched on a hunch look identical in a JSON file`,
       typeof o.booking_evidence === 'string' && o.booking_evidence.length > 40,
       String(o.booking_evidence).slice(0, 60));
  }
  ok('no two functions claim the same booking — that would file one night’s '
     + 'gross profit under another night’s name',
     new Set(ids).size === ids.length, ids.join(', '));
  ok('the schema declares the key, so the contract is written down and not '
     + 'just observed',
     'booking_id' in schema.$defs.outcome.properties
     && 'booking_evidence' in schema.$defs.outcome.properties);
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

  // The two bookings, off GET /api/admin/functions/diary. Pinned because
  // getting this pair the wrong way round is the one failure that cannot be
  // seen on a screen: both figures are real, both carry their caveats, and
  // Roman's night is filed under Harry Baker.
  ok('Dazzle drinks is Roman Bunting’s booking — 40 covers, Old Stow, 15:30, '
     + 'the only note of the four that names the Razzle Dazzle package',
     A && A.booking_id === 'e93280ad65d1', A && A.booking_id);
  ok('Harry is Harry Baker’s — $60 Soiree plus the $20pp food his note names, '
     + 'which is exactly the $80 ticket and the $380 food line on this tab',
     B && B.booking_id === '1878ce4a6350', B && B.booking_id);
  ok('...and the food line is what proves it: $380 over 19 heads is $20 a head',
     B && B.food_revenue_inc_cents === 38000 && B.actual_heads === 19
       && B.food_revenue_inc_cents / B.actual_heads === 2000);
  ok('...and each night carries the covers off ITS OWN booking, which is what '
     + 'lets the report say out loud that it divides by who came',
     A && B && A.booked_guests === 40 && B.booked_guests === 25,
     A && B && `${A.booked_guests} / ${B.booked_guests}`);
  ok('...while Roman’s ticket is all beverage, because his food was paid for '
     + 'at the till on arrival',
     A && A.food_revenue_inc_cents === null
       && A.bev_revenue_inc_cents === A.revenue_inc_cents);
}

// ------------------- the real feed, joined to the real diary, drawn by the page
// The last gap. Everything above proves the feed is well formed and that
// gpFigureHTML draws an entry. This proves the whole path: a diary row with
// this booking id stops saying "no report yet" and starts drawing the report,
// through the same renderer a hand-recorded one goes through — and that the
// caveats rule holds on THIS door as well as the other one.
{
  const past = '2027-01-01';      // everything on the feed is history by then
  for (const o of feed.functions) {
    const row = { id: o.booking_id, date: o.date, time: '18:00',
                  name: 'a booking', covers: 0, status: 'confirmed',
                  brief_id: null, notes: '' };
    const [joined] = F.joinComputedReports([row], feed);
    ok(`${o.id}: joins to its booking and is no longer "no report yet"`,
       !!joined.computed_outcome
       && !/no report yet/.test(F.diaryRowHTML(joined, null, past)));
    const card = F.functionCardHTML(joined, past);
    ok(`${o.id}: the card draws the percentage, with its caveats`,
       card.includes(`${o.gp_pct.toFixed(1)}%`) && card.includes('class="caveats"')
       && o.caveats.every((c) => card.includes(F.esc(c.note))),
       card.slice(card.indexOf('How it went'), card.indexOf('How it went') + 300));
    ok(`${o.id}: ...exactly one gross profit figure on the night, never two`,
       (card.match(/class="gp"/g) || []).length === 1);
    ok(`${o.id}: ...and it says the figure was computed, off which cost book`,
       /Computed, not typed/.test(F.flat(card))
       && F.flat(card).includes(F.esc(o.booking_evidence)));

    // THE RULE, on the feed path.
    const [bare] = F.joinComputedReports([row],
      { functions: [{ ...o, caveats: [] }] });
    const bareCard = F.functionCardHTML(bare, past);
    ok(`${o.id}: strip the caveats off the FEED entry and the number is refused`,
       bareCard.includes('GP withheld')
       && !bareCard.includes(`${o.gp_pct.toFixed(1)}%`),
       bareCard.slice(bareCard.indexOf('How it went'),
                      bareCard.indexOf('How it went') + 240));
  }

  // And the other half of the rule: a booking the feed says nothing about is
  // untouched. Most functions will never have a tab file.
  const [orphan] = F.joinComputedReports(
    [{ id: 'nosuchbooking', date: '2026-08-08', time: '18:00', name: 'x',
       covers: 0, status: 'confirmed', brief_id: null, notes: '' }], feed);
  ok('a past function absent from the feed still says "no report yet" and '
     + 'still offers the form',
     !orphan.computed_outcome
     && /no report yet/.test(F.diaryRowHTML(orphan, null, past))
     && /data-act="recordoutcome"/.test(F.functionCardHTML(orphan, past)));
}

// ----------------------------------------------------- registered to ship
{
  const guard = fs.readFileSync(path.join(ROOT, 'scripts/arch_guard.py'), 'utf8');
  ok('arch_guard runs this suite, so a regression cannot deploy',
     guard.includes('scripts/test_functions_gp_feed.mjs'));
}

console.log(`\n${n} functions-GP-feed assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
