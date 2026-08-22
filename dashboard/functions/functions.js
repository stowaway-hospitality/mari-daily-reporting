/**
 * Functions module — all logic for /functions/ (the HTML is a shell).
 *
 * WHAT THIS IS
 * ------------
 * Three screens behind one segmented control, because "what is on this
 * Saturday", "who have I not chased" and "who has actually paid" are different
 * questions:
 *
 *   DIARY    — function BOOKINGS: rooms actually held, a month calendar of the
 *              dates that have functions on them, and what has already run.
 *              See the diary section below for why this half had to be added.
 *   PIPELINE — every ENQUIRY on the monday.com FUNCTIONS ENQUIRY TRACKER, read
 *              from data/functions_pipeline.json: whose move it is with the
 *              log line that says so, what is outstanding, and a hand-off into
 *              a brief when it is time to take money.
 *   BRIEFS   — function BRIEFS: the booking engine's own table, one brief in
 *              the panel, every field editable, and a live minimum-spend quote
 *              that explains itself.
 *
 * PIPELINE used to BE the briefs list, and the briefs table has always been
 * empty, so the tab said "Pipeline 0" while sixty enquiries sat on the board.
 * The two are now separate because they are two things: an enquiry is somebody
 * asking, and a brief is the paperwork that holds a room.
 *
 * It already existed, at /admin/functions on the booking engine itself, and it
 * was in the wrong place twice over: staff never open the Render app, and it
 * asked for the service token in a box. Nothing about the SERVER moved — every
 * route called below is the same route that page called. What moved is the
 * client, onto the platform's own auth and its own design tokens.
 *
 * AUTH — two layers, and staff should never see a token box:
 *   1. Supabase gate decides who may open the page.
 *   2. The booking engine's bearer token is the REAL auth, verified by the
 *      service on every call. It is fetched automatically via
 *      Auth.bookingToken() from the Supabase app_config table (RLS: readable
 *      by authenticated users only), so a signed-in user is simply in.
 *      The manual paste box is a FALLBACK for when that lookup fails — it is
 *      not the normal path, and it must not become one.
 *
 * WHY SO MUCH IS EXPORTED
 * -----------------------
 * The half of this file that decides things — what a booking costs, which of
 * the package total and the minimum spend binds, which rail group a brief
 * lands in, whose move it is next — is pure and takes its config as an
 * argument. scripts/test_functions_page.mjs imports it and drives it directly,
 * because every one of those is a number or a sentence a client is given, and
 * a wrong one fails nothing else in this repo: it just ships.
 */
import { Auth } from '/_shared/auth.js';

// The booking engine. Same constant, same service, same mechanism as
// dashboard/bookings/bookings.js — a second base URL here would be a second
// thing to change the day the service moves.
const API = 'https://stowaway-bookings.onrender.com';
// Deliberately the SAME localStorage key as /bookings/: it is one service
// token for one engine, so a fallback paste on one page connects the other.
const TOKEN_KEY = 'stowaway_booking_token';

const $ = (id) => document.getElementById(id);

let BRIEFS = [], CHASE = [], CONFIG = {}, AREAS = [], CUR = null;
// The enquiry pipeline: the monday board as a published feed, which enquiry is
// open in the panel, and what went wrong if it could not be read. PIPE_ERR is
// held apart from "no enquiries" on purpose -- see pipeMissingHTML.
let PIPE = null, PIPE_ERR = null, PIPE_SEL = null;
// The diary half: the bookings, the server's per-date rollup that drives the
// calendar, and where in it we are looking. DIARY_ERR is held separately from
// "no functions" on purpose — see diaryPanelHTML.
let DIARY = [], BYDATE = [], DIARY_ERR = null;
// The night being recorded, if one is: { booking, brief }. Module state
// rather than DOM state because recording an outcome on a booking with no
// brief creates one first, and the reload that follows redraws the panel
// wholesale -- a form living only in the markup would be thrown away
// between the two halves of what the user thinks is one action.
let OUTCOME_EDIT = null;
// THREE halves now, not two. See the pipeline section: `pipeline` is the
// monday enquiry tracker and `briefs` is the booking engine's own table -- the
// thing a deposit is taken against. They were one tab, reading the empty one.
let MODE = 'diary', MONTH = null, SEL_DATE = null, SEL_FN = null;
let DIRTY = false;
let SVC = null;   // service token held in memory for this session

// The pickers. These are the SHAPES functions.py accepts (FOOD_CHOICES,
// DRINK_CHOICES, ARRIVAL_ADDONS); the PRICES are never copied here — they come
// off /api/admin/functions/config, because two copies of a price drift and the
// copy that drifts is the one a client gets quoted.
export const FOODS = ['set menu', 'grazing', 'shared', 'pizza', 'asian feast'];
export const DRINKS = ['SHIN-DIGG', 'SOIRÈE', 'RAZZLE DAZZLE', 'bar tab'];
export const ADDONS = ['classic cocktail', 'signature cocktail', 'veuve'];
export const DEPOSIT_METHODS = ['eftpos', 'phone', 'transfer', 'cash'];

// A brief chased inside this many days is somebody else's move to make. Four
// days is about how long a follow-up can wait before a second email stops
// being a follow-up and starts being a nag.
export const ASKED_FRESH_DAYS = 4;

// Every string below is drawn from a client's own words — a name, an occasion,
// a note somebody typed down the phone — so it is escaped on the way into the
// DOM without exception.
export const esc = (s) => (s == null ? '' : String(s))
  .replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export const money = (c) => (c == null ? '—'
  : '$' + (c / 100).toLocaleString('en-AU', { maximumFractionDigits: 0 }));
// Cakeage is $1.50 a head. money() rounds to whole dollars and would print
// that as "$2", which is a wrong price rather than a tidy one.
export const money2 = (c) => (c == null ? '—'
  : '$' + (c / 100).toLocaleString('en-AU',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

// ------------------------------------------------------------------- dates
/** Today in Sydney as YYYY-MM-DD. sv-SE because it formats that way, so the
 *  anchor is parsed by exactly the same rule as the target rather than by a
 *  second one. */
export const todayInSydney = () =>
  new Date().toLocaleDateString('sv-SE', { timeZone: 'Australia/Sydney' });

/** Whole days between two ISO dates, both anchored at NOON.
 *
 *  An earlier version of this compared Sydney's MIDNIGHT with the target's
 *  noon, so every difference came out as a whole number of days plus a half
 *  and Math.round broke the tie away from zero: today read as "1d", tomorrow
 *  as "2d", yesterday as "0", and the "today" chip could never fire at all.
 *  Noon against noon makes the difference a whole number, which leaves
 *  Math.round doing the job it is actually here for — absorbing the one hour
 *  either side of a daylight-saving change. */
export function daysBetween(fromIso, toIso) {
  if (!fromIso || !toIso) return null;
  return Math.round((new Date(toIso + 'T12:00:00') - new Date(fromIso + 'T12:00:00'))
                    / 86400000);
}
export const daysAway = (iso, today) => daysBetween(today || todayInSydney(), iso);
export const daysSince = (iso, today) => {
  const d = daysAway(iso, today);
  return d === null ? null : -d;
};

export function askedLabel(iso, today) {
  const n = daysSince(iso, today);
  if (n === null) return 'never asked';
  if (n <= 0) return 'asked today';
  if (n === 1) return 'asked yesterday';
  return `asked ${n} days ago`;
}

export function niceDate(iso) {
  if (!iso) return 'no date';
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-AU',
    { weekday: 'short', day: 'numeric', month: 'short' });
}
export const dayName = (iso) => new Date(iso + 'T12:00:00')
  .toLocaleDateString('en-AU', { weekday: 'long' });

export function hhmmLabel(t) {
  if (!t) return '';
  let [h, m] = String(t).split(':').map(Number);
  const ap = h >= 12 ? 'pm' : 'am';
  h = h % 12 || 12;
  return m ? `${h}:${String(m).padStart(2, '0')}${ap}` : `${h}${ap}`;
}

// ------------------------------------------------------------------ pricing
/**
 * The minimum spend, and WHY. Recomputed in the browser so the number moves as
 * she types rather than after a save; the server stays the authority, and this
 * must keep agreeing with functions.is_peak().
 *
 * The `why` string is the point of the whole function. "Why is this $75 a
 * head?" was the question nobody could answer off a spreadsheet, and a quote
 * that cannot explain itself gets argued with.
 */
export function priceOf(b, cfg = CONFIG) {
  const heads = b.final_numbers || b.guests;
  if (!b.date || !heads) return { text: 'Needs a date and a guest count', unknown: true };
  const wd = new Date(b.date + 'T12:00:00').getDay();       // 0=Sun … 6=Sat
  const isFriSat = wd === 5 || wd === 6;
  const isPH = (cfg.public_holidays || []).includes(b.date);
  const [w0, w1] = (cfg.peak_window || '18:00-21:00').split('-')
                     .map((s) => Number(s.split(':')[0]));
  const base = cfg.base_rate_cents || 5500;
  const peak = cfg.peak_rate_cents || 7500;
  const dur = cfg.default_duration_hours || 3;
  const h12 = (h) => (h > 12 ? h - 12 : h);

  if (!isFriSat && !isPH) {
    return { cents: base * heads, rate: base, heads,
             why: `${dayName(b.date)} — standard rate.` };
  }

  const dayWhy = isPH ? 'public holiday' : dayName(b.date);
  if (!b.start_time) {
    // Refuse to guess. functions.is_peak() raises here for the same reason: a
    // blank start time prompts a question, a defaulted one sends a wrong quote.
    return { unknown: true,
             text: `Can't price a ${dayWhy} without a start time`,
             why: `The ${money(peak)}pp rate depends on whether the booking `
                + `touches ${h12(w0)}–${h12(w1)}pm. Ask what time they're starting.` };
  }

  const [h, m] = b.start_time.split(':').map(Number);
  const s = h + m / 60, e = s + dur;
  const hit = s < w1 && e > w0;
  const win = `${h12(w0)}–${h12(w1)}pm`;
  const span = `${hhmmLabel(b.start_time)}–${hhmmLabel(
      String(Math.floor(e % 24)).padStart(2, '0') + ':' +
      String(Math.round((e % 1) * 60)).padStart(2, '0'))}`;
  return {
    cents: (hit ? peak : base) * heads, rate: hit ? peak : base, heads,
    why: hit
      ? `${dayWhy}, and ${span} overlaps the ${win} window.`
      : `${dayWhy}, but ${span} finishes before the ${win} window opens — standard rate.`,
  };
}

/**
 * Package total against the minimum spend.
 *
 * The minimum spend answers "what is the least they may pay"; the package
 * answers "what will they actually pay", and on most package bookings those
 * are different numbers. Thirty heads on RAZZLE DAZZLE reads $1,650 minimum
 * spend with nothing saying the package alone is $2,400 — that gap is the most
 * commercially useful sum on this screen.
 */
export function packageMaths(b, cfg = CONFIG) {
  const heads = b.final_numbers || b.guests;
  if (!b.drink || !heads) return null;
  const floor = b.min_spend_is_agreed ? b.min_spend_cents
                                      : (priceOf(b, cfg).cents ?? null);
  const cakeagePP = cfg.cakeage_cents_pp;
  if (b.drink === 'bar tab') return { tab: true, heads, floor, cakeagePP };
  const pp = (cfg.drink_packages || {})[b.drink];
  if (pp == null) return null;          // a package this server doesn't price
  const addon = b.arrival_addon || '';
  const addonPP = addon ? ((cfg.arrival_addons || {})[addon] ?? 0) : 0;
  return { tab: false, heads, pkg: b.drink, pp, addon, addonPP,
           total: (pp + addonPP) * heads, floor, cakeagePP };
}

export function sumsHTML(m) {
  if (!m) return '';
  // Cakeage is STATED, never added in. It applies only if they bring a cake,
  // and a total that quietly assumes they will is a total she has to correct.
  const cake = m.cakeagePP
    ? `<div class="aside">Cakeage is ${money2(m.cakeagePP)} a head on top —
       ${money(m.cakeagePP * m.heads)} for ${m.heads} — and only if they bring
       a cake, so it is not in the figures above.</div>`
    : '';
  if (m.tab) {
    return `<div class="sums">
      <div><b>Bar tab</b> — open-ended.</div>
      <div class="binds">${m.floor == null
        ? 'No minimum spend to compare it against yet.'
        : `The ${money(m.floor)} minimum spend is the floor and the tab has no
           ceiling, so anything they run past that is on top.`}</div>${cake}</div>`;
  }
  const line = `<div><b>${money(m.total)}</b> on the ${esc(m.pkg)} package —
      ${m.heads} × ${money(m.pp)}pp${m.addonPP
        ? ` plus ${money(m.addonPP)}pp for the ${esc(m.addon)} on arrival` : ''}.</div>`;
  const binds = m.floor == null
    ? `<div class="binds">No minimum spend to compare yet — that needs a date,
       a headcount and a start time.</div>`
    : m.total >= m.floor
      ? `<div class="binds">The package is what they'll actually pay: it clears
         the ${money(m.floor)} minimum spend by ${money(m.total - m.floor)}.</div>`
      : `<div class="binds">The ${money(m.floor)} minimum spend is what binds —
         the package is ${money(m.floor - m.total)} short of it, so they would
         be topping up.</div>`;
  return `<div class="sums">${line}${binds}${cake}</div>`;
}

// --------------------------------------------------------------------- rail
/** Anything still to get out of the CLIENT. The $100 deposit is deliberately
 *  excluded: it has its own card, its own buttons and its own badge, and
 *  chasing money is a different errand from chasing facts. */
export const outstanding = (b) => (b.missing || []).some((m) => m !== '$100 deposit')
                               || (b.problems || []).length > 0;

/** Whose move is it — 'onme', 'onthem', or neither. */
export function whoseMove(b, today) {
  if (!outstanding(b)) return '';
  const n = daysSince(b.last_asked, today);
  return (n !== null && n <= ASKED_FRESH_DAYS) ? 'onthem' : 'onme';
}

/**
 * One pass, one bucket each.
 *
 * Four independent filters put a paid-but-dateless brief in BOTH `held` and
 * `nodate` — the same enquiry drawn twice in one rail — and that state is
 * reachable, not theoretical: /confirm writes deposit_status 'paid' and only
 * then calls hold_the_room(), which can raise. An if/else chain makes the
 * groups exclusive by construction instead of by four predicates happening to
 * agree with each other.
 *
 * `stranded` is tested first and drawn first because paid-with-no-room is the
 * worst state a brief can reach: the money is taken, the client believes they
 * have a venue, and nothing holds the floor. It must never appear under
 * "Confirmed".
 */
export function groupRail(briefs, chase) {
  const chaseIds = new Set((chase || []).map((c) => c.id));
  const order = (id) => {
    const i = (chase || []).findIndex((c) => c.id === id);
    return i < 0 ? 9e9 : i;
  };
  const live = briefs.filter((b) => !['lost', 'done'].includes(b.stage));
  const past = briefs.filter((b) => ['lost', 'done'].includes(b.stage));
  const needs = [], stranded = [], held = [], waiting = [], nodate = [];
  for (const b of live) {
    if (b.deposit_status === 'paid' && !b.booking_id) stranded.push(b);
    else if (chaseIds.has(b.id)) needs.push(b);
    else if (b.deposit_status === 'paid') held.push(b);
    else if (!b.date) nodate.push(b);
    else waiting.push(b);
  }
  needs.sort((a, b) => order(a.id) - order(b.id));
  return { stranded, needs, waiting, held, nodate, past, live };
}

export function chipsFor(b, today) {
  const out = [];
  const d = daysAway(b.date, today);
  if (d !== null && d >= 0 && d <= 7) {
    out.push(`<span class="chip soon">${d === 0 ? 'today' : d + 'd'}</span>`);
  }
  // Said only on a row that still wants something. "never asked" against a
  // brief with nothing outstanding is noise, and noise is what makes a rail of
  // forty rows unreadable.
  if (outstanding(b)) {
    const n = daysSince(b.last_asked, today);
    const cls = (n !== null && n <= ASKED_FRESH_DAYS) ? 'asked' : 'need';
    out.push(`<span class="chip ${cls}">${esc(askedLabel(b.last_asked, today))}</span>`);
  }
  (b.problems || []).forEach((p) =>
    out.push(`<span class="chip bad">${esc(p.split('—')[0].trim().slice(0, 28))}</span>`));
  (b.missing || []).forEach((m) => {
    if (m === '$100 deposit') return;
    out.push(`<span class="chip need">${esc(m.split('(')[0].trim())}</span>`);
  });
  if (b.deposit_status === 'paid') out.push('<span class="chip ok">deposit paid</span>');
  else if (b.deposit_status === 'sent') out.push('<span class="chip">link sent</span>');
  return out.join('');
}

export function rowHTML(b, selectedId, today) {
  // The chips say WHAT is missing. The left edge says whose move it is, which
  // is the question a glance down forty rows is actually asking.
  const own = whoseMove(b, today);
  const on = selectedId === b.id ? ' on' : '';
  return `<div class="row ${own}${on}" data-open="${esc(b.id)}" role="button" tabindex="0">
    <div class="l1"><span class="nm">${esc(b.name)}</span>
      <span class="when">${esc(niceDate(b.date))}${
        b.start_time ? ' · ' + esc(hhmmLabel(b.start_time)) : ''}</span></div>
    <div class="l2">${chipsFor(b, today)}</div></div>`;
}

/** The rail's markup, given a filter box's contents. Pure, so the grouping and
 *  the ordering can be asserted without a browser. */
export function railHTML(briefs, chase, query, selectedId, today) {
  const raw = query || '';
  const q = raw.toLowerCase();
  const match = (b) => !q ||
    (b.name + ' ' + (b.occasion || '') + ' ' + (b.date || '')).toLowerCase().includes(q);
  const g = groupRail(briefs.filter(match), chase);
  const grp = (title, arr, hint) => (!arr.length ? '' :
    `<div class="grp">${title}<span class="n">${arr.length}</span></div>` +
    (hint ? `<div class="hint" style="margin:-2px 0 6px">${hint}</div>` : '') +
    arr.map((b) => rowHTML(b, selectedId, today)).join(''));
  return `<div><label for="q">Search</label><input id="q" style="width:100%"
      placeholder="name, occasion, date" value="${esc(raw)}"></div>` +
    grp('Paid — room NOT held', g.stranded,
        'Deposit taken and nothing is holding the floor. These first.') +
    grp('Needs something', g.needs, 'Soonest first. This is the list.') +
    grp('Quoted, no deposit', g.waiting) +
    grp('Confirmed', g.held) +
    grp('No date yet', g.nodate) +
    grp('Lost / done', g.past) +
    (g.live.length ? '' : '<div class="empty">Nothing live.</div>');
}

// ------------------------------------------------------- the two copy buttons
// They exist because the reply still goes out of Outlook. Nobody should have to
// retype a number the app already knows.
export function quoteText(b, cfg = CONFIG) {
  const p = priceOf(b, cfg);
  const heads = b.final_numbers || b.guests;
  const spend = b.min_spend_is_agreed ? b.min_spend_cents : p.cents;
  return p.unknown
    ? `For ${heads || 'your group'} on ${b.date ? niceDate(b.date) : 'the date'} — `
      + 'I need a start time before I can confirm the minimum spend.'
    : `For ${heads} guests on ${niceDate(b.date)}`
      + `${b.start_time ? ' from ' + hhmmLabel(b.start_time) : ''}, `
      + `you're looking at a minimum spend of ${money(spend)}.`;
}

const ASK_WORDING = {
  'date': "which date you're after",
  'start time': "what time you'd like to start",
  'room': 'which space suits you',
  'guest count': 'roughly how many guests',
  'food choice': "which food option you'd like",
  'drink choice': "whether you'd like a drinks package or a bar tab",
};

/** The outstanding list turned into one sentence a human can send. Returns ''
 *  when there is nothing to ask, so the caller can say so rather than paste an
 *  empty line into an email. */
export function askText(b) {
  const gaps = (b.missing || []).filter((m) => m !== '$100 deposit').map((m) => {
    for (const k in ASK_WORDING) if (m.startsWith(k)) return ASK_WORDING[k];
    if (m.startsWith('bar tab terms')) return 'whether the bar tab is open or restricted';
    // The minimum spend is OUR sum, not something to ask them for.
    if (m.startsWith('minimum spend')) return null;
    return m;
  }).filter(Boolean);
  if (!gaps.length) return '';
  const list = gaps.length === 1 ? gaps[0]
    : gaps.slice(0, -1).join(', ') + ' and ' + gaps[gaps.length - 1];
  return `Just need to lock in ${list} and we're set.`;
}

/** The rooms the picker may offer.
 *
 *  /api/admin/areas answers "what does the floor plan have units for" and has
 *  never sent a bookable_for_functions field, so filtering on it was true of
 *  every row and the picker offered the lot — including "Whole venue", which
 *  functions.validate() then rejects as an unknown area. Offering a room the
 *  save refuses is worse than not offering it. What may be shown is the
 *  INTERSECTION: a unit the engine can hold AND an area a brief may name.
 *
 *  Whatever is already on file stays visible even when it is not offerable, so
 *  the panel can't quietly show "not chosen" for a room that IS recorded.
 *  validate() flags it as a problem in the card above, which is the honest
 *  outcome — a wrong answer shown and named beats a right one hidden. */
export function areaOptions(areas, cfg = CONFIG, current) {
  const ids = (areas || []).map((a) => a.id);
  const accepted = cfg.accepted_areas || [];
  const out = accepted.length ? ids.filter((id) => accepted.includes(id)) : ids;
  if (current && !out.includes(current)) out.unshift(current);
  return out;
}

// ====================================================================== diary
// WHY THIS HALF EXISTS
// --------------------
// A brief is an enquiry. A booking is a room actually held. They are not the
// same record and most of the second kind have none of the first: the
// confirmed functions in the book were pushed in from the Monday tracker, so
// every one of them carries brief_id null. The rail listed briefs and nothing
// else, which meant this screen reported an empty diary while a confirmed
// 35-person engagement party sat on 25 October. Nothing was broken — the page
// was answering a different question from the one being asked of it.
//
// So there are two halves and they are never mixed:
//   DIARY    — GET /api/admin/functions/diary. What is booked. Rooms held.
//   PIPELINE — the briefs list above. What is being worked.
//
// They are a segmented control rather than two regions on one screen because
// they are read at different moments and by different questions. "What is on
// this Saturday" and "who have I not chased" share a name and nothing else,
// and stacking them means the answer to whichever one you came for is always
// half a screen down. Tabs across the top of a rail would have hidden the
// switch inside one column; a control beside the title is the first thing on
// the page, so which question the screen is answering is never a mystery.
// Diary is the default: it is the half that was invisible, and it is the one
// somebody standing in the venue needs.
//
// A booking with NO BRIEF is the normal case and is drawn as a complete
// record, never as a half-entered one. Where a booking does have a brief the
// two link to each other, both ways.

/** The floor of the diary window.
 *
 *  A fixed date rather than "today minus N months". A rolling window silently
 *  drops the oldest function out of the historic section and nothing says it
 *  happened — the same truncation the diary route refuses to do at the server
 *  end, where `to` is deliberately open. There are single-digit numbers of
 *  functions a month; there is nothing here to protect against. 2019 predates
 *  the booking engine, so this asks for everything there has ever been. */
export const DIARY_FROM = '2019-01-01';

/** Monday first. This is a venue diary, not an American calendar: a function
 *  week runs to Saturday, and putting Sunday at the head splits the weekend
 *  across two rows of the grid. */
export const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export const ym = (iso) => String(iso || '').slice(0, 7);

/** Month arithmetic on 'YYYY-MM' strings, without a Date in sight. new
 *  Date(y, m + 1) rolls the year for you but also drags a timezone and a day
 *  of month along, and the day of month is what breaks on the 31st. */
export function shiftMonth(m, n) {
  const [y, mo] = String(m).split('-').map(Number);
  const t = y * 12 + (mo - 1) + n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}

export const monthLabel = (m) => new Date(m + '-01T12:00:00')
  .toLocaleDateString('en-AU', { month: 'long', year: 'numeric' });

/** The cells of a month grid: nulls for the padding either side, ISO dates
 *  for the days. Whole weeks, so the grid never ends ragged. */
export function monthCells(m) {
  const [y, mo] = String(m).split('-').map(Number);
  const lead = (new Date(Date.UTC(y, mo - 1, 1)).getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  const cells = new Array(lead).fill(null);
  for (let d = 1; d <= days; d++) cells.push(`${m}-${String(d).padStart(2, '0')}`);
  while (cells.length % 7) cells.push(null);
  return cells;
}

/** by_date comes off the wire as a sorted ARRAY. Keyed here for lookup and
 *  nowhere else: the calendar is drawn from the server's rollup rather than
 *  from a second grouping of `functions`, because two groupings are two things
 *  that can disagree, and a calendar that disagrees with the list under it is
 *  worse than no calendar. */
export const byDateMap = (byDate) => Object.fromEntries(
  (byDate || []).map((s) => [s.date, s]));

/** Coming up, and already happened. A function ON today is still coming up —
 *  it is tonight's work, not history. */
export function splitDiary(fns, today) {
  const t = today || todayInSydney();
  const upcoming = (fns || []).filter((f) => f.date >= t)
    .slice().sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
  const past = (fns || []).filter((f) => f.date < t)
    .slice().sort((a, b) => (b.date + b.time).localeCompare(a.date + a.time));
  return { upcoming, past };
}

// ------------------------------------------------------ reading the notes
// The notes field is the REAL record. It carries the occasion, the room
// restated in prose, the drinks and food, how the money is being collected,
// how the headcount moved, and what is still open. On two of the four
// functions live today it also contradicts the structured columns. So it is
// shown in full, never summarised, and three narrow readings are taken off it
// — each one answering a question the columns answer wrongly.

/** Sentences, near enough. Split on line breaks and full stops so a flag can
 *  quote the fragment it fired on rather than the whole paragraph. No
 *  lookbehind: Safari only learned it in 16.4 and this runs on the venue iPad. */
export const noteSegments = (notes) => String(notes || '')
  .split(/\n+|\.\s+/).map((s) => s.trim()).filter(Boolean)
  // The leading "FUNCTION - " is a marker, not content: it is how a row gets
  // recognised as a function in the first place. Quoting it back inside a
  // sentence about the headcount reads as noise. The note itself is drawn
  // from the raw field and keeps it.
  .map((s, i) => (i === 0 ? s.replace(/^FUNCTION\s*[-–—]\s*/i, '') : s))
  .filter(Boolean);

const DOUBT = /\b(tbc|tba|placeholder|provisional|tentative|to be confirmed|not confirmed|unconfirmed)\b/i;
const TIMEISH = /\b(time|start|starts|starting|kick[- ]?off)\b/i;
const OPEN_Q = /\b(tbc|tba|to confirm|to be confirmed|not (?:yet |re-)?confirmed|unconfirmed|assumed|placeholder|provisional)\b|[-–—]\s*confirm\.?$/i;
const PAXISH = /\b(pax|guests?|people|heads?|numbers?)\b/i;

/**
 * Is the stored start time actually agreed?
 *
 * 25 October says `time: "18:00"` and the note says
 * "*** START TIME TBC (placeholder 6pm) ***". Printing "6pm" off the column is
 * printing a time nobody agreed to, and it is the kind of wrong that gets
 * repeated down a phone before anyone checks.
 *
 * A general rule and not a special case, because the next one will be worded
 * differently: a segment of the note that talks about TIME and hedges is a
 * start time that is not settled. Deliberately narrow — it wants both halves,
 * so "20 pax on file, not re-confirmed" is a headcount question and not a time
 * one, and "START 6:30PM - confirmed by Di via text" stays a firm time.
 *
 * Returns the client's own words, so the screen quotes rather than paraphrases.
 */
export function timeDoubt(notes) {
  for (const s of noteSegments(notes)) {
    if (DOUBT.test(s) && TIMEISH.test(s)) return s;
  }
  return null;
}

/** Everything in the note still hanging: "- confirm", "Steph to confirm",
 *  "SPACE ASSUMED", "not re-confirmed", "TBC". These are the questions a
 *  function fails on, and they are invisible in a structured field because
 *  there is no structured field for them. */
export const openQuestions = (notes) =>
  noteSegments(notes).filter((s) => OPEN_Q.test(s));

/**
 * What the note says about numbers, beside what the booking says.
 *
 * Harry Baker is `adults: 25` under a note reading "15-20 pax". One of those
 * is wrong and this screen has no way to know which, so it must not present
 * the column as if it settles it. Quote the note; flag the disagreement; let
 * the person who can ring them decide.
 */
export function headcountSays(f) {
  const quotes = noteSegments(f && f.notes).filter((s) => PAXISH.test(s));
  const nums = quotes.flatMap((s) => (s.match(/\d+/g) || []).map(Number));
  return { quotes, disagrees: quotes.length > 0 && !nums.includes(f.covers) };
}

// ------------------------------------------------------------- when, and how long
export function endTime(time, holdMinutes) {
  if (!time || !holdMinutes) return '';
  const [h, m] = String(time).split(':').map(Number);
  const t = h * 60 + m + holdMinutes;
  return String(Math.floor(t / 60) % 24).padStart(2, '0') + ':'
       + String(t % 60).padStart(2, '0');
}

/** "4 hours". Stated as a duration and never compared to anything.
 *
 *  Two of the four live functions hold for 240 minutes where the app writes
 *  180. That is not a fault, an override or a signal — it is what somebody
 *  typed when the tracker rows were pushed in — and a screen that called it
 *  "longer than usual" would invent a meaning it does not have. */
export function holdLabel(mins) {
  if (!mins) return '';
  const h = Math.floor(mins / 60), m = mins % 60;
  return [h ? `${h} hour${h === 1 ? '' : 's'}` : '', m ? `${m} min` : '']
    .filter(Boolean).join(' ');
}

/** The one line that answers "when is this". Uncertain start times get a
 *  different answer, not a hedged version of the same one. */
export function whenLine(f) {
  const quote = timeDoubt(f && f.notes);
  const hold = holdLabel(f && f.hold_minutes);
  if (quote) {
    return { uncertain: true, quote, hold, short: 'start time TBC',
             stored: hhmmLabel(f.time), label: 'Start time not agreed' };
  }
  if (!f || !f.time) {
    return { uncertain: false, hold, short: '', label: 'No time on the booking' };
  }
  const end = endTime(f.time, f.hold_minutes);
  return { uncertain: false, hold, short: hhmmLabel(f.time),
           label: hhmmLabel(f.time) + (end ? '–' + hhmmLabel(end) : '') };
}

export const fullDate = (iso) => new Date(iso + 'T12:00:00')
  .toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric',
                                 month: 'long', year: 'numeric' });

export const roomOf = (f) => f.area || f.pinned_table || null;

// --------------------------------------------------------------- the calendar
/**
 * A month of dates that have functions on them.
 *
 * Driven entirely by the server's `by_date` rollup. A cell prints ONE PIP PER
 * ROOM, with a multiplier if a room holds more than one that day, so 8 August
 * reads "Old Stow · 40" and "Main Hall · 25" — two bookings, two rooms,
 * overlapping holds, and no way for the grid to imply there is one function
 * there. A cell that collapsed a date to a single entry would be wrong on the
 * only date in the live data that has two.
 */
export function calendarHTML(month, byDate, selDate, today) {
  const map = byDateMap(byDate);
  const cells = monthCells(month).map((iso) => {
    if (!iso) return '<div class="cell pad"></div>';
    const slot = map[iso];
    const cls = ['cell'];
    if (slot) cls.push('has');
    if (iso === selDate) cls.push('sel');
    if (iso === today) cls.push('today');
    const pips = slot ? (slot.areas || []).map((a) =>
      `<span class="pip">${esc(a.area)}${a.count > 1 ? ' ×' + a.count : ''} · ${a.covers}</span>`
    ).join('') : '';
    const open = slot ? ` data-date="${esc(iso)}" role="button" tabindex="0"` : '';
    return `<div class="${cls.join(' ')}"${open}><div class="cd">${
      Number(iso.slice(8))}</div>${pips}</div>`;
  }).join('');
  const n = (byDate || []).filter((s) => ym(s.date) === month)
    .reduce((t, s) => t + s.count, 0);
  return `<div class="card">
    <div class="calhead">
      <button class="ghost small" data-month="prev" aria-label="previous month">‹</button>
      <div class="calmonth">${esc(monthLabel(month))}</div>
      <button class="ghost small" data-month="next" aria-label="next month">›</button>
      <span class="hint" style="margin-top:0">${
        n ? (n === 1 ? '1 function' : n + ' functions') + ' this month'
          : 'nothing on this month'}</span>
    </div>
    <div class="calgrid">${DOW.map((d) => `<div class="cdow">${d}</div>`).join('')}${cells}</div>
  </div>`;
}

// ------------------------------------------------------------------ the rail
export function diaryRowHTML(f, selId, today) {
  const d = daysAway(f.date, today);
  const w = whenLine(f);
  const chips = [];
  if (d !== null && d >= 0 && d <= 7) {
    chips.push(`<span class="chip soon">${d === 0 ? 'today' : d + 'd'}</span>`);
  }
  chips.push(`<span class="chip">${esc(roomOf(f) || 'no room named')}</span>`);
  chips.push(`<span class="chip">${f.covers} covers</span>`);
  // The whole point: this row must not read "6pm".
  if (w.uncertain) chips.push('<span class="chip need">start time TBC</span>');
  if (f.status && f.status !== 'confirmed') {
    chips.push(`<span class="chip bad">${esc(f.status)}</span>`);
  }
  // Only on a function that has actually run. "Reported" against a party three
  // weeks away is a category error, and "no report yet" on one is a job nobody
  // can do. The GP itself never comes out here: a rail row has nowhere to put
  // what qualifies it, which is the same reason functions.summary() carries
  // outcome_reported as a boolean and not a number.
  // A report is a report whichever way it arrived — hand-recorded on the brief
  // or computed off the tab. Which one it was belongs on the card, where there
  // is room to say what qualifies it; a row has none, which is the same reason
  // no percentage comes out here.
  if (d !== null && d < 0) {
    chips.push(f.computed_ambiguous
      ? '<span class="chip bad">report ambiguous</span>'
      : hasReport(f) ? '<span class="chip ok">reported</span>'
                     : '<span class="chip need">no report yet</span>');
  }
  return `<div class="row${selId === f.id ? ' on' : ''}" data-fn="${esc(f.id)}"
      role="button" tabindex="0">
    <div class="l1"><span class="nm">${esc(f.name)}</span>
      <span class="when">${esc(niceDate(f.date))}${
        w.short ? ' · ' + esc(w.short) : ''}</span></div>
    <div class="l2">${chips.join('')}</div></div>`;
}

/**
 * Coming up, then what has already run.
 *
 * The historic half is the same list read backwards, most recent first, and it
 * is not a separate screen: four functions exist in total and three of them are
 * past, so a dedicated history page would be most of the data behind a click.
 * Both groups always draw their heading and their count, even at zero — an
 * empty group that says "0" and one calm sentence reads as a quiet diary. A
 * group that vanishes reads as a page that failed to load.
 */
export function diaryRailHTML(fns, selId, today) {
  const { upcoming, past } = splitDiary(fns, today);
  const grp = (title, arr, hint, empty) =>
    `<div class="grp">${title}<span class="n">${arr.length}</span></div>`
    + `<div class="hint" style="margin:-2px 0 6px">${hint}</div>`
    + (arr.length ? arr.map((f) => diaryRowHTML(f, selId, today)).join('')
                  : `<div class="quiet">${empty}</div>`);
  return grp('Coming up', upcoming, 'Rooms held. Soonest first.',
             'Nothing booked ahead. The diary is clear.')
       + grp('Already happened', past, 'Most recent first.',
             'Nothing has run yet.');
}

// ============================================== how it went, after the night
// WHY THIS HALF EXISTS
// --------------------
// Everything above answers questions asked BEFORE a function: what will they
// pay, what is still missing, whose move is it. This answers the only one
// asked afterwards — was it worth doing — and nothing in the platform could,
// because the package SKUs carry no costed recipe. In the P&L a function books
// at 100% GP and therefore looks free. The two nights on 8 August 2026 were
// worked out by hand precisely because the system had no way to.
//
// THE RULE THAT SHAPES ALL OF THIS: a GP percentage NEVER renders without its
// caveats. Not "should not" — cannot. gpFigureHTML() below is the only place
// in this module that formats gp_pct or gp_pct_ex_mixer at all, and the branch
// that prints the number sits INSIDE the guard that requires a non-empty
// caveat list. An outcome that somehow arrives with a percentage and no
// caveats draws a refusal where the number would have been, and says why.
//
// That is deliberate over-engineering for one specific failure. A bare "59.3%"
// on a screen is quoted as fact in a meeting six months later, by which point
// "beverage only, mixer estimated, packages uncosted" has been left behind on
// a page nobody reopened. A tooltip does not survive that journey; a sentence
// physically inside the same block does. The API takes the same view — it
// keeps the GP off summary() and puts it only where the caveats fit.
//
// gp_pct_ex_mixer is drawn as the OTHER END OF A RANGE rather than as a
// footnote, because that is what it is: the same sum with the assumed mixer
// cost taken back out. Nobody has measured which end is right, and saying
// "between 59.3% and 65.6%" is the truthful width of the answer.

/** One decimal, or an em dash. Percentages are quoted to a tenth because that
 *  is how they were worked out; a rounded "59%" invites a second, different
 *  rounding somewhere else. */
export const pct = (v) => (v == null ? '—' : `${Number(v).toFixed(1)}%`);

/** A plain count, to `dp` places. Drinks per head is 9.56 and reads wrong at
 *  either 10 or 9.6 — it is a measured ratio, not an estimate. */
export const num = (v, dp = 2) => (v == null ? '—'
  : Number(v).toLocaleString('en-AU',
      { minimumFractionDigits: dp, maximumFractionDigits: dp }));

/** A sentence written across several source lines is still one sentence.
 *  Collapsed here rather than left to the browser so the string this module
 *  hands out is the string a human reads — the suite asserts on these
 *  sentences and a run of indentation in the middle of one is not a fact
 *  worth encoding in a test. */
export const flat = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();

/** Hours as a human writes them: 3, not 3.00; 3.5, not 3.50. */
export const hoursLabel = (h) => (h == null ? '—'
  : (Math.abs(h - Math.round(h)) < 0.005 ? String(Math.round(h)) : num(h, 1)));

/** The machine codes functions._outcome_caveats() emits, given a heading a
 *  human reads first. The prose stays the SERVER'S — it is the authority on
 *  what is soft about its own numbers, and a second wording here would be a
 *  second thing to keep true. Only the heading is ours, and an unknown code
 *  falls back to itself rather than being dropped: a caveat this page has
 *  never heard of is still a caveat, and silently swallowing it is the exact
 *  failure this section exists to prevent. */
export const CAVEAT_TITLES = {
  mixer_estimated: 'Mixer',
  // Emitted by modules/functions when a tab holds a product with no costed
  // recipe. It is the caveat that says the COGS beside it is a LOWER bound —
  // an uncosted line contributes nothing to the sum and looks on a screen
  // exactly like a cheap one.
  uncosted_lines: 'Uncosted lines',
  food_cogs_unknown: 'Food',
  package_sku_uncosted: 'Packages',
};

export function caveatsHTML(caveats) {
  const list = caveats || [];
  if (!list.length) return '';
  return flat(`<div class="caveats">
    <div class="cvh">What qualifies that figure — ${list.length === 1
      ? 'one thing' : list.length + ' things'}, and they are part of it</div>
    <ul>${list.map((c) => `<li><b>${esc(CAVEAT_TITLES[c.code] || c.code)}</b> —
      ${esc(c.note)}${c.effect ? ` <span class="cvx">${esc(c.effect)}.</span>` : ''}</li>`)
      .join('')}</ul></div>`);
}

/**
 * The GP figure, and the ONLY place one is drawn.
 *
 * Three outcomes, and the middle one is the point:
 *   * no percentage yet   — say which inputs are missing; do not print 0%.
 *   * a percentage with NO caveats — refuse, and explain the refusal. This is
 *     unreachable against the real API (package_sku_uncosted is emitted
 *     unconditionally) and it is written anyway, because "unreachable" is a
 *     claim about today's server and the number outlives it.
 *   * a percentage with caveats — the number, its basis, the range it lives
 *     in, and the caveats, in one block that cannot be screenshotted apart.
 */
export function gpFigureHTML(o) {
  if (!o) return '';
  const caveats = o.caveats || [];
  if (o.gp_pct == null) {
    return flat(`<div class="gp none">
      <div class="big">No gross profit worked out yet</div>
      <div class="why">That needs the revenue taken, the food share of it and
        the drinks cost. Whatever is recorded above is real — the GP simply
        cannot be computed from it yet, which is not the same as it being
        nothing.</div>
      ${caveatsHTML(caveats)}</div>`);
  }
  if (!caveats.length) {
    return flat(`<div class="gp refused">
      <div class="big">GP withheld</div>
      <div class="why">This report carries a gross profit percentage and none
        of the qualifications that make it quotable, so the number is not
        shown. A bare percentage is the thing this screen exists to prevent: it
        gets read back as fact once the basis has been left behind. Refresh —
        if it stays like this the report is malformed and the figure is not
        safe to use.</div></div>`);
  }
  const basis = esc(o.gp_basis || 'unstated');
  const range = o.gp_pct_ex_mixer == null ? '' : flat(`<div class="range">The true
      figure is somewhere between <b>${pct(o.gp_pct)}</b> and
      <b>${pct(o.gp_pct_ex_mixer)}</b>. The top of that range is the same sum
      with the estimated mixer cost taken back out. Both ends are arithmetic;
      nobody has measured which one is right.</div>`);
  return flat(`<div class="gp">
    <div class="big">${pct(o.gp_pct)}<span class="basis">${basis} GP</span></div>
    <div class="why">${money2(o.gross_profit_ex_cents)} gross profit on
      ${money2(o.bev_revenue_ex_cents)} of ${basis} revenue ex-GST, after
      ${money2(o.total_cogs_ex_cents)} of cost.</div>
    ${range}${caveatsHTML(caveats)}</div>`);
}

/**
 * The out-earn ratio, in words.
 *
 * A bare "1.29" means nothing to anyone. The fact underneath it is that a
 * function does not add trade to an empty room — it takes the seats ordinary
 * trade would have had, and swaps 76.4% beverage margin for package margin. So
 * the sentence says what was swapped, what the swap cost in dollars, and what
 * the night therefore had to turn over just to draw level.
 *
 * It deliberately does NOT restate the GP percentage. That number renders in
 * exactly one place, with its caveats attached, and repeating it here would be
 * a second copy free of them — which is the whole failure mode.
 */
export function outEarnSentence(o) {
  if (!o || o.margin_foregone_ex_cents == null) return '';
  const bench = pct(o.benchmark_gp_pct);
  const gap = o.margin_foregone_ex_cents;
  const r = o.out_earn_ratio;
  if (gap <= 0) {
    return flat(`Those seats return ${bench} on beverage in ordinary trade.
      This function beat that, by ${money2(-gap)} of gross profit on the same
      revenue${r == null ? '' : ` — it out-earned the trade it displaced at
      ${num(r, 2)} times before it was even level`}. It did not have to make
      the case; it made it.`);
  }
  const uplift = r == null ? null : Math.round((r - 1) * 100);
  return flat(`A function does not add trade to an empty room — it displaces
    it. Those seats return ${bench} on beverage in ordinary trade, and this one
    returned less, by ${money2(gap)} of gross profit on the same revenue.${
    r == null ? '' : ` To break even on gross profit it had to out-earn the
    trade it replaced by ${num(r, 2)} times — ${uplift}% more money through
    the till for the same margin.`} "It made a profit" and "it was worth
    doing" are different questions, and this is the second one.`);
}

/**
 * Who actually came, beside who was booked.
 *
 * Both measured functions differed, and in the same direction: 25 booked and
 * 19 through the door on one, 40 on the booking and 25 counted on the other.
 * Every per-head figure divides by the people who came, so a report that
 * quietly showed the booking figure would be dividing by the wrong number and
 * saying nothing about it.
 */
export function headsLine(o) {
  const a = o && o.actual_heads, b = o && o.booked_guests;
  if (a == null) {
    return { flag: false, text: 'Nobody recorded how many turned up, so there '
      + 'are no per-head figures below.' };
  }
  if (b == null || a === b) {
    return { flag: false, text: `${a} through the door.` };
  }
  const d = b - a;
  return { flag: true, text: flat(`${a} through the door, against ${b} on the
    booking — ${Math.abs(d)} ${d > 0 ? 'fewer' : 'more'}. Every per-head figure
    below is divided by the ${a} who came, not the ${b} who were booked.`) };
}

const metric = (k, v, s) => `<div class="met"><div class="k">${k}</div>
  <div class="v">${v}</div>${s ? `<div class="s">${flat(s)}</div>` : ''}</div>`;

/** The measured half — everything that is a count or a dollar somebody read
 *  off a report. No percentage appears here; see gpFigureHTML. */
export function outcomeMetricsHTML(o) {
  const cells = [];
  if (o.revenue_inc_cents != null) {
    cells.push(metric('Revenue taken', money(o.revenue_inc_cents),
      `inc-GST · ${money2(o.revenue_ex_cents)} ex-GST${o.food_revenue_inc_cents
        ? ` · ${money(o.food_revenue_inc_cents)} of it food, which is part of
           this line and not on top of it` : ''}`));
  }
  if (o.tickets_sold != null) {
    cells.push(metric('Tickets sold', String(o.tickets_sold),
      o.actual_heads != null && o.tickets_sold !== o.actual_heads
        ? `${o.actual_heads} came — paid for and turned up are different counts`
        : 'paid for, which is not the same count as who came'));
  }
  if (o.drinks_poured != null) {
    // The pace is PER HEAD per hour, because that is the one a person can
    // reason about — 4.8 drinks an hour is somebody's night, and it is what
    // break-even is argued against. This used to read the room's whole
    // throughput ("79.67 an hour") in the same breath as the per-head figure,
    // which invited the two to be read as the same measure. The room figure
    // stays, because 120 drinks an hour is a rostering fact, but it is said
    // last and it is said to be the room's.
    cells.push(metric('Drinks poured', String(o.drinks_poured),
      `${num(o.drinks_per_head)} a head over
       ${hoursLabel(o.package_hours)} hours of drinks package${
        o.drinks_per_head_per_hour != null
          ? ` · ${num(o.drinks_per_head_per_hour)} a head an hour` : ''}${
        o.drinks_per_hour_room != null
          ? `. Across the whole room the bar poured
             ${num(o.drinks_per_hour_room)} an hour — a measure of the bar's
             workload, not of how hard anybody drank` : ''}`));
  }
  if (o.cogs_ex_cents_per_head != null) {
    cells.push(metric('Cost a head', money2(o.cogs_ex_cents_per_head),
      `ex-GST · ${money2(o.total_cogs_ex_cents)} in total${o.mixer_est_ex_cents
        ? `, of which ${money2(o.mixer_est_ex_cents)} is the mixer estimate` : ''}`));
  }
  if (o.gross_profit_ex_cents != null) {
    cells.push(metric('Gross profit', money2(o.gross_profit_ex_cents),
      'ex-GST, on the beverage side only'));
  }
  if (o.menu_value_inc_cents_per_head != null) {
    cells.push(metric('Given away a head',
      money2(o.menu_value_inc_cents_per_head),
      `at menu price, inc-GST · ${money(o.menu_value_inc_cents)} across the
       night. Not a loss — it is what was handed over, priced as it would
       otherwise have sold`));
  }
  return cells.length ? `<div class="mets">${cells.join('')}</div>` : '';
}

/** Why the references matter, said where they are typed and where they are
 *  read. A figure nobody can get back to is a figure that has to be believed. */
export const POS_REFS_WHY = 'The tab name, the receipt number and the sale id. '
  + 'They are what makes every figure here reproducible: with them anyone can '
  + 'pull the same report in six months and get the same answer, and without '
  + 'them all of this is somebody’s word.';

/** Has this function happened? A function ON today has not finished, so it is
 *  not something to report on yet — the same line splitDiary() draws. */
export const hasHappened = (f, today) => {
  const d = daysAway(f && f.date, today);
  return d !== null && d < 0;
};

// ------------------------------------------ two sources, and which one wins
// WHY THERE ARE TWO
// -----------------
// A night can now be reported on twice, by two different mechanisms:
//
//   BY HAND     — somebody types the nine measured numbers into the form
//                 below; the engine derives the report and it arrives as
//                 `f.outcome` on the diary row.
//   COMPUTED    — modules/functions works the same night out from the comped
//                 tab's own line items against a cost book pinned to that
//                 date, publishes it to data/functions_gp.json, and
//                 joinComputedReports() attaches it as `f.computed_outcome`.
//
// They are the SAME SHAPE — the feed publishes the field names functions
// .outcome() returns, deliberately — so everything below this line draws
// either one without knowing which it has. That is the whole reason the shape
// was copied rather than invented: one renderer, one set of rules about what
// may appear beside a percentage, and no second place for the caveat rule to
// be forgotten.
//
// THE JOIN IS THE BOOKING ID AND NOTHING ELSE
// -------------------------------------------
// The feed knows what the bar called the tab ("Dazzle drinks"); the diary
// knows who booked ("Roman Bunting"). Those never match, and 8 August 2026
// carries TWO functions, so a date join is ambiguous by construction. Either
// would silently file one night's gross profit under the other booking:
// nothing 404s, both numbers are real, and the wrong one is under the wrong
// name. So the id is recorded on the tab file by a human against the evidence,
// travels on the feed as `booking_id`, and is the only thing matched on here.
//
// WHICH WINS: THE COMPUTED ONE
// ----------------------------
// Not because it is ours or because it is newer, but because it is
// RE-DERIVABLE. Every figure on it comes from line items still sitting in
// data/function_tabs/, priced by a book pinned to the night, so anyone can run
// it again in a year and get the same answer back. The hand-recorded one is a
// summary somebody typed once and nothing can check it.
//
// A DISAGREEMENT IS NEVER SETTLED SILENTLY
// ----------------------------------------
// If the two do not match then one of them is wrong about a MEASURED fact —
// how many drinks, how much revenue, how many people through the door — and
// that is something to go and look at, not a tie to break quietly. So the
// clash is named field by field, above the figure.
//
// ...AND THE SCREEN STILL SHOWS ONLY ONE PERCENTAGE
// ------------------------------------------------
// The clash notice names the measured INPUTS the two sources disagree about
// and never touches either percentage — comparing one would mean holding one,
// and there is one formatter for a function's GP in this file. Two GP figures
// for one night on one screen is the exact failure this half of the module
// exists to prevent — one of them would be read out later without the caveats
// that qualify it, and it would be the one nobody can reproduce.

/** Attach each computed report to the booking it belongs to.
 *
 *  Pure: takes the diary rows and the feed, returns new rows. A booking with
 *  no entry comes back untouched, which is the normal case — most functions
 *  will never have a tab file, and a past one with no report rightly goes on
 *  saying "no report yet" and offering the form.
 *
 *  Two entries claiming ONE booking attaches NEITHER, and flags it. One of
 *  them belongs to a different night, and picking either would put one
 *  function's profit under another's name — the precise error the id exists to
 *  make impossible. An entry with no `booking_id` joins to nothing at all. */
export function joinComputedReports(fns, feed) {
  const seen = new Map();                 // booking id -> the entry, or null if >1
  for (const o of (feed && feed.functions) || []) {
    const k = o && o.booking_id;
    if (!k) continue;
    seen.set(k, seen.has(k) ? null : o);
  }
  return (fns || []).map((f) => {
    if (!f || !seen.has(f.id)) return f;
    const o = seen.get(f.id);
    return o ? { ...f, computed_outcome: o } : { ...f, computed_ambiguous: true };
  });
}

/** The MEASURED facts both sources claim to hold, so a disagreement can be
 *  named rather than averaged.
 *
 *  MEASURED ONLY, and the percentage is deliberately not among them. Two
 *  reports can only really disagree about a fact somebody counted — how many
 *  came, how many drinks, what the invoice said. Everything else follows from
 *  these by arithmetic, so listing the consequences of one wrong drink count
 *  would read as six separate problems. And the percentage in particular is
 *  never compared here, because comparing it means holding it, and this module
 *  formats a function's GP in exactly one place with its caveats attached. The
 *  suite enforces that at the source: a second reader of it anywhere in this
 *  file fails the build. */
export const REPORT_COMPARE = [
  { field: 'actual_heads', label: 'heads through the door', kind: 'count' },
  { field: 'tickets_sold', label: 'tickets sold', kind: 'count' },
  { field: 'revenue_inc_cents', label: 'revenue taken', kind: 'money' },
  { field: 'food_revenue_inc_cents', label: 'the food share of it', kind: 'money' },
  { field: 'drinks_poured', label: 'drinks poured', kind: 'count' },
  { field: 'menu_value_inc_cents', label: 'the menu value of the tab', kind: 'money' },
  { field: 'cogs_ex_cents', label: 'drinks COGS', kind: 'money' },
  { field: 'mixer_est_ex_cents', label: 'the mixer estimate', kind: 'money' },
];

/** Where two reports of the same night disagree. A field measured on only one
 *  side is NOT a disagreement — it is a gap, and saying "they disagree about
 *  the food share" when one of them simply never recorded it would send
 *  somebody looking for a contradiction that is not there. */
export function reportClash(hand, computed) {
  if (!hand || !computed) return [];
  return REPORT_COMPARE
    .filter((c) => hand[c.field] != null && computed[c.field] != null
                   && hand[c.field] !== computed[c.field])
    .map((c) => ({ ...c, hand: hand[c.field], computed: computed[c.field] }));
}

/** Which report this screen draws for one booking, and what it is hiding. */
export function reportFor(f) {
  const hand = (f && f.outcome) || null;
  const computed = (f && f.computed_outcome) || null;
  if (!computed) return { outcome: hand, source: hand ? 'hand' : null, clash: [] };
  return { outcome: computed, source: 'computed', clash: reportClash(hand, computed) };
}

/** Has anybody reported on this night, by either route? */
export const hasReport = (f) => !!(f && (f.outcome || f.computed_outcome));

const clashValue = (c, v) => (c.kind === 'money' ? money2(v) : String(v));

/** The disagreement, named. No percentage appears in here; see the section
 *  header above for why that is a rule and not a preference. */
export function reportClashHTML(clash) {
  if (!clash || !clash.length) return '';
  const items = clash.map((c) => `<li>${esc(c.label)} —
    <b>${esc(clashValue(c, c.computed))}</b> computed,
    ${esc(clashValue(c, c.hand))} hand-recorded</li>`).join('');
  return flat(`<div class="prob" style="margin-top:0">
    <b>Two reports exist for this night and they do not agree.</b> The figure
    below is the computed one: it is worked out from the comped tab's own line
    items against the cost book as it stood on the night, so anybody can run it
    again and get it back. The hand-recorded one is a typed summary and nothing
    can check it, which is why it does not get to be the number on this screen.
    What the two disagree about:<ul>${items}</ul>
    Both cannot be right about a measured fact, so this is worth an hour with
    the POS rather than a shrug. The hand-entered percentage is deliberately
    not printed beside the one below — one night gets one gross profit figure
    here, and a second would be quoted later without its caveats.</div>`);
}

/** Where a computed figure came from, said on the screen that shows it. The
 *  pairing evidence travels too: a booking id matched on the money and a
 *  booking id matched on a hunch look identical in a JSON file, and this is
 *  the only place anybody would ever notice the difference. */
export function computedProvenanceHTML(o) {
  if (!o) return '';
  const book = o.cost_book_as_of === 'live'
    ? `the cost book as it stands TODAY — no dated snapshot exists for that
       night, so this figure moves as recipes are recosted and is not
       reproducible`
    : o.cost_book_as_of
      ? `the cost book as it stood on ${esc(niceDate(o.cost_book_as_of))}`
      : 'a cost book that did not say when it was from';
  return flat(`<div class="hint" style="margin-top:0">Computed, not typed:
    worked out from the comped tab line by line against ${book}.${
    o.source_file ? ` The lines are in ${esc(o.source_file)}.` : ''}${
    o.booking_evidence
      ? ` Attached to this booking on the evidence, not the name —
          ${esc(o.booking_evidence)}`
      : ' Nothing on the feed says why it is attached to this booking, which is'
        + ' worth checking before the figure is quoted.'}</div>`);
}

/** Two computed reports claiming one booking. Neither is drawn. */
export function ambiguousJoinHTML(f) {
  if (!f || !f.computed_ambiguous) return '';
  return flat(`<div class="prob" style="margin-top:0">
    <b>Two computed reports claim this booking, so neither is shown.</b> One of
    them is another night's, and showing either would put one function's gross
    profit under a different function's name. The booking ids on the tab files
    in data/function_tabs/ need fixing and the feed rebuilding; until then this
    night has no computed report, which is better than having the wrong
    one.</div>`);
}

/**
 * The whole "How it went" block for one function.
 *
 * Nothing at all for a function that has not happened — a report on a night
 * still to come is a forecast, and this screen does not do forecasts.
 *
 * A past function with NO outcome gets a sentence, not a blank and not a row
 * of zeros. "Nobody has counted this" and "it made nothing" are different
 * facts about a night and only one of them is true here, which is exactly the
 * distinction functions.outcome() protects by returning null.
 */
export function outcomeHTML(f, today, edit) {
  if (!f || !hasHappened(f, today)) return '';
  const rep = reportFor(f);
  const o = rep.outcome;
  // The BUTTON follows the hand-recorded report, never the drawn one. A
  // computed figure is not something anybody can go and correct in this form —
  // it comes off the tab file — so offering "Correct the figures" beside one
  // would point at boxes that are empty and a save that changes nothing.
  const hand = (f && f.outcome) || null;
  const editing = !!(edit && edit.booking === f.id);
  const act = `<button class="${o ? 'ghost small' : 'small'}"
      data-act="recordoutcome" data-booking="${esc(f.id)}">${
      hand ? 'Correct the figures' : 'Record how it went'}</button>`;
  const ambig = ambiguousJoinHTML(f);

  if (!o) {
    return `<div class="rep">
      <h2>How it went</h2>
      ${ambig}
      <div class="quiet">Nobody has reported on this one. That is not a
        function that made nothing — it is a night nobody has counted yet. The
        heads, the tab, the drinks off it and what they cost are all on the
        POS; until somebody puts them here there is no gross profit for this
        function anywhere in the business, because the package SKUs book at
        100%.</div>
      <div class="acts" style="margin-top:10px">${act}
        <span class="hint" style="margin-top:0">${f.brief_id
          ? 'Nine measured numbers. Everything else is worked out from them.'
          : 'There is no brief behind this booking yet, so this makes one and '
            + 'opens the form in the same step.'}</span></div>
      ${editing ? outcomeFormHTML(edit.brief) : ''}</div>`;
  }

  const heads = headsLine(o);
  const earn = outEarnSentence(o);
  const computed = rep.source === 'computed';
  // Recording by hand is still offered beside a computed report — the nine
  // boxes are measured facts and worth having on the brief — but it is said
  // out loud that they will not become the figure on this screen. A control
  // whose effect is invisible is a control people press twice.
  const also = computed && !hand
    ? `<span class="hint" style="margin-top:0">The figure above is computed
        from the tab and stays the one this screen shows. Recording the
        measured numbers by hand as well is useful — it puts them on the brief
        — but it does not replace it, and if the two disagree this card will
        say so.</span>`
    : '';
  return `<div class="rep">
    <h2>How it went</h2>
    ${ambig}
    ${reportClashHTML(rep.clash)}
    <div class="${heads.flag ? 'prob' : 'hint'}" style="margin-top:0">${
      esc(heads.text)}</div>
    ${outcomeMetricsHTML(o)}
    ${gpFigureHTML(o)}
    ${computed ? computedProvenanceHTML(o) : ''}
    ${earn ? `<div class="earn">${esc(earn)}</div>` : ''}
    ${o.pos_refs ? `<div class="refs"><label>Where these came from</label>
      <div class="log">${esc(o.pos_refs)}</div>
      <div class="hint">${esc(POS_REFS_WHY)}</div></div>`
      : `<div class="hint" style="margin-top:10px">No POS references recorded,
        so nothing here can be traced back to a receipt. ${esc(POS_REFS_WHY)}</div>`}
    <div class="acts" style="margin-top:10px">${act}${also}</div>
    ${editing ? outcomeFormHTML(edit.brief) : ''}</div>`;
}

// -------------------------------------------------------- recording a night
/**
 * The nine boxes, and nothing else.
 *
 * Every one is MEASURED — counted off a POS report or read off an invoice.
 * There is no box for the GP percentage, the drinks per head or the margin
 * comparison, and there must never be one: they are derived, and a derived
 * number that can be typed is a number that can disagree with its own inputs.
 * That is the same reason functions.py stores none of them.
 *
 * `kind` decides the parse, not the widget: money is typed in DOLLARS because
 * that is what the invoice says, and converted to cents on the way out,
 * because that is what the column holds.
 */
export const OUTCOME_INPUTS = [
  { id: 'o_heads', field: 'actual_heads', kind: 'int',
    label: 'Heads through the door',
    hint: 'Who actually came. Not what was booked and not what was ticketed.' },
  { id: 'o_tickets', field: 'tickets_sold', kind: 'int',
    label: 'Tickets sold',
    hint: 'How many were paid for. Often not the same number.' },
  { id: 'o_rev', field: 'revenue_inc_cents', kind: 'money',
    label: 'Revenue taken ($ inc GST)',
    hint: 'What the function brought in, as the till shows it.' },
  { id: 'o_food', field: 'food_revenue_inc_cents', kind: 'money',
    label: 'Food share of it ($ inc GST)',
    hint: 'Part of the line above, never on top of it. Leave blank if there '
        + 'was no food: it is taken out of the GP sum because there is no '
        + 'food cost to put against it.' },
  { id: 'o_drinks', field: 'drinks_poured', kind: 'int',
    label: 'Drinks poured',
    hint: 'Off the comped tab, counted.' },
  { id: 'o_menu', field: 'menu_value_inc_cents', kind: 'money',
    label: 'Menu value of that tab ($ inc GST)',
    hint: 'What those drinks would have rung up at menu price — what was '
        + 'given away, at the price it would otherwise have sold for.' },
  { id: 'o_cogs', field: 'cogs_ex_cents', kind: 'money',
    label: 'Drinks COGS ($ ex GST)',
    hint: 'Cost of goods on the drinks, ex-GST as invoiced.' },
  { id: 'o_mixer', field: 'mixer_est_ex_cents', kind: 'money',
    label: 'Mixer estimate ($ ex GST)',
    hint: 'An estimate, and the report says so every time it quotes a GP. '
        + 'House spirits are costed as the nip only, so the mixer is added '
        + 'by assumption.' },
  { id: 'o_refs', field: 'pos_refs', kind: 'text',
    label: 'POS references', hint: POS_REFS_WHY },
];

/** Dollars off the form to cents for the wire, counts to integers, text as
 *  typed. A blank box is OMITTED rather than sent as zero — F.upsert never
 *  overwrites a value with an empty one, so an absent key means "not measured"
 *  and 0 means "measured, and it was none". */
export function outcomeBody(brief, read) {
  const out = { name: brief && brief.name };
  for (const f of OUTCOME_INPUTS) {
    const raw = String(read(f.id) == null ? '' : read(f.id)).trim();
    if (raw === '') continue;
    if (f.kind === 'text') { out[f.field] = raw; continue; }
    const v = parseFloat(raw.replace(/[$,\s]/g, ''));
    if (!isFinite(v)) continue;
    out[f.field] = f.kind === 'money' ? Math.round(v * 100) : Math.round(v);
  }
  return out;
}

/** What goes in each box when the form opens: the raw measured column off the
 *  brief, in the units it is typed in. Never the derived report. */
export function outcomeFieldValue(brief, f) {
  const v = brief ? brief[f.field] : null;
  if (v == null || v === '') return '';
  if (f.kind === 'money') return String(v / 100);
  return String(v);
}

export function outcomeFormHTML(brief) {
  const b = brief || {};
  const box = (f) => `<div class="f${f.kind === 'text' ? ' full' : ''}">
    <label for="${f.id}">${esc(f.label)}</label>
    <input id="${f.id}" data-outcome="1" ${f.kind === 'int' ? 'type="number" min="0"' : ''}
      value="${esc(outcomeFieldValue(b, f))}">
    <div class="hint">${esc(f.hint)}</div></div>`;
  return `<div class="oform">
    <h2>Record what was measured</h2>
    <div class="hint" style="margin-top:0">Nine numbers, all of them counted or
      invoiced. There is no box for the GP percentage, the drinks per head or
      the margin comparison because those are worked out from these — a GP you
      can type is a GP nobody can reproduce.</div>
    <div class="kv" style="margin-top:11px">${OUTCOME_INPUTS.map(box).join('')}</div>
    <div class="acts" style="margin-top:12px">
      <button data-act="saveoutcome" data-brief="${esc(b.id)}">Save the figures</button>
      <button class="ghost" data-act="canceloutcome">Cancel</button>
      <span class="hint" style="margin-top:0">Everything derived is recomputed
        on save, so a corrected input corrects the report.</span>
    </div></div>`;
}

// ------------------------------------------------------------- one function
const MARK_WORDS = { is_function: 'the function flag',
                     notes: 'the note prefix',
                     area: 'the room it is pinned to' };

export function functionCardHTML(f, today, edit) {
  const w = whenLine(f);
  const heads = headcountSays(f);
  // The unsettled start time has its own block above; repeating it here as an
  // open question says the same thing twice on the only row that has it.
  const qs = openQuestions(f.notes).filter((q) => q !== w.quote);
  const d = daysAway(f.date, today);
  const room = roomOf(f);

  const head = `<div style="display:flex;justify-content:space-between;gap:12px;align-items:start">
    <div>
      <h2 style="font-size:19px">${esc(f.name)}</h2>
      <div class="strip">
        <span>${esc(fullDate(f.date))}</span>·
        <span>${room ? esc(room) : 'no room named'}</span>·
        <span>${f.covers} covers</span>
        ${d !== null ? `·<span>${d === 0 ? 'today'
          : d < 0 ? Math.abs(d) + ' days ago' : d + ' days away'}</span>` : ''}
      </div>
    </div>
    <div class="chip ${f.status === 'confirmed' ? 'ok' : 'bad'}">${
      esc(f.status || 'status unknown')}</div>
  </div>`;

  // An unsettled start time is a DIFFERENT statement, not a footnote under a
  // confident one. The stored value is still shown — it is what the engine is
  // holding and staff will be asked about it — but it is never the headline.
  const when = w.uncertain
    ? `<div class="quote unknown" style="margin-top:11px">
         <div class="big">${esc(w.label)}</div>
         <div class="why">The booking stores ${esc(w.stored)}, and the note says
           that is not settled. Do not read it back to anyone as a start time.</div>
         <div class="log" style="margin-top:8px">${esc(w.quote)}</div>
         ${w.hold ? `<div class="hint">The room is held for ${esc(w.hold)} from
           whenever it does start.</div>` : ''}
       </div>`
    : `<div class="quote" style="margin-top:11px">
         <div class="big">${esc(w.label)}</div>
         <div class="why">${room ? esc(room) : 'No room named'}${
           w.hold ? `, held for ${esc(w.hold)}` : ''}.</div>
       </div>`;

  const headBlock = heads.quotes.length
    ? `<div class="${heads.disagrees ? 'prob' : 'hint'}" style="margin-top:11px">
         ${heads.disagrees
           ? `<b>${f.covers} on the booking, but the note says otherwise —
              somebody has to ask which is right.</b> ` : ''}
         The note on numbers: ${heads.quotes.map((q) => `“${esc(q)}”`).join(' ')}</div>`
    : '';

  const open = qs.length
    ? `<div style="margin-top:11px"><b style="font-size:14px">Still open in the note</b>
         <ul style="margin:6px 0 0 18px;font-size:14px">${
           qs.map((q) => `<li>${esc(q)}</li>`).join('')}</ul></div>`
    : '';

  const contact = `<div class="strip" style="margin:11px 0 0">
    ${f.email ? `<a href="mailto:${esc(f.email)}">${esc(f.email)}</a>·` : ''}
    ${f.phone ? `<a href="tel:${esc(f.phone)}">${esc(f.phone)}</a>`
      : '<span>No phone on file — email is the only way to reach them.</span>'}
  </div>`;

  const notes = `<div style="margin-top:11px">
    <label>The note, in full</label>
    <div class="log full">${esc(f.notes) || '—'}</div></div>`;

  // brief_id null is the ordinary state of a booked function, not a gap to be
  // chased. It is said plainly so nobody goes looking for a missing record.
  const brief = f.brief_id
    ? `<div class="acts" style="margin-top:11px">
         <button class="ghost small" data-act="gobrief" data-brief="${esc(f.brief_id)}">
           Open the enquiry behind this</button>
         <span class="hint" style="margin-top:0">There is a brief for this one.</span>
       </div>`
    : `<div class="hint" style="margin-top:11px">No brief behind this one — it came
         in through the tracker and was booked straight into the diary. That is
         how most functions arrive; nothing is missing.</div>`;

  const marks = (f.matched_on || []).map((m) => MARK_WORDS[m] || m);
  const foot = `<div class="hint">booking ${esc(f.id)}${
    f.created_at ? ' · entered ' + esc(String(f.created_at).slice(0, 10)) : ''}${
    marks.length ? ' · in the diary because of ' + esc(marks.join(', ')) : ''}</div>`;

  // The report sits high on the card, under the headcount and above the
  // prose, because on a function that has already run it IS the record. It
  // draws nothing at all on one that has not happened yet.
  return `<div class="card">${head}${when}${headBlock}${
    outcomeHTML(f, today, edit)}${open}${contact}${notes}${brief}${foot}</div>`;
}

/** The heading over a clicked date. Says how many, in words, before it says
 *  anything else — a date with two functions must never look like one. */
export function dateHeadHTML(iso, on) {
  const rooms = [...new Set(on.map((f) => roomOf(f) || 'no room named'))];
  const covers = on.reduce((t, f) => t + f.covers, 0);
  return `<div class="card">
    <div class="dhead">${esc(fullDate(iso))}</div>
    <div class="hint">${on.length === 1 ? 'One function' : on.length + ' functions'}
      · ${covers} covers · ${esc(rooms.join(', '))}</div>
    ${on.length > 1 ? `<div class="hint">${on.length} separate bookings on this
      date, each held in its own right. All of them are below, in full.</div>` : ''}
  </div>`;
}

export function diaryPanelHTML(fns, byDate, month, selDate, selId, today,
                               err, edit) {
  const cal = calendarHTML(month, byDate, selDate, today);
  // An empty diary and a diary that would not load look identical, and one of
  // them is the bug this whole half exists to fix. Say which one this is.
  if (err) {
    return cal + `<div class="card"><div class="prob">The diary didn’t load: ${esc(err)}</div>
      <div class="hint">This is not an empty diary — it is a diary that could not
        be read. Nothing above is to be trusted until Refresh works.</div></div>`;
  }
  if (selId) {
    const f = (fns || []).find((x) => x.id === selId);
    if (f) return cal + functionCardHTML(f, today, edit);
  }
  if (selDate) {
    const on = (fns || []).filter((f) => f.date === selDate);
    if (on.length) {
      return cal + dateHeadHTML(selDate, on)
           + on.map((f) => functionCardHTML(f, today, edit)).join('');
    }
  }
  return cal + `<div class="card"><div class="quiet">Pick a date on the calendar,
    or a function on the left.</div></div>`;
}

/** Which of the two questions the screen is answering. */
export function modesHTML(mode, diaryCount, pipelineCount, briefCount) {
  const b = (id, label, n) => `<button data-mode="${id}"${
    mode === id ? ' class="on"' : ''}>${label}<span class="n">${n}</span></button>`;
  // Pipeline counts EVERY enquiry on the board, archive included, because that
  // is what the feed is and a badge that quietly meant something narrower is
  // how "Pipeline 0" survived for weeks. Briefs is its own count and is
  // usually small: a brief is minted to take a deposit, not to track a lead.
  return b('diary', 'Diary', diaryCount)
       + b('pipeline', 'Pipeline', pipelineCount)
       + b('briefs', 'Briefs', briefCount);
}

// ================================================================= pipeline
// WHY THIS HALF WAS REWRITTEN
// ---------------------------
// It read BRIEFS — rows in the booking engine's own `function_briefs` table —
// and that table has been empty since the day it was created. So the tab said
// "Pipeline 0" while sixty live enquiries sat on the monday.com FUNCTIONS
// ENQUIRY TRACKER, each with a dated history of every reply, every chase and
// every silence. Zak asked three times why it was empty. It was empty because
// it was reading the wrong table.
//
// It now reads `data/functions_pipeline.json` — a STATIC FILE on this origin,
// published by modules/functions/pipeline/build_functions_pipeline.py from a
// capture of the board. Not a route on the booking engine: the engine has
// never heard of monday and should not start.
//
// The briefs did not go away and must not. A brief is what a deposit link is
// minted against and what holds a room, so it has its own third tab, and
// "Take a deposit" on an enquiry is what creates one. That is the whole
// relationship: sixty enquiries, and a brief for each of the few that get as
// far as money.
//
// MISSING IS AN ERROR HERE, unlike the GP feed. A function with no computed
// report is normal; a functions screen with no enquiries is the bug this
// replaced, so an absent or unreadable feed says so in words rather than
// drawing an empty list that looks like "nothing to do".
export const PIPE_FEED_URL = '/data/functions_pipeline.json';
export const PIPE_FEED_SCHEMA = 'functions_pipeline/1';

// How old the capture may be before the banner turns from a statement into a
// warning. Two days: the board moves daily — the autodraft writes to it every
// morning — so a three-day-old capture has already missed replies.
export const STALE_DAYS = 2;

export const MOVE_ORDER = ['us', 'unclear', 'them', 'nobody'];
export const MOVE_TITLE = {
  us: 'Waiting on us',
  unclear: "Can't tell whose move it is",
  them: 'Waiting on them',
  nobody: 'Nobody has done anything',
};
export const MOVE_HINT = {
  us: 'The ball is in our court. Soonest event first.',
  unclear: 'The log does not say, so nothing here guesses. Somebody has to read these.',
  them: 'We answered and it is their turn.',
  nobody: 'No reply, no chase, nothing logged — ever.',
};
// Reuses the rail's existing left-edge vocabulary: amber is our move, grey is
// theirs. `unsure` and `untouched` are new and deliberately not amber — a
// row nobody can read is not the same errand as a row with an answer waiting.
export const MOVE_CLASS = { us: 'onme', them: 'onthem',
                            unclear: 'unsure', nobody: 'untouched' };

export const FLAG_TITLE = {
  date_conflict: 'two dates',
  date_shared: 'date shared',
  notes_truncated: 'note cut off',
  no_floor_plan: 'no floor plan',
  no_contact: 'no contact',
};

/** How far away the event is, in words. Says "no date" rather than nothing,
 *  because a third of this board has no date and a blank reads as a bug. */
export function awayLabel(iso, today) {
  const d = daysAway(iso, today);
  if (d === null) return 'no date';
  if (d === 0) return 'today';
  if (d === 1) return 'tomorrow';
  if (d === -1) return 'yesterday';
  return d > 0 ? `in ${d} days` : `${-d} days ago`;
}

/** The sort key inside a group: soonest first, then the undated, then the past.
 *
 *  Three buckets rather than one date comparison, because a plain ascending
 *  sort puts July at the top of "waiting on us" and buries next Saturday under
 *  a month of events that have already happened. The undated sit between: they
 *  are real work with no deadline, and they must not be first or last. */
export function urgency(e, today) {
  const d = daysAway(e.event_date, today);
  if (d === null) return [1, 0];
  return d >= 0 ? [0, d] : [2, -d];
}

export function sortByUrgency(list, today) {
  return (list || []).slice().sort((a, b) => {
    const x = urgency(a, today), y = urgency(b, today);
    return x[0] - y[0] || x[1] - y[1]
        || String(a.name || '').localeCompare(String(b.name || ''));
  });
}

/**
 * One pass, one bucket each — whose move it is, then how soon.
 *
 * The archive is its own bucket and is drawn LAST rather than mixed in by
 * urgency. Thirty-six of the sixty rows are archived, most of them for events
 * that have already happened, and a "waiting on us" list that opened with
 * sixteen dead Julys would be ignored inside a week. It is not dropped — Zak:
 * "the pipeline should reflect all enquiries" — it is just not the top of the
 * list.
 */
export function groupPipeline(enquiries, today) {
  const all = enquiries || [];
  const live = all.filter((e) => !e.archived);
  const out = { live, all };
  for (const k of MOVE_ORDER) {
    out[k] = sortByUrgency(live.filter((e) => e.whose_move === k), today);
  }
  out.archived = all.filter((e) => e.archived).slice().sort((a, b) =>
    String(b.event_date || '').localeCompare(String(a.event_date || '')));
  return out;
}

// ------------------------------------------------------------- staleness
/**
 * How old the capture is, and what that means.
 *
 * There is NO monday token in this repo's Actions secrets, so nothing
 * refreshes this on its own yet. That is stated on the page, in words, above
 * the list — not in a tooltip and not in a footer. A feed that quietly ages
 * while presenting itself as live is worse than no feed at all: the empty
 * screen at least told the truth.
 */
export function feedAge(capturedAt, today) {
  const iso = String(capturedAt || '').slice(0, 10);
  const days = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? daysSince(iso, today) : null;
  return { iso, days };
}

export function stalenessHTML(feed, today) {
  const { iso, days } = feedAge(feed && feed.captured_at, today);
  const old = days === null || days > STALE_DAYS;
  const when = days === null ? 'at a time this feed does not record'
    : days <= 0 ? 'today'
    : days === 1 ? 'yesterday'
    : `${days} days ago`;
  return `<div class="stale${old ? ' bad' : ''}">
    <b>Read from the board ${esc(when)}${iso ? ` (${esc(iso)})` : ''}.</b>
    ${old ? 'These enquiries have moved since. ' : ''}Nothing refreshes this
    automatically yet — it needs a <code>MONDAY_API_TOKEN</code> in the repo's
    Actions secrets. The board itself is always current:
    <a href="${esc((feed && feed.board_url) || '#')}" target="_blank"
       rel="noopener">open the tracker</a>.</div>`;
}

// ------------------------------------------------------------------- rail
export function pipeChips(e, today) {
  const out = [];
  const d = daysAway(e.event_date, today);
  if (d !== null && d >= 0 && d <= 7) {
    out.push(`<span class="chip soon">${d === 0 ? 'today' : d + 'd'}</span>`);
  }
  if (e.group_size) out.push(`<span class="chip">${esc(e.group_size)} pax</span>`);
  const gaps = e.outstanding || [];
  // Four or more gaps is a row nobody has started, and naming each one turns
  // nineteen of these into a wall of identical chips. One chip says the same
  // thing and the panel still lists them.
  if (gaps.length >= 4) {
    out.push(`<span class="chip need">${gaps.length} things outstanding</span>`);
  } else {
    gaps.forEach((g) => out.push(`<span class="chip need">${esc(g)}</span>`));
  }
  (e.flags || []).forEach((f) => out.push(
    `<span class="chip ${f.code === 'no_floor_plan' ? '' : 'bad'}">${
      esc(FLAG_TITLE[f.code] || f.code)}</span>`));
  if (e.deposit) out.push(`<span class="chip ok">${esc(String(e.deposit).toLowerCase())}</span>`);
  return out.join('');
}

export function pipeRowHTML(e, selId, today) {
  const on = selId === e.item_id ? ' on' : '';
  // The evidence line rides on the ROW, trimmed, not only in the panel. The
  // question a glance down sixty rows is asking is "is this mine", and a
  // verdict with nothing behind it is exactly what nobody trusted about the
  // old chase list.
  const ev = e.whose_move_evidence
    ? `<div class="evid">${esc(flat(e.whose_move_evidence).slice(0, 120))}${
        flat(e.whose_move_evidence).length > 120 ? '…' : ''}</div>`
    : '';
  return `<div class="row ${MOVE_CLASS[e.whose_move] || ''}${on}"
      data-pipe="${esc(e.item_id)}" role="button" tabindex="0">
    <div class="l1"><span class="nm">${esc(e.name)}</span>
      <span class="when">${esc(niceDate(e.event_date))} · ${
        esc(awayLabel(e.event_date, today))}</span></div>
    <div class="l2">${pipeChips(e, today)}</div>${ev}</div>`;
}

// The whole board's whose-move split, counted, before anyone searches or
// opens a row. Deliberately built from the UNFILTERED board, not the search
// result: it is the answer to "what is the state of the pipeline", not "what
// did I just filter to", and a summary that shrank with the search box would
// stop being a fixed reference point the moment anyone typed into it. Sits
// above the search box for the same reason a dashboard sits above a search
// bar — orient first, then narrow.
//
// Plain anchors, not buttons: no click handler to wire, and jumping to a
// group that is already on the page is exactly what a hash link is for.
// Nothing here is a second source of truth for the count — every number is
// g[key].length off the same groupPipeline() the sections below are drawn
// from, so the strip and the list under it can never disagree.
export function pipeSummaryHTML(g) {
  if (!g.all.length) return '';
  const stat = (k) => {
    const n = g[k].length;
    return `<a class="pstat ${MOVE_CLASS[k] || ''}${n ? '' : ' zero'}"
        href="#pipe-grp-${k}">
      <span class="pstat-n">${n}</span>
      <span class="pstat-l">${esc(MOVE_TITLE[k])}</span></a>`;
  };
  return `<div class="pstats">${MOVE_ORDER.map(stat).join('')}</div>`;
}

export function pipeRailHTML(feed, query, selId, today) {
  const raw = query || '';
  const q = raw.toLowerCase();
  const match = (e) => !q || (
    [e.name, e.occasion, e.event_date, e.group, e.email, e.notes]
      .join(' ').toLowerCase().includes(q));
  const all = feed.enquiries || [];
  const g = groupPipeline(all.filter(match), today);
  const grp = (title, arr, hint, key) => (!arr.length ? '' :
    `<div class="grp"${key ? ` id="pipe-grp-${key}"` : ''}>${title}<span class="n">${arr.length}</span></div>` +
    (hint ? `<div class="hint" style="margin:-2px 0 6px">${hint}</div>` : '') +
    arr.map((e) => pipeRowHTML(e, selId, today)).join(''));
  const summary = q ? '' : pipeSummaryHTML(groupPipeline(all, today));
  return summary +
    `<div><label for="pq">Search</label><input id="pq" style="width:100%"
      placeholder="name, occasion, date, anything in the notes"
      value="${esc(raw)}"></div>` +
    MOVE_ORDER.map((k) => grp(MOVE_TITLE[k], g[k], MOVE_HINT[k], k)).join('') +
    grp('Archive', g.archived,
        'The board\'s archive group. Kept, because half the board is in it.',
        'archived') +
    (g.all.length ? '' : '<div class="empty">Nothing matches.</div>');
}

// ------------------------------------------------------------------ panel
export function moveHTML(e) {
  return `<div class="move ${MOVE_CLASS[e.whose_move] || ''}">
    <div class="verdict">${esc(MOVE_TITLE[e.whose_move] || e.whose_move)}</div>
    <div class="why">${esc(e.whose_move_why || '')}</div>
    ${e.whose_move_evidence ? `<div class="evid full">${
      e.whose_move_since ? `<b>${esc(e.whose_move_since)}</b> — ` : ''
      }${esc(e.whose_move_evidence)}</div>` : ''}</div>`;
}

export const PIPE_FACTS = [
  ['occasion', 'Occasion'], ['group_size', 'Group size'],
  ['start_time', 'Start time'], ['area', 'Area'],
  ['drinks', 'Drinks'], ['bar_tab_covers', 'Bar tab covers'],
  ['food', 'Food'], ['deposit', 'Deposit'], ['music', 'Music'],
  ['settling_up', 'Settling up'], ['source', 'Heard about us via'],
  ['stage', 'Stage'], ['outcome', 'Outcome'], ['lost_reason', 'Lost reason'],
  ['follow_up_date', 'Follow-up date'], ['email', 'Email'], ['phone', 'Phone'],
];

/** The structured columns, but ONLY the ones that are filled.
 *
 *  An empty box and an unanswered question look identical, and the board is
 *  mostly unanswered questions — so a blank field is left out of the grid and
 *  named in the outstanding list instead, where it is a task rather than a
 *  gap. A row with nothing on it says so in a sentence. */
export function factsHTML(e) {
  const cell = (label, v) =>
    `<div class="met"><div class="k">${esc(label)}</div>
       <div class="v sm">${esc(v)}</div></div>`;
  const cells = PIPE_FACTS
    .filter(([k]) => e[k] !== null && e[k] !== undefined && e[k] !== '')
    .map(([k, label]) => cell(label, e[k]));
  if (e.min_spend_cents != null) cells.push(cell('Min spend quoted', money(e.min_spend_cents)));
  if (e.revenue_dollars != null) cells.push(cell('Revenue taken', money(e.revenue_dollars * 100)));
  return cells.length ? `<div class="mets">${cells.join('')}</div>`
    : `<div class="quiet">Nothing but a name has ever been filled in on this
       row. It is still an enquiry, and it is still somebody.</div>`;
}

export function outstandingHTML(e) {
  const gaps = e.outstanding || [];
  const contact = e.contactable ? '' :
    `<div class="prob">There is no email and no phone on this row, so there is
     nobody to ask — whatever the list above says.</div>`;
  if (!gaps.length) {
    return `<div class="need"><h2>Outstanding</h2>
      <div>Nothing. Everything the booking engine needs is on the row.</div>
      ${contact}</div>`;
  }
  return `<div class="need"><h2>Outstanding</h2><ul>${
    gaps.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>${contact}</div>`;
}

export function pipeFlagsHTML(e) {
  const fl = e.flags || [];
  if (!fl.length) return '';
  return `<div class="card"><h2>Unresolved</h2><ul class="flaglist">${
    fl.map((f) => `<li><b>${esc(FLAG_TITLE[f.code] || f.code)}</b> — ${
      esc(f.note)}</li>`).join('')}</ul></div>`;
}

export function pipeNotesHTML(e) {
  if (!e.notes) {
    return `<div class="card"><h2>The log</h2>
      <div class="quiet">There is no note on this row at all. Nothing has been
      replied, chased or written down.</div></div>`;
  }
  const cut = e.notes_truncated
    ? `<div class="prob">This note is at monday's ${esc(e.notes_chars)}-character
       cap, so it ENDS MID-SENTENCE and what is missing is the most recent
       part. Read the row on the board before acting on it.</div>`
    : '';
  return `<div class="card"><h2>The log</h2>${cut}
    <div class="log full">${esc(e.notes)}</div></div>`;
}

/** What would actually be POSTed, once the server's own vocabulary is applied.
 *
 *  The feed maps the board's labels into the engine's ("SOIRÈE $60pp" is the
 *  engine's "SOIRÈE", because a package name is a price). This is the second
 *  filter, and it can only run in the browser: `accepted_areas` comes off
 *  /api/admin/functions/config, and offering a room the save refuses is worse
 *  than not offering one. */
export function depositPrefill(e, cfg = CONFIG) {
  const p = e && e.brief_prefill;
  if (!p) return null;
  const out = { ...p };
  const areas = cfg.accepted_areas || [];
  if (out.area && areas.length && !areas.includes(out.area)) delete out.area;
  if (out.drink && !DRINKS.includes(out.drink)) delete out.drink;
  return out;
}

/** The brief this enquiry already became, if it did. Matched on source_ref and
 *  nothing else — the same key the server upserts on, so the page and the
 *  engine cannot disagree about which brief belongs to which board row. */
export function briefFor(e, briefs) {
  return (briefs || []).find((b) => b.source_ref && b.source_ref === e.source_ref) || null;
}

/**
 * The deposit hand-off.
 *
 * A brief exists so a deposit link can be minted and a room held — that is the
 * only thing it is for, and it is why sixty enquiries are not sixty briefs.
 * One button, and it says what it will do before it does it: create the brief
 * prefilled from this row, keyed `monday:<item id>` so a second press converges
 * on the same brief instead of making another.
 *
 * It is offered even when something is outstanding, with the consequence
 * spelled out, because the engine will refuse to mint the link and list what
 * is missing — which is the same list, from the authority, on the brief where
 * it can be filled in.
 */
export function depositHTML(e, cfg = CONFIG, briefs = BRIEFS) {
  const have = briefFor(e, briefs);
  if (have) {
    return `<div class="card"><h2>Take a deposit</h2>
      <div>This enquiry already has a brief.</div>
      <div class="acts"><button data-act="gobrief" data-brief="${esc(have.id)}">
        Open the brief</button></div></div>`;
  }
  if (!e.brief_prefill) {
    return `<div class="card"><h2>Take a deposit</h2>
      <div class="prob">Not from here. Harry Gatos has no floor plan in the
      booking engine, so nothing could hold the room even once the money
      landed. Track it here, book it by hand.</div></div>`;
  }
  const gaps = e.outstanding || [];
  const p = depositPrefill(e, cfg);
  const dropped = Object.keys(e.brief_prefill).filter((k) => !(k in p));
  const note = gaps.length
    ? `<div class="hint">${esc(gaps.join(', '))} ${gaps.length === 1 ? 'is' : 'are'}
       still outstanding. The brief will carry everything the board does know;
       the engine will refuse the deposit link until the rest is answered on
       it, and it will say exactly which.</div>`
    : `<div class="hint">Nothing is outstanding, so the link can be minted as
       soon as the brief exists.</div>`;
  const drop = dropped.length
    ? `<div class="hint">${esc(dropped.join(' and '))} will not be carried
       across: this server does not accept the value the board holds.</div>`
    : '';
  return `<div class="card"><h2>Take a deposit</h2>
    <div>Creates a brief from this row as
    <code>${esc(e.source_ref)}</code> and opens it. Pressing it twice updates
    the same brief rather than making a second one.</div>${note}${drop}
    <div class="acts">
      <button class="go" data-act="takedeposit" data-pipe="${esc(e.item_id)}">
        Take a deposit</button>
      <a href="${esc(e.url || '#')}" target="_blank" rel="noopener"
         style="font-size:13px;color:var(--ink)">open on monday →</a>
    </div></div>`;
}

// One rail row, redrawn for the empty panel: same name/when/chips, inside a
// card instead of the rail, and clickable via the SAME data-pipe attribute
// the rail rows use — wire() answers it on the panel too (see pickEnquiry).
// Not exported: it has no reason to exist anywhere but inside the digest
// below, and pipeRowHTML already IS the exported, tested shape for a row
// that lives in the rail.
function pipeNextHTML(e, today) {
  return `<div class="pnext" data-pipe="${esc(e.item_id)}" role="button" tabindex="0">
    <div class="l1"><span class="nm">${esc(e.name)}</span>
      <span class="when">${esc(niceDate(e.event_date))} · ${
        esc(awayLabel(e.event_date, today))}</span></div>
    <div class="l2">${pipeChips(e, today)}</div></div>`;
}

export function pipePanelHTML(e, feed, today, cfg = CONFIG, briefs = BRIEFS) {
  if (!e) {
    const total = (feed && feed.counts && feed.counts.total) || 0;
    const g = groupPipeline(feed.enquiries || [], today);
    // The empty state used to be one quiet sentence. It is now the answer to
    // "what needs me" without a click, because that question is why this tab
    // gets opened — a screen that makes you select something before it will
    // tell you what to do is asking the wrong thing first. Nothing here is a
    // second whose-move computation: g.us is the same array the "Waiting on
    // us" rail group draws from, already sorted soonest-first.
    if (!g.us.length) {
      return `<div class="card"><div class="quiet">Pick an enquiry on the left.
        ${esc(String(total))} of them, every one on the board, archive
        included.${g.live.length ? ' Nothing is waiting on us right now.' : ''}
        </div></div>`;
    }
    const shown = g.us.slice(0, 5);
    const rest = g.us.length - shown.length;
    return `<div class="card"><h2>What needs you</h2>
      <div class="hint" style="margin-top:0">${esc(MOVE_HINT.us)}${
        rest > 0 ? ` ${rest} more after these — see the rail.` : ''}</div>
      ${shown.map((x) => pipeNextHTML(x, today)).join('')}</div>`;
  }
  const head = `<div class="card">
    <div class="strip"><span class="dhead">${esc(e.name)}</span>
      <span>${esc(e.group)}</span>
      ${e.archived ? '<span class="chip">archived</span>' : ''}
      <a href="${esc(e.url || '#')}" target="_blank" rel="noopener">monday →</a>
    </div>
    <div class="hint" style="margin-top:0">${esc(fullDateOrNone(e.event_date))}
      · ${esc(awayLabel(e.event_date, today))}${
        e.group_size ? ` · ${esc(e.group_size)} guests` : ''}</div>
    ${moveHTML(e)}
    ${factsHTML(e)}
  </div>`;
  return head + `<div class="card">${outstandingHTML(e)}</div>`
       + pipeFlagsHTML(e) + depositHTML(e, cfg, briefs) + pipeNotesHTML(e);
}

/** The long date, or the sentence that says there isn't one. `fullDate()`
 *  builds a Date and would answer "Invalid Date" for the third of this board
 *  that has no date at all. */
export const fullDateOrNone = (iso) => (iso ? fullDate(iso) : 'No date on the row');

export function pipeMissingHTML(err) {
  return `<div class="card"><h2>The enquiry feed isn't there</h2>
    <p style="font-size:14px">This tab reads
    <code>${esc(PIPE_FEED_URL)}</code>, published by
    <code>modules/functions/pipeline/build_functions_pipeline.py</code>. It is
    either missing, unreadable, or declaring a schema this page does not know.
    ${err ? `<br><br>What happened: ${esc(err)}` : ''}</p>
    <p style="font-size:14px">This is <b>not</b> the same as "no enquiries".
    The board has sixty of them and it is
    <a href="https://stowaway-bar.monday.com/boards/5027645686" target="_blank"
       rel="noopener">still the place they live</a>.</p></div>`;
}

// ---------------------------------------------------------------- service io
const svcToken = () => SVC || localStorage.getItem(TOKEN_KEY) || '';

/** Supabase first (no paste), then any locally-saved fallback. */
async function ensureToken() {
  if (SVC) return SVC;
  try {
    const t = await Auth.bookingToken();
    if (t) { SVC = t; return SVC; }
  } catch (_) { /* fall through to the manual fallback */ }
  return localStorage.getItem(TOKEN_KEY) || '';
}

const hdrs = () => ({ 'Authorization': 'Bearer ' + svcToken(),
                      'Content-Type': 'application/json' });

async function call(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
  if (!r.ok) {
    // A refused deposit or confirm answers with an OBJECT — what is missing,
    // who is in the way, what to do about it. new Error(object) would flatten
    // that to "[object Object]" and throw away the only actionable part, so
    // the structured detail rides along on the error for the caller to unpack.
    const d = (await r.json().catch(() => ({}))).detail;
    const err = new Error(typeof d === 'string' ? d
      : (d && d.error) || ('HTTP ' + r.status));
    err.detail = d;
    throw err;
  }
  return r;
}

/** One place that turns any thrown error into a sentence, including the
 *  blocking-bookings list a refused confirm carries. */
function why(e) {
  const d = e && e.detail;
  let m = typeof d === 'string' ? d : (d && d.error) || (e && e.message) || 'unknown error';
  if (d && d.blocking && d.blocking.length) {
    m += ' — in the way: ' + d.blocking.map((x) => `${x.time} (${x.covers}p)`).join(', ');
  }
  if (d && d.hint) m += `. ${d.hint}`;
  return m;
}

function say(msg, bad) {
  const el = $('status');
  if (!el) return;
  el.textContent = msg;
  el.style.color = bad ? 'var(--red)' : 'var(--ink-soft)';
}

// -------------------------------------------------------------------- panel
function selHTML(id, val, opts, blank) {
  return `<select id="${id}" data-field="1">` +
    (blank ? `<option value="">${blank}</option>` : '') +
    opts.map((o) => `<option${o === val ? ' selected' : ''}>${esc(o)}</option>`).join('') +
    '</select>';
}

/** The whole detail panel for one brief. Pure given the brief, the config and
 *  the areas, which is what lets the suite prove the deposit card refuses to
 *  offer a button it cannot honour. */
export function panelHTML(b, cfg = CONFIG, areas = AREAS, today) {
  const p = priceOf(b, cfg);
  const d = daysAway(b.date, today);
  const agreed = b.min_spend_is_agreed;
  const areaOpts = areaOptions(areas, cfg, b.area);
  const finalDue = b.final_numbers_due;
  const finalLate = finalDue && b.final_numbers == null && d !== null && d >= 0
                    && d <= (cfg.chase_final_numbers_days || 6);
  const gaps = (b.missing || []).filter((m) => m !== '$100 deposit');
  const problems = b.problems || [];
  const ok = b.can_request_deposit;
  const n = daysSince(b.last_asked, today);

  const head = `<div class="card">
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:start">
      <div>
        <h2 style="font-size:19px">${esc(b.name)}</h2>
        <div class="strip">
          ${b.occasion ? `<span>${esc(b.occasion)}</span>·` : ''}
          ${b.email ? `<a href="mailto:${esc(b.email)}">${esc(b.email)}</a>·` : ''}
          ${b.phone ? `<a href="tel:${esc(b.phone)}">${esc(b.phone)}</a>·` : ''}
          <span>${b.enquiry_source ? 'via ' + esc(b.enquiry_source) : 'source unknown'}</span>
          ${d !== null ? `·<span>${d < 0 ? Math.abs(d) + ' days ago' : d + ' days away'}</span>` : ''}
        </div>
      </div>
      <div style="text-align:right">
        <div class="chip ${b.deposit_status === 'paid' ? 'ok'
                         : b.deposit_status === 'sent' ? '' : 'need'}">${
          b.deposit_status === 'paid' ? 'deposit paid'
          : b.deposit_status === 'sent' ? 'link sent' : 'no deposit'}</div>
        ${b.booking_id ? `<div class="hint">room held · booking ${esc(b.booking_id)}</div>
          <button class="ghost small" data-act="godiary" data-booking="${esc(b.booking_id)}"
            style="margin-top:5px">See it in the diary</button>` : ''}
      </div>
    </div>
  </div>`;

  const quote = `<div class="card">
    <div class="quote ${p.unknown ? 'unknown' : ''}">
      ${p.unknown
        ? `<div class="big">${esc(p.text)}</div><div class="why">${esc(p.why || '')}</div>`
        : `<div class="big">${money(agreed ? b.min_spend_cents : p.cents)}
             <span style="font-size:13px;font-weight:400;color:var(--ink-soft)">
               minimum spend${agreed ? ' — agreed' : ''}</span></div>
           <div class="why">${agreed
              ? `Agreed figure. The standard calculation says ${money(p.cents)}
                 (${p.heads} × ${money(p.rate)}pp).`
              : `${p.heads} guests × ${money(p.rate)}pp. ${esc(p.why)}`}</div>`}
      ${sumsHTML(packageMaths(b, cfg))}
      <div class="acts" style="margin-top:9px">
        <button class="ghost small" data-act="copyquote">Copy the quote</button>
        ${gaps.length ? '<button class="ghost small" data-act="copyask">Copy what to ask</button>' : ''}
      </div>
    </div>
  </div>`;

  const chaseCard = (gaps.length || problems.length) ? `<div class="card need">
    ${problems.length ? `<div class="prob"><b>Problem:</b> ${
      problems.map(esc).join('<br>')}</div>` : ''}
    ${gaps.length ? `<div><b style="font-size:14px">Still to nail down</b>
      <ul>${gaps.map((g) => `<li>${esc(g)}</li>`).join('')}</ul></div>` : ''}
    <div class="acts" style="margin-top:9px">
      <button class="ghost small" data-act="asked">Mark as asked</button>
      <span class="hint" style="margin-top:0">${esc(askedLabel(b.last_asked, today))}${
        n === null ? ' — nobody has written to them yet, so this one is on us'
        : n <= ASKED_FRESH_DAYS ? " — we're waiting on them"
        : ' — long enough to ask again'}</span>
    </div>
  </div>` : '';

  const fields = `<div class="card">
    <div class="kv">
      <div class="f"><label for="f_date">Date</label>
        <input id="f_date" type="date" value="${esc(b.date)}" data-field="1"></div>
      <div class="f"><label for="f_start">Start time</label>
        <input id="f_start" placeholder="7pm" value="${esc(b.start_time)}" data-field="1"></div>
      <div class="f"><label for="f_area">Room</label>${
        selHTML('f_area', b.area, areaOpts, '— not chosen —')}</div>
      <div class="f"><label for="f_guests">Guests</label>
        <input id="f_guests" type="number" min="1" value="${b.guests ?? ''}" data-field="1"></div>
      <div class="f"><label for="f_guests_raw">As they said it</label>
        <input id="f_guests_raw" placeholder="35-65" value="${esc(b.guests_raw)}" data-field="1"></div>
      <div class="f"><label for="f_final">Final numbers${
        finalDue ? ` · due ${esc(niceDate(finalDue))}` : ''}</label>
        <input id="f_final" type="number" min="1" value="${b.final_numbers ?? ''}"
          class="${finalLate ? 'late' : ''}" data-field="1"></div>
      <div class="f"><label for="f_food">Food</label>${
        selHTML('f_food', b.food, FOODS, '— not chosen —')}</div>
      <div class="f"><label for="f_drink">Drinks</label>${
        selHTML('f_drink', b.drink, DRINKS, '— not chosen —')}</div>
      <div class="f"><label for="f_tab">Bar tab terms</label>
        <input id="f_tab" placeholder="open, or beers + wines only"
          value="${esc(b.tab_restriction)}" data-field="1"></div>
      <div class="f"><label for="f_addon">Arrival drink</label>${
        selHTML('f_addon', b.arrival_addon, ADDONS, '— none —')}</div>
      <div class="f"><label for="f_spend">Agreed spend (overrides)</label>
        <input id="f_spend" placeholder="e.g. 2000"
          value="${agreed ? b.min_spend_cents / 100 : ''}" data-field="1"></div>
      <div class="f"><label for="f_occasion">Occasion</label>
        <input id="f_occasion" value="${esc(b.occasion)}" data-field="1"></div>
      <div class="f full"><label for="f_diet">Dietaries</label>
        <input id="f_diet" value="${esc(b.dietaries)}" data-field="1"></div>
      <div class="f full"><label for="f_extras">Extras / requests</label>
        <input id="f_extras" value="${esc(b.extras)}" data-field="1"></div>
    </div>
    <div class="acts" style="margin-top:12px">
      <button id="savebtn" data-act="save">Save</button>
      <span class="hint" id="dirty"></span>
    </div>
    ${finalDue ? `<div class="hint">Refund deadline ${esc(niceDate(b.refundable_until))} ·
        final numbers due ${esc(niceDate(finalDue))}${
        finalLate ? ' — overdue, chase today' : ''}</div>` : ''}
  </div>`;

  const deposit = `<div class="card">
    <h2>Deposit</h2>
    ${b.deposit_status === 'paid'
      ? `<div style="font-size:14px">Paid${
           b.deposit_paid_at ? ' · ' + esc(String(b.deposit_paid_at).replace('T', ' ')) : ''}.
           ${b.booking_id ? 'The room is held.'
             : '<span class="prob">Paid but the room is NOT held — see the log below.</span>'}</div>`
      : ok
        ? `<div style="font-size:14px">Ready. Nothing outstanding.</div>
           <div class="acts" style="margin-top:8px">
             <button class="go" data-act="mint">Send deposit link</button>
             <button class="ghost" data-act="took">Record a deposit taken</button>
             ${selHTML('f_how', 'eftpos', DEPOSIT_METHODS)}
           </div>`
        : `<div style="font-size:14px;color:var(--amber)">Can't take a deposit yet.</div>
           <ul style="margin:6px 0 0 18px;font-size:14px">${
             (b.deposit_blockers || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`}
    ${b.deposit_url ? `<div class="linkbox">
        <input id="dlink" readonly value="${esc(b.deposit_url)}">
        <button class="ghost" data-act="copylink">Copy</button></div>
        <div class="hint">Same link as before — safe to send again.</div>` : ''}
  </div>`;

  const notes = `<div class="card">
    <h2>Notes</h2>
    <div class="log">${esc(b.notes) || '—'}</div>
    <div class="acts" style="margin-top:8px">
      <input id="f_note" style="flex:1" placeholder="Add a note — it gets a date stamp">
      <button class="ghost" data-act="note">Add</button>
    </div>
  </div>`;

  const stages = `<div class="card">
    <div class="acts">
      <button class="ghost" data-act="stage" data-stage="done">Mark done</button>
      <button class="warn" data-act="stage" data-stage="lost">Mark lost</button>
      ${['lost', 'done'].includes(b.stage)
        ? '<button class="ghost" data-act="stage" data-stage="enquiry">Reopen</button>' : ''}
      <span class="hint">Lost and done drop out of the chase list.</span>
    </div>
  </div>`;

  return head + quote + chaseCard + fields + deposit + notes + stages;
}

/** The one-line rate banner. Config that nobody can see is config that nobody
 *  notices is wrong. */
export function ratesLine(cfg) {
  return `${money(cfg.base_rate_cents)}pp standard · ${money(cfg.peak_rate_cents)}pp `
       + `${cfg.peak_days} inside ${cfg.peak_window} · deposit ${money(cfg.deposit_cents)}`
       + (cfg.deposits_live ? ' · online deposits live' : ' · online deposits OFF');
}

// ------------------------------------------------------------------ drawing
function drawModes() {
  const { upcoming } = splitDiary(DIARY, todayInSydney());
  const live = BRIEFS.filter((b) => !['lost', 'done'].includes(b.stage)).length;
  const enquiries = PIPE ? ((PIPE.counts && PIPE.counts.total) || 0) : 0;
  $('modes').innerHTML = modesHTML(MODE, upcoming.length, enquiries, live);
}

/** Switch halves. "New enquiry" belongs to the pipeline and is meaningless
 *  against the diary, so it goes with it rather than sitting there inert. */
function setMode(m) {
  MODE = m;
  const nb = $('newbtn');
  // "New enquiry" makes a BRIEF, so it belongs with the briefs. Against the
  // pipeline it would be a button that writes to the wrong system: a new
  // enquiry belongs on the monday board, where the form puts it.
  if (nb) nb.style.display = (m === 'briefs' ? '' : 'none');
  drawModes(); drawRail(); drawPanel();
}

/** Where the diary opens: the next function if there is one, otherwise the
 *  most recent that has run, otherwise this month. Never a blank month with
 *  nothing on it while four functions sit one click away. */
function defaultFocus() {
  const today = todayInSydney();
  const { upcoming, past } = splitDiary(DIARY, today);
  const f = upcoming[0] || past[0];
  SEL_DATE = f ? f.date : null;
  MONTH = f ? ym(f.date) : ym(today);
}

function pickDate(iso) { SEL_DATE = iso; SEL_FN = null; drawRail(); drawPanel(); }

function openFunction(id) {
  const f = DIARY.find((x) => x.id === id);
  SEL_FN = id;
  if (f) { SEL_DATE = f.date; MONTH = ym(f.date); }
  drawRail(); drawPanel();
}

/** The two halves point at each other. A booking that has a brief can open it;
 *  a brief that holds a room can jump to it. */
async function goBrief(id) {
  if (!BRIEFS.some((b) => b.id === id)) { say('that brief is no longer on file', true); return; }
  setMode('pipeline');
  await openBrief(id);
  drawModes();
}
function goDiary(bookingId) {
  if (!DIARY.some((f) => f.id === bookingId)) {
    say('that booking is not in the diary — it may have been cancelled', true);
    return;
  }
  setMode('diary');
  openFunction(bookingId);
}

function drawRail() {
  if (MODE === 'diary') {
    $('rail').innerHTML = diaryRailHTML(DIARY, SEL_FN, todayInSydney());
    return;
  }
  if (MODE === 'pipeline') {
    if (!PIPE) { $('rail').innerHTML = ''; return; }
    // Same caret dance as the briefs box below, and for the same reason: the
    // input being typed into is inside the markup this replaces.
    const pb = $('pq');
    const praw = pb ? pb.value : '';
    const pfoc = pb !== null && document.activeElement === pb;
    const pcar = pfoc ? pb.selectionStart : 0;
    $('rail').innerHTML = pipeRailHTML(PIPE, praw, PIPE_SEL, todayInSydney());
    const pback = $('pq');
    if (pfoc && pback) { pback.focus(); pback.setSelectionRange(pcar, pcar); }
    return;
  }
  // Read the box before this function destroys it. The search input is part of
  // the markup replaced below, so each keystroke removes the element being
  // typed into. The text survives via value=; focus and caret do not, and
  // without them only one character can be typed per click.
  const box = $('q');
  const raw = box ? box.value : '';
  const focused = box !== null && document.activeElement === box;
  const caret = focused ? box.selectionStart : 0;
  $('rail').innerHTML = railHTML(BRIEFS, CHASE, raw, CUR && CUR.id);
  const back = $('q');
  if (focused && back) { back.focus(); back.setSelectionRange(caret, caret); }
}

function drawPanel() {
  if (MODE === 'pipeline') {
    const today = todayInSydney();
    if (!PIPE) { $('panel').innerHTML = pipeMissingHTML(PIPE_ERR); return; }
    const e = (PIPE.enquiries || []).find((x) => x.item_id === PIPE_SEL) || null;
    $('panel').innerHTML = stalenessHTML(PIPE, today)
                         + pipePanelHTML(e, PIPE, today, CONFIG, BRIEFS);
    return;
  }
  if (MODE === 'diary') {
    $('panel').innerHTML = diaryPanelHTML(DIARY, BYDATE, MONTH || ym(todayInSydney()),
                                          SEL_DATE, SEL_FN, todayInSydney(),
                                          DIARY_ERR, OUTCOME_EDIT);
    return;
  }
  if (!CUR) {
    $('panel').innerHTML =
      '<div class="card"><div class="quiet">Pick an enquiry on the left, or start a new one.</div></div>';
    return;
  }
  $('panel').innerHTML = panelHTML(CUR, CONFIG, AREAS);
  DIRTY = false;
}

function touch() {
  DIRTY = true;
  const el = $('dirty');
  if (el) el.textContent = 'unsaved changes';
}

// ------------------------------------------------------------------ actions
/** What a Save sends. Blank stays blank — F.upsert never overwrites a known
 *  value with an empty one, so an omitted field means "unchanged", not
 *  "cleared". */
export function patchBody(cur, read) {
  const v = (id) => (read(id) || '').trim();
  const out = { name: cur.name };
  const put = (k, val) => { if (val !== '' && val != null) out[k] = val; };
  put('date', v('f_date'));
  put('start_time', v('f_start'));
  put('area', v('f_area'));
  put('guests', v('f_guests') ? +v('f_guests') : null);
  put('guests_raw', v('f_guests_raw'));
  put('final_numbers', v('f_final') ? +v('f_final') : null);
  put('food', v('f_food'));
  put('drink', v('f_drink'));
  put('tab_restriction', v('f_tab'));
  put('arrival_addon', v('f_addon'));
  put('occasion', v('f_occasion'));
  put('dietaries', v('f_diet'));
  put('extras', v('f_extras'));
  const s = v('f_spend');
  if (s) out.min_spend_cents = Math.round(parseFloat(s) * 100);
  return out;
}

/** A note, date-stamped and APPENDED. The notes field is a running log — a
 *  replace would lose the deposit stamps /confirm writes into it. */
export function appendNote(existing, text, stamp) {
  return ((existing || '') + `\n[${stamp}] ${text}`).trim();
}

const noteStamp = () => new Date().toLocaleDateString('en-AU',
  { timeZone: 'Australia/Sydney', day: 'numeric', month: 'short' });

async function save() {
  const btn = $('savebtn');
  try {
    if (btn) btn.disabled = true;
    const read = (id) => { const e = $(id); return e ? e.value : ''; };
    await call(`/api/admin/functions/${CUR.id}`,
      { method: 'PATCH', body: JSON.stringify(patchBody(CUR, read)) });
    say('saved');
    await loadAll(CUR.id);
  } catch (e) { say('save refused: ' + why(e), true); }
  finally { const b = $('savebtn'); if (b) b.disabled = false; }
}

// Every one of these catches. Without it a stale token, a 500 or the wifi
// dropping becomes an unhandled rejection: the note does not appear, the stage
// does not change, and the page says nothing whatsoever — which reads exactly
// like a save that worked.
async function addNote() {
  const box = $('f_note');
  const t = (box && box.value || '').trim();
  if (!t) return;
  try {
    await call(`/api/admin/functions/${CUR.id}`, { method: 'PATCH',
      body: JSON.stringify({ name: CUR.name, notes: appendNote(CUR.notes, t, noteStamp()) }) });
    say('note added');
    await loadAll(CUR.id);
  } catch (e) { say('note not saved: ' + why(e), true); }
}

async function setStage(stage) {
  if (stage === 'lost' && !confirm('Mark this enquiry lost?')) return;
  try {
    await call(`/api/admin/functions/${CUR.id}`, { method: 'PATCH',
      body: JSON.stringify({ name: CUR.name, stage }) });
    say(stage === 'enquiry' ? 'reopened' : 'marked ' + stage);
    await loadAll(CUR.id);
  } catch (e) { say('stage not changed: ' + why(e), true); }
}

/** Stamps the chase SERVER-side: the route uses Sydney's today, so the date
 *  cannot be whatever this laptop's clock believes. */
async function markAsked() {
  try {
    await call(`/api/admin/functions/${CUR.id}/asked`, { method: 'POST' });
    say('marked as asked today');
    await loadAll(CUR.id);
  } catch (e) { say("couldn't mark it asked: " + why(e), true); }
}

async function mintLink() {
  try {
    const r = await (await call(`/api/admin/functions/${CUR.id}/deposit-link`,
      { method: 'POST' })).json();
    say('link ready — copy it into your reply');
    await loadAll(CUR.id);
    // The engine PERSISTS the link, so reopening shows the same one and a
    // second press cannot mint a second live checkout for the same $100.
    if (r.payment_url && $('dlink')) { $('dlink').value = r.payment_url; copyLink(); }
  } catch (e) { say('no link: ' + why(e), true); }
}

async function tookIt() {
  if (!confirm('Record the $100 deposit as taken? This holds the room.')) return;
  try {
    const how = $('f_how') ? $('f_how').value : 'manual';
    const r = await (await call(`/api/admin/functions/${CUR.id}/confirm`,
      { method: 'POST', body: JSON.stringify({ how, note: '' }) })).json();
    say(r.already_held ? 'already held — booking ' + r.id : 'room held — booking ' + r.id);
    await loadAll(CUR.id);
  } catch (e) { say(why(e), true); }
}

// ------------------------------------------------- recording how it went
/**
 * The two-step, as one step.
 *
 * The functions actually in the book are BOOKINGS WITH NO BRIEF -- they were
 * pushed in from the Monday tracker before briefs existed -- and the nine
 * measured columns live on a brief. So recording a night on one of them is two
 * API calls: give the booking a brief, then fill the brief in.
 *
 * They are never two buttons. "Create a brief" is not a thing anybody wants to
 * do; it is a thing the machine needs before it can be told what happened, and
 * a screen that asks for it first is a screen that has handed its own
 * bookkeeping to the user. One control, one sentence saying what it will do,
 * and the form opens on the far side either way.
 *
 * A 409 means somebody made the brief between this diary loading and this
 * click. The engine hands back the id it collided with, so that is the one we
 * open -- refetching and asking again would just lose the click.
 */
async function recordOutcome(bookingId) {
  const f = DIARY.find((x) => x.id === bookingId);
  if (!f) { say('that booking is no longer in the diary', true); return; }
  let briefId = f.brief_id;
  try {
    if (!briefId) {
      try {
        const made = await (await call(
          `/api/admin/functions/diary/${bookingId}/brief`,
          { method: 'POST' })).json();
        briefId = made.id;
        say('brief created — now the numbers');
      } catch (e) {
        const id = e.detail && e.detail.brief_id;
        if (!id) throw e;
        briefId = id;
      }
    }
    // The FULL brief, not the diary row's outcome: the boxes are filled from
    // the raw measured columns, and the PATCH needs the brief's own name.
    const brief = await (await call(`/api/admin/functions/${briefId}`)).json();
    OUTCOME_EDIT = { booking: bookingId, brief };
    await loadAll();
  } catch (e) { say("couldn't open the report: " + why(e), true); }
}

/** Save the measured figures. Nothing derived is sent, so nothing derived can
 *  be wrong: the report is recomputed server-side from what goes up here. */
async function saveOutcome(briefId) {
  const brief = OUTCOME_EDIT && OUTCOME_EDIT.brief;
  if (!brief) return;
  const read = (id) => { const e = $(id); return e ? e.value : ''; };
  try {
    await call(`/api/admin/functions/${briefId || brief.id}`,
      { method: 'PATCH', body: JSON.stringify(outcomeBody(brief, read)) });
    OUTCOME_EDIT = null;
    say('recorded');
    await loadAll();
  } catch (e) { say('not saved: ' + why(e), true); }
}

function cancelOutcome() { OUTCOME_EDIT = null; drawPanel(); }

function copyText(t, msg) {
  navigator.clipboard.writeText(t).then(() => say(msg),
    () => say("couldn't reach the clipboard — select and copy by hand", true));
}
const copyQuote = () => copyText(quoteText(CUR, CONFIG), 'quote copied');
const copyAsk = () => {
  const t = askText(CUR);
  if (!t) { say('nothing outstanding'); return; }
  copyText(t, 'copied');
};
const copyLink = () => { const el = $('dlink'); if (el) copyText(el.value, 'link copied'); };

// --------------------------------------------------------------------- load
/** The computed-GP feed. A STATIC FILE on this origin, published by
 *  modules/functions/pipeline/build_functions_gp.py — not a route on the
 *  booking engine, which knows nothing about cost books.
 *
 *  MISSING IS NOT AN ERROR, and that is load-bearing. Most functions will
 *  never have a tab file; a clean deploy may not have the feed at all. Every
 *  failure here — 404, a proxy serving HTML, invalid JSON, no network — lands
 *  on the same answer as "this night was never costed": no computed report,
 *  the diary says "no report yet", and the record-by-hand form is offered
 *  exactly as before. It must never take the diary down with it.
 *
 *  A feed declaring a schema this page does not know is refused rather than
 *  half-read. The contract is additive-only, so `functions_gp/1` will keep
 *  meaning what it means; something else means the field names may have moved
 *  underneath, and reading them anyway is how a screen ends up drawing an
 *  em dash where a number was. */
export const GP_FEED_URL = '/data/functions_gp.json';
export const GP_FEED_SCHEMA = 'functions_gp/1';

async function loadGpFeed() {
  try {
    const r = await fetch(GP_FEED_URL, { cache: 'no-store' });
    if (!r.ok) return null;
    const d = await r.json();
    return d && d.schema === GP_FEED_SCHEMA ? d : null;
  } catch (_) { return null; }
}

/** The enquiry feed. Refuses a schema it does not know rather than half-reading
 *  it, exactly as loadGpFeed does -- the contract is additive-only, so
 *  `functions_pipeline/1` keeps meaning what it means and anything else means
 *  the field names may have moved underneath. Unlike the GP feed, a failure
 *  here is REPORTED: this tab has nothing else to draw. */
async function loadPipeFeed() {
  try {
    const r = await fetch(PIPE_FEED_URL, { cache: 'no-store' });
    if (!r.ok) return { feed: null, err: `the feed answered HTTP ${r.status}` };
    const d = await r.json();
    if (!d || d.schema !== PIPE_FEED_SCHEMA) {
      return { feed: null,
               err: `it declares schema "${d && d.schema}", and this page reads `
                  + `"${PIPE_FEED_SCHEMA}"` };
    }
    return { feed: d, err: null };
  } catch (err) {
    return { feed: null, err: String((err && err.message) || err) };
  }
}

function pickEnquiry(itemId) { PIPE_SEL = itemId; drawRail(); drawPanel(); }

/** Enquiry -> brief, then the ordinary deposit flow.
 *
 *  One press, two things, because "create a brief" is not something anybody
 *  wants to do -- it is what the machine needs before it can take money. The
 *  body is keyed `monday:<item id>` and create_brief upserts on source_ref, so
 *  a double press, or a future sync, converges on one brief. */
async function takeDeposit(itemId) {
  const e = (PIPE && PIPE.enquiries || []).find((x) => x.item_id === itemId);
  if (!e) { say('that enquiry is no longer in the feed', true); return; }
  const body = depositPrefill(e, CONFIG);
  if (!body) {
    say('Harry Gatos has no floor plan in the booking engine — nothing here '
      + 'can hold that room', true);
    return;
  }
  try {
    const r = await (await call('/api/admin/functions',
      { method: 'POST', body: JSON.stringify(body) })).json();
    say(`brief ready from the monday row — ${r.name}`);
    setMode('briefs');
    await loadAll(r.id);
  } catch (err) { say("couldn't start the brief: " + why(err), true); }
}

async function openBrief(id) {
  if (DIRTY && !confirm('You have unsaved changes. Discard them?')) return;
  try {
    CUR = await (await call(`/api/admin/functions/${id}`)).json();
    MODE = 'briefs';
    drawRail();
    drawPanel();
  } catch (e) { say("couldn't open: " + why(e), true); }
}

async function newBrief() {
  const name = prompt('Who is it? (name is all that’s needed to start)');
  if (!name) return;
  try {
    const r = await (await call('/api/admin/functions',
      { method: 'POST', body: JSON.stringify({ name }) })).json();
    await loadAll(r.id);
  } catch (e) { say("couldn't start it: " + why(e), true); }
}

async function loadAll(keep) {
  try {
    // Promise.all over the PROMISES, not over already-awaited values: four
    // awaits in an array literal run one after another and the page waits for
    // the sum of the round trips instead of the slowest one.
    const [briefs, chase, cfg, areas, diary, gp, pipe] = await Promise.all([
      call('/api/admin/functions').then((r) => r.json()),
      call('/api/admin/functions/chase').then((r) => r.json()),
      call('/api/admin/functions/config').then((r) => r.json()),
      call('/api/admin/areas').then((r) => r.json()),
      // Caught HERE rather than left to the Promise.all, so a diary that is
      // down takes the diary with it and not the pipeline as well. The other
      // four still reject on a bad token, which is what re-shows the box.
      call(`/api/admin/functions/diary?from=${DIARY_FROM}`).then((r) => r.json())
        .catch((e) => ({ error: why(e), functions: [], by_date: [] })),
      // Swallows its own failures — see loadGpFeed. No feed simply means no
      // computed reports, which is the state every function was in yesterday.
      loadGpFeed(),
      // The enquiry feed. Also catches its own failures — a booking engine
      // that is down must not take the sixty enquiries down with it, and a
      // missing enquiry feed must not take the diary down either. The two
      // halves fail independently or neither is trustworthy.
      loadPipeFeed(),
    ]);
    BRIEFS = briefs; CHASE = chase; CONFIG = cfg; AREAS = areas;
    PIPE = pipe.feed; PIPE_ERR = pipe.err;
    // Open on the first thing waiting on us, so the tab lands on work rather
    // than on an empty panel with sixty rows beside it.
    if (PIPE && !PIPE_SEL) {
      const g = groupPipeline(PIPE.enquiries, todayInSydney());
      const first = g.us[0] || g.unclear[0] || g.them[0] || g.live[0];
      PIPE_SEL = first ? first.item_id : null;
    }
    // The join happens ONCE, here, so every renderer downstream reads a row
    // that already knows whether it has a computed report. Joining inside a
    // renderer would run it per draw and give two of them room to disagree.
    DIARY = joinComputedReports(diary.functions || [], gp);
    BYDATE = diary.by_date || [];
    DIARY_ERR = diary.error || null;
    // Only on the first load: a Refresh should leave you on the month and the
    // function you were reading.
    if (!MONTH) defaultFocus();
    drawModes();
    $('rates').textContent = ratesLine(cfg);
    // The config route warns while the public-holiday list still has runway.
    // It is the only thing that will ever say a peak day is about to quote at
    // the base rate, so it goes in front of the count, not behind it.
    if (cfg.warning) say(cfg.warning, true);
    const id = keep || (CUR && CUR.id);
    if (id && BRIEFS.some((b) => b.id === id)) await openBrief(id);
    else { CUR = null; }
    drawRail();
    drawPanel();
    if (!cfg.warning) {
      const { upcoming, past } = splitDiary(DIARY, todayInSydney());
      const mine = PIPE ? (PIPE.counts.by_whose_move || {}).us || 0 : 0;
      const total = PIPE ? PIPE.counts.total : 0;
      say(`${upcoming.length} coming up · ${past.length} already run · `
        + (PIPE ? `${total} enquiries on the board, ${mine} waiting on us`
                : 'the enquiry feed could not be read'));
    }
  } catch (e) {
    if (String(e.message).includes('bad admin token')) {
      SVC = null;
      localStorage.removeItem(TOKEN_KEY);
      showToken();
    } else {
      say('load failed: ' + why(e), true);
    }
  }
}

// ---------------------------------------------------------------------- boot
function showToken() {
  $('tokenbox').style.display = 'block';
  $('main').style.display = 'none';
}

async function init() {
  // Signed in = authorised. The service token comes from Supabase; the paste
  // box only appears if that lookup fails.
  const t = await ensureToken();
  if (!t) { showToken(); return; }
  $('tokenbox').style.display = 'none';
  $('main').style.display = 'block';
  await loadAll();
  setMode(MODE);
}

/** One delegated listener per container. The rail and the panel are redrawn
 *  wholesale on every change, so a listener bound to a row or a button would
 *  be thrown away with it — and inline onclick= cannot reach anything in here,
 *  because a module's top-level names are not globals. */
function wire() {
  const rail = $('rail');
  const railOpen = (t) => {
    const brief = t.closest('[data-open]');
    if (brief) { openBrief(brief.dataset.open); return true; }
    const fn = t.closest('[data-fn]');
    if (fn) { openFunction(fn.dataset.fn); return true; }
    const enq = t.closest('[data-pipe]');
    if (enq) { pickEnquiry(enq.dataset.pipe); return true; }
    return false;
  };
  rail.addEventListener('click', (ev) => railOpen(ev.target));
  rail.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    if (railOpen(ev.target)) ev.preventDefault();
  });
  rail.addEventListener('input', (ev) => {
    if (ev.target.id === 'q' || ev.target.id === 'pq') drawRail();
  });

  $('modes').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-mode]');
    if (b) setMode(b.dataset.mode);
  });

  const panel = $('panel');
  panel.addEventListener('change', (ev) => { if (ev.target.dataset.field) touch(); });
  panel.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const cell = ev.target.closest('[data-date]');
    if (cell) { ev.preventDefault(); pickDate(cell.dataset.date); return; }
    // The "what needs you" digest cards in the empty pipeline panel — same
    // data-pipe attribute the rail rows carry, same keyboard reachability.
    const pipeCard = ev.target.closest('[data-pipe]');
    if (pipeCard) { ev.preventDefault(); pickEnquiry(pipeCard.dataset.pipe); }
  });
  panel.addEventListener('click', (ev) => {
    const mth = ev.target.closest('[data-month]');
    if (mth) {
      MONTH = shiftMonth(MONTH || ym(todayInSydney()),
                         mth.dataset.month === 'next' ? 1 : -1);
      drawPanel();
      return;
    }
    const cell = ev.target.closest('[data-date]');
    if (cell) { pickDate(cell.dataset.date); return; }
    // Only reachable from the empty-panel "what needs you" digest — a real
    // selected enquiry's panel has no data-pipe element of its own to click.
    const pipeCard = ev.target.closest('[data-pipe]');
    if (pipeCard) { pickEnquiry(pipeCard.dataset.pipe); return; }
    const btn = ev.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === 'save') save();
    else if (act === 'note') addNote();
    else if (act === 'asked') markAsked();
    else if (act === 'mint') mintLink();
    else if (act === 'took') tookIt();
    else if (act === 'copyquote') copyQuote();
    else if (act === 'copyask') copyAsk();
    else if (act === 'copylink') copyLink();
    else if (act === 'stage') setStage(btn.dataset.stage);
    else if (act === 'recordoutcome') recordOutcome(btn.dataset.booking);
    else if (act === 'saveoutcome') saveOutcome(btn.dataset.brief);
    else if (act === 'canceloutcome') cancelOutcome();
    else if (act === 'gobrief') goBrief(btn.dataset.brief);
    else if (act === 'godiary') goDiary(btn.dataset.booking);
    else if (act === 'takedeposit') takeDeposit(btn.dataset.pipe);
  });

  $('refreshbtn').addEventListener('click', () => loadAll());
  $('newbtn').addEventListener('click', newBrief);
  $('savetoken').addEventListener('click', () => {
    SVC = $('svc_token').value.trim();
    localStorage.setItem(TOKEN_KEY, SVC);
    init();
  });
}

Auth.gate($('gate'), {
  roles: null,        // open to all signed-in staff, exactly like /bookings/.
                      // NB: client names, emails and phone numbers are visible
                      // here, and so is every agreed price.
  onOk: (user) => {
    $('app').style.display = '';
    $('whotop').innerHTML = `<strong>${esc(user.name)}</strong>`;
    $('signout').onclick = async (e) => {
      e.preventDefault(); await Auth.logout(); location.href = '/';
    };
    wire();
    init();
  },
});
