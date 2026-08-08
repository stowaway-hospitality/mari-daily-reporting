/**
 * /recipes/ — the ONE recipe module, and the only thing its index.html calls.
 *
 * WHAT THIS REPLACED
 * ------------------
 * Two pages. `/recipes-book/` was the costed book (every recipe, its cost, its
 * GP, and the open-questions panel underneath it) and `/recipes/` was the
 * builder (cost a dish, save it, time a prep). They shared a cost book, a sales
 * feed and an audience, and nothing else — you noticed a bad number on one page
 * and retyped the dish name into a search box on the other.
 *
 * Now: four tabs, one page, one sign-in.
 *
 *   Book    every costed recipe. Click a row and it opens in Build.
 *   Build   the builder, untouched, in /_shared/recipe_builder.js.
 *   Prep    the prep timer, likewise.
 *   Flags   data/cost_book_flags.json — everything the book still needs from a
 *           human. It used to render BELOW 913 rows of table, which is why it
 *           kept being reported as missing. It is a tab with a count on it now.
 *
 * URLS ARE A CONTRACT (scripts/build_site.py says so, about this exact class of
 * breakage). /recipes-book/ still resolves — it is a redirect stub that lands
 * on the Book tab — and every hash form is in the routing table in
 * /_shared/recipe_tabs.js, which is pure and tested under node.
 *
 * ROLES ARE UNCHANGED, BOTH WAYS. /recipes/ has always admitted any signed-in
 * user (a chef with no role can still cost a dish; only a kitchen role can
 * save). /recipes-book/ has always required admin/bigchef/stowfood/hgfood/pizza.
 * Merging them must not quietly widen either, so the GATE stays open and the
 * Book and Flags TABS are what the book's roles guard.
 */

import { Auth } from '/_shared/auth.js';
import { hashForTab, recipeFromHash, tabFor, TABS } from '/_shared/recipe_tabs.js';
import { mountBook } from '/_shared/recipe_book.js';
import { mountBuilder, openRecipe } from '/_shared/recipe_builder.js';
import { renderFlags } from '/_shared/flags.js';

// Exactly the list dashboard/recipes-book/index.html gated on before the merge.
const BOOK_ROLES = ['admin', 'bigchef', 'stowfood', 'hgfood', 'pizza'];

const el = (id) => document.getElementById(id);
let ready = null;          // the builder's feeds; a deep link has to wait for it
let allowed = TABS.slice();

/** Show one tab. Buttons, panels and the two sticky action bars move together. */
function show(tab) {
  const t = allowed.includes(tab) ? tab : 'build';
  for (const x of TABS) {
    const b = el('mt-' + x);
    if (b) {
      b.classList.toggle('on', x === t);
      b.setAttribute('aria-selected', x === t ? 'true' : 'false');
    }
    const v = el('view-' + x);
    if (v) v.classList.toggle('hidden', x !== t);
  }
  // The save bar belongs to Build and the log bar to Prep; on Book and Flags
  // neither applies, and a floating "Save recipe" over a read-only table is an
  // invitation to save an empty one.
  el('bar-build').classList.toggle('hidden', t !== 'build');
  el('bar-prep').classList.toggle('hidden', t !== 'prep');
  document.body.classList.toggle('wide', t === 'book' || t === 'flags');
  window.scrollTo(0, 0);
  return t;
}

/** Switch tab AND put it in the address bar, so the tab is linkable and Back works. */
function go(tab) {
  const t = show(tab);
  if (window.location.hash !== hashForTab(t)) {
    window.history.replaceState(null, '', hashForTab(t));
  }
}

/** Open one recipe in the builder, from a book row or from a '#recipe=' link. */
async function open(name) {
  show('build');
  window.history.replaceState(null, '', '#recipe=' + encodeURIComponent(name));
  await ready;
  const msg = el('save-result');
  if (!openRecipe(name)) {
    // Say why. A tab that switched and stayed blank is indistinguishable from a
    // broken deploy, and the real reason is almost always that this row is a
    // Lightspeed recipe whose lines did not survive into data/recipes_full.json.
    el('editing').style.display = '';
    el('editing-name').textContent = name;
    if (msg) msg.textContent = 'No editable copy of this recipe in the feed yet.';
  } else if (msg) {
    msg.textContent = '';
  }
}

export function start() {
  Auth.gate(el('gate'), {
    roles: null,
    onOk: async (user) => {
      el('app').style.display = '';
      el('who').textContent = user.name;

      const canBook = BOOK_ROLES.includes(user.role);
      if (!canBook) {
        allowed = ['build', 'prep'];
        for (const x of ['book', 'flags']) {
          const b = el('mt-' + x);
          if (b) b.remove();
        }
      }

      for (const x of TABS) {
        const b = el('mt-' + x);
        if (b) b.addEventListener('click', () => go(x));
      }

      ready = mountBuilder(user);

      if (canBook) {
        mountBook({ onOpen: open });
        // The count goes ON the tab. The whole reason this panel kept being
        // reported as missing is that it drew below a 913-row table, so nobody
        // scrolled to it and nothing said it was there.
        renderFlags(el('flags')).then((d) => {
          const n = d && d.counts && d.counts.total;
          const b = el('mt-flags-n');
          if (b && n) b.textContent = n;
        });
      }

      const first = tabFor(window.location);
      const deep = recipeFromHash(window.location.hash);
      if (deep) open(deep); else go(first);

      // Back/forward between tabs, and a link pasted into the same open tab.
      window.addEventListener('hashchange', () => {
        const r = recipeFromHash(window.location.hash);
        if (r) open(r); else show(tabFor(window.location));
      });
    },
  });
}
