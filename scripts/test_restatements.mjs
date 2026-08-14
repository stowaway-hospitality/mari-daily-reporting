/* The restatement panel — run: node scripts/test_restatements.mjs
 *
 * The panel exists because Marilyna's July moved for weeks after July ended and
 * nothing said so. So the things worth testing are: does a real move survive to
 * the screen, is the DIRECTION of travel shown rather than only the last hop, and
 * does "final" mean what it says. A panel that quietly drops a restatement is the
 * same failure as having no panel.
 */
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const V = await import('file://' + path.join(ROOT, 'dashboard/_shared/restatements.js'));

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

const feed = {
  series: {
    'mari|2026-07|mari_overheads': {
      venue: 'mari', period: '2026-07', label: 'Overheads', first: 1312.57,
      latest: 5662.98, delta: 4350.41, pct: 331.4, revision_count: 8,
      quiet_days: 4, maturity: 'provisional',
    },
    'mari|2026-07|mari_uber_fees': {
      venue: 'mari', period: '2026-07', label: 'Uber fees', first: 2130.37,
      latest: 8528.99, delta: 6398.62, pct: 300.4, revision_count: 6,
      quiet_days: 11, maturity: 'provisional',
    },
    'stow|2026-04|stow_overheads': {
      venue: 'stow', period: '2026-04', label: 'Overheads', first: 42422.77,
      latest: 30286.08, delta: -12136.69, pct: -28.6, revision_count: 5,
      quiet_days: 18, maturity: 'final',
    },
    'hg|2026-08|hg_overheads': {          // never moved — must not appear
      venue: 'hg', period: '2026-08', label: 'Overheads', first: 100, latest: 100,
      delta: 0, pct: 0, revision_count: 1, quiet_days: 0, maturity: 'provisional',
    },
  },
};

{
  const rows = V.restated(feed);
  ok('a figure that never moved is not a restatement', rows.length === 3, String(rows.length));
  // By ABSOLUTE dollars: Stowaway's -$12,137 outranks Marilyna's +$6,399. A
  // restatement that took cost OUT matters as much as one that put it in.
  ok('biggest dollar swing leads, either direction',
     rows[0].venue === 'stow' && Math.abs(rows[0].delta) === 12136.69,
     `${rows[0].venue} ${rows[0].delta}`);
  ok('rows are ordered by absolute swing',
     rows.every((r, i) => i === 0 || Math.abs(rows[i - 1].delta) >= Math.abs(r.delta)));
  ok('scoping to a venue works', V.restated(feed, { venue: 'mari' }).length === 2);
  ok('sinceDays keeps only what moved recently',
     V.restated(feed, { sinceDays: 7 }).every(s => s.quiet_days <= 7));
}

{
  // The whole move, not the last hop. Marilyna's last hop was +$527, which reads
  // as noise; the move was +$4,350, which does not. This is the point of the panel.
  const l = V.line(feed.series['mari|2026-07|mari_overheads']);
  ok('the line names the venue and period', /Marilyna's 2026-07/.test(l), l);
  ok('it shows first -> latest', /\$1,313.*\$5,663/.test(l), l);
  ok('it shows the whole move, not the last hop', /\+\$4,350/.test(l), l);
  ok('it says how many times it was restated', /8 revisions/.test(l), l);
  ok('it carries the status word', /provisional/.test(l), l);
}

{
  const html = V.panelHtml(feed);
  ok('every restatement reaches the HTML',
     ['Overheads', 'Uber fees'].every(t => html.includes(t)));
  ok('the never-moved figure stays out', !/hg|Harry Gatos/.test(html));
  ok('it counts what is still moving', /<b>2<\/b>/.test(html));
  ok('a negative move is drawn as one', html.includes('down') && html.includes('−$12,137'));
  ok('final is shown as final', /rs-chip good[^>]*>final/.test(html));
  ok('provisional explains itself on hover', /still moving/.test(html));
}

{
  const empty = V.panelHtml({ series: {} });
  ok('an empty ledger says nothing has changed, not "no data"',
     /No reported figure has changed/.test(empty));
  ok('...and explains what would put something here', /late supplier bills/i.test(empty));
}

console.log(`\n${n} restatement assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
