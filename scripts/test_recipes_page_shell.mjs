/* The merged page holds together.  Run: node scripts/test_recipes_page_shell.mjs

   WHAT THIS IS FOR
   ----------------
   Merging two pages into one is mostly a move, and the way a move breaks is an
   element that used to exist on page A being referenced by script B that now
   runs on the same document. Nothing throws at build time. Nothing 404s.
   `getElementById` returns null, one feature is dead, and it is dead only for
   whoever opens that tab.

   So: read the shell and read every module it pulls in, collect every id each
   module asks the document for, and prove the shell has all of them. Then prove
   the shell still offers every control BOTH old pages offered, by id — because
   "preserve every existing capability" is not a thing you can eyeball across a
   22KB file.

   It also holds the hard rule this repo is built on: NO BUSINESS LOGIC IN
   index.html. scripts/arch_guard.py enforces that on dashboard/sales/index.html
   and fails CI and the deploy over it; the recipe module is now the same shape
   and gets the same guarantee here.
*/
import fs from 'fs'; import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHELL = path.join(ROOT, 'modules/recipes/app/index.html');
const html = fs.readFileSync(SHELL, 'utf8');

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

// --- the shell is a shell ---------------------------------------------------
{
  const inline = [...html.matchAll(/<script type="module">([\s\S]*?)<\/script>/g)]
    .map(m => m[1]).join('\n');
  const noComments = inline.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const fns = [...noComments.matchAll(/^\s*function\s+([A-Za-z0-9_]+)\s*\(/gm)].map(m => m[1]);
  ok('index.html declares no functions — logic lives in modules', fns.length === 0, fns.join(', '));
  ok('...and its whole script is one bootstrap call',
     /import \{ start \} from '\/_shared\/recipes_page\.js';\s*start\(\);/.test(inline),
     inline.trim().slice(0, 200));
  ok('the shell is under the 40KB cap a shell has no business exceeding',
     Buffer.byteLength(html) < 40 * 1024, `${Math.round(Buffer.byteLength(html) / 1024)}KB`);
}

// --- every id a module asks for exists in the shell -------------------------
{
  const MODULES = ['recipes_page.js', 'recipe_book.js', 'recipe_builder.js', 'flags.js'];
  const ids = new Set();
  for (const m of MODULES) {
    const src = fs.readFileSync(path.join(ROOT, 'dashboard/_shared', m), 'utf8');
    ok(`module present and parses: ${m}`, src.length > 100);
    for (const mm of src.matchAll(/getElementById\(\s*'([A-Za-z0-9_-]+)'\s*\)/g)) ids.add(mm[1]);
    // the ones built from a literal + a variable, e.g. 'mt-' + x
    for (const mm of src.matchAll(/getElementById\('([a-z-]+)'\s*\+\s*[a-z]\)/g)) {
      for (const t of ['book', 'build', 'prep', 'flags']) ids.add(mm[1] + t);
    }
  }
  // Ids written by the modules into HTML they generate (lc-0, ct-0, lw-0) are
  // created at render time and are not the shell's to provide.
  const runtime = /^(lc|ct|lw)-/;
  const missing = [...ids].filter(id => !runtime.test(id) && !html.includes(`id="${id}"`));
  ok(`every id the modules read exists in the shell (${ids.size} checked)`,
     missing.length === 0, 'missing: ' + missing.join(', '));
}

// --- both old pages' capabilities survive -----------------------------------
{
  // From /recipes-book/ (the book).
  const book = { q: 'search box', filter: 'the five-way filter',
                 size: 'the size filter', cat: 'the category filter',
                 stat: 'the "N shown" count', body: 'the table itself',
                 flags: 'the flags panel container' };
  // From /recipes/ (the builder + the prep timer).
  const builder = { editq: 'edit-an-existing-recipe search', editlist: 'its results',
                    editing: 'the "editing X" banner', 'editing-name': 'the name in it',
                    dish: 'dish name', sell: 'sell price', sup: 'supplier filter',
                    picklist: 'the ingredient picker', lines: 'the recipe lines',
                    'c-food': 'food cost per serve', 'gp-food': 'the food GP',
                    'gp-marg': 'margin per serve', 'gp-note': 'the GP note',
                    yq: 'batch yield qty', yu: 'batch yield unit',
                    sublist: 'the sub-recipe picker',
                    'save-btn': 'Save', 'save-result': 'the save result',
                    'prep-q': 'the batch search', 'prep-list': 'the batch list',
                    'prep-panel': 'the timer panel', 'prep-name': 'which batch',
                    'prep-avg': 'the last-4 average', clock: 'the clock',
                    timerbtn: 'start/stop', mins: 'type the minutes',
                    whoprep: 'who is prepping', 'log-btn': 'Log this prep',
                    'log-result': 'the log result',
                    'bar-build': 'the save bar', 'bar-prep': 'the log bar' };
  for (const [id, what] of Object.entries({ ...book, ...builder })) {
    ok(`kept from the old pages: ${what} (#${id})`, html.includes(`id="${id}"`));
  }
  ok('the pack-confirm prompt survived the move',
     fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_builder.js'), 'utf8')
       .includes('Pack size needs confirming'));
  ok('the worker save path survived the move',
     fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_builder.js'), 'utf8')
       .includes('${WORKER_URL}/recipes'));
  ok('the prep-log worker path survived the move',
     fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_builder.js'), 'utf8')
       .includes('${WORKER_URL}/prep'));
  ok('the pack-override worker path survived the move',
     fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_builder.js'), 'utf8')
       .includes('${WORKER_URL}/pack'));
  ok('the line plausibility guard is still wired into the builder',
     fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_builder.js'), 'utf8')
       .includes('builderLineWarnings'));
}

// --- the tabs ---------------------------------------------------------------
// The strip is no longer static markup in this file: it is rendered by
// _shared/recipe_tabs.js::navHtml so that /pricing/ and /invoices/ draw the SAME
// strip and the five screens read as one module. So the shell must carry the
// host and the four PANELS, and the strip's own contract is checked against the
// function that now produces it.
{
  ok('the shell hosts the shared tab strip', html.includes('id="maintabs"'));
  for (const t of ['book', 'build', 'prep', 'flags']) {
    ok(`tab panel exists: ${t}`, html.includes(`id="view-${t}"`));
  }
  ok('the builder\'s old two-tab strip is gone',
     !html.includes("onclick=\"mainTab("),
     'mainTab still wired');

  const { navHtml } = await import('../dashboard/_shared/recipe_tabs.js');
  const strip = navHtml('build', { isAdmin: true });
  for (const t of ['book', 'build', 'prep', 'flags']) {
    ok(`tab button exists: ${t}`, strip.includes(`id="mt-${t}"`));
  }
  ok('the tab strip is a tablist', strip.includes('role="tablist"'));
  ok('every entry is a tab', (strip.match(/role="tab"/g) || []).length === 6);
  ok('the flags tab carries a count badge', strip.includes('id="mt-flags-n"'));
  ok('the active tab is the one asked for',
     /id="mt-build"[^>]*aria-selected="true"/.test(strip));
  // Price compare and Invoices are the two that live on their own pages.
  ok('price compare is on the strip', strip.includes('href="/pricing/"'));
  ok('invoices is on the strip for an admin', strip.includes('href="/invoices/"'));
  ok('invoices is NOT on the strip for a chef',
     !navHtml('book', { isAdmin: false }).includes('/invoices/'));
}

// --- the deletions, in the shell itself -------------------------------------
{
  for (const s of ['recipes costed', 'fully on our book', 'avg food GP',
                   'GP alerts (<55%)', 'class="kpi"']) {
    ok(`no summary card in the shell: "${s}"`, !html.includes(s), s);
  }
  ok('no dead .tag.ls style for the deleted chip', !html.includes('.tag.ls{'));
  ok('no dead .ings style for the deleted ingredient list', !html.includes('.ings{'));
  ok('no dead tr.det style for the deleted detail row', !html.includes('tr.det '));
}

// --- the old URL still resolves ---------------------------------------------
{
  const stub = fs.readFileSync(path.join(ROOT, 'dashboard/recipes-book/index.html'), 'utf8');
  ok('/recipes-book/ still exists as a redirect', stub.includes('redirectTarget'));
  ok('...with a no-JavaScript floor', /http-equiv="refresh"/.test(stub));
  ok('...and a visible link for anyone the redirect fails',
     stub.includes('href="/recipes/#book"'));
  ok('...and it is a stub, not a second copy of the book',
     Buffer.byteLength(stub) < 3 * 1024, `${Buffer.byteLength(stub)} bytes`);
  ok('the old page\'s modules moved rather than being duplicated',
     !fs.existsSync(path.join(ROOT, 'dashboard/recipes-book/flags_view.js'))
     && !fs.existsSync(path.join(ROOT, 'dashboard/recipes-book/flags.js')));
}

// --- THE TAB STRIP IS PAINTED BEFORE IT IS WIRED -----------------------------
// 2026-08-15: every tab on /recipes/ was unclickable. Not a 404, not an
// exception — the shell ships `<div id="maintabs"></div>` EMPTY and navHtml
// creates every button at runtime, but the wiring loop that calls
// addEventListener on mt-* ran BEFORE that paint. el('mt-book') was null, the
// listener was never attached, and the buttons painted a moment later had no
// handler. Deep links kept working because 'hashchange' is bound to window, so
// it presented as "the page is fine, the tabs are dead".
//
// The ids all existed, so the id-coverage checks above could not see it. This is
// an ORDER check, which is the only thing that could have.
{
  const page = fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipes_page.js'), 'utf8');
  const paint = page.indexOf('navHtml(');
  const wire = page.indexOf("addEventListener('click'");
  ok('recipes_page.js paints the tab strip (navHtml)', paint > -1);
  ok('recipes_page.js wires the tab buttons by id', wire > -1);
  ok('...and the paint comes BEFORE the wiring, or no tab is clickable',
     paint > -1 && wire > -1 && paint < wire,
     `navHtml at ${paint}, addEventListener at ${wire}`);

  // The shell really does ship the host empty — which is WHY the order matters.
  ok('the shell ships #maintabs empty, so the buttons are runtime-created',
     /<div id="maintabs">\s*<\/div>/.test(html));

  // Painting must not be gated on the book role, or a build/prep-only user gets
  // no strip at all and cannot reach Prep Timer either.
  const gate = page.indexOf('if (canBook) {');
  ok('...and the strip is painted for every role, not only book-readers',
     gate === -1 || paint < gate, `navHtml at ${paint}, if (canBook) at ${gate}`);
}

// --- MUTATION CHECK ----------------------------------------------------------
{
  const broken = html.replace('id="prep-panel"', 'id="prep-panel-OOPS"');
  ok('MUTATION: renaming one id would be caught', !broken.includes('id="prep-panel"'));
  const relogicked = '<script type="module">\nfunction calc(){}\n</script>';
  ok('MUTATION: a function declared in the shell would be caught',
     /^\s*function\s+([A-Za-z0-9_]+)\s*\(/m.test(relogicked));
}

console.log(`\n${n} page-shell assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
