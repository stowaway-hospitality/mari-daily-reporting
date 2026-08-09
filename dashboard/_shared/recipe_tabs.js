/**
 * Which tab /recipes/ opens on, as a PURE function of the URL.
 *
 * WHY THIS IS A MODULE AND NOT THREE LINES IN THE PAGE
 * ---------------------------------------------------
 * The recipe BOOK (/recipes-book/) and the recipe BUILDER (/recipes/) used to be
 * two pages. They are now one page with tabs, and the old URLs are bookmarked,
 * pasted into chats, and linked from data/cost_book_flags.json's own action text
 * ("the builder at /recipes/ warns on this as it is typed"). A URL is a
 * contract — scripts/build_site.py says so at the top of the file, about this
 * exact class of breakage — so every URL that used to land somewhere must still
 * land on the same thing.
 *
 * That is a routing table with real cases in it, and a routing table nobody can
 * run under node is a routing table nobody checks. So it is pure, it takes a
 * plain object instead of `location`, and scripts/test_recipe_tabs.mjs drives
 * every historical URL through it.
 *
 * THE CONTRACT
 * ------------
 *   /recipes/                    -> build   (what /recipes/ has always opened on)
 *   /recipes-book/               -> book    (redirected; the stub sends it here)
 *   /recipes/#book|#build|#prep|#flags -> that tab, explicitly
 *   /recipes/#recipe=Negroni     -> build, with Negroni loaded
 *   anything unrecognised        -> build, never a blank page
 */

export const TABS = ['book', 'build', 'prep', 'flags'];

/** The tab a hash names, or null. Case- and '#'-insensitive. */
export function tabFromHash(hash) {
  const h = String(hash ?? '').replace(/^#/, '').trim().toLowerCase();
  if (!h) return null;
  if (TABS.includes(h)) return h;
  // A recipe deep link is a request to EDIT that recipe, so it is the build tab.
  if (h.startsWith('recipe=')) return 'build';
  // Historical aliases. '#recipes-book' and '#cost-book' were both written down
  // in handoffs; neither should 404 a person into the wrong tab.
  if (h === 'recipes-book' || h === 'cost-book' || h === 'recipes_book') return 'book';
  if (h === 'builder' || h === 'recipes') return 'build';
  if (h === 'prep-timer' || h === 'timer') return 'prep';
  if (h === 'flag' || h === 'queue' || h === 'open-questions') return 'flags';
  return null;
}

/** The product a '#recipe=' deep link names, or null. Decoded, never throws. */
export function recipeFromHash(hash) {
  const h = String(hash ?? '').replace(/^#/, '');
  const m = /^recipe=(.*)$/i.exec(h);
  if (!m) return null;
  try {
    const name = decodeURIComponent(m[1]);
    return name.trim() || null;
  } catch (e) {
    return m[1].trim() || null;      // a stray % is not a reason to show nothing
  }
}

/**
 * The tab this location opens on. `loc` is { pathname, hash } — a plain object,
 * so the whole routing table is testable without a browser.
 *
 * The hash wins over the path, because the hash is the explicit request: the
 * /recipes-book/ stub redirects to '/recipes/#book' and must not be re-read as
 * "path says /recipes/, so build".
 */
export function tabFor(loc) {
  const l = loc || {};
  const fromHash = tabFromHash(l.hash);
  if (fromHash) return fromHash;
  const path = String(l.pathname ?? '').toLowerCase();
  if (path.includes('/recipes-book')) return 'book';
  return 'build';
}

/** The hash a tab should put in the address bar. */
export const hashForTab = (tab) => '#' + (TABS.includes(tab) ? tab : 'build');

/**
 * Where the old /recipes-book/ page must send a visitor, preserving anything
 * they arrived with. A bare visit lands on the book; a '#recipe=' deep link on
 * that page was always a request to open one recipe, so it keeps meaning that.
 */
export function redirectTarget(loc) {
  const l = loc || {};
  const hash = String(l.hash ?? '');
  const search = String(l.search ?? '');
  const keep = tabFromHash(hash) ? hash : hashForTab('book');
  return '/recipes/' + search + keep;
}

/* ---------------------------------------------------------------------------
 * THE MODULE STRIP
 *
 * Book, Build, Prep and Flags are tabs INSIDE this page. Price compare and
 * Invoices are their own pages. To a person that distinction is meaningless —
 * it is one job (what a dish costs and what we paid for it) split across five
 * screens — so all five render the SAME strip and read as one module. The two
 * outside tabs navigate; the four inside ones switch in place.
 *
 * Invoices is admin-only and MUST stay that way: a chef with a kitchen role can
 * open this module, and supplier bills are not theirs to see. The strip omits
 * the tab entirely rather than showing one that errors on click — but the real
 * enforcement is still Auth.gate on /invoices/ itself, because a hidden button
 * is decoration, not a permission.
 * ------------------------------------------------------------------------- */

export const NAV = [
  { key: 'book',     label: 'Book',           href: '/recipes/#book',  inPage: true },
  { key: 'build',    label: 'Build a recipe', href: '/recipes/#build', inPage: true },
  { key: 'prep',     label: 'Prep timer',     href: '/recipes/#prep',  inPage: true },
  { key: 'flags',    label: 'Flags',          href: '/recipes/#flags', inPage: true, count: true },
  { key: 'pricing',  label: 'Price compare',  href: '/pricing/' },
  { key: 'invoices', label: 'Invoices',       href: '/invoices/', adminOnly: true },
];

/** The strip entry a location is on. Pages first, then the in-page tab. */
export function navFor(loc) {
  const path = String((loc || {}).pathname ?? '').toLowerCase();
  if (path.includes('/pricing')) return 'pricing';
  if (path.includes('/invoices')) return 'invoices';
  return tabFor(loc);
}

/** Which entries a role may see. Unknown/!admin never gets invoices. */
export const navFordRole = (isAdmin) => NAV.filter(n => !n.adminOnly || !!isAdmin);

/**
 * The strip's markup. Pure: same input, same string, so it is testable under
 * node. `active` is a NAV key; in-page tabs keep their historical button ids
 * (mt-book etc.) so the page's own wiring is untouched.
 */
export function navHtml(active, opts) {
  const o = opts || {};
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const n = o.flagCount;
  return '<div class="maintabs" role="tablist">'
    + navFordRole(o.isAdmin).map((t) => {
        const on = t.key === active;
        const count = (t.count && n != null && n !== '') ? `<span class="n" id="mt-flags-n">${esc(n)}</span>` : (t.count ? '<span class="n" id="mt-flags-n"></span>' : '');
        return t.inPage
          ? `<button id="mt-${t.key}" role="tab" aria-controls="view-${t.key}" aria-selected="${on}"${on ? ' class="on"' : ''}>${esc(t.label)}${count}</button>`
          : `<a class="mt-link${on ? ' on' : ''}" role="tab" aria-selected="${on}" href="${esc(t.href)}">${esc(t.label)}</a>`;
      }).join('')
    + '</div>';
}
