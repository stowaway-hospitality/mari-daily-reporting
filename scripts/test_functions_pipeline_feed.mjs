/* data/functions_pipeline.json, drawn by the real /functions/ module.
   Run: node scripts/test_functions_pipeline_feed.mjs

   WHAT THIS IS FOR
   ----------------
   The Pipeline tab said "0" for weeks while sixty enquiries sat on the monday
   board, and nothing in this repo could tell. Nothing failed: the tab was
   reading the booking engine's `function_briefs` table, that table was empty,
   and an empty list is a perfectly good rendering of an empty table.

   That is the shape of every failure this half has. A feed that publishes
   `whose_move` and a page that reads `whoseMove`; a verdict drawn without the
   log line it was read off; a capture from last week presented as live; a
   "Take a deposit" button on a Harry Gatos row that no floor plan can hold.
   Each one ships green — a pytest proves the derivation, and the derivation is
   fine. What is wrong is the join between the two, and this is the only thing
   that stands on it.

   So this loads the REAL module and draws the REAL feed through it.

   Hermetic: no network, no clock — TODAY is fixed, because "in 3 days" would
   otherwise be a different assertion every morning. Skips cleanly (exit 0)
   when the feed has not been built, per the arch_guard R0 convention.
*/
import fs from 'fs';
import path from 'path';
import { register } from 'node:module';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHARED = path.join(ROOT, 'dashboard/_shared');
const PAGE = path.join(ROOT, 'dashboard/functions');
const FEED = path.join(ROOT, 'data/functions_pipeline.json');
const SCHEMA = path.join(ROOT, 'data/schemas/functions_pipeline.schema.json');

if (!fs.existsSync(FEED)) {
  console.log('data/functions_pipeline.json not built — skipping (0 assertions)');
  process.exit(0);
}

// The day the board was captured. Fixed so "in 99 days" is a fact about the
// data and not about when the suite happened to run.
const TODAY = '2026-08-21';

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

const feed = JSON.parse(fs.readFileSync(FEED, 'utf8'));
const schema = JSON.parse(fs.readFileSync(SCHEMA, 'utf8'));
const src = fs.readFileSync(path.join(PAGE, 'functions.js'), 'utf8');
const html = fs.readFileSync(path.join(PAGE, 'index.html'), 'utf8');
const find = (name) => feed.enquiries.find((e) => e.name === name);

// ------------------------------------------------ the contract, before the page
{
  ok('the feed declares the schema the file in data/schemas describes',
     feed.schema === schema.title, `${feed.schema} vs ${schema.title}`);
  ok('there are enquiries on it — the whole point',
     (feed.enquiries || []).length === 60, String((feed.enquiries || []).length));
  ok('...and the count the tab badge reads agrees with the list',
     feed.counts.total === feed.enquiries.length);
  ok('the archive is on the feed, not filtered out of it',
     feed.counts.by_group.archive === 36, JSON.stringify(feed.counts.by_group));
  ok('it says when the board was read',
     /^\d{4}-\d{2}-\d{2}T/.test(feed.captured_at), feed.captured_at);
  ok('every enquiry carries the monday row it came from',
     feed.enquiries.every((e) => /^monday:\d+$/.test(e.source_ref)
                             && String(e.url || '').includes('monday.com')));
  ok('...and the source_ref is the id, so a re-run upserts instead of doubling',
     feed.enquiries.every((e) => e.source_ref === `monday:${e.item_id}`));
  const moves = new Set(schema.$defs.enquiry.properties.whose_move.enum);
  ok('every verdict is one the schema declares',
     feed.enquiries.every((e) => moves.has(e.whose_move)));
  ok('every verdict says why, in words',
     feed.enquiries.every((e) => typeof e.whose_move_why === 'string'
                             && e.whose_move_why.length > 10));
  ok('every verdict except "nobody" quotes the line it was read off',
     feed.enquiries.every((e) => e.whose_move === 'nobody'
                             || (e.whose_move_evidence || '').length > 0));
  const codes = new Set(schema.$defs.flag.properties.code.enum);
  ok('every flag code is one the schema declares',
     feed.enquiries.every((e) => (e.flags || []).every((f) => codes.has(f.code))));
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
ok('the page module loads without a browser', typeof F.pipeRailHTML === 'function');

// ------------------------------------------------ it reads the feed it claims
{
  ok('the page points at the published feed',
     F.PIPE_FEED_URL === '/data/functions_pipeline.json', F.PIPE_FEED_URL);
  ok('...and refuses a schema it does not know',
     F.PIPE_FEED_SCHEMA === schema.title, F.PIPE_FEED_SCHEMA);
  ok('a feed it cannot read is NOT drawn as "no enquiries"',
     F.pipeMissingHTML('boom').includes('not') &&
     /not.*the same as/i.test(F.pipeMissingHTML('boom')),
     F.pipeMissingHTML('boom').slice(0, 200));
  ok('...and it names the board, which is still where they live',
     F.pipeMissingHTML(null).includes('5027645686'));
  ok('the page module is what fetches it — the shell stays a shell',
     src.includes(F.PIPE_FEED_URL) && !/fetch\s*\(/.test(html)
     && !/<script(?![^>]*src=)/.test(html));
}

// ------------------------------------------------------------ whose move
{
  for (const k of ['us', 'them', 'nobody', 'unclear']) {
    ok(`the page has a heading for the '${k}' verdict`,
       typeof F.MOVE_TITLE[k] === 'string' && F.MOVE_TITLE[k].length > 3,
       'add it to MOVE_TITLE in dashboard/functions/functions.js');
    ok(`...and a left-edge marker for it`,
       typeof F.MOVE_CLASS[k] === 'string' && html.includes('.row.' + F.MOVE_CLASS[k]),
       `${F.MOVE_CLASS[k]} is not styled in the shell`);
  }
  ok('"waiting on us" and "cannot tell" do NOT share a marker — they are '
     + 'different errands', F.MOVE_CLASS.us !== F.MOVE_CLASS.unclear);

  const us = find('Ariyah - 28 Nov');
  const block = F.moveHTML(us);
  ok('the verdict is drawn', block.includes(F.MOVE_TITLE.us), block.slice(0, 200));
  ok('...with the reason beside it', block.includes(F.esc(us.whose_move_why)));
  ok('...and the log line VERBATIM, so it can be checked',
     block.includes(F.esc(us.whose_move_evidence)), block.slice(0, 400));
  ok('...and the date that line was written',
     block.includes(us.whose_move_since), block.slice(0, 400));

  // The evidence is body text inside the same block as the verdict. Not a
  // title=, not a <details>: a verdict that can be screenshotted away from
  // what it was read off is the failure this feed exists to end.
  ok('the evidence is never a tooltip or a collapsible',
     !/title="[^"]*whose_move/i.test(src)
     && !/<details/.test(src) && !/<summary/.test(src));

  const nb = F.moveHTML(find('Charlotte'));
  ok('a row nobody has touched says so rather than showing an empty quote',
     nb.includes(F.MOVE_TITLE.nobody) && !nb.includes('class="evid full"'),
     nb.slice(0, 300));
}

// ------------------------------------------------------- the rail, in order
{
  const rail = F.pipeRailHTML(feed, '', null, TODAY);
  ok('the ball-in-our-court group is drawn FIRST',
     rail.indexOf(F.MOVE_TITLE.us) < rail.indexOf(F.MOVE_TITLE.them)
     && rail.indexOf(F.MOVE_TITLE.us) < rail.indexOf(F.MOVE_TITLE.nobody),
     `us@${rail.indexOf(F.MOVE_TITLE.us)} them@${rail.indexOf(F.MOVE_TITLE.them)}`);
  ok('...and "cannot tell" is drawn before "waiting on them", because it is '
     + 'work and the other is not',
     rail.indexOf(F.MOVE_TITLE.unclear) < rail.indexOf(F.MOVE_TITLE.them));
  ok('the archive is last, kept but not first',
     rail.lastIndexOf('Archive') > rail.indexOf(F.MOVE_TITLE.nobody));
  ok('every live enquiry is somewhere in the rail',
     feed.enquiries.filter((e) => !e.archived)
       .every((e) => rail.includes(`data-pipe="${e.item_id}"`)));
  ok('...and so is every archived one',
     feed.enquiries.filter((e) => e.archived)
       .every((e) => rail.includes(`data-pipe="${e.item_id}"`)));

  const g = F.groupPipeline(feed.enquiries, TODAY);
  ok('inside "waiting on us" the soonest event reads first',
     g.us[0].name === 'Jack - 29 Aug'
     && g.us.every((e) => e.event_date >= g.us[0].event_date),
     g.us.map((e) => `${e.name}:${e.event_date}`).join(' '));
  ok('...and an undated enquiry sits after the dated ones and before the past '
     + 'ones, because it is real work with no deadline',
     F.urgency({ event_date: '2026-12-01' }, TODAY)[0]
       < F.urgency({ event_date: null }, TODAY)[0]
     && F.urgency({ event_date: null }, TODAY)[0]
       < F.urgency({ event_date: '2026-07-01' }, TODAY)[0]);
  ok('a past event does not squat at the top of the list',
     F.urgency({ event_date: '2026-07-01' }, TODAY)[0]
       > F.urgency({ event_date: '2026-12-01' }, TODAY)[0]);
  ok('every live enquiry lands in exactly one group',
     ['us', 'them', 'nobody', 'unclear'].reduce((s, k) => s + g[k].length, 0)
       === g.live.length, String(g.live.length));

  ok('the search matches on anything in the note, not just the name',
     F.pipeRailHTML(feed, 'wristbands', null, TODAY)
       .includes(`data-pipe="${find('Maryanne - 16th aug').item_id}"`));
}

// ------------------------------------------------------------- one row
{
  const e = find('Lisbet - 21 Nov');
  const row = F.pipeRowHTML(e, null, TODAY);
  ok('the row names the enquiry', row.includes(F.esc(e.name)));
  ok('...how far away the event is, not only the date',
     row.includes('in 92 days'), row);
  ok('...how many people', row.includes('100 pax'), row);
  ok('...whose move it is, on the left edge',
     row.includes(`row ${F.MOVE_CLASS.them}`), row.slice(0, 120));
  ok('...and the evidence line, trimmed, on the row itself',
     row.includes('Awaiting customer reply'), row);

  const bare = F.pipeRowHTML(find('CJ Mckenzie'), null, TODAY);
  ok('a row with nothing on it says "no date" rather than drawing a blank',
     bare.includes('no date'), bare);
  ok('...and rolls six identical gaps into one chip instead of six',
     bare.includes('6 things outstanding'), bare);

  const soon = F.pipeRowHTML(find('Christina - 22 Aug'), null, TODAY);
  ok('an event inside the week is chipped', soon.includes('chip soon'), soon);
  ok('...and tomorrow reads as tomorrow', soon.includes('tomorrow'), soon);
}

// ------------------------------------------------------------- the panel
{
  const e = find('Diane - 15 Aug');
  const panel = F.pipePanelHTML(e, feed, TODAY, { accepted_areas: ['Main Hall', 'Old Stow'] }, []);
  for (const [k, label] of F.PIPE_FACTS) {
    if (e[k]) ok(`the panel draws ${label}`, panel.includes(F.esc(String(e[k]))),
                 `${label} = ${e[k]}`);
  }
  ok('the minimum spend is drawn as money, from cents',
     panel.includes('$1,500'), panel.slice(panel.indexOf('Min spend'), 400));
  ok('the note is drawn IN FULL, never summarised',
     panel.includes(F.esc(e.notes)), 'the log is the real record');

  const gaps = F.pipePanelHTML(find('CJ Mckenzie'), feed, TODAY, {}, []);
  ok('a row with nothing filled in says so in a sentence',
     gaps.includes('Nothing but a name'), gaps.slice(0, 400));
  ok('...and its six outstanding items are listed one by one in the panel',
     ['date', 'start time', 'room', 'guest count', 'food choice', 'drink choice']
       .every((g) => gaps.includes(`<li>${g}</li>`)));

  const cut = F.pipePanelHTML(find('Emma - 3 Sep'), feed, TODAY, {}, []);
  ok('a note at monday\'s cap is never presented as the whole record',
     /ENDS MID-SENTENCE/.test(cut), cut.slice(cut.indexOf('The log'), 400));

  const clash = F.pipePanelHTML(find('Marcus - 10th Oct'), feed, TODAY, {}, []);
  ok('the two-dates row shows BOTH dates and resolves neither',
     clash.includes('2026-10-10') && clash.includes('2026-10-03'),
     clash.slice(clash.indexOf('Unresolved'), 600));
}

// --------------------------------------------------------- take a deposit
{
  const e = find('Diane - 15 Aug');
  const cfg = { accepted_areas: ['Main Hall', 'Old Stow'] };
  const body = F.depositPrefill(e, cfg);
  ok('the brief is keyed on the monday row',
     body.source_ref === `monday:${e.item_id}`, JSON.stringify(body).slice(0, 120));
  ok('...and the package name is the one the engine accepts',
     !body.drink || F.DRINKS.includes(body.drink), String(body.drink));
  ok('...and the room is one the server said it would take',
     !body.area || cfg.accepted_areas.includes(body.area), String(body.area));

  const refused = F.depositPrefill(
    { brief_prefill: { source_ref: 'monday:1', name: 'x', venue: 'Stowaway',
                       area: 'Whole venue', drink: 'SOIRÈE $60pp' } }, cfg);
  ok('a room the server would refuse is dropped rather than sent',
     !('area' in refused), JSON.stringify(refused));
  ok('...and so is a package name it does not price',
     !('drink' in refused), JSON.stringify(refused));

  const card = F.depositHTML(e, cfg, []);
  ok('the button says what it will do before it does it',
     card.includes('data-act="takedeposit"') && card.includes(e.source_ref), card.slice(0, 400));
  ok('...and that pressing it twice does not make two briefs',
     /twice/.test(card), card.slice(0, 500));
  ok('the handler is wired to the same action name',
     src.includes("act === 'takedeposit'"));
  ok('...and it POSTs to the briefs route, which upserts on source_ref',
     /call\('\/api\/admin\/functions',\s*\n?\s*\{ method: 'POST'/.test(src),
     'takeDeposit must create the brief through the engine');

  const hg = find('Ruth');
  ok('Harry Gatos has no floor plan, so it is never offered a deposit',
     hg.brief_prefill === null
     && !F.depositHTML(hg, cfg, []).includes('data-act="takedeposit"'),
     F.depositHTML(hg, cfg, []).slice(0, 300));
  ok('...and the card says why rather than just going quiet',
     /floor plan/.test(F.depositHTML(hg, cfg, [])));

  const already = F.depositHTML(e, cfg, [{ id: 'b1', source_ref: e.source_ref }]);
  ok('an enquiry that already has a brief opens it instead of making another',
     already.includes('data-act="gobrief"') && already.includes('b1'), already);
}

// ------------------------------------------------------------- staleness
{
  const banner = F.stalenessHTML(feed, TODAY);
  ok('the age of the capture is stated at body size, above the list',
     banner.includes('Read from the board') && banner.includes('today'), banner);
  ok('...and it says that nothing refreshes it yet, and what would',
     banner.includes('MONDAY_API_TOKEN'), banner);
  ok('...and links to the board, which is always current',
     banner.includes(feed.board_url), banner);

  const old = F.stalenessHTML({ ...feed, captured_at: '2026-08-01T00:00:00Z' }, TODAY);
  ok('a capture older than the threshold is drawn as a warning, not a note',
     old.includes('stale bad') && old.includes('20 days ago'), old);
  ok('...and says out loud that the enquiries have moved since',
     /moved since/.test(old), old);
  ok('the shell styles the banner from tokens',
     html.includes('.stale{') && html.includes('.stale.bad{'));
  ok('the module never draws today\'s date as the capture date',
     !/captured_at\s*=\s*new Date/.test(src));
}

// -------------------------------------------------- three halves, one control
{
  const modes = F.modesHTML('pipeline', 3, 60, 0);
  ok('the pipeline is a tab of its own, beside the diary and the briefs',
     /data-mode="diary"/.test(modes) && /data-mode="pipeline"/.test(modes)
     && /data-mode="briefs"/.test(modes), modes);
  ok('the pipeline badge is the real count, not zero',
     modes.includes('Pipeline<span class="n">60</span>'), modes);
  ok('the briefs keep their own count, which is a different number',
     modes.includes('Briefs<span class="n">0</span>'), modes);
  ok('"New enquiry" belongs to the briefs, not to the board',
     /m === 'briefs' \? '' : 'none'/.test(src));
}

// ------------------------------------------------------------- escaping
// Every string here is a client's own words, typed into a board by a stranger.
{
  const nasty = '<script>alert(1)</script>';
  const e = { item_id: 'x', name: nasty, group: 'Stowaway Bar', archived: false,
    event_date: '2026-09-01', group_size: 10, outstanding: [nasty],
    flags: [{ code: 'no_contact', note: nasty }], notes: nasty, notes_chars: 9,
    notes_truncated: false, whose_move: 'us', whose_move_why: nasty,
    whose_move_evidence: nasty, url: 'https://x/', source_ref: 'monday:1',
    contactable: false, brief_prefill: null };
  for (const [what, out] of [['a row', F.pipeRowHTML(e, null, TODAY)],
                             ['the panel', F.pipePanelHTML(e, feed, TODAY, {}, [])],
                             ['the verdict block', F.moveHTML(e)]]) {
    ok(`${what} escapes a name, a note and an evidence line`,
       !out.includes(nasty), out.slice(0, 200));
  }
}

console.log(`\n${n} functions-pipeline assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
