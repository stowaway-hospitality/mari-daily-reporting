/* The recipe book's flags panel, under node.  Run: node scripts/test_recipe_book_flags.mjs

   WHAT THIS IS FOR
   ----------------
   The panel at /recipes-book/ is a work queue. Its whole value is that a human
   reads it and believes it, and the two ways a work queue loses that are:

     * it shows an unknown as a zero, so the reader deprioritises a real problem
       because it "costs nothing";
     * it goes stale, so the reader finds an item that is already done and stops
       trusting the rest.

   Both are display bugs. Neither fails a pytest, neither fails a deploy, and
   both would ship as a wrong number on a chef's screen. So the deciding half of
   the panel is a pure function (dashboard/recipes-book/flags_view.js — the same
   split arch_guard enforces between pnl.js and render.js) and this runs it.

   It runs against the REAL data/cost_book_flags.json when one has been built,
   and against hand-built fixtures for the cases the real feed does not contain
   today. A missing feed skips the real-data half rather than failing: the file
   is generated at build time and deliberately not committed, so a clean
   checkout has not got one.
*/
import fs from 'fs'; import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const view = await import(
  'file://' + path.join(ROOT, 'dashboard/recipes-book/flags_view.js'));

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

// --- an unknown impact is never a dollar figure ----------------------------
// THE POINT OF THE WHOLE FILE. 39 of the 45 flags on the real feed have
// impact_per_year: null, because no honest arithmetic reaches a number for
// them. Rendering that as "$0" would put every one of them at the bottom of a
// queue sorted by money, which is the opposite of the truth.
{
  const f = { severity: 'high', category: 'x', subject: 'S', owner: 'Zak',
              what_is_wrong: 'w', why_it_matters: 'y', action: 'a',
              impact_per_year: null, evidence: [] };
  const w = view.weight(f);
  ok('an unquantified flag says so', w.kind === 'none' && w.text === 'not quantified', w.text);
  ok('...and its row contains no dollar sign in the weight slot',
     !/class="fl-w none">[^<]*\$/.test(view.flagRow(f)));
}
{
  const w = view.weight({ impact_per_year: 2726.03 });
  ok('a measured impact renders per YEAR', w.kind === 'impact' && w.text === '$2,726/yr', w.text);
}
{
  // Revenue at stake is NOT an under-cost, and must not be dressed as one: how
  // much of an uncosted dish's cost is missing is exactly what having no recipe
  // means we cannot say.
  const w = view.weight({ impact_per_year: null, revenue_13wk: 4708.2 });
  ok('uncosted revenue is labelled as revenue, not impact',
     w.kind === 'revenue' && w.text === '$4,708 13wk', w.text);
}

// --- escaping ---------------------------------------------------------------
{
  const html = view.flagRow({ severity: 'low', category: 'c', owner: 'o',
    subject: 'Fancy <b>Pants</b> "Parmy" & Chips', what_is_wrong: 'w',
    why_it_matters: 'y', action: 'a', evidence: ['<script>x</script>'] });
  ok('a product name is escaped', html.includes('Fancy &lt;b&gt;Pants&lt;/b&gt;'));
  ok('evidence is escaped', !html.includes('<script>'));
}

// --- a missing feed explains itself ----------------------------------------
{
  const html = view.flagsHtml(null);
  ok('no feed -> a plain explanation, not a blank panel',
     html.includes('has not been built yet'));
  ok('...and it names the builder', html.includes('build_cost_book_flags.py'));
}

// --- sections only appear when they have something in them -----------------
{
  const cat = { key: 'cook_loss', title: 'Yields', why: 'because' };
  ok('an empty category renders nothing', view.section(cat, []) === '');
  ok('a populated one renders its count',
     view.section(cat, [{ category: 'cook_loss', severity: 'high', subject: 's',
       what_is_wrong: 'w', why_it_matters: 'y', action: 'a', owner: 'o' }])
       .includes('<span class="fl-n">1</span>'));
}

// --- the real feed ----------------------------------------------------------
const feedPath = path.join(ROOT, 'data/cost_book_flags.json');
if (!fs.existsSync(feedPath)) {
  console.log('  (data/cost_book_flags.json not built — skipping the real-data checks)');
} else {
  const d = JSON.parse(fs.readFileSync(feedPath, 'utf8'));
  const html = view.flagsHtml(d);

  ok('every flag reaches the page',
     d.flags.every(f => html.includes(`id="${f.id}"`)),
     `${d.flags.filter(f => !html.includes(`id="${f.id}"`)).map(f => f.id).join(', ')}`);

  // Every flag belongs to a declared category, or the builder added a family
  // the panel silently drops on the floor — the failure mode where a flag
  // exists in the feed and nobody ever sees it.
  const known = new Set((d.categories || []).map(c => c.key));
  const orphans = d.flags.filter(f => !known.has(f.category)).map(f => f.id);
  ok('no flag belongs to a category the panel does not draw', orphans.length === 0,
     orphans.join(', '));

  // The contract each flag owes a reader, per the brief: a stable id, a
  // severity, the thing it is about, what is wrong in one line, an impact (or
  // an explicit null), and what closes it.
  const REQUIRED = ['id', 'severity', 'subject', 'what_is_wrong', 'action', 'owner'];
  const thin = d.flags.filter(f => REQUIRED.some(k => !f[k])
                                || !('impact_per_year' in f));
  ok('every flag carries the full contract', thin.length === 0,
     thin.map(f => f.id).join(', '));

  const ids = d.flags.map(f => f.id);
  ok('ids are unique and stable-looking', new Set(ids).size === ids.length
     && ids.every(i => /^[a-z0-9][a-z0-9\-$.]*$/i.test(i)),
     ids.filter((v, i) => ids.indexOf(v) !== i).join(', '));

  ok('severities are the three the panel knows',
     d.flags.every(f => ['high', 'medium', 'low'].includes(f.severity)));

  // Never a guess: a stated dollar figure must carry its arithmetic.
  const naked = d.flags.filter(f => f.impact_per_year && !f.impact_basis);
  ok('every dollar figure shows its working', naked.length === 0,
     naked.map(f => f.id).join(', '));

  ok('the headline total is the sum of the measured ones',
     Math.abs(d.known_impact_per_year
              - d.flags.reduce((a, f) => a + (Number(f.impact_per_year) || 0), 0)) < 0.02);

  // "Open Price" can never be costed. If it ever appears as a FLAG the exempt
  // list has stopped working, and the top of the queue fills with a payment
  // method wearing a SKU.
  ok('Open Price is exempt, not flagged',
     !d.flags.some(f => /^open (price|food)$/i.test(f.subject || '')));
  ok('...and it is still listed, so nobody re-finds it as work',
     (d.exempt || []).some(e => /^open price$/i.test(e.subject || ''))
     && html.includes('Permanently exempt'));

  // The cook-loss yield is an assumption and the panel must say so where a
  // reader meets the number, not only in the feed.
  ok('the assumed yield is disclosed on the panel',
     html.includes('applied to no cost anywhere'));
}

console.log(`\n${n} flag-panel assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
