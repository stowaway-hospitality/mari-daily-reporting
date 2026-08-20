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

console.log(`\n${n} functions-page assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
