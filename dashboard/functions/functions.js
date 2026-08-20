/**
 * Functions module — all logic for /functions/ (the HTML is a shell).
 *
 * WHAT THIS IS
 * ------------
 * Two screens behind one segmented control, because "what is on this Saturday"
 * and "who have I not chased" are different questions:
 *
 *   DIARY    — function BOOKINGS: rooms actually held, a month calendar of the
 *              dates that have functions on them, and what has already run.
 *              See the diary section below for why this half had to be added.
 *   PIPELINE — function BRIEFS: the working list (what needs something,
 *              soonest event first), with one brief in the panel, every field
 *              editable, and a live minimum-spend quote that explains itself.
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
// The diary half: the bookings, the server's per-date rollup that drives the
// calendar, and where in it we are looking. DIARY_ERR is held separately from
// "no functions" on purpose — see diaryPanelHTML.
let DIARY = [], BYDATE = [], DIARY_ERR = null;
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

// ------------------------------------------------------------- one function
const MARK_WORDS = { is_function: 'the function flag',
                     notes: 'the note prefix',
                     area: 'the room it is pinned to' };

export function functionCardHTML(f, today) {
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

  return `<div class="card">${head}${when}${headBlock}${open}${contact}${notes}${brief}${foot}</div>`;
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

export function diaryPanelHTML(fns, byDate, month, selDate, selId, today, err) {
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
    if (f) return cal + functionCardHTML(f, today);
  }
  if (selDate) {
    const on = (fns || []).filter((f) => f.date === selDate);
    if (on.length) {
      return cal + dateHeadHTML(selDate, on)
           + on.map((f) => functionCardHTML(f, today)).join('');
    }
  }
  return cal + `<div class="card"><div class="quiet">Pick a date on the calendar,
    or a function on the left.</div></div>`;
}

/** Which of the two questions the screen is answering. */
export function modesHTML(mode, diaryCount, pipelineCount) {
  const b = (id, label, n) => `<button data-mode="${id}"${
    mode === id ? ' class="on"' : ''}>${label}<span class="n">${n}</span></button>`;
  return b('diary', 'Diary', diaryCount) + b('pipeline', 'Pipeline', pipelineCount);
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
  $('modes').innerHTML = modesHTML(MODE, upcoming.length, live);
}

/** Switch halves. "New enquiry" belongs to the pipeline and is meaningless
 *  against the diary, so it goes with it rather than sitting there inert. */
function setMode(m) {
  MODE = m;
  const nb = $('newbtn');
  if (nb) nb.style.display = (m === 'pipeline' ? '' : 'none');
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
  if (MODE === 'diary') {
    $('panel').innerHTML = diaryPanelHTML(DIARY, BYDATE, MONTH || ym(todayInSydney()),
                                          SEL_DATE, SEL_FN, todayInSydney(), DIARY_ERR);
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
async function openBrief(id) {
  if (DIRTY && !confirm('You have unsaved changes. Discard them?')) return;
  try {
    CUR = await (await call(`/api/admin/functions/${id}`)).json();
    MODE = 'pipeline';
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
    const [briefs, chase, cfg, areas, diary] = await Promise.all([
      call('/api/admin/functions').then((r) => r.json()),
      call('/api/admin/functions/chase').then((r) => r.json()),
      call('/api/admin/functions/config').then((r) => r.json()),
      call('/api/admin/areas').then((r) => r.json()),
      // Caught HERE rather than left to the Promise.all, so a diary that is
      // down takes the diary with it and not the pipeline as well. The other
      // four still reject on a bad token, which is what re-shows the box.
      call(`/api/admin/functions/diary?from=${DIARY_FROM}`).then((r) => r.json())
        .catch((e) => ({ error: why(e), functions: [], by_date: [] })),
    ]);
    BRIEFS = briefs; CHASE = chase; CONFIG = cfg; AREAS = areas;
    DIARY = diary.functions || [];
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
      say(`${upcoming.length} coming up · ${past.length} already run · `
        + `${CHASE.length} enquiries need something`);
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
    return false;
  };
  rail.addEventListener('click', (ev) => railOpen(ev.target));
  rail.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    if (railOpen(ev.target)) ev.preventDefault();
  });
  rail.addEventListener('input', (ev) => { if (ev.target.id === 'q') drawRail(); });

  $('modes').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-mode]');
    if (b) setMode(b.dataset.mode);
  });

  const panel = $('panel');
  panel.addEventListener('change', (ev) => { if (ev.target.dataset.field) touch(); });
  panel.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const cell = ev.target.closest('[data-date]');
    if (cell) { ev.preventDefault(); pickDate(cell.dataset.date); }
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
    else if (act === 'gobrief') goBrief(btn.dataset.brief);
    else if (act === 'godiary') goDiary(btn.dataset.booking);
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
