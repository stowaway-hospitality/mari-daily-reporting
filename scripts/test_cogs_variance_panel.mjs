/* The "COGS — bought vs used" panel: does it render, and does it render HONESTLY?

   The number it shows is Xero purchases minus our recipe cost = stock movement +
   waste + theft. It is easy to render a confident dollar figure that should not
   be read as a loss, because consumption is only real to the extent products
   have recipes — Harry Gatos has ~31% of revenue with no cost behind it, so its
   gap is inflated BY CONSTRUCTION. These tests exist mostly to hold that caveat
   in place.

   Run: node scripts/test_cogs_variance_panel.mjs */
import fs from 'fs'; import vm from 'vm'; import path from 'path';
import { fileURLToPath } from 'url';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

let fails = 0, n = 0;
const ok = (cond, msg) => { n++; if (!cond) { fails++; console.log('✗ ' + msg); } };

// --- a DOM stub just big enough for the two functions under test -------------
function makeCtx(role) {
  const els = {};
  const ctx = vm.createContext({
    console, Math, Date, JSON, isNaN, parseFloat, parseInt, Number, Object, Array,
    String, Set, Map, Boolean, RegExp, Intl,
    document: { getElementById: id => (els[id] = els[id] || { innerHTML: '' }) },
  });
  ctx.STATE = {}; ctx.CURRENT_ROLE = role;
  ctx.COGS_TARGET_PCT = 22; ctx.IS_DARK = true;
  const src = fs.readFileSync(path.join(ROOT, 'dashboard/_shared/render.js'), 'utf8');
  // Take just the two functions — render.js as a whole pulls in Chart.js et al.
  const grab = (name) => {
    const i = src.indexOf('function ' + name + '(');
    if (i < 0) throw new Error('not found: ' + name);
    let d = 0, j = src.indexOf('{', i);
    for (let k = j; k < src.length; k++) {
      if (src[k] === '{') d++; else if (src[k] === '}') { d--; if (!d) return src.slice(i, k + 1); }
    }
    throw new Error('unbalanced: ' + name);
  };
  vm.runInContext(grab('cogsVarianceWeek') + '\n' + grab('renderCogsVariance'), ctx);
  ctx.__els = els;
  return ctx;
}

const week = (over) => Object.assign({
  week_ending: '2026-08-02', days: 7, revenue_ex_gst: 10000,
  purchases_ex_gst: 3000, consumption_ex_gst: 2000, variance: 1000,
  variance_pct_of_revenue: 10, rolling_weeks: 4, rolling_purchases_ex_gst: 12000,
  rolling_consumption_ex_gst: 8000, rolling_revenue_ex_gst: 40000,
  rolling_variance: 4000, rolling_variance_pct_of_revenue: 10,
  recipe_coverage_pct: null, coverage_measured_on_pct_of_revenue: null,
  trustworthy: false,
}, over || {});

const feed = (w) => ({ coverage_floor: 60, roll_weeks: 4,
  venues: { stow: { label: 'Stowaway', weeks: [w] } } });

// --- it renders at all ------------------------------------------------------
{
  const c = makeCtx('admin');
  c.STATE = { currentVenue: 'stow', cogsVariance: feed(week()) };
  vm.runInContext("renderCogsVariance('2026-08-02')", c);
  const html = c.__els['cogs-variance'].innerHTML;
  ok(html.includes('bought vs used'), 'panel renders a heading');
  ok(html.includes('$4,000'), 'headline is the 4-week rolling variance, not one week');
  ok(html.includes('10.0% of revenue'), 'rolling % uses ROLLING revenue, not one week');
  ok(html.includes('$3,000') && html.includes('$2,000'), 'shows bought and used');
}

// --- the caveat is the point ------------------------------------------------
{
  const c = makeCtx('admin');
  c.STATE = { currentVenue: 'stow', cogsVariance: feed(week()) };
  vm.runInContext("renderCogsVariance('2026-08-02')", c);
  const html = c.__els['cogs-variance'].innerHTML;
  ok(/unknown/i.test(html) && /question, not a loss/i.test(html),
    'unknown coverage must say so — a bare figure would read as a loss');
}
{
  const c = makeCtx('admin');
  c.STATE = { currentVenue: 'stow',
    cogsVariance: feed(week({ recipe_coverage_pct: 31, coverage_measured_on_pct_of_revenue: 100 })) };
  vm.runInContext("renderCogsVariance('2026-08-02')", c);
  const html = c.__els['cogs-variance'].innerHTML;
  ok(/only 31%/.test(html) && /cannot cost yet/i.test(html),
    'low coverage must be named, with why it inflates the gap');
}
{
  const c = makeCtx('admin');
  c.STATE = { currentVenue: 'stow',
    cogsVariance: feed(week({ recipe_coverage_pct: 88, coverage_measured_on_pct_of_revenue: 95, trustworthy: true })) };
  vm.runInContext("renderCogsVariance('2026-08-02')", c);
  ok(/coverage 88%/i.test(c.__els['cogs-variance'].innerHTML),
    'good coverage is stated plainly');
}

// --- it is admin-only, like every other economic feed on this page ----------
{
  const c = makeCtx('manager');
  c.STATE = { currentVenue: 'stow', cogsVariance: feed(week()) };
  vm.runInContext("renderCogsVariance('2026-08-02')", c);
  ok(c.__els['cogs-variance'].innerHTML === '',
    'a manager role must not see purchases or profit');
}

// --- absent / partial feed must not throw or draw a lie --------------------
for (const st of [{}, { cogsVariance: null }, { cogsVariance: { venues: {} } },
                  { cogsVariance: { venues: { stow: { weeks: [] } } } }]) {
  const c = makeCtx('admin');
  c.STATE = Object.assign({ currentVenue: 'stow' }, st);
  let threw = false;
  try { vm.runInContext("renderCogsVariance('2026-08-02')", c); } catch (e) { threw = true; }
  ok(!threw, 'missing feed must not throw: ' + JSON.stringify(st));
  ok(c.__els['cogs-variance'].innerHTML === '', 'missing feed draws nothing');
}

// --- group view sums the venues -------------------------------------------
{
  const c = makeCtx('admin');
  c.STATE = { currentVenue: 'group', cogsVariance: { coverage_floor: 60, roll_weeks: 4, venues: {
    stow: { weeks: [week()] }, hg: { weeks: [week()] }, mari: { weeks: [week()] } } } };
  vm.runInContext("renderCogsVariance('2026-08-02')", c);
  ok(c.__els['cogs-variance'].innerHTML.includes('$12,000'),
    'group rolling variance is the sum of the three venues (3 x $4,000)');
}

// --- the real feed, if it is built ----------------------------------------
const real = path.join(ROOT, 'data/cogs_variance.json');
if (fs.existsSync(real)) {
  const f = JSON.parse(fs.readFileSync(real, 'utf8'));
  for (const v of ['stow', 'hg', 'mari']) {
    const c = makeCtx('admin');
    c.STATE = { currentVenue: v, cogsVariance: f };
    let threw = null;
    try { vm.runInContext("renderCogsVariance(null)", c); } catch (e) { threw = e.message; }
    ok(!threw, `real feed renders for ${v}: ${threw}`);
    const html = c.__els['cogs-variance'].innerHTML;
    ok(html.includes('bought vs used'), `real feed produces a panel for ${v}`);
    ok(!/NaN|undefined|Infinity/.test(html), `no NaN/undefined in the ${v} panel`);
  }
}

console.log(`\n${n} cogs-variance panel assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
