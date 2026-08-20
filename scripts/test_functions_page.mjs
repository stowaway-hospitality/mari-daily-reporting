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
  ok('MUTATION: filing a stranded brief under Confirmed would be caught',
     F.groupRail([{ id: 's', stage: 'enquiry', deposit_status: 'paid',
                    missing: [], problems: [] }], []).held.length === 0);
}

console.log(`\n${n} functions-page assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
