/**
 * The cost book's open-questions panel — the Flags tab of /recipes/.
 *
 * This half touches the page and nothing else: fetch the feed, put the HTML in
 * the container, wire the one filter. All the deciding — grouping, ordering,
 * wording, what a flag with no dollar figure says — is in ./flags_view.js,
 * which is pure and runs under node in scripts/test_recipe_book_flags.mjs.
 *
 * WHY THERE IS NO LOGIC IN index.html
 * -----------------------------------
 * Every dashboard page's index.html is a shell that fetches JSON and draws.
 * scripts/arch_guard.py fails CI and the deploy when that stops being true of
 * the sales dashboard — R1, zero function declarations in the inline script —
 * after a 138KB index.html that nobody could touch. The guard does not police
 * this page, so the discipline here is a choice; index.html supplies a <div>
 * and one call, and that is all it will ever supply. (arch_guard.py now runs
 * scripts/test_recipe_book_flags.mjs, so flags_view.js is gated on every deploy
 * the same way pnl.js is.)
 *
 * WHAT IT SHOWS
 * -------------
 * data/cost_book_flags.json, built by scripts/build_cost_book_flags.py: one
 * place for everything the cost book still needs from a human, instead of the
 * handoff files nobody opens. Almost every flag on it is DERIVED from data/ on
 * each build, so a flag disappears when the work is done rather than when
 * somebody remembers to delete a line.
 */

import { Feed } from '/_shared/feed.js';
import { flagsHtml } from '/_shared/flags_view.js';

/** Show only the high-severity flags, and hide any section left empty by that. */
function wireFilter(el) {
  const only = el.querySelector('#fl-high');
  if (!only) return;
  only.addEventListener('change', () => {
    for (const n of el.querySelectorAll('.fl')) {
      n.style.display = (only.checked && n.dataset.sev !== 'high') ? 'none' : '';
    }
    for (const s of el.querySelectorAll('.fl-sec')) {
      // The exempt list is not a severity — it must survive the filter, or
      // turning it on would look like those products became work again.
      if (s.querySelector('.fl-exempt')) continue;
      const any = [...s.querySelectorAll('.fl')].some(n => n.style.display !== 'none');
      s.style.display = (only.checked && !any) ? 'none' : '';
    }
  });
}

/**
 * Render the panel into `el`. A missing feed is not an error — the builder may
 * simply not have run on this deploy — so flags_view says so plainly and the
 * rest of the page is untouched. (Feed.load already returns null on a 404 for
 * exactly this reason; see dashboard/_shared/feed.js.)
 */
export async function renderFlags(el) {
  if (!el) return null;
  el.innerHTML = '<div class="fl-loading">Loading the flags…</div>';
  const d = await Feed.load('/data/cost_book_flags.json');
  el.innerHTML = flagsHtml(d);
  wireFilter(el);
  return d;
}
