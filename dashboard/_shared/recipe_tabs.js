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
