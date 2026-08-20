/* The /functions/ screen.  Run: node scripts/test_functions_page.mjs

   WHAT THIS IS FOR
   ----------------
   This page decides what a client is charged and which of forty enquiries gets
   chased today. Every one of its failure modes is silent: a quote that is $20 a
   head light, a package total that never appears beside the minimum spend, a
   paid-but-unheld deposit filed under "Confirmed", a row that says "needs a
   start time" without saying whether anyone has asked. None of them 404s, none
   of them throws, and none of them fails anything else in this repo. They just
   ship, and the first person to notice is a client.

   So the deciding half of dashboard/functions/functions.js is pure and takes
   its config as an argument, and this drives it directly — the real module,
   under node, with a stub document, over briefs shaped exactly as
   functions.summary() returns them.

   It also holds the two rules the page was MOVED to satisfy:
     * the HTML is a shell — no logic on it, ids only;
     * signed-in staff never paste a token. Auth.bookingToken() is the path and
       #tokenbox is the fallback. The Render original asked for the token in a
       box on every load; that is the thing this port exists to stop, and a
       future edit that quietly reinstates it goes red here.

   Hermetic: no network, no feeds, no clock. Every date is passed in.
*/
import fs from 'fs';
import path from 'path';
import { register } from 'node:module';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHARED = path.join(ROOT, 'dashboard/_shared');
const PAGE = path.join(ROOT, 'dashboard/functions');

const html = fs.readFileSync(path.join(PAGE, 'index.html'), 'utf8');
const src = fs.readFileSync(path.join(PAGE, 'functions.js'), 'utf8');
const bookings = fs.readFileSync(path.join(ROOT, 'dashboard/bookings/bookings.js'), 'utf8');

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

// ---------------------------------------------------------------- the shell
{
  const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
    .map((m) => m[1]).join('\n');
  const bare = inline.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const fns = [...bare.matchAll(/^\s*function\s+([A-Za-z0-9_]+)\s*\(/gm)].map((m) => m[1]);
  ok('index.html declares no functions — logic lives in functions.js',
     fns.length === 0, fns.join(', '));
  ok('...and carries no inline script at all', bare.trim() === '', bare.trim().slice(0, 120));
  ok('the shell is under the 40KB cap a shell has no business exceeding',
     Buffer.byteLength(html) < 40 * 1024, `${Math.round(Buffer.byteLength(html) / 1024)}KB`);
  ok('it wears the platform design system, not its own palette',
     html.includes('/_shared/tokens.css'));
  ok('...and uses the tokens rather than hardcoded hex',
     ['--ink', '--paper', '--line', '--shadow'].every((v) => html.includes(v)));
  ok('the module is loaded as a versioned ES module',
     /<script type="module" src="\.\/functions\.js\?v=[0-9a-z]+"><\/script>/.test(html),
     (html.match(/<script type="module"[^>]*>/) || [''])[0]);
}

// ------------------------------------------------- no token box as the path
{
  ok('#tokenbox is hidden in the markup — it is a fallback, not the front door',
     /id="tokenbox"[^>]*style="display:none"/.test(html));
  ok('...and the shell says so out loud, so the next edit knows',
     /never paste a token/i.test(html) && /fallback/i.test(html));
  ok('the module reads the service token from Supabase',
     src.includes('Auth.bookingToken()'));
  ok('...and only falls back to a locally-saved one',
     /localStorage\.getItem\(TOKEN_KEY\)/.test(src));
  ok('the page is gated by the platform auth, not by the token box',
     /Auth\.gate\(/.test(src));
  ok('a rejected token clears the fallback rather than looping on it',
     src.includes('localStorage.removeItem(TOKEN_KEY)'));
}

// ------------------------------------------- one engine, one base, one token
{
  const grab = (text, name) => (text.match(new RegExp(`const ${name} = '([^']+)'`)) || [])[1];
  ok('the API base is the same constant /bookings/ resolves',
     grab(src, 'API') === grab(bookings, 'API'),
     `${grab(src, 'API')} vs ${grab(bookings, 'API')}`);
  ok('...and so is the fallback token key — one engine, one token',
     grab(src, 'TOKEN_KEY') === grab(bookings, 'TOKEN_KEY'));
  ok('no server logic came with it: every call is to the engine',
     !/\/api\/(?!admin\/(functions|areas))/.test(src));
}

// -------------------------------------- every id it reads is somewhere real
{
  const ids = new Set([
    ...[...src.matchAll(/getElementById\(\s*'([A-Za-z0-9_-]+)'\s*\)/g)].map((m) => m[1]),
    ...[...src.matchAll(/\$\('([A-Za-z0-9_-]+)'\)/g)].map((m) => m[1]),
    // ...and the boxes a Save reads back off the panel. A field renamed in the
    // markup and not in patchBody() silently stops being saved.
    ...[...src.matchAll(/\bv\('(f_[A-Za-z0-9_]+)'\)/g)].map((m) => m[1]),
  ]);
  ok('the module reads a sensible number of ids', ids.size > 10, String(ids.size));
  // An id is legitimate if the SHELL provides it or the module itself draws it.
  // Anything else is a getElementById that returns null, throws nothing, 404s
  // nothing, and silently kills one feature.
  // ...where "draws it" means a literal id= in its markup, or a <select> built
  // by selHTML(), which writes the id through a template and so cannot be found
  // by searching for the literal.
  const drawn = (id) => src.includes(`id="${id}"`) || src.includes(`selHTML('${id}'`);
  const orphan = [...ids].filter((id) => !html.includes(`id="${id}"`) && !drawn(id));
  ok(`every id the module reads exists in the shell or in its own markup (${ids.size} checked)`,
     orphan.length === 0, 'orphaned: ' + orphan.join(', '));
  for (const id of ['gate', 'app', 'main', 'tokenbox', 'rail', 'panel', 'rates', 'status'])
    ok(`the shell provides #${id}`, html.includes(`id="${id}"`));
}

// ------------------------------------------------------- registered to ship
{
  const layout = fs.readFileSync(path.join(ROOT, 'scripts/build_site.py'), 'utf8');
  ok('build_site.py ships dashboard/functions at /functions/',
     /\("dashboard\/functions",\s*"functions"\)/.test(layout));
  const home = fs.readFileSync(path.join(ROOT, 'dashboard/home/index.html'), 'utf8');
  ok('the hub home page has a Functions card',
     /href: '\/functions\/'/.test(home));
  const guard = fs.readFileSync(path.join(ROOT, 'scripts/arch_guard.py'), 'utf8');
  ok('arch_guard runs this suite, so a regression cannot deploy',
     guard.includes('scripts/test_functions_page.mjs'));
}

// ------------------------------------ load the real module under a stub DOM
// It imports '/_shared/auth.js' (site-absolute) which pulls Supabase off a CDN.
// Neither is what this suite tests, so both get resolved to something inert.
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
  disabled: false, selectionStart: 0,
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, setAttribute() {}, getAttribute: () => null,
  appendChild() {}, focus() {}, setSelectionRange() {},
  querySelector: () => mk('stub'), querySelectorAll: () => [], closest: () => null,
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
ok('the module loads without a browser', typeof F.priceOf === 'function');

// The config the engine actually serves (GET /api/admin/functions/config).
const CFG = {
  base_rate_cents: 5500, peak_rate_cents: 7500, peak_window: '18:00-21:00',
  peak_days: 'Fri, Sat, public holidays', default_duration_hours: 3,
  deposit_cents: 10000, chase_final_numbers_days: 6,
  public_holidays: ['2026-10-05'], deposits_live: true,
  drink_packages: { 'SHIN-DIGG': 4900, 'SOIRÈE': 6000, 'RAZZLE DAZZLE': 8000 },
  arrival_addons: { 'classic cocktail': 1200, 'signature cocktail': 1500, veuve: 2000 },
  cakeage_cents_pp: 150, accepted_areas: ['Harry Gatos', 'Main Hall', 'Old Stow'],
};
const TODAY = '2026-08-20';        // a Thursday. Every date below is relative to it.

// --------------------------------------------------------------- the quote
{
  const at = (over) => ({ name: 'X', guests: 30, ...over });

  const tue = F.priceOf(at({ date: '2026-08-25', start_time: '19:00' }), CFG);
  ok('a Tuesday is the standard rate', tue.cents === 5500 * 30 && tue.rate === 5500,
     JSON.stringify(tue));
  ok('...and says which day it is', /Tuesday/.test(tue.why), tue.why);

  const fri = F.priceOf(at({ date: '2026-08-21', start_time: '19:00' }), CFG);
  ok('a Friday inside the window is the peak rate', fri.cents === 7500 * 30, JSON.stringify(fri));
  ok('...and names the day, the span and the window',
     /Friday/.test(fri.why) && /7pm–10pm/.test(fri.why) && /6–9pm/.test(fri.why), fri.why);

  // 3pm + 3h ends at 6pm exactly, which does not OVERLAP a window that opens
  // at 6pm. This is the boundary functions.is_peak() draws and the one worth
  // $20 a head to get right.
  const early = F.priceOf(at({ date: '2026-08-21', start_time: '15:00' }), CFG);
  ok('a Friday that FINISHES as the window opens is standard',
     early.cents === 5500 * 30, JSON.stringify(early));
  ok('...and explains that, rather than just quoting a number',
     /finishes before/.test(early.why), early.why);

  const noTime = F.priceOf(at({ date: '2026-08-21' }), CFG);
  ok('a peak DAY with no start time refuses to guess', noTime.unknown === true);
  ok('...and asks the question instead', /what time/i.test(noTime.why), noTime.why);

  const ph = F.priceOf(at({ date: '2026-10-05', start_time: '19:00' }), CFG);
  ok('a public-holiday MONDAY is peak', ph.cents === 7500 * 30, JSON.stringify(ph));
  ok('...and says so in those words', /public holiday/.test(ph.why), ph.why);

  ok('no date and no headcount is unknown, not zero',
     F.priceOf(at({ guests: null }), CFG).unknown === true);

  const agreed = { name: 'X', guests: 30, date: '2026-08-21', start_time: '19:00',
                   min_spend_cents: 180000, min_spend_is_agreed: true };
  const panel = F.panelHTML({ ...agreed, missing: [], problems: [] }, CFG, [], TODAY);
  ok('an agreed spend is shown as agreed', /minimum spend — agreed/.test(panel));
  ok('...with the standard calculation still stated beside it',
     /standard calculation says \$2,250/.test(panel), panel.slice(panel.indexOf('quote'), 900));
}

// ------------------------------------------------------------ package maths
{
  const base = { name: 'X', guests: 30, date: '2026-08-25', start_time: '19:00' };

  const razzle = F.packageMaths({ ...base, drink: 'RAZZLE DAZZLE' }, CFG);
  ok('the package total is heads x package price', razzle.total === 8000 * 30);
  ok('...against the minimum spend as the floor', razzle.floor === 5500 * 30);
  const rh = F.sumsHTML(razzle);
  ok('the package binds when it clears the floor', /package is what they'll actually pay/.test(rh), rh);
  ok('...and says by how much',
     /clears\s+the \$1,650 minimum spend by \$750/.test(rh), rh);

  const shin = F.packageMaths({ ...base, drink: 'SHIN-DIGG' }, CFG);
  const sh = F.sumsHTML(shin);
  ok('the minimum spend binds when the package falls short',
     /minimum spend is what binds/.test(sh), sh);
  ok('...and says how far short', /\$180 short of it/.test(sh), sh);

  const addon = F.packageMaths({ ...base, drink: 'SOIRÈE', arrival_addon: 'veuve' }, CFG);
  ok('an arrival drink is per head, on top', addon.total === (6000 + 2000) * 30);
  ok('...and is named in the sum', /veuve on arrival/.test(F.sumsHTML(addon)));

  const tab = F.packageMaths({ ...base, drink: 'bar tab' }, CFG);
  ok('a bar tab has no total to compare', tab.tab === true && tab.total === undefined);
  ok('...so it says the floor is a floor and the tab has no ceiling',
     /no\s+ceiling/.test(F.sumsHTML(tab)), F.sumsHTML(tab));

  // Cakeage is $1.50. Rounded to whole dollars it prints as "$2", which is a
  // wrong price rather than a tidy one.
  ok('cakeage is stated to the cent', /\$1\.50 a head/.test(F.sumsHTML(razzle)),
     F.sumsHTML(razzle));
  ok('...and is NOT folded into either total',
     razzle.total === 240000 && !/2,445/.test(F.sumsHTML(razzle)));
  ok('...and says it only applies if they bring a cake',
     /only if they bring\s+a cake/.test(F.sumsHTML(razzle)));

  ok('a drink the server does not price yields no sum, rather than a wrong one',
     F.packageMaths({ ...base, drink: 'MYSTERY' }, CFG) === null);
  ok('no drink chosen yet yields no sum', F.packageMaths(base, CFG) === null);
}

// ----------------------------------------------------------------- the rail
{
  const b = (over) => ({ stage: 'enquiry', missing: [], problems: [],
                         deposit_status: 'none', ...over });
  const briefs = [
    b({ id: 'far', name: 'Far', date: '2026-11-01', missing: ['room'] }),
    b({ id: 'soon', name: 'Soon', date: '2026-08-24', missing: ['start time'] }),
    b({ id: 'paid', name: 'Paid', date: '2026-09-01', deposit_status: 'paid', booking_id: 'abc' }),
    b({ id: 'stranded', name: 'Stranded', date: '2026-09-02', deposit_status: 'paid' }),
    b({ id: 'quoted', name: 'Quoted', date: '2026-09-03' }),
    b({ id: 'nodate', name: 'Nodate' }),
    b({ id: 'gone', name: 'Gone', stage: 'lost', date: '2026-09-04' }),
  ];
  const chase = [{ id: 'soon' }, { id: 'far' }];
  const g = F.groupRail(briefs, chase);

  ok('needs-something is ordered by the chase list, soonest event first',
     g.needs.map((x) => x.id).join(',') === 'soon,far', g.needs.map((x) => x.id).join(','));
  ok('a paid deposit with a held room is Confirmed', g.held.map((x) => x.id).join(',') === 'paid');
  ok('a paid deposit with NO room is stranded, never Confirmed',
     g.stranded.map((x) => x.id).join(',') === 'stranded');
  ok('quoted-no-deposit is its own group', g.waiting.map((x) => x.id).join(',') === 'quoted');
  ok('no date yet is its own group', g.nodate.map((x) => x.id).join(',') === 'nodate');
  ok('lost and done drop out of the live list', g.past.map((x) => x.id).join(',') === 'gone');

  // The bug an if/else chain exists to make unreachable.
  const seen = [...g.stranded, ...g.needs, ...g.waiting, ...g.held, ...g.nodate]
    .map((x) => x.id);
  ok('every live brief appears in exactly one group',
     new Set(seen).size === seen.length && seen.length === 6, seen.join(','));

  const rail = F.railHTML(briefs, chase, '', 'soon', TODAY);
  ok('stranded is drawn FIRST — the money is taken and nothing holds the floor',
     rail.indexOf('Paid — room NOT held') < rail.indexOf('Needs something'));
  ok('the selected row is marked', /data-open="soon"[^>]*|class="row [^"]*on"/.test(rail));
  ok('every row is reachable from a keyboard', /role="button" tabindex="0"/.test(rail));
  ok('the search box keeps what was typed',
     F.railHTML(briefs, chase, 'Soo', null, TODAY).includes('value="Soo"'));
  ok('...and filters on it',
     !F.railHTML(briefs, chase, 'Soo', null, TODAY).includes('data-open="far"'));
}

// ------------------------------------------------- whose move is it, and when
{
  const outstanding = { missing: ['start time'], problems: [] };
  ok('never asked, still outstanding — that one is on us',
     F.whoseMove({ ...outstanding }, TODAY) === 'onme');
  ok('asked today — we are waiting on them',
     F.whoseMove({ ...outstanding, last_asked: TODAY }, TODAY) === 'onthem');
  ok('asked four days ago is still their move',
     F.whoseMove({ ...outstanding, last_asked: '2026-08-16' }, TODAY) === 'onthem');
  ok('asked five days ago is ours again',
     F.whoseMove({ ...outstanding, last_asked: '2026-08-15' }, TODAY) === 'onme');
  ok('nothing outstanding is nobody’s move — not a task',
     F.whoseMove({ missing: ['$100 deposit'], problems: [] }, TODAY) === '');
  ok('a hard problem is outstanding even with nothing missing',
     F.whoseMove({ missing: [], problems: ['unknown area'] }, TODAY) === 'onme');

  ok('never asked reads as never asked', F.askedLabel(null, TODAY) === 'never asked');
  ok('asked today reads as today', F.askedLabel(TODAY, TODAY) === 'asked today');
  ok('asked yesterday reads as yesterday',
     F.askedLabel('2026-08-19', TODAY) === 'asked yesterday');
  ok('older reads in days', F.askedLabel('2026-08-13', TODAY) === 'asked 7 days ago');

  // The off-by-one that made today read as "1d" and hid the today chip entirely.
  ok('today is 0 days away', F.daysAway(TODAY, TODAY) === 0);
  ok('tomorrow is 1 day away', F.daysAway('2026-08-21', TODAY) === 1);
  ok('yesterday is -1', F.daysAway('2026-08-19', TODAY) === -1);
  const chips = F.chipsFor({ date: TODAY, missing: [], problems: [],
                             deposit_status: 'none' }, TODAY);
  ok('a function happening TODAY says today', chips.includes('>today<'), chips);
  ok('a chase chip is only drawn on a row that wants something',
     !chips.includes('never asked'), chips);
}

// ----------------------------------------------- the two things she pastes
{
  const b = { name: 'X', guests: 30, date: '2026-08-21', start_time: '19:00',
              missing: ['room', 'food choice', '$100 deposit'] };
  const q = F.quoteText(b, CFG);
  ok('the quote is a sentence, with the money in it', /minimum spend of \$2,250\./.test(q), q);
  ok('...and the date and time as a human writes them',
     /Fri, 21 Aug/.test(q) && /from 7pm/.test(q), q);
  ok('an unpriceable quote asks for the start time instead of inventing one',
     /need a start time/.test(F.quoteText({ ...b, start_time: null }, CFG)));

  ok('what to ask reads as one sentence, in the order you would ask it',
     /^Just need to lock in which space suits you and which food option you'd like and we're set\.$/
       .test(F.askText(b)), F.askText(b));
  ok('the $100 deposit is never something to ask for here',
     !/deposit/.test(F.askText(b)), F.askText(b));
  ok('the minimum spend is OUR sum, not a question for them',
     !/minimum spend/.test(F.askText({ ...b, missing: ['minimum spend (needs date…)'] })),
     F.askText({ ...b, missing: ['minimum spend (needs date…)'] }));
  ok('nothing outstanding gives nothing to paste',
     F.askText({ missing: ['$100 deposit'] }) === '');
  ok('a bar tab with no terms is asked for in words',
     /open or restricted/.test(F.askText({ missing: ['bar tab terms (open, or restricted to what?)'] })));
}

// -------------------------------------------------------------- the deposit
{
  const b = (over) => ({ name: 'X', guests: 30, date: '2026-08-25', start_time: '19:00',
                         missing: [], problems: [], deposit_status: 'none', ...over });

  const blocked = F.panelHTML(b({ can_request_deposit: false,
    deposit_blockers: ['room', 'food choice'], missing: ['room', 'food choice'] }),
    CFG, [], TODAY);
  ok('a deposit that cannot be taken offers no button', !/data-act="mint"/.test(blocked));
  ok('...and lists exactly why', /<li>room<\/li>/.test(blocked) && /<li>food choice<\/li>/.test(blocked));

  const ready = F.panelHTML(b({ can_request_deposit: true }), CFG, [], TODAY);
  ok('a complete brief can mint a link', /data-act="mint"/.test(ready));
  ok('...and can record one taken another way', /data-act="took"/.test(ready));
  ok('...by a named method', F.DEPOSIT_METHODS.join(',') === 'eftpos,phone,transfer,cash');

  const minted = F.panelHTML(b({ can_request_deposit: true,
    deposit_status: 'sent', deposit_url: 'https://pay.example/abc' }), CFG, [], TODAY);
  ok('a minted link is shown again on reopening — the engine persists it',
     minted.includes('https://pay.example/abc'));
  ok('...and says it is the same one, so nobody mints a second checkout',
     /Same link as before/.test(minted));

  const held = F.panelHTML(b({ deposit_status: 'paid', booking_id: 'ab12',
    deposit_paid_at: '2026-08-19T14:30' }), CFG, [], TODAY);
  ok('a paid deposit with a booking says the room is held', /The room is held/.test(held));
  const strand = F.panelHTML(b({ deposit_status: 'paid' }), CFG, [], TODAY);
  ok('a paid deposit with no booking says so loudly',
     /Paid but the room is NOT held/.test(strand));
}

// ------------------------------------------------------------- the brief form
{
  const b = { name: 'Smith', guests: 30, date: '2026-08-25', start_time: '19:00',
              area: 'Old Stow', food: 'pizza', drink: 'bar tab',
              missing: [], problems: [], deposit_status: 'none',
              final_numbers_due: '2026-08-20', refundable_until: '2026-08-18' };
  const p = F.panelHTML(b, CFG, [{ id: 'Old Stow' }, { id: 'Main Hall' }], TODAY);
  for (const id of ['f_date', 'f_start', 'f_area', 'f_guests', 'f_guests_raw', 'f_final',
                    'f_food', 'f_drink', 'f_tab', 'f_addon', 'f_spend', 'f_occasion',
                    'f_diet', 'f_extras'])
    ok(`the brief field #${id} is editable on the panel`, p.includes(`id="${id}"`));
  ok('the notes log takes a date-stamped append', p.includes('id="f_note"'));
  ok('stage changes are on the panel',
     /data-stage="done"/.test(p) && /data-stage="lost"/.test(p));
  ok('the refund deadline and the final-numbers date are stated',
     /Refund deadline/.test(p) && /final numbers due/.test(p));

  // Overdue final numbers: due today, none recorded, inside the chase window.
  ok('an overdue final-numbers box is marked, not merely mentioned',
     /id="f_final"[^>]*class="late"/.test(p), p.slice(p.indexOf('f_final'), p.indexOf('f_final') + 200));

  // The picker must not offer a room the save refuses.
  const areas = [{ id: 'Old Stow' }, { id: 'Main Hall' }, { id: 'Whole venue' }];
  ok('the room picker offers only rooms a brief may name',
     F.areaOptions(areas, CFG).join(',') === 'Old Stow,Main Hall');
  ok('...but never hides a room that is already on file',
     F.areaOptions(areas, CFG, 'Whole venue')[0] === 'Whole venue');
  ok('...and falls back to the floor plan if the engine sends no list',
     F.areaOptions(areas, {}, null).length === 3);
}

// ------------------------------------------------------- what a Save sends
{
  const boxes = { f_date: '2026-08-25', f_start: '7pm', f_area: 'Old Stow',
                  f_guests: '30', f_guests_raw: '', f_final: '', f_food: 'pizza',
                  f_drink: 'bar tab', f_tab: 'open', f_addon: '', f_spend: '2000',
                  f_occasion: ' 40th ', f_diet: '', f_extras: '' };
  const body = F.patchBody({ id: 'x', name: 'Smith' }, (id) => boxes[id]);
  ok('the name always rides along — upsert keys on it', body.name === 'Smith');
  ok('a blank box is OMITTED, not sent as empty',
     !('f_guests_raw' in body) && !('guests_raw' in body) && !('final_numbers' in body),
     JSON.stringify(body));
  ok('...because a blank would otherwise read as an answer', body.date === '2026-08-25');
  ok('the agreed spend is sent in CENTS', body.min_spend_cents === 200000);
  ok('text is trimmed', body.occasion === '40th');
  ok('a number is a number, not a string', body.guests === 30);

  ok('a note is appended to the log with a date stamp',
     F.appendNote('old line', 'rang them', '20 Aug') === 'old line\n[20 Aug] rang them');
  ok('...and the first note does not start with a blank line',
     F.appendNote('', 'first', '20 Aug') === '[20 Aug] first');
  ok('...and nothing already in the log is lost',
     F.appendNote('[19 Aug] deposit taken (eftpos)', 'x', '20 Aug')
       .startsWith('[19 Aug] deposit taken (eftpos)'));
}

// ------------------------------------------------------------------ escaping
{
  const nasty = '<img src=x onerror=alert(1)>';
  const p = F.panelHTML({ name: nasty, occasion: nasty, notes: nasty,
                          missing: [], problems: [], deposit_status: 'none' }, CFG, [], TODAY);
  ok('a client-typed name cannot inject markup', !p.includes('<img src=x'), p.slice(0, 300));
  const r = F.railHTML([{ id: 'a', name: nasty, stage: 'enquiry', missing: [],
                          problems: [], deposit_status: 'none' }], [], nasty, null, TODAY);
  ok('...nor in the rail', !r.includes('<img src=x'));
  ok('...nor through the search box', !r.includes('value="<img'));
}

// -------------------------------------------------------------- the banner
{
  const line = F.ratesLine(CFG);
  ok('the rate banner states both rates, the window and the deposit',
     /\$55pp standard/.test(line) && /\$75pp/.test(line)
     && /18:00-21:00/.test(line) && /deposit \$100/.test(line), line);
  ok('...and whether online deposits are actually on', /online deposits live/.test(line));
  ok('...and says so when they are off',
     /online deposits OFF/.test(F.ratesLine({ ...CFG, deposits_live: false })));
}

// =========================================================== THE DIARY HALF
// The page listed BRIEFS and called that the diary. Every confirmed function
// in the book has brief_id null, because they were pushed in from the Monday
// tracker, so the screen showed nothing while a 35-person engagement party sat
// on 25 October. The fixtures below are the four real rows, copied from
// GET /api/admin/functions/diary on the live service — including the two that
// contradict their own columns. They are the regression fixture: a change that
// cannot draw these four correctly is wrong.
const DIARY = [
  { id: 'e93280ad65d1', date: '2026-08-08', time: '15:30', name: 'Roman Bunting',
    email: 'romanbunting@gmail.com', phone: null, area: 'Old Stow',
    pinned_table: 'Old Stow', adults: 40, kids: 0, covers: 40, hold_minutes: 240,
    status: 'confirmed', created_at: '2026-08-06T07:28:23.965221',
    is_function: true, brief_id: null, matched_on: ['is_function', 'notes', 'area'],
    notes: 'FUNCTION - birthday. Old Stow booked out. $80 Razzle Dazzle bottomless '
      + '(2hr), pizzas/wings through the night, everyone pays on arrival. Numbers '
      + 'rose 30->40. Source: Monday tracker + functions inbox 1 Aug.' },
  { id: '1878ce4a6350', date: '2026-08-08', time: '18:30', name: 'Harry Baker',
    email: 'harryjbaker04@gmail.com', phone: '0459391889', area: 'Main Hall',
    pinned_table: 'Main Hall', adults: 25, kids: 0, covers: 25, hold_minutes: 240,
    status: 'confirmed', created_at: '2026-08-06T07:28:25.200641',
    is_function: true, brief_id: null, matched_on: ['is_function', 'notes', 'area'],
    notes: 'FUNCTION - 15-20 pax. Soiree package + $20pp food. Invoice/deposit '
      + 'settled 29 Jul. Paying at the door. Mates keen to DJ (chill house) - '
      + 'Steph to confirm. SPACE ASSUMED Main Hall (Old Stow taken by Roman) - confirm.' },
  { id: '1e871002336d', date: '2026-08-15', time: '18:30', name: 'Diane Godfrey',
    email: 'oblanks@bigpond.net.au', phone: '0417488842', area: 'Old Stow',
    pinned_table: 'Old Stow', adults: 20, kids: 0, covers: 20, hold_minutes: 240,
    status: 'confirmed', created_at: '2026-08-06T07:28:26.453645',
    is_function: true, brief_id: null, matched_on: ['is_function', 'notes', 'area'],
    notes: 'FUNCTION - birthday, Old Stow. START 6:30PM - confirmed by Di via text '
      + '15 Aug. Bar TAB, not prepaid. A couple of approved cocktails - Di sets the '
      + 'list with the bar on arrival. Food list to drop through the night. 20 pax '
      + 'on file, not re-confirmed.' },
  { id: 'f9eb11be3b3a', date: '2026-10-25', time: '18:00', name: 'Michael Jordan',
    email: 'jordanmichael228@gmail.com', phone: '0403166130', area: 'Old Stow',
    pinned_table: 'Old Stow', adults: 35, kids: 0, covers: 35, hold_minutes: 240,
    status: 'confirmed', created_at: '2026-08-06T07:28:27.680901',
    is_function: true, brief_id: null, matched_on: ['is_function', 'notes', 'area'],
    notes: 'FUNCTION - engagement party, Old Stow. DEPOSIT PAID 30 Jul, date locked '
      + 'in. 2hr Soiree drinks package + light nibbles. *** START TIME TBC '
      + '(placeholder 6pm) ***' },
];
// The server's rollup, exactly as it arrives. Note 8 Aug: one date, two
// bookings, two rooms, two ids.
const BY_DATE = [
  { date: '2026-08-08', count: 2, covers: 65,
    areas: [{ area: 'Old Stow', count: 1, covers: 40 },
            { area: 'Main Hall', count: 1, covers: 25 }],
    booking_ids: ['e93280ad65d1', '1878ce4a6350'] },
  { date: '2026-08-15', count: 1, covers: 20,
    areas: [{ area: 'Old Stow', count: 1, covers: 20 }],
    booking_ids: ['1e871002336d'] },
  { date: '2026-10-25', count: 1, covers: 35,
    areas: [{ area: 'Old Stow', count: 1, covers: 35 }],
    booking_ids: ['f9eb11be3b3a'] },
];
const fn = (id) => DIARY.find((f) => f.id === id);
const ROMAN = fn('e93280ad65d1'), HARRY = fn('1878ce4a6350');
const DIANE = fn('1e871002336d'), MJ = fn('f9eb11be3b3a');

// --------------------------------------------- it asks for the whole diary
{
  ok('the diary is fetched from the engine, not regrouped out of the briefs',
     /\/api\/admin\/functions\/diary\?from=\$\{DIARY_FROM\}/.test(src));
  ok('...from a fixed floor, so the historic section cannot silently truncate',
     /^\d{4}-\d{2}-\d{2}$/.test(F.DIARY_FROM) && F.DIARY_FROM < '2024-01-01',
     F.DIARY_FROM);
  ok('...and with no `to`, because the engine leaves it open on purpose',
     !/functions\/diary\?[^`']*to=/.test(src));
}

// -------------------------------------------------- booked vs being chased
{
  const modes = F.modesHTML('diary', 1, 12);
  ok('the two halves are one control, not two pages',
     /data-mode="diary"/.test(modes) && /data-mode="pipeline"/.test(modes), modes);
  ok('the half you are in is marked', /data-mode="diary" class="on"/.test(modes), modes);
  ok('...and each carries its own count',
     modes.includes('Diary<span class="n">1</span>')
     && modes.includes('Pipeline<span class="n">12</span>'), modes);
  ok('the diary is the default half — it is the one that was invisible',
     /let MODE = 'diary'/.test(src));
}

// ------------------------------------------------------- what is coming up
{
  const { upcoming, past } = F.splitDiary(DIARY, TODAY);
  ok('a confirmed function with no brief is IN the diary',
     upcoming.length === 1 && upcoming[0].name === 'Michael Jordan',
     upcoming.map((f) => f.name).join(','));
  ok('history is most recent first', past.map((f) => f.id).join(',')
     === '1e871002336d,1878ce4a6350,e93280ad65d1', past.map((f) => f.name).join(','));
  ok('...and two on one date are ordered by time within it',
     past[1].time === '18:30' && past[2].time === '15:30');
  ok('a function happening TODAY is still coming up, not history',
     F.splitDiary(DIARY, '2026-08-15').upcoming.some((f) => f.id === '1e871002336d'));

  const rail = F.diaryRailHTML(DIARY, null, TODAY);
  ok('coming up is drawn before what has already run',
     rail.indexOf('Coming up') < rail.indexOf('Already happened'));
  ok('every diary row is reachable from a keyboard',
     (rail.match(/role="button" tabindex="0"/g) || []).length === 4);
  ok('all four are on the rail, none collapsed away',
     DIARY.every((f) => rail.includes(`data-fn="${f.id}"`)));

  // Real usage is thin. Empty must read as calm, not as a page that failed.
  const none = F.diaryRailHTML([], null, TODAY);
  ok('an empty diary still draws both headings and their zeros',
     /Coming up<span class="n">0<\/span>/.test(none)
     && /Already happened<span class="n">0<\/span>/.test(none), none);
  ok('...and says so in a calm sentence rather than an error',
     /The diary is clear/.test(none) && !/error|failed|problem/i.test(none), none);
  const onlyPast = F.diaryRailHTML(DIARY, null, '2026-12-01');
  ok('nothing ahead but plenty behind still reads calm',
     /The diary is clear/.test(onlyPast) && onlyPast.includes('data-fn="f9eb11be3b3a"'));
}

// --------------------------------------------------------- the month grid
{
  ok('the grid starts on Monday, so a weekend is not split across two rows',
     F.DOW[0] === 'Mon' && F.DOW[5] === 'Sat' && F.DOW[6] === 'Sun');
  const cells = F.monthCells('2026-08');
  ok('August 2026 starts on a Saturday, so five blanks lead it',
     cells.slice(0, 5).every((c) => c === null) && cells[5] === '2026-08-01', cells.slice(0, 7).join('|'));
  ok('...and the month is whole weeks', cells.length % 7 === 0 && cells.length === 42, String(cells.length));
  ok('every day of the month is present', cells.filter(Boolean).length === 31);
  ok('February in a non-leap year is 28 days',
     F.monthCells('2026-02').filter(Boolean).length === 28);
  ok('moving off December rolls the year', F.shiftMonth('2026-12', 1) === '2027-01');
  ok('...and off January rolls it back', F.shiftMonth('2026-01', -1) === '2025-12');
  ok('...and January the 31st does not become March',
     F.shiftMonth('2026-01', 1) === '2026-02');

  const aug = F.calendarHTML('2026-08', BY_DATE, null, TODAY);
  ok('the month is movable in both directions',
     /data-month="prev"/.test(aug) && /data-month="next"/.test(aug));
  ok('a date with functions is marked and clickable',
     /class="cell has"[^>]*data-date="2026-08-08"/.test(aug), aug.slice(aug.indexOf('2026-08-08') - 90, aug.indexOf('2026-08-08') + 40));
  ok('a date with nothing on it is not a button',
     !/data-date="2026-08-09"/.test(aug));
  ok('today is marked', aug.includes('class="cell today"') || /cell[^"]*today/.test(aug));
  // Three: 8 August carries two of them.
  ok('the month says how many functions are in it — counting BOTH on 8 Aug',
     /3 functions this month/.test(aug), aug.slice(0, 700));
  ok('...and says nothing rather than 0 when there are none',
     /nothing on this month/.test(F.calendarHTML('2026-09', BY_DATE, null, TODAY)));

  // The one that matters. 8 Aug is two parties in two rooms with overlapping
  // holds. A cell that shows one of them, or shows "2" and no more, is wrong.
  const cell = aug.slice(aug.indexOf('data-date="2026-08-08"'));
  const body = cell.slice(0, cell.indexOf('</div><div class="cell'));
  ok('a date with two functions draws BOTH, one pip each',
     (body.match(/class="pip"/g) || []).length === 2, body);
  ok('...and names both rooms, because they are different rooms',
     /Old Stow/.test(body) && /Main Hall/.test(body), body);
  ok('...with the covers each, not one merged number',
     /Old Stow · 40/.test(body) && /Main Hall · 25/.test(body), body);
  ok('a single-function date draws one pip',
     (F.calendarHTML('2026-10', BY_DATE, null, TODAY).match(/class="pip"/g) || []).length === 1);
  // Two in the SAME room on one date would arrive as one areas entry, count 2.
  const twice = F.calendarHTML('2026-08', [{ date: '2026-08-08', count: 2, covers: 60,
    areas: [{ area: 'Old Stow', count: 2, covers: 60 }], booking_ids: ['a', 'b'] }],
    null, TODAY);
  ok('two functions in the SAME room say so rather than reading as one',
     /Old Stow ×2 · 60/.test(twice), twice.slice(twice.indexOf('2026-08-08'), twice.indexOf('2026-08-08') + 200));

  // The calendar must come off by_date, not off a second grouping of the rows.
  const orphaned = F.calendarHTML('2026-08', BY_DATE, null, TODAY);
  ok('MUTATION: the calendar is drawn from the server rollup, so it cannot '
     + 'disagree with the list', orphaned === F.calendarHTML('2026-08', BY_DATE, null, TODAY)
     && /data-date="2026-08-15"/.test(F.calendarHTML('2026-08', BY_DATE, null, TODAY)));
  const selected = F.calendarHTML('2026-08', BY_DATE, '2026-08-08', TODAY);
  ok('the chosen date is marked as chosen', /class="cell has sel"/.test(selected));
}

// ------------------------------------------- clicking a date with two on it
{
  const on = DIARY.filter((f) => f.date === '2026-08-08');
  const head = F.dateHeadHTML('2026-08-08', on);
  ok('a two-function date says two, in words, before anything else',
     /2 functions/.test(head), head);
  ok('...names both rooms', /Old Stow, Main Hall/.test(head), head);
  ok('...adds the covers up', /65 covers/.test(head), head);
  ok('...and says they are separate bookings, each below in full',
     /separate bookings/.test(head), head);
  ok('a one-function date says one, not "1 functions"',
     /One function/.test(F.dateHeadHTML('2026-10-25', [MJ])));

  const panel = F.diaryPanelHTML(DIARY, BY_DATE, '2026-08', '2026-08-08', null, TODAY, null);
  ok('clicking the date shows BOTH functions, not the first one',
     panel.includes('Roman Bunting') && panel.includes('Harry Baker'), 'one of them is missing');
  ok('...each in full, with its own note',
     panel.includes('everyone pays on arrival') && panel.includes('Steph to confirm'));
  ok('...and the calendar stays above them', panel.indexOf('calgrid') < panel.indexOf('Roman'));
  const one = F.diaryPanelHTML(DIARY, BY_DATE, '2026-08', '2026-08-08',
                               '1878ce4a6350', TODAY, null);
  ok('picking one function off the rail shows just that one',
     one.includes('Harry Baker') && !one.includes('Roman Bunting'));
}

// ------------------------------------------------ the start time nobody agreed
{
  ok('a note that hedges about the TIME is a start time that is not settled',
     F.timeDoubt(MJ.notes) === '*** START TIME TBC (placeholder 6pm) ***',
     String(F.timeDoubt(MJ.notes)));
  ok('a start time confirmed in the note is NOT flagged',
     F.timeDoubt(DIANE.notes) === null, String(F.timeDoubt(DIANE.notes)));
  ok('...nor is a note with no time in it at all',
     F.timeDoubt(ROMAN.notes) === null && F.timeDoubt(HARRY.notes) === null);
  ok('the rule is general, not Michael Jordan',
     F.timeDoubt('Kick-off time TBA, waiting on the venue') !== null
     && F.timeDoubt('Start time provisional until they hear back') !== null,
     String(F.timeDoubt('Kick-off time TBA, waiting on the venue')));
  ok('...and a hedge about something else is not a hedge about the time',
     F.timeDoubt('Cake TBC') === null && F.timeDoubt('20 pax, not re-confirmed') === null);

  const w = F.whenLine(MJ);
  ok('an unsettled start time is a different statement, not a hedged 6pm',
     w.uncertain === true && w.label === 'Start time not agreed', JSON.stringify(w));
  ok('the rail row says TBC where it would have said 6pm',
     /start time TBC/.test(F.diaryRowHTML(MJ, null, TODAY))
     && !/· 6pm/.test(F.diaryRowHTML(MJ, null, TODAY)), F.diaryRowHTML(MJ, null, TODAY));

  const card = F.functionCardHTML(MJ, TODAY);
  ok('the card headlines that it is not agreed', /Start time not agreed/.test(card));
  ok('...quotes the note that says so, in their own words',
     card.includes('START TIME TBC (placeholder 6pm)'), card.slice(0, 400));
  ok('...tells anyone not to read 6pm back to a client',
     /not to read it back|not settled/i.test(card));
  ok('...and does not put 6pm anywhere it could be mistaken for the answer',
     !/<div class="big">6pm/.test(card) && !/6pm–10pm/.test(card));

  const dw = F.whenLine(DIANE);
  ok('a firm start time gets its span, opening to end of hold',
     dw.uncertain === false && dw.label === '6:30pm–10:30pm', JSON.stringify(dw));
  ok('an end time rolls past midnight without going negative',
     F.endTime('23:30', 240) === '03:30');
}

// ----------------------------------------------- the hold is a hold, not a signal
{
  const card = F.functionCardHTML(DIANE, TODAY);
  ok('the hold is stated as a duration', /held for 4 hours/.test(card), card.slice(card.indexOf('quote'), card.indexOf('quote') + 300));
  ok('...and never compared to what the app writes by default',
     !/180/.test(card) && !/unusual|non-standard|longer than|shorter than/i.test(card));
  ok('a part-hour hold reads properly', F.holdLabel(210) === '3 hours 30 min');
  ok('one hour is not "1 hours"', F.holdLabel(60) === '1 hour');
}

// ------------------------------------- the note is the record, in full
{
  const card = F.functionCardHTML(ROMAN, TODAY);
  ok('the whole note is on the card, not a first line of it',
     card.includes('$80 Razzle Dazzle bottomless (2hr), pizzas/wings through the night')
     && card.includes('Source: Monday tracker + functions inbox 1 Aug.'), 'truncated');
  ok('...and it is not clipped to a scroll box either', /class="log full"/.test(card));
  ok('a booking with nothing open says nothing about open questions',
     !/Still open in the note/.test(card), card);

  const h = F.functionCardHTML(HARRY, TODAY);
  ok('open questions in the note are surfaced as questions',
     /Still open in the note/.test(h));
  ok('...including "Steph to confirm"', /Steph to confirm/.test(h.slice(h.indexOf('Still open'))));
  ok('...and an ASSUMED room', /SPACE ASSUMED/.test(h.slice(h.indexOf('Still open'))));
  ok('a headcount that has not been re-confirmed is an open question too',
     F.openQuestions(DIANE.notes).some((q) => /not re-confirmed/.test(q)),
     JSON.stringify(F.openQuestions(DIANE.notes)));
  ok('a clean note raises none', F.openQuestions(ROMAN.notes).length === 0,
     JSON.stringify(F.openQuestions(ROMAN.notes)));
  ok('the FUNCTION marker is not quoted back as if it were the note',
     F.headcountSays(HARRY).quotes.join('') === '15-20 pax',
     JSON.stringify(F.headcountSays(HARRY).quotes));
  const mj = F.functionCardHTML(MJ, TODAY);
  ok('the unsettled start time is said once, not twice on the same card',
     (mj.match(/START TIME TBC/g) || []).length === 2, 'once in its own block, '
     + 'once in the note in full: ' + (mj.match(/START TIME TBC/g) || []).length);
  ok('...so it does not also reappear under "still open"',
     !/Still open in the note/.test(mj), mj.slice(mj.indexOf('Still open')));
}

// ---------------------------------- when the note and the columns disagree
{
  const hb = F.headcountSays(HARRY);
  ok('adults:25 under a note saying "15-20 pax" is a disagreement',
     hb.disagrees === true && hb.quotes.join(' ').includes('15-20 pax'), JSON.stringify(hb));
  const card = F.functionCardHTML(HARRY, TODAY);
  ok('...and the screen says so rather than printing 25 as the answer',
     /but the note says otherwise/.test(card), card.slice(card.indexOf('covers'), card.indexOf('covers') + 400));
  ok('...quoting the note beside it', /15-20 pax/.test(card));
  ok('a note whose numbers agree with the booking is not a disagreement',
     F.headcountSays(ROMAN).disagrees === false
     && F.headcountSays(DIANE).disagrees === false,
     JSON.stringify([F.headcountSays(ROMAN), F.headcountSays(DIANE)]));
  // Escaped on the way out, which is why the needle is not what was typed.
  ok('...and "Numbers rose 30->40" is still quoted, because it is the story',
     /Numbers rose 30-&gt;40/.test(F.functionCardHTML(ROMAN, TODAY)));
  ok('a note that says nothing about numbers claims no disagreement',
     F.headcountSays(MJ).quotes.length === 0 && F.headcountSays(MJ).disagrees === false);
}

// -------------------------------------------------------- the missing phone
{
  const card = F.functionCardHTML(ROMAN, TODAY);
  ok('a null phone never becomes a tel: link to nowhere',
     !/href="tel:/.test(card) && !/null/.test(card), card.slice(card.indexOf('mailto'), card.indexOf('mailto') + 260));
  ok('...and says the phone is simply not on file',
     /No phone on file/.test(card));
  ok('a phone that IS on file is a call control',
     /href="tel:0459391889"/.test(F.functionCardHTML(HARRY, TODAY)));
}

// ------------------------------------------------ no brief is not a fault
{
  const card = F.functionCardHTML(MJ, TODAY);
  ok('a confirmed function with no brief is drawn as a complete record',
     /No brief behind this one/.test(card));
  ok('...and says plainly that nothing is missing',
     /nothing is missing/.test(card) && !/error|invalid|incomplete/i.test(card));
  ok('...and offers no button to a brief that does not exist',
     !/data-act="gobrief"/.test(card));
  const linked = F.functionCardHTML({ ...MJ, brief_id: 'ab12cd34ef56' }, TODAY);
  ok('a booking that DOES have a brief links to it',
     /data-act="gobrief" data-brief="ab12cd34ef56"/.test(linked));
  const brief = F.panelHTML({ name: 'X', guests: 30, date: '2026-10-25',
    start_time: '18:00', missing: [], problems: [], deposit_status: 'paid',
    booking_id: 'f9eb11be3b3a' }, CFG, [], TODAY);
  ok('...and the brief links back to the diary, so it goes both ways',
     /data-act="godiary" data-booking="f9eb11be3b3a"/.test(brief));
  ok('how the engine recognised it as a function is stated, not hidden',
     /in the diary because of/.test(card) && /the room it is pinned to/.test(card),
     card.slice(card.lastIndexOf('booking ')));
}

// ------------------------------------ an empty diary vs a diary that broke
{
  const empty = F.diaryPanelHTML([], [], '2026-08', null, null, TODAY, null);
  ok('an empty diary is calm', !/error|failed|didn/i.test(empty), empty);
  const broken = F.diaryPanelHTML([], [], '2026-08', null, null, TODAY, 'HTTP 502');
  ok('a diary that would not load says so, and says it is not the same thing',
     /didn’t load/.test(broken) && /not an empty diary/.test(broken), broken);
  ok('...and names what went wrong', /HTTP 502/.test(broken));
  ok('a diary outage does not take the pipeline down with it',
     /\.catch\(\(e\) => \(\{ error: why\(e\), functions: \[\], by_date: \[\] \}\)\)/.test(src));
}

// -------------------------------------------------------- escaping, again
{
  const nasty = { ...ROMAN, name: '<img src=x onerror=alert(1)>',
    notes: 'FUNCTION — “mates’ rates” said the client — <script>alert(1)</script> & co.',
    pinned_table: '<b>Old Stow</b>', area: null, phone: '"><script>x</script>' };
  const card = F.functionCardHTML(nasty, TODAY);
  ok('a client-typed name cannot inject markup here either',
     !card.includes('<img src=x'), card.slice(0, 240));
  ok('...nor can the note', !card.includes('<script>alert(1)</script>'));
  ok('...and a smart quote or an em dash survives intact rather than breaking',
     card.includes('“mates’ rates”') && card.includes('FUNCTION —'));
  ok('...a room name is escaped in the card', !card.includes('<b>Old Stow</b>'));
  ok('...and a phone is escaped inside the href attribute',
     !/href="tel:"><script>/.test(card), card.slice(card.indexOf('tel:'), card.indexOf('tel:') + 90));
  const cal = F.calendarHTML('2026-08', [{ date: '2026-08-08', count: 1, covers: 4,
    areas: [{ area: '<img src=x onerror=alert(1)>', count: 1, covers: 4 }],
    booking_ids: ['x'] }], null, TODAY);
  ok('...and so is a room name in a calendar pip', !cal.includes('<img src=x'));
  const rail = F.diaryRailHTML([nasty], null, TODAY);
  ok('...and in the rail', !rail.includes('<img src=x'));
}

// ------------------------------------------------------------ MUTATION CHECK
{
  ok('MUTATION: a token box shown by default would be caught',
     !/id="tokenbox"[^>]*style="display:none"/
        .test(html.replace('id="tokenbox" style="display:none"', 'id="tokenbox"')));
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  ok('Auth.bookingToken() is CODE, not a promise made in a comment',
     code.includes('Auth.bookingToken()'));
  ok('the paste box is reached ONLY when the automatic lookup came back empty',
     /const t = await ensureToken\(\);\s*\n\s*if \(!t\) \{ showToken\(\); return; \}/.test(code),
     code.slice(code.indexOf('async function init'), code.indexOf('async function init') + 400));
  ok('MUTATION: prompting unconditionally would be caught',
     !/const t = await ensureToken\(\);\s*\n\s*if \(!t\) \{ showToken\(\); return; \}/
        .test(code.replace('if (!t) { showToken(); return; }', 'showToken(); return;')));
  ok('MUTATION: a calendar that collapsed a date to one entry would be caught',
     (F.calendarHTML('2026-08', BY_DATE, null, TODAY).match(/class="pip"/g) || []).length === 3);
  ok('MUTATION: printing the placeholder 6pm as a start time would be caught',
     !/start time TBC/.test(F.diaryRowHTML({ ...MJ, notes: 'FUNCTION - engagement party' },
       null, TODAY)));
  ok('MUTATION: filing a stranded brief under Confirmed would be caught',
     F.groupRail([{ id: 's', stage: 'enquiry', deposit_status: 'paid',
                    missing: [], problems: [] }], []).held.length === 0);
}

// ================================================ HOW IT WENT, AFTER THE NIGHT
// The post-event half. Zak's question is "was it worth doing", and the answer
// is a GP percentage that is soft in three specific ways — the mixer cost is
// assumed, food is uncosted, and the package SKUs book at 100% in the P&L.
//
// The failure this section exists to catch is not an arithmetic one. It is a
// screenshot: "59.3%" lifted off a page and quoted in a meeting six months
// later with "beverage only, mixer estimated, packages uncosted" left behind.
// Nothing 404s, nothing throws, and the number is even correct. So the tests
// below assert co-presence structurally — a percentage and its caveats are one
// block or there is no percentage at all.
//
// The two fixtures are the only two functions anybody has measured, both
// verified by hand off the POS, shaped exactly as functions.outcome() returns
// them. They are the regression fixture: a change that cannot draw these two
// truthfully is wrong.
const esc0 = (s) => F.esc(s);
const ROMAN_OUT = {
  actual_heads: 25, booked_guests: 40, tickets_sold: 25,
  revenue_inc_cents: 200000, food_revenue_inc_cents: null,
  drinks_poured: 239, menu_value_inc_cents: 359900,
  cogs_ex_cents: 62548, mixer_est_ex_cents: 11380,
  pos_refs: 'Tab Roman B 40th · receipt 1188213 · sale 3f2a91',
  revenue_ex_cents: 181818, bev_revenue_inc_cents: 200000,
  bev_revenue_ex_cents: 181818, total_cogs_ex_cents: 73928,
  gross_profit_ex_cents: 107890, gp_pct: 59.34, gp_pct_ex_mixer: 65.6,
  gp_basis: 'beverage', drinks_per_head: 9.56, package_hours: 3,
  drinks_per_hour: 79.67, cogs_ex_cents_per_head: 2957,
  menu_value_inc_cents_per_head: 14396,
  benchmark_gp_pct: 76.4, margin_foregone_ex_cents: 31019, out_earn_ratio: 1.29,
  caveats: [
    { code: 'mixer_estimated', gp_pct_ex_mixer: 65.6, gp_pct_points: 6.26,
      note: 'the mixer cost is an estimate, not a repo figure -- house spirits '
        + 'are costed as the nip only, so a per-pour mixer blend is added by '
        + 'assumption',
      effect: 'strip it out and GP rises 6.3 points, to 65.6%' },
    { code: 'package_sku_uncosted',
      note: 'the package SKUs have no costed recipe and book at 100% GP in the '
        + 'P&L, which is why functions look free until someone works one out by '
        + 'hand',
      effect: 'this figure does not appear anywhere in the P&L' },
  ],
};
// Harry Baker is the one with food: $80pp was $60 drinks and $20 food, so his
// GP is a beverage GP on $1,140 and not a GP on $1,520. Three caveats, not two.
const HARRY_OUT = {
  actual_heads: 19, booked_guests: 25, tickets_sold: 19,
  revenue_inc_cents: 152000, food_revenue_inc_cents: 38000,
  drinks_poured: 149, menu_value_inc_cents: 182050,
  cogs_ex_cents: 33256, mixer_est_ex_cents: 7966,
  pos_refs: 'Tab HB 21st · receipt 1188402 · sale 91cc07',
  revenue_ex_cents: 138182, bev_revenue_inc_cents: 114000,
  bev_revenue_ex_cents: 103636, total_cogs_ex_cents: 41222,
  gross_profit_ex_cents: 62414, gp_pct: 60.22, gp_pct_ex_mixer: 67.91,
  gp_basis: 'beverage', drinks_per_head: 7.84, package_hours: 3,
  drinks_per_hour: 49.67, cogs_ex_cents_per_head: 2170,
  menu_value_inc_cents_per_head: 9582,
  benchmark_gp_pct: 76.4, margin_foregone_ex_cents: 16764, out_earn_ratio: 1.27,
  caveats: [
    { code: 'mixer_estimated', gp_pct_ex_mixer: 67.91, gp_pct_points: 7.69,
      note: 'the mixer cost is an estimate, not a repo figure -- house spirits '
        + 'are costed as the nip only, so a per-pour mixer blend is added by '
        + 'assumption',
      effect: 'strip it out and GP rises 7.7 points, to 67.9%' },
    { code: 'food_cogs_unknown', food_revenue_inc_cents: 38000,
      note: 'food COGS is unknown -- kitchen items are uncosted, so this is a '
        + 'beverage-only GP, not a blended one',
      effect: 'food revenue is excluded from the GP above rather than being '
        + 'credited against a cost nobody has' },
    { code: 'package_sku_uncosted',
      note: 'the package SKUs have no costed recipe and book at 100% GP in the '
        + 'P&L, which is why functions look free until someone works one out by '
        + 'hand',
      effect: 'this figure does not appear anywhere in the P&L' },
  ],
};
// Roman's brief exists and carries the numbers; Harry's does not yet, which is
// the ordinary state of everything in this book.
const ROMAN_DONE = { ...ROMAN, brief_id: 'b0b0b0b0b0b0', outcome: ROMAN_OUT };
const HARRY_DONE = { ...HARRY, brief_id: 'c1c1c1c1c1c1', outcome: HARRY_OUT };

// ------------------------- THE ONE THAT MATTERS: no GP without its caveats
{
  const full = F.gpFigureHTML(ROMAN_OUT);
  ok('the GP figure renders, to a tenth', /59\.3%/.test(full), full);
  ok('...and says what basis it is on, beside the number',
     /59\.3%<span class="basis">beverage GP<\/span>/.test(full), full);
  for (const c of ROMAN_OUT.caveats) {
    ok(`...with the "${c.code}" caveat in the SAME block as the number`,
       full.includes(esc0(c.note)) && full.includes(esc0(c.effect)), full);
  }
  ok('...in ONE element with the number, so there is nothing to screenshot apart',
     full.startsWith('<div class="gp">') && full.endsWith('</div>')
     && full.indexOf('59.3%') < full.indexOf('class="caveats"'), full);

  // THE REQUIREMENT. Strip the caveats and the percentage must not survive.
  const bare = F.gpFigureHTML({ ...ROMAN_OUT, caveats: [] });
  ok('a GP figure CANNOT render without its caveats — an empty caveat list '
     + 'draws a refusal, not a number',
     !/59\.3/.test(bare) && !/\d+(\.\d+)?%/.test(bare) && /withheld/i.test(bare),
     bare);
  ok('...and the refusal explains itself rather than just going blank',
     /read back as fact/.test(bare) && /not safe to use/.test(bare), bare);
  const undef = F.gpFigureHTML({ ...ROMAN_OUT, caveats: undefined });
  ok('...and a caveat list that is missing entirely behaves the same way',
     !/59\.3/.test(undef) && /withheld/i.test(undef), undef);

  // ...and not just in the helper. The whole card must not leak it either.
  const card = F.functionCardHTML(
    { ...ROMAN_DONE, outcome: { ...ROMAN_OUT, caveats: [] } }, TODAY);
  ok('...on the whole diary card too: no caveats, no percentage anywhere on it',
     !/59\.3/.test(card) && !/65\.6/.test(card), card.slice(card.indexOf('How it went')));
  ok('...while everything MEASURED still shows, because those are not in doubt',
     /239/.test(card) && /\$2,000/.test(card), card.slice(card.indexOf('How it went')));

  // Structural, at the source. One formatter, and the number lives inside the
  // guard rather than beside it.
  const code0 = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const start = code0.indexOf('export function gpFigureHTML(');
  const after = code0.indexOf('\nexport ', start + 10);
  ok('gpFigureHTML exists and is bounded', start > 0 && after > start);
  // \b so benchmark_gp_pct — the venue's run rate, which is not a function's
  // GP and carries no doubt — is not swept up as a stray reader.
  const stray = [];
  for (const m of code0.matchAll(/\bgp_pct\w*/g)) {
    if (m.index < start || m.index >= after) {
      stray.push(code0.slice(Math.max(0, m.index - 70), m.index + 30));
    }
  }
  ok('gp_pct and gp_pct_ex_mixer are read in exactly ONE function in the whole '
     + 'module — a second reader is a second place a bare number can appear',
     stray.length === 0, stray.join('\n    || '));
  const region = code0.slice(start, after);
  ok('MUTATION: the number is drawn INSIDE the caveat guard, not beside it',
     region.indexOf('!caveats.length') < region.indexOf('${pct(o.gp_pct)}'),
     region.slice(0, 400));
  ok('MUTATION: deleting the guard would be caught here',
     !/59\.3/.test(F.gpFigureHTML({ ...ROMAN_OUT, caveats: [] })));
}

// ---------------------------------------- the other end of the range, not a footnote
{
  const full = F.gpFigureHTML(ROMAN_OUT);
  ok('the mixer-free figure is drawn as the other end of a RANGE',
     /between <b>59\.3%<\/b> and\s*<b>65\.6%<\/b>/.test(full), full);
  ok('...and says which end is which, and that nobody knows',
     /estimated mixer cost taken back out/.test(full)
     && /nobody has measured which one is right/.test(full), full);
  ok('...above the caveats, so it reads as a figure rather than a caveat',
     full.indexOf('class="range"') < full.indexOf('class="caveats"'), full);
  const noMixer = F.gpFigureHTML({ ...ROMAN_OUT, gp_pct_ex_mixer: null,
    caveats: [ROMAN_OUT.caveats[1]] });
  ok('no mixer estimate means no range, rather than a range with a hole in it',
     !/class="range"/.test(noMixer) && /59\.3%/.test(noMixer), noMixer);
}

// -------------------------------------------------- Roman Bunting, 8 August
{
  const card = F.functionCardHTML(ROMAN_DONE, TODAY);
  const rep = card.slice(card.indexOf('How it went'));
  ok('a past function reports on itself', /How it went/.test(card));
  ok('25 came against 40 on the booking, and the card says which it divides by',
     /25 through the door, against 40 on the booking — 15 fewer/.test(rep), rep);
  ok('...as a flag, not a footnote — the two numbers disagree',
     /class="prob"[^>]*>25 through the door/.test(rep), rep);
  ok('the revenue is stated inc-GST with the ex-GST figure beside it',
     /\$2,000/.test(rep) && /\$1,818\.18 ex-GST/.test(rep), rep);
  ok('239 drinks, at 9.56 a head', /239/.test(rep) && /9\.56 a head/.test(rep), rep);
  ok('...and 79.67 an hour over the three hours the package ran',
     /79\.67 an hour over 3 hours/.test(rep), rep);
  ok('cost a head is ex-GST and says what it includes',
     /\$29\.57/.test(rep) && /\$739\.28 in total/.test(rep)
     && /\$113\.80 is the mixer estimate/.test(rep), rep);
  ok('gross profit is stated in dollars, not only as a percentage',
     /\$1,078\.90/.test(rep), rep);
  ok('what was given away is per head, at menu price',
     /\$143\.96/.test(rep) && /\$3,599/.test(rep), rep);
  ok('...and is never called a loss', /Not a loss/.test(rep)
     && !/lost \$3,599|loss of \$3,599/.test(rep), rep);
  ok('the POS references are shown, so the figures can be got back to',
     /receipt 1188213/.test(rep) && /reproducible/.test(rep), rep);
}

// ------------------------------------- the out-earn ratio, said in English
{
  const s = F.outEarnSentence(ROMAN_OUT);
  ok('the comparison starts from what a function actually does to a room',
     /does not add trade to an empty room — it displaces it/.test(s), s);
  ok('...names the run rate it is being measured against', /76\.4%/.test(s), s);
  ok('...states the gap in dollars', /\$310\.19 of gross profit/.test(s), s);
  ok('...turns 1.29 into a sentence rather than leaving it as a number',
     /out-earn the trade it replaced by 1\.29 times/.test(s)
     && /29% more money through the till/.test(s), s);
  ok('...and says why "it made a profit" is not the question',
     /"It made a profit" and "it was worth doing" are different questions/.test(s), s);
  ok('the sentence does NOT restate the GP percentage — that number has one home',
     !/59\.3/.test(s), s);

  const beat = F.outEarnSentence({ ...ROMAN_OUT, margin_foregone_ex_cents: -12345,
    out_earn_ratio: 0.9 });
  ok('a function that BEAT the run rate is not described as having lost margin',
     /beat that, by \$123\.45/.test(beat) && !/displaces/.test(beat), beat);
  ok('nothing to compare gives no sentence, rather than a hedged one',
     F.outEarnSentence({ ...ROMAN_OUT, margin_foregone_ex_cents: null }) === '');
}

// ----------------------------------------- Harry Baker, and the food he had
{
  const card = F.functionCardHTML(HARRY_DONE, TODAY);
  const rep = card.slice(card.indexOf('How it went'));
  ok('19 came against 25 booked', /19 through the door, against 25 on the booking — 6 fewer/.test(rep), rep);
  ok('the food is shown as PART of the revenue, never on top of it',
     /\$380 of it food, which is part of this line and not on top of it/.test(rep), rep);
  ok('a function with food carries the food-COGS caveat',
     /beverage-only GP, not a blended one/.test(rep), rep);
  ok('...and Roman, who had none, does not',
     !/beverage-only GP/.test(F.functionCardHTML(ROMAN_DONE, TODAY)));
  ok('three caveats on Harry, two on Roman, and the block says how many',
     /3 things, and they are part of it/.test(rep)
     && /2 things, and they are part of it/.test(F.functionCardHTML(ROMAN_DONE, TODAY)),
     rep.slice(rep.indexOf('cvh')));
  ok('his GP is the beverage one, on $1,036.36 and not on $1,381.82',
     /\$1,036\.36 of beverage revenue/.test(rep) && !/\$1,381\.82 of beverage/.test(rep), rep);
  ok('60.2% between its two ends', /between <b>60\.2%<\/b> and\s*<b>67\.9%<\/b>/.test(rep), rep);
  ok('7.84 drinks a head', /7\.84 a head/.test(rep), rep);
  ok('and 1.27 to break even', /by 1\.27 times/.test(rep) && /27% more money/.test(rep), rep);
}

// ------------------------------- the package caveat is the reason for all of it
{
  for (const [who, card] of [['Roman', F.functionCardHTML(ROMAN_DONE, TODAY)],
                             ['Harry', F.functionCardHTML(HARRY_DONE, TODAY)]]) {
    ok(`${who}: the packages-uncosted caveat is ALWAYS there — it is why this `
       + 'report has to exist at all',
       /no costed recipe and book at 100% GP/.test(card), card.slice(card.indexOf('caveats')));
    ok(`${who}: ...and says the figure appears nowhere in the P&L`,
       /does not appear anywhere in the P&amp;L/.test(card));
  }
  const odd = F.gpFigureHTML({ ...ROMAN_OUT,
    caveats: [{ code: 'something_new', note: 'a caveat this page has never seen',
                effect: null }] });
  ok('a caveat code the page has never heard of is still drawn, not swallowed',
     /something_new/.test(odd) && /never seen/.test(odd), odd);
}

// --------------------------------- a night nobody counted is not a night that made nothing
{
  const card = F.functionCardHTML(ROMAN, TODAY);   // past, no outcome at all
  const rep = card.slice(card.indexOf('How it went'));
  ok('a past function with nothing recorded still says "How it went"',
     /How it went/.test(card));
  ok('...and says plainly that this is not a function that made nothing',
     /not a\s*function that made nothing/.test(rep), rep);
  ok('...and never draws a zero for an unmeasured figure',
     !/\$0\b/.test(rep) && !/\b0%/.test(rep) && !/0 drinks/.test(rep), rep);
  ok('...and says why it matters: the P&L books these at 100%',
     /package SKUs book at\s*100%/.test(rep), rep);

  ok('a function that has not happened yet gets no report at all — this screen '
     + 'does not forecast', F.outcomeHTML(MJ, TODAY) === '');
  ok('...and one happening TODAY has not finished, so nor does it',
     F.outcomeHTML({ ...ROMAN, date: TODAY }, TODAY) === '');
  ok('...but yesterday has', F.hasHappened({ date: '2026-08-19' }, TODAY) === true);

  // Half-measured: heads counted, nothing else. The API returns figures where
  // it can and nulls where it cannot, and so must the screen.
  const half = F.functionCardHTML({ ...ROMAN_DONE, outcome: {
    actual_heads: 25, booked_guests: 40, drinks_poured: 239,
    drinks_per_head: 9.56, package_hours: 3, drinks_per_hour: 79.67,
    gp_pct: null, gp_pct_ex_mixer: null, benchmark_gp_pct: 76.4,
    margin_foregone_ex_cents: null, out_earn_ratio: null,
    caveats: [ROMAN_OUT.caveats[1]] } }, TODAY);
  ok('a half-measured night reports what it has', /9\.56 a head/.test(half));
  ok('...and says the GP cannot be worked out yet rather than showing 0%',
     /No gross profit worked out yet/.test(half) && !/0\.0%/.test(half),
     half.slice(half.indexOf('How it went')));
  ok('...and names the inputs it is waiting on', /the food share of it/.test(half));
}

// -------------------------------------------------- the rail says which is which
{
  const done = F.diaryRowHTML(ROMAN_DONE, null, TODAY);
  const notYet = F.diaryRowHTML(HARRY, null, TODAY);
  const ahead = F.diaryRowHTML(MJ, null, TODAY);
  ok('a past function that has been reported on is marked as such',
     /reported<\/span>/.test(done), done);
  ok('...and one that has not is a job, not a silence',
     /no report yet<\/span>/.test(notYet), notYet);
  ok('a function still to come is neither', !/report/.test(ahead), ahead);
  const rail = F.diaryRailHTML([ROMAN_DONE, HARRY_DONE, MJ], null, TODAY);
  ok('the rail never carries a GP percentage — there is nowhere on a row to put '
     + 'what qualifies it', !/%/.test(rail), rail);
}

// ================================== the computed report, joined by booking id
// WHAT THIS SECTION IS FOR
// ------------------------
// data/functions_gp.json holds a gross profit worked out from a function's
// comped tab. The diary holds the bookings. Until the feed carried
// `booking_id` nothing joined them: the feed says "Dazzle drinks" and the
// diary says "Roman Bunting", and 8 August 2026 has TWO functions on it, so
// the date cannot disambiguate either. Both nights therefore sat in "Already
// happened" saying "no report yet" while their reports existed.
//
// The failure this section exists to catch is the one that would have come of
// closing that gap sloppily: a join on the name or the date puts ONE night's
// gross profit under the OTHER booking. Nothing 404s, both numbers are real,
// the caveats are all present and correct — and Roman's 59.3% is filed under
// Harry Baker. So the join is asserted to be the id and only the id.
//
// The feed here is a FIXTURE, not the real file: this suite is hermetic. The
// real feed is drawn by the real module in scripts/test_functions_gp_feed.mjs.
const GP_ENTRY_ROMAN = {
  ...ROMAN_OUT, id: '2026-08-08_dazzle_drinks', name: 'Dazzle drinks',
  date: '2026-08-08', venue: 'stowaway', booking_id: 'e93280ad65d1',
  booking_evidence: 'Roman Bunting, 8 Aug 2026 15:30, Old Stow, 40 covers. '
    + 'Matched on the package and the money, never on the name.',
  cost_book_as_of: '2026-08-08',
  source_file: 'data/function_tabs/2026-08-08_dazzle_drinks.json',
  pos_refs: 'Tab: Dazzle drinks -- comped to $0.00, 8 Aug 2026',
};
const GP_ENTRY_HARRY = {
  ...HARRY_OUT, id: '2026-08-08_harry', name: 'Harry', date: '2026-08-08',
  venue: 'stowaway', booking_id: '1878ce4a6350',
  booking_evidence: 'Harry Baker, 8 Aug 2026 18:30, Main Hall, 25 covers. '
    + 'Matched on the money: $60 Soiree plus $20pp food is the $80 ticket.',
  cost_book_as_of: '2026-08-08',
  source_file: 'data/function_tabs/2026-08-08_harry.json',
  pos_refs: 'Tab: Harry -- comped to $0.00, 8 Aug 2026',
};
const GP_FEED = { schema: 'functions_gp/1', benchmark_gp_pct: 76.4,
                  functions: [GP_ENTRY_ROMAN, GP_ENTRY_HARRY] };

// ------------------------------------------------------------ the join itself
{
  const joined = F.joinComputedReports(DIARY, GP_FEED);
  const byId = Object.fromEntries(joined.map((f) => [f.id, f]));
  ok('the computed report lands on the booking whose id it names',
     byId['e93280ad65d1'].computed_outcome === GP_ENTRY_ROMAN
     && byId['1878ce4a6350'].computed_outcome === GP_ENTRY_HARRY);
  ok('...which is the one whose tab name is nothing like the customer name — '
     + '"Dazzle drinks" is Roman Bunting and no string join could say so',
     byId['e93280ad65d1'].computed_outcome.name === 'Dazzle drinks'
     && byId['e93280ad65d1'].name === 'Roman Bunting');
  ok('...and the two functions on 8 August do NOT get each other’s report',
     byId['e93280ad65d1'].computed_outcome.drinks_poured === 239
     && byId['1878ce4a6350'].computed_outcome.drinks_poured === 149,
     JSON.stringify(joined.map((f) => [f.name,
       f.computed_outcome && f.computed_outcome.drinks_poured])));
  ok('a booking the feed says nothing about is returned untouched',
     byId['1e871002336d'].computed_outcome === undefined
     && byId['f9eb11be3b3a'].computed_outcome === undefined);
  ok('the join does not mutate the diary rows it was given',
     ROMAN.computed_outcome === undefined && DIARY.every((f) => !f.computed_outcome));

  ok('a feed entry with no booking id joins to nothing rather than to a guess',
     F.joinComputedReports(DIARY, { functions: [
       { ...GP_ENTRY_ROMAN, booking_id: null }] })
       .every((f) => !f.computed_outcome));
  ok('a date match is not a join — same night, wrong booking, no report',
     F.joinComputedReports([{ id: 'somethingelse', date: '2026-08-08' }], GP_FEED)
       .every((f) => !f.computed_outcome));
  ok('no feed at all is not an error, it is just no reports',
     F.joinComputedReports(DIARY, null).length === DIARY.length
     && F.joinComputedReports(DIARY, null).every((f) => !f.computed_outcome));
  ok('...and neither is a feed with nothing on it',
     F.joinComputedReports(DIARY, { schema: 'functions_gp/1', functions: [] })
       .every((f) => !f.computed_outcome));
}

// ----------------------------- two entries claiming one booking: show NEITHER
{
  const bad = F.joinComputedReports(DIARY, { functions: [
    GP_ENTRY_ROMAN, { ...GP_ENTRY_HARRY, booking_id: 'e93280ad65d1' }] });
  const r = bad.find((f) => f.id === 'e93280ad65d1');
  ok('two computed reports claiming one booking attaches NEITHER — one of them '
     + 'is another night and guessing files a profit under the wrong name',
     !r.computed_outcome && r.computed_ambiguous === true, JSON.stringify(r));
  const card = F.functionCardHTML(r, TODAY);
  ok('...and the card says so instead of drawing a figure',
     /Two computed reports claim this booking/.test(F.flat(card))
     && !/59\.3%/.test(card) && !/60\.2%/.test(card), F.flat(card).slice(0, 400));
  ok('...and the rail flags it rather than calling it reported',
     /report ambiguous/.test(F.diaryRowHTML(r, null, TODAY)),
     F.diaryRowHTML(r, null, TODAY));
}

// -------------------------------- the SAME renderer draws it, not a second one
{
  const joined = F.joinComputedReports(DIARY, GP_FEED);
  const roman = joined.find((f) => f.id === 'e93280ad65d1');
  const harry = joined.find((f) => f.id === '1878ce4a6350');
  const card = F.functionCardHTML(roman, TODAY);
  const rep = card.slice(card.indexOf('How it went'));

  ok('a past function whose booking id is on the feed no longer says "no '
     + 'report yet" — it draws the report',
     !/Nobody has reported on this one/.test(rep) && /59\.3%/.test(rep), rep);
  ok('...through the same block as a hand-recorded one, caveats and all',
     rep.includes('class="gp"') && rep.includes('class="caveats"')
     && GP_ENTRY_ROMAN.caveats.every((c) => rep.includes(F.esc(c.note))), rep);
  ok('...with the measured figures on the same card',
     /239/.test(rep) && /9\.56 a head/.test(rep), rep);
  ok('...and the displaced-trade sentence, which is the actual question',
     /out-earn the trade it replaced/.test(F.flat(rep)), F.flat(rep));
  ok('Harry gets HIS night, on the same date, from the same feed',
     /60\.2%/.test(F.functionCardHTML(harry, TODAY))
     && !/59\.3%/.test(F.functionCardHTML(harry, TODAY)));

  ok('the report says it was computed rather than typed',
     /Computed, not typed/.test(F.flat(rep)), F.flat(rep));
  ok('...names the dated cost book it was priced against, because a live one '
     + 'would not be reproducible',
     /the cost book as it stood on/.test(F.flat(rep)), F.flat(rep));
  ok('...and says so loudly when there is no dated book',
     /moves as recipes are recosted and is not\s*reproducible/
       .test(F.flat(F.computedProvenanceHTML({ ...GP_ENTRY_ROMAN,
         cost_book_as_of: 'live' }))));
  ok('...and carries the evidence the booking was paired on, on the screen',
     F.flat(rep).includes('Matched on the package and the money'), F.flat(rep));
  ok('...and says when there is no evidence, rather than looking equally sure',
     /Nothing on the feed says why it is attached/
       .test(F.flat(F.computedProvenanceHTML({ ...GP_ENTRY_ROMAN,
         booking_evidence: null }))));

  ok('the rail marks it reported like any other report',
     /reported<\/span>/.test(F.diaryRowHTML(roman, null, TODAY)));
  ok('...and the rail STILL carries no percentage, whichever way it arrived',
     !/%/.test(F.diaryRailHTML(joined, null, TODAY)),
     F.diaryRailHTML(joined, null, TODAY));
  ok('a past function absent from the feed still says "no report yet"',
     /no report yet/.test(F.diaryRowHTML(joined.find((f) => f.id === '1e871002336d'),
       null, TODAY)));
  ok('...and still offers the form, because the feed is not the only way in',
     /data-act="recordoutcome"/.test(
       F.functionCardHTML(joined.find((f) => f.id === '1e871002336d'), TODAY)));
  ok('...and a function still to come gets nothing at all, feed or no feed',
     F.outcomeHTML(F.joinComputedReports([MJ], GP_FEED)[0], TODAY) === '');
}

// ------------- THE CAVEATS RULE, ON THE FEED PATH: strip them, lose the number
// The rule already holds for a hand-recorded outcome. The feed is a SECOND way
// into the same renderer, and a second way in is a second chance for a bare
// percentage to reach a screen. Same assertion, other door.
{
  const stripped = F.joinComputedReports(DIARY,
    { functions: [{ ...GP_ENTRY_ROMAN, caveats: [] }] })
    .find((f) => f.id === 'e93280ad65d1');
  const card = F.functionCardHTML(stripped, TODAY);
  ok('a COMPUTED report with no caveats draws the refusal, not the percentage',
     /GP withheld/.test(card) && !/59\.3/.test(card) && !/65\.6/.test(card),
     card.slice(card.indexOf('How it went')));
  ok('...and the measured figures still show, because those are not in doubt',
     /239/.test(card) && /\$2,000/.test(card), card.slice(card.indexOf('How it went')));
  const gone = F.joinComputedReports(DIARY,
    { functions: [{ ...GP_ENTRY_ROMAN, caveats: undefined }] })
    .find((f) => f.id === 'e93280ad65d1');
  ok('...and a caveat list missing entirely behaves the same way',
     /GP withheld/.test(F.functionCardHTML(gone, TODAY)));
}

// ------------------------------------ two sources for one night, and one truth
// A night can now hold a hand-recorded report AND a computed one. The computed
// one wins — every figure on it is re-derivable from line items still in the
// repo, priced by a book pinned to the night, and the hand one is a summary
// somebody typed once. But a disagreement is a wrong MEASURED fact on one side
// or the other, so it is named rather than quietly resolved.
{
  const agreeing = F.joinComputedReports(
    [{ ...ROMAN, outcome: ROMAN_OUT, brief_id: 'b0b0b0b0b0b0' }], GP_FEED)[0];
  ok('when the two agree there is nothing to report but the report',
     F.reportFor(agreeing).clash.length === 0
     && F.reportFor(agreeing).source === 'computed');
  const card0 = F.functionCardHTML(agreeing, TODAY);
  ok('...and only ONE gross profit block is drawn',
     (card0.match(/class="gp"/g) || []).length === 1, card0);

  // The hand report says 240 drinks and $612.48 of COGS; the tab says 239 and
  // $625.48. One of them is wrong and neither the page nor anybody else can
  // tell which from here.
  const HAND_OFF = { ...ROMAN_OUT, drinks_poured: 240, cogs_ex_cents: 61248,
                     actual_heads: 25, gp_pct: 60.61 };
  const clashing = F.joinComputedReports(
    [{ ...ROMAN, outcome: HAND_OFF, brief_id: 'b0b0b0b0b0b0' }], GP_FEED)[0];
  const clash = F.reportFor(clashing).clash;
  ok('a disagreement is detected field by field',
     clash.length === 2 && clash.map((c) => c.field).sort().join(',')
       === 'cogs_ex_cents,drinks_poured', JSON.stringify(clash));
  ok('...and the computed one is the one drawn',
     F.reportFor(clashing).outcome === GP_ENTRY_ROMAN);
  ok('a field measured on only one side is a GAP, not a disagreement',
     F.reportClash({ drinks_poured: 239 }, GP_ENTRY_ROMAN).length === 0);

  const card = F.functionCardHTML(clashing, TODAY);
  const rep = F.flat(card.slice(card.indexOf('How it went')));
  ok('the clash is surfaced, not silently resolved',
     /Two reports exist for this night and they do not agree/.test(rep), rep);
  ok('...naming both sides of every measured fact they differ on',
     rep.includes('drinks poured — <b>239</b> computed, 240 hand-recorded')
     && rep.includes('drinks COGS — <b>$625.48</b> computed, $612.48 hand-recorded'),
     rep);
  ok('...and saying WHY the computed one wins: it can be run again',
     /worked out from the comped tab.s own line items/.test(rep), rep);
  ok('...above the figure, so nobody reads the number before the doubt',
     rep.indexOf('do not agree') < rep.indexOf('class="gp"'), rep);

  // THE RULE. One night, one percentage on the screen.
  ok('ONE gross profit figure for one night, never two',
     (card.match(/class="gp"/g) || []).length === 1, card);
  ok('...and the hand-entered percentage is nowhere on the card — it would be '
     + 'read back later without the caveats that qualify it, and it is the one '
     + 'nobody can reproduce',
     !/60\.6/.test(card), card.slice(card.indexOf('How it went')));
  ok('...while the computed one is, with its caveats',
     /59\.3%/.test(card) && /class="caveats"/.test(card));

  ok('a hand-recorded report still offers a correction',
     /Correct the figures/.test(F.outcomeHTML(clashing, TODAY)));
  const computedOnly = F.joinComputedReports([ROMAN], GP_FEED)[0];
  ok('a computed-only night offers first-time entry, not a correction — there '
     + 'are no typed figures to correct',
     /Record how it went/.test(F.outcomeHTML(computedOnly, TODAY))
     && !/Correct the figures/.test(F.outcomeHTML(computedOnly, TODAY)));
  ok('...and says plainly that typing them will not replace the figure above',
     /does not replace it/.test(F.flat(F.outcomeHTML(computedOnly, TODAY))),
     F.flat(F.outcomeHTML(computedOnly, TODAY)));
}

// --------------------------------------- a missing feed is not a broken screen
{
  ok('the feed is a static file on this origin, not a route on the engine',
     /GP_FEED_URL = '\/data\/functions_gp\.json'/.test(src), 'not found');
  ok('...fetched with the diary rather than after it',
     /loadGpFeed\(\),/.test(src) && /const \[briefs, chase, cfg, areas, diary, gp\]/.test(src));
  ok('...and every way it can fail returns null instead of throwing',
     /if \(!r\.ok\) return null;/.test(src) && /catch \(_\) \{ return null; \}/.test(src));
  ok('a feed declaring a schema this page does not know is refused whole, not '
     + 'half-read', /d\.schema === GP_FEED_SCHEMA \? d : null/.test(src));
  ok('the join runs once at load, not inside a renderer',
     /DIARY = joinComputedReports\(diary\.functions \|\| \[\], gp\)/.test(src));
}

// ------------------------------------------------ recording it: nine measured boxes
{
  const brief = { id: 'b0b0b0b0b0b0', name: 'Roman Bunting', ...{
    actual_heads: 25, tickets_sold: 25, revenue_inc_cents: 200000,
    food_revenue_inc_cents: null, drinks_poured: 239,
    menu_value_inc_cents: 359900, cogs_ex_cents: 62548,
    mixer_est_ex_cents: 11380, pos_refs: 'Tab Roman B 40th · receipt 1188213' } };
  const form = F.outcomeFormHTML(brief);

  ok('the form carries exactly nine boxes',
     (form.match(/data-outcome="1"/g) || []).length === 9,
     String((form.match(/data-outcome="1"/g) || []).length));
  ok('...and they are the nine MEASURED columns the engine holds, in its order',
     F.OUTCOME_INPUTS.map((f) => f.field).join(',')
       === 'actual_heads,tickets_sold,revenue_inc_cents,food_revenue_inc_cents,'
         + 'drinks_poured,menu_value_inc_cents,cogs_ex_cents,mixer_est_ex_cents,'
         + 'pos_refs',
     F.OUTCOME_INPUTS.map((f) => f.field).join(','));
  for (const derived of ['gp_pct', 'gross_profit', 'drinks_per_head',
                         'margin_foregone', 'out_earn_ratio', 'gp_pct_ex_mixer',
                         'cogs_ex_cents_per_head'])
    ok(`there is no box for ${derived} — it is derived, and a GP you can type `
       + 'is a GP nobody can reproduce',
       !F.OUTCOME_INPUTS.some((f) => f.field === derived) && !form.includes(derived));
  ok('...and the form says so, where somebody would otherwise go looking',
     /no box for the GP percentage/.test(form) && /nobody can reproduce/.test(form),
     form);
  ok('the boxes are prefilled from the raw columns, in the units they are typed in',
     /id="o_rev"[^>]*value="2000"/.test(form) && /id="o_heads"[^>]*value="25"/.test(form),
     form.slice(form.indexOf('o_rev') - 60, form.indexOf('o_rev') + 120));
  ok('...and an unmeasured column is an empty box, not a zero',
     /id="o_food"[^>]*value=""/.test(form), form.slice(form.indexOf('o_food') - 40));
  ok('the food box says it is a SPLIT of the revenue, not an addition to it',
     /Part of the line above, never on top of it/.test(form), form);
  ok('the mixer box says it is an estimate before anybody types one',
     /id="o_mixer"[\s\S]{0,400}?An estimate/.test(form), form.slice(form.indexOf('o_mixer')));
  ok('pos_refs is free text and explains why it is worth filling in',
     /reproducible/.test(form) && /somebody’s word/.test(form), form);
  ok('...and the same explanation is on the report, not only on the form',
     F.POS_REFS_WHY.length > 80
     && F.functionCardHTML(ROMAN_DONE, TODAY).includes(esc0(F.POS_REFS_WHY)));
  ok('a function with no references recorded says the trail is missing',
     /nothing here can be traced back to a receipt/
       .test(F.functionCardHTML({ ...ROMAN_DONE,
         outcome: { ...ROMAN_OUT, pos_refs: null } }, TODAY)));
}

// ------------------------------------------------------- what a Save sends
{
  const boxes = { o_heads: '25', o_tickets: '25', o_rev: '2000', o_food: '',
                  o_drinks: '239', o_menu: '3599', o_cogs: '625.48',
                  o_mixer: '113.80', o_refs: '  receipt 1188213  ' };
  const body = F.outcomeBody({ id: 'b1', name: 'Roman Bunting' }, (id) => boxes[id]);
  ok('the name rides along, because upsert needs it', body.name === 'Roman Bunting');
  ok('dollars typed become CENTS on the wire', body.revenue_inc_cents === 200000
     && body.cogs_ex_cents === 62548 && body.mixer_est_ex_cents === 11380,
     JSON.stringify(body));
  ok('counts stay counts', body.actual_heads === 25 && body.drinks_poured === 239);
  ok('a blank box is OMITTED, so it reads as unmeasured rather than as zero',
     !('food_revenue_inc_cents' in body), JSON.stringify(body));
  ok('references are trimmed but otherwise kept verbatim',
     body.pos_refs === 'receipt 1188213');
  ok('a typed dollar sign or comma does not silently become a different number',
     F.outcomeBody({ name: 'X' }, () => '$3,599').revenue_inc_cents === 359900);
  ok('nothing derived can ever be in the body',
     !Object.keys(F.outcomeBody({ name: 'X' }, (id) => boxes[id]))
       .some((k) => /gp|profit|margin|ratio|per_head/.test(k)),
     Object.keys(F.outcomeBody({ name: 'X' }, (id) => boxes[id])).join(','));
  ok('zero IS a measurement and is sent',
     F.outcomeBody({ name: 'X' }, (id) => (id === 'o_drinks' ? '0' : ''))
       .drinks_poured === 0);
  ok('a value already on file comes back out in dollars',
     F.outcomeFieldValue({ revenue_inc_cents: 200000 }, F.OUTCOME_INPUTS[2]) === '2000');
}

// ------------------------------- the two-step, made one step, for a booking with no brief
{
  ok('the engine route that gives a booking a brief is the one it calls',
     /\/api\/admin\/functions\/diary\/\$\{bookingId\}\/brief/.test(src), 'not called');
  const noBrief = F.outcomeHTML(ROMAN, TODAY);          // brief_id null
  ok('a past function with no brief still offers ONE action, not two',
     (noBrief.match(/data-act="recordoutcome"/g) || []).length === 1, noBrief);
  ok('...and it is not called "create a brief" — nobody wants to do that',
     !/create a brief|make a brief/i.test(noBrief) && /Record how it went/.test(noBrief),
     noBrief);
  ok('...but it says what it will do, so the extra record is not a surprise',
     F.flat(noBrief).includes('no brief behind this booking yet, so this makes '
       + 'one and opens the form in the same step'), F.flat(noBrief));
  const withBrief = F.outcomeHTML({ ...ROMAN, brief_id: 'b0b0b0b0b0b0' }, TODAY);
  ok('a function that already has a brief goes straight to the form',
     !/makes one/.test(withBrief) && /data-act="recordoutcome"/.test(withBrief));
  ok('a booking that already has a brief made by somebody else mid-click is '
     + 'followed to it rather than failing',
     /e\.detail && e\.detail\.brief_id/.test(src), 'no 409 recovery');
  ok('a reported function offers a correction, not a second first-time entry',
     /Correct the figures/.test(F.outcomeHTML(ROMAN_DONE, TODAY)));

  const open = F.functionCardHTML(ROMAN_DONE, TODAY,
    { booking: ROMAN.id, brief: { id: 'b0b0b0b0b0b0', name: 'Roman Bunting' } });
  ok('the form opens on the card it belongs to', /data-act="saveoutcome"/.test(open));
  ok('...and only on that one',
     !/data-act="saveoutcome"/.test(F.functionCardHTML(HARRY_DONE, TODAY,
       { booking: ROMAN.id, brief: { id: 'x', name: 'y' } })));
  ok('...and can be abandoned', /data-act="canceloutcome"/.test(open));
  ok('the panel passes the open form through to the card it is on',
     /data-act="saveoutcome"/.test(F.diaryPanelHTML([ROMAN_DONE], BY_DATE, '2026-08',
       null, ROMAN.id, TODAY, null,
       { booking: ROMAN.id, brief: { id: 'b', name: 'n' } })));
}

// ------------------------------------------------------- escaping, once more
{
  const nastyOut = { ...ROMAN_OUT,
    pos_refs: 'Tab <img src=x onerror=alert(1)> · "receipt"',
    gp_basis: '<b>beverage</b>',
    caveats: [{ code: '<script>alert(1)</script>', note: '<img src=y>',
                effect: '<img src=z>' }] };
  const card = F.functionCardHTML({ ...ROMAN_DONE, outcome: nastyOut }, TODAY);
  ok('a POS reference cannot inject markup', !card.includes('<img src=x'), card);
  ok('a caveat cannot either',
     !card.includes('<script>alert(1)</script>') && !card.includes('<img src=y'), card);
  ok('nor can the basis the server states', !card.includes('<b>beverage</b>'), card);
  const form = F.outcomeFormHTML({ id: 'b', name: 'X',
    pos_refs: '"><script>alert(1)</script>' });
  ok('...nor a value already on file, coming back into an input',
     !form.includes('<script>alert(1)</script>'), form.slice(form.indexOf('o_refs')));
}

// ------------------------------------------------ the shell grew CSS, not logic
{
  for (const cls of ['.gp', '.caveats', '.mets', '.oform'])
    ok(`the shell styles ${cls}`, html.includes(cls + '{') || html.includes(cls + ' '),
       cls);
  ok('the report block is styled from tokens, never from a hex colour',
     !/#[0-9a-fA-F]{3,8}\b/.test(html.slice(html.indexOf('<style>'),
                                            html.indexOf('</style>'))),
     (html.slice(html.indexOf('<style>'), html.indexOf('</style>'))
       .match(/#[0-9a-fA-F]{3,8}\b/) || [''])[0]);
  ok('the caveats are not a tooltip, a title= or a collapsible anywhere in the '
     + 'module', !/title="[^"]*caveat/i.test(src) && !/<details/.test(src)
       && !/<summary/.test(src));
}

console.log(`\n${n} functions-page assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
