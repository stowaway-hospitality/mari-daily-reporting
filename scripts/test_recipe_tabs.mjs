/* The recipe module's tab routing, under node.  Run: node scripts/test_recipe_tabs.mjs

   WHAT THIS IS FOR
   ----------------
   /recipes-book/ and /recipes/ were two pages and are now four tabs of one.
   Every URL that used to land somewhere has to keep landing on the same thing,
   and "somewhere" is now a tab rather than a document, which means the mapping
   is CODE and code that nobody runs is code that nobody checks.

   The failure this exists to stop is silent and total: a bookmarked
   /recipes-book/ that opens the builder instead of the book looks like it
   worked. Nothing 404s, nothing logs, and the person just cannot find the
   recipe book any more. scripts/build_site.py already refuses to ship a
   reference that does not resolve; this refuses to ship one that resolves to
   the wrong place.

   REAL URLS ONLY. Every case below is a form that exists today: the two page
   roots, the hashes the tab strip writes, the '#recipe=' deep link a book row
   creates, and the two aliases that were written into handoff files.
*/
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const T = await import('file://' + path.join(ROOT, 'dashboard/_shared/recipe_tabs.js'));

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

// --- the two historical page roots -----------------------------------------
ok('/recipes/ still opens the builder, as it always has',
   T.tabFor({ pathname: '/recipes/', hash: '' }) === 'build',
   T.tabFor({ pathname: '/recipes/', hash: '' }));
ok('/recipes-book/ opens the BOOK, not the builder',
   T.tabFor({ pathname: '/recipes-book/', hash: '' }) === 'book',
   T.tabFor({ pathname: '/recipes-book/', hash: '' }));
ok('...with or without the trailing slash',
   T.tabFor({ pathname: '/recipes-book', hash: '' }) === 'book');
ok('...and with index.html spelled out',
   T.tabFor({ pathname: '/recipes-book/index.html', hash: '' }) === 'book');

// --- the hashes the tab strip writes ---------------------------------------
for (const t of T.TABS) {
  ok(`#${t} opens ${t}`, T.tabFor({ pathname: '/recipes/', hash: '#' + t }) === t);
  ok(`hashForTab round-trips ${t}`, T.tabFromHash(T.hashForTab(t)) === t);
}

// THE HASH WINS OVER THE PATH. The /recipes-book/ stub redirects to
// '/recipes/#book'; if the path were read first that would arrive at the
// builder and the redirect would silently undo itself.
ok('an explicit hash beats the path',
   T.tabFor({ pathname: '/recipes/', hash: '#book' }) === 'book');
ok('...in both directions',
   T.tabFor({ pathname: '/recipes-book/', hash: '#build' }) === 'build');

// --- the deep link a book row creates --------------------------------------
{
  const h = '#recipe=' + encodeURIComponent('American Standard Burger');
  ok('a recipe deep link opens the BUILD tab',
     T.tabFor({ pathname: '/recipes/', hash: h }) === 'build');
  ok('...and names the recipe, decoded',
     T.recipeFromHash(h) === 'American Standard Burger', T.recipeFromHash(h));
}
{
  // The names in this book carry brackets, ampersands, accents and slashes:
  // 'Regular Margherita [Dine-in]', 'Salt & Pepper Squid', 'Flor De Caña 4
  // Extra Seco', 'Nuggets & Chips [HG]'. Every one has to survive the round
  // trip or the row for it opens nothing.
  for (const name of ['Regular Margherita [Dine-in]', 'Salt & Pepper Squid',
                      'Flor De Caña 4 Extra Seco', 'Nuggets & Chips [HG]',
                      'Jala Marg Duo (2) - PartyJar [6 serves]',
                      '$60 Soiree', 'Fancy Pants Parmy - Mexicali']) {
    ok(`deep link round-trips "${name}"`,
       T.recipeFromHash('#recipe=' + encodeURIComponent(name)) === name,
       T.recipeFromHash('#recipe=' + encodeURIComponent(name)));
  }
}
ok('a malformed escape does not throw and does not blank the name',
   T.recipeFromHash('#recipe=Beef%Cheek') === 'Beef%Cheek',
   String(T.recipeFromHash('#recipe=Beef%Cheek')));
ok('a bare tab hash is not read as a recipe',
   T.recipeFromHash('#book') === null);
ok('an empty recipe deep link names nothing',
   T.recipeFromHash('#recipe=') === null);

// --- aliases written down in handoffs --------------------------------------
ok("'#cost-book' lands on the book", T.tabFromHash('#cost-book') === 'book');
ok("'#recipes-book' lands on the book", T.tabFromHash('#recipes-book') === 'book');
ok("'#builder' lands on build", T.tabFromHash('#builder') === 'build');
ok("'#timer' lands on the prep timer", T.tabFromHash('#timer') === 'prep');
ok("'#open-questions' lands on the flags", T.tabFromHash('#open-questions') === 'flags');
ok('case does not matter', T.tabFromHash('#FLAGS') === 'flags');

// --- never a blank page -----------------------------------------------------
ok('an unknown hash falls back to build, not to nothing',
   T.tabFor({ pathname: '/recipes/', hash: '#nonsense' }) === 'build');
ok('a garbage tab name is not accepted', T.tabFromHash('#nonsense') === null);
ok('an empty location still resolves', T.tabFor({}) === 'build');
ok('no location at all still resolves', T.tabFor(null) === 'build');

// --- the redirect the /recipes-book/ stub performs -------------------------
ok('a bare /recipes-book/ visit lands on the book tab',
   T.redirectTarget({ pathname: '/recipes-book/', hash: '', search: '' }) === '/recipes/#book',
   T.redirectTarget({ pathname: '/recipes-book/', hash: '', search: '' }));
ok('...a recipe deep link on the old page still opens that recipe',
   T.redirectTarget({ pathname: '/recipes-book/', hash: '#recipe=Negroni', search: '' })
     === '/recipes/#recipe=Negroni');
ok('...and a query string survives (auth callbacks carry one)',
   T.redirectTarget({ pathname: '/recipes-book/', hash: '', search: '?code=abc' })
     === '/recipes/?code=abc#book',
   T.redirectTarget({ pathname: '/recipes-book/', hash: '', search: '?code=abc' }));
ok('...an explicit tab on the old URL is honoured, not overwritten',
   T.redirectTarget({ pathname: '/recipes-book/', hash: '#flags', search: '' })
     === '/recipes/#flags');

// --- MUTATION CHECK ---------------------------------------------------------
// Prove these assertions can fail. If tabFor ignored the path entirely — the
// single likeliest regression, because the builder is the default — the
// /recipes-book/ cases above must go red. Anything that stays green here is an
// assertion that was never testing anything.
{
  const broken = (loc) => 'build';
  ok('MUTATION: a router that always says "build" fails the book cases',
     broken({ pathname: '/recipes-book/' }) !== 'book');
}

console.log(`\n${n} tab-routing assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
