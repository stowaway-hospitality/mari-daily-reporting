/**
 * The BUILD and PREP TIMER tabs of /recipes/ — cost a dish, time a prep, save.
 *
 * Lifted VERBATIM out of modules/recipes/app/index.html when the recipe book
 * and the recipe builder became one module. Nothing about the save path, the
 * worker calls, the pack-confirm prompt or the GP maths changed in the move —
 * this is a live write surface for three kitchens and the only safe way to
 * merge two pages is to move the code, not to rewrite it.
 *
 * WHY IT MOVED AT ALL
 * -------------------
 * The merged page now carries the book, the builder, the prep timer and the
 * flags. Left inline it would have been a 1,300-line index.html, which is
 * exactly the shape dashboard/_shared/README.md exists to describe the cost of:
 * a 194KB sales/index.html with the P&L maths inches from a UI tweak, nothing
 * tested, every change eyeballed. index.html is now markup plus one bootstrap
 * call, and scripts/arch_guard.py holds it there.
 *
 * WHAT IS STILL HERE THAT SHOULD NOT BE
 * ------------------------------------
 * The builder's own maths (lineCost, lineRate, the batch-vs-menu GP rule) is
 * still mixed with its rendering, because the plausibility rules that were
 * worth extracting already are: /_shared/recipe_line_guard.js is pure and
 * calibrated against the real book. Extracting the rest is a separate change
 * with its own tests, not something to do in the same commit as a page merge.
 */

import { Auth } from '/_shared/auth.js';
import { Feed } from '/_shared/feed.js';
import { WORKER_URL } from '/_shared/config.js';
// Deciding whether a line is implausible is business logic, so it is NOT in
// this file: it is pure, calibrated against the real book, and unit-tested by
// scripts/test_recipe_line_guard.mjs. This page only draws what it returns.
import { builderLineWarnings, warningsHtml, peerIndex } from '/_shared/recipe_line_guard.js';

let ING = [], SUBS = [], RECIPES = [], EDITABLE = [], LINES = [], USER = null, RATE = 0;
let PEERS = new Map();   // ingredient id -> every qty the saved book uses for it
let prepRecipe = null, prepMin = 0;
// Set the moment somebody uses the g/ml/ea selector — see buildYaml.
let UNIT_TOUCHED = false;
window.markUnitChosen = () => { UNIT_TOUCHED = true; };
let timing = false, tStart = 0, tPrev = 0, tick = null;

const money = n => '$' + (Math.round(n * 100) / 100).toFixed(2);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const venueOf = u => u.venue || (u.role === 'hgfood' ? 'harry_gatos' : u.role === 'pizza' ? 'marilynas' : 'stowaway');
// price per sensible unit: g→kg, ml→L, else as-is; strip any stray "per "
const perUnit = u => u === 'g' ? 'kg' : u === 'ml' ? 'L' : String(u || 'ea').replace(/^per\s+/i, '');
const perMult = u => (u === 'g' || u === 'ml') ? 1000 : 1;

/**
 * Wire the Build + Prep tabs for a signed-in user. The GATE is the page's job
 * now, not this module's: one gate serves four tabs, and it stays exactly as
 * permissive as /recipes/ has always been (roles: null — any signed-in user can
 * cost a dish; only a kitchen role can save one).
 *
 * Returns the boot promise so the page can wait before honouring a '#recipe='
 * deep link — loadRecipe reads EDITABLE, and EDITABLE arrives with the feeds.
 */
export function mountBuilder(user) {
  USER = user;
  document.getElementById('whoprep').textContent = user.name;
  const canWrite = Auth.canWrite();
  const sb = document.getElementById('save-btn');
  sb.disabled = !canWrite;
  if (!canWrite) sb.textContent = 'No kitchen role';
  return boot();
}

async function boot() {
  const [ing, idx, lab, full] = await Promise.all([
    Feed.load('/data/ingredients.json'),
    Feed.load('/data/recipes_index.json').catch(() => null),
    Feed.load('/data/labour_rate.json').catch(() => null),
    Feed.load('/data/recipes_full.json').catch(() => null),
  ]);
  ING = (ing && ing.ingredients) || [];
  RECIPES = (idx && idx.recipes) || [];
  EDITABLE = (full && full.recipes) || [];
  SUBS = RECIPES.filter(r => r.usable_as_subrecipe);
  PEERS = peerIndex(EDITABLE);
  const v = venueOf(USER);
  RATE = +((lab && (lab.venues?.[v]?.rate_per_minute || lab.default_rate_per_minute)) || 0);

  const bySup = {};
  ING.forEach(i => { bySup[i.supplier] = (bySup[i.supplier] || 0) + 1; });
  document.getElementById('sup').insertAdjacentHTML('beforeend',
    Object.keys(bySup).sort().map(s => `<option value="${esc(s)}">${esc(s)} (${bySup[s]})</option>`).join(''));

  renderPick(); renderSubs(); renderPrepList();
}

// The builder's own two-tab strip is gone: Build and Prep are two of the FOUR
// tabs the page now has, and /_shared/recipes_page.js owns switching between
// them. Leaving `mainTab` here would have been a second thing writing the same
// buttons' className, which is how a tab ends up highlighted and hidden at once.

// ---------------- BUILD: ingredient picker ----------------
function renderPick() {
  const sup = document.getElementById('sup').value;
  // pickq — the BUILDER's search box. NOT 'q': that id belongs to the recipe
  // book's search, which sits earlier in the same document since the two pages
  // merged, so getElementById('q') returned the book's box and this filter saw
  // an empty string no matter what was typed here.
  const q = document.getElementById('pickq').value.toLowerCase().trim();
  let hits = ING;
  if (q) hits = hits.filter(i => i.description.toLowerCase().includes(q) || (i.supplier || '').toLowerCase().includes(q));
  else if (sup) hits = hits.filter(i => i.supplier === sup);
  else hits = hits.slice(0, 0);   // avoid dumping everything; pick a supplier or search
  // sub-recipes are searchable here too, so "achio" surfaces Achiote Chicken — not
  // only in the Batch/sub-recipe section. They render first, tagged "recipe".
  const subHits = q ? SUBS.filter(s => s.product.toLowerCase().includes(q)) : [];
  const subHtml = subHits.map(s => {
    const c = subCpu(s);
    const cost = c ? `${money(c)} <span class="m">/${esc(s.yield_unit)}</span>` : '<span class="m">cost pending</span>';
    return `<div class="ing" onclick="addSub('${encodeURIComponent(s.product)}')">
      <div><div class="n">${esc(s.product)}<span class="pill sub">recipe</span></div>
      <div class="m">makes ${esc(s.yield_qty)} ${esc(s.yield_unit)}</div></div>
      <div class="c">${cost}</div></div>`;
  }).join('');
  const ingHtml = hits.map(i => {
    const ok = !i.needs_pack_review;
    const cost = ok ? `${money(i.cost_per_base_unit * perMult(i.pack_unit))} <span class="m">/${esc(perUnit(i.pack_unit))}</span>`
                    : `<span class="m">pack?</span>`;
    return `<div class="ing" onclick="add('${i.id}')">
      <div><div class="n">${esc(i.description)}${ok ? '' : '<span class="pill">confirm pack</span>'}</div>
      <div class="m">${esc(i.supplier)}</div></div>
      <div class="c">${cost}</div></div>`;
  }).join('');
  document.getElementById('picklist').innerHTML = (subHtml + ingHtml) ||
    `<div class="empty">${sup || q ? 'No match.' : 'Choose a supplier or search to add ingredients.'}</div>`;
}

// a batch's per-unit cost INCLUDING its prep labour, when we have it — so a dish
// built from it carries the batch's prep too. Falls back to food-only.
const subCpu = s => +(s.cost_per_yield_unit_with_prep ?? s.cost_per_yield_unit) || 0;

function renderSubs() {
  document.getElementById('sublist').innerHTML = SUBS.length ? SUBS.map(s => {
    const c = subCpu(s);
    const cpu = c ? `${money(c)} <span class="m">/${esc(s.yield_unit)}${s.cost_per_yield_unit_with_prep ? ' (incl prep)' : ''}</span>` : '<span class="m">cost pending</span>';
    return `<div class="ing" onclick="addSub('${encodeURIComponent(s.product)}')">
      <div><div class="n">${esc(s.product)}<span class="pill sub">recipe</span></div>
      <div class="m">makes ${esc(s.yield_qty)} ${esc(s.yield_unit)}</div></div><div class="c">${cpu}</div></div>`;
  }).join('') : '<div class="empty">No sub-recipes yet.</div>';
}

window.add = (id) => {
  const i = ING.find(x => x.id === id);
  if (!i) return;
  if (i.needs_pack_review) {
    const g = prompt(`Pack size needs confirming.\n\n${i.description}\n${money(i.pack_cost_incl)} per pack\n\nHow much is in ONE pack, in grams (or mL)?`, '');
    if (!g || isNaN(+g) || +g <= 0) return;
    i.pack_qty = +g; i.pack_unit = i.pack_unit === 'ml' ? 'ml' : 'g';
    i.cost_per_base_unit = +i.pack_cost_incl / +g;
    i.needs_pack_review = false; i.confirmed_by_chef = true;
    savePack(i);   // persist so it's costed for everyone from the next build on
  }
  if (LINES.some(l => l.kind === 'ing' && l.id === id)) return;
  LINES.push({ kind: 'ing', id, qty: 0 });
  calc();
};

// Persist a chef's pack confirmation to data/pack_overrides.yaml via the worker,
// so this ingredient becomes costable for everyone (build reads it). Best-effort:
// if you're not signed in as a kitchen writer it just stays a session confirm.
async function savePack(i) {
  try {
    const token = Auth.requireToken();
    await fetch(`${WORKER_URL}/pack`, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ venue: venueOf(USER), id: i.id, pack_qty: i.pack_qty, pack_unit: i.pack_unit }),
    });
  } catch (e) { /* not a writer — session-only confirm, no persistence */ }
}
window.addSub = (enc) => {
  const product = decodeURIComponent(enc);
  const s = SUBS.find(x => x.product === product);
  if (!s || LINES.some(l => l.kind === 'sub' && l.product === product)) return;
  LINES.push({ kind: 'sub', product, unit: s.yield_unit, cpu: subCpu(s), qty: 0 });
  calc();
};
window.setQty = (k, v) => { const l = LINES[k]; if (l) l.qty = +v || 0; recompute(); };
window.del = (k) => { LINES.splice(k, 1); calc(); };

// ---------------- BUILD: load an existing recipe for editing ----------------
window.renderEdit = () => {
  const q = document.getElementById('editq').value.toLowerCase().trim();
  const el = document.getElementById('editlist');
  if (!q) { el.innerHTML = ''; return; }
  const hits = EDITABLE.filter(r => r.product.toLowerCase().includes(q)).slice(0, 8);
  el.innerHTML = hits.length ? hits.map(r =>
    `<div class="ing" onclick="loadRecipe('${encodeURIComponent(r.product)}')">
       <div><div class="n">${esc(r.product)}</div><div class="m">${r.lines.length} ingredient(s)</div></div>
       <div class="c m">edit →</div></div>`).join('')
    : `<div class="empty">No saved recipe matches "${esc(q)}".</div>`;
};

window.loadRecipe = (enc) => {
  const product = decodeURIComponent(enc);
  const r = EDITABLE.find(x => x.product === product);
  if (!r) return;
  document.getElementById('dish').value = r.product;
  document.getElementById('sell').value = r.sell_incl_gst ?? '';
  document.getElementById('yq').value = r.yield_qty ?? '';
  document.getElementById('yu').value = r.yield_unit ?? '';
  // An existing record that already carries a confirmed unit keeps it confirmed
  // without the person having to re-pick it every time they open the recipe.
  UNIT_TOUCHED = !!r.unit_confirmed;
  LINES = [];
  for (const ln of r.lines) {
    if (ln.manual) {
      LINES.push({ kind: 'manual', name: ln.name, unit: ln.unit || 'ea', cpu: +ln.unit_cost_incl || 0, qty: +ln.qty || 0 });
    } else if (ln.subrecipe) {
      const s = SUBS.find(x => x.product === ln.subrecipe);
      LINES.push({ kind: 'sub', product: ln.subrecipe, unit: (s && s.yield_unit) || ln.unit, cpu: s ? subCpu(s) : 0, qty: +ln.qty || 0 });
    } else if (ln.id) {
      LINES.push({ kind: 'ing', id: ln.id, qty: +ln.qty || 0 });
    }
  }
  document.getElementById('editq').value = '';
  document.getElementById('editlist').innerHTML = '';
  document.getElementById('editing').style.display = '';
  document.getElementById('editing-name').textContent = r.product;
  calc();
  window.scrollTo(0, 0);
};

function lineCost(l) {
  if (l.kind === 'sub') return l.cpu * l.qty;
  if (l.kind === 'manual') return (+l.cpu || 0) * l.qty;
  const i = ING.find(x => x.id === l.id);
  return (i && !i.needs_pack_review) ? (+i.cost_per_base_unit) * l.qty : 0;
}
// the per-line RATE, in a sensible unit (g→kg, ml→L), so the cost never reads as
// "$X per ml". Returns e.g. { rate: 2.32, unit: 'L' } = $2.32 per litre.
function lineRate(l) {
  let per, u;
  if (l.kind === 'sub') { per = +l.cpu || 0; u = l.unit; }
  else if (l.kind === 'manual') { per = +l.cpu || 0; u = l.unit || 'ea'; }
  else { const i = ING.find(x => x.id === l.id); per = i ? +i.cost_per_base_unit : 0; u = i ? i.pack_unit : (l.unit || 'ea'); }
  return { rate: per * perMult(u), unit: perUnit(u) };
}
function lineCostLabel(l) {           // the RATE only; the line's own cost has a column
  const r = lineRate(l);
  return `${money(r.rate)}/${esc(r.unit)}`;
}
function lineMeta(l) {
  // "(recipe)" marks a line as a sub-recipe rather than a bought ingredient. Six
  // of them are NAMED "[Recipe]"/"[Batch]"/"[Prep]" already, which read as
  // "Pizza Dough [Recipe] (recipe)". Only add the tag when the name doesn't say it.
  if (l.kind === 'sub') {
    const s = /\[(recipe|batch|prep)\]/i.test(l.product) ? '' : ' (recipe)';
    return { name: l.product + s, unit: l.unit };
  }
  if (l.kind === 'manual') return { name: l.name + ' (imported)', unit: l.unit || 'ea' };
  const i = ING.find(x => x.id === l.id);
  if (!i) return { name: (l.name || l.id) + ' (no live cost)', unit: l.unit || 'ea' };
  return { name: i.description, unit: i.pack_unit === 'g' ? 'g' : i.pack_unit === 'ml' ? 'mL' : i.pack_unit };
}

function renderLines() {
  const box = document.getElementById('lines');
  box.innerHTML = LINES.length ? LINES.map((l, k) => {
    const { name, unit } = lineMeta(l);
    return `<div class="line">
      <div class="nm">${esc(name)}<small id="ct-${k}">${lineCostLabel(l)}</small></div>
      <input type="number" inputmode="decimal" step="any" value="${l.qty || ''}" placeholder="0 ${esc(unit)}" oninput="setQty(${k},this.value)">
      <div class="lc" id="lc-${k}">${money(lineCost(l))}</div>
      <button class="x" onclick="del(${k})" aria-label="remove">×</button>
      <div class="lw" id="lw-${k}"></div></div>`;
  }).join('') : '<div class="empty">Nothing added yet — tap an ingredient above.</div>';
}

function calc() { renderLines(); recompute(); }
function recompute() {
  const sell = +document.getElementById('sell').value || 0;
  const nm = (document.getElementById('dish')?.value || '');
  const yq = +(document.getElementById('yq')?.value || 0);
  const isPrep = yq > 0
    || /\[(batch|prep|recipe|\d+\s*(kg|g|l|ml))\]|\b(prep|batch|blend|mix)\b/i.test(nm)
    || (sell > 0 && sell < 3);
  LINES.forEach((l, k) => {
    const cell = document.getElementById('ct-' + k);
    if (cell) cell.textContent = lineCostLabel(l);
    const lc = document.getElementById('lc-' + k);
    if (lc) lc.textContent = money(lineCost(l));
    // The caution next to the offending line. It never blocks the save — a chef
    // must still be able to enter a genuinely unusual recipe — and an empty
    // string CLEARS the slot, so a warning cannot outlive the qty that caused it.
    const lw = document.getElementById('lw-' + k);
    if (lw) lw.innerHTML = warningsHtml(builderLineWarnings(
      l, l.kind === 'ing' ? ING.find(x => x.id === l.id) : null,
      { isBatch: isPrep, peerQuantities: (l.kind === 'ing' && PEERS.get(l.id)) || [] }));
  });
  const food = LINES.reduce((a, l) => a + lineCost(l), 0);
  document.getElementById('c-food').textContent = money(food);
  const ex = sell / 1.1;
  const gpEl = document.getElementById('gp-food'), mgEl = document.getElementById('gp-marg');
  // A BATCH IS NOT A MENU ITEM. "Tandoori Chicken [2Kg]" carries a $2 placeholder
  // POS price against a $19 tray, and the builder was reporting that as -574% GP
  // with a -$10.44 margin, in red. Nothing is wrong with the recipe's cost; the
  // sell price just isn't a sell price. The cost book already refuses to compute a
  // GP for these (is_prep), and the builder now agrees: a recipe with a batch YIELD,
  // or a prep/batch name, or a price too low to be a menu line, shows its cost and
  // no GP. Showing a frightening number that means nothing trains people to ignore
  // the number that does.
  if (isPrep && food > 0) {
    gpEl.textContent = '—'; gpEl.className = 'gp';
    mgEl.textContent = '—'; mgEl.className = 'gp';
    document.getElementById('gp-note').textContent =
      `${money(food)} to make this batch. No GP shown — a batch's POS price is a `
      + `placeholder, not what it sells for, so any GP off it is meaningless.`;
  } else if (sell > 0 && food > 0) {
    const gp = 100 * (ex - food) / ex;
    gpEl.textContent = gp.toFixed(0) + '%';
    gpEl.className = 'gp ' + (gp > 85 ? 'warn' : gp < 55 ? 'bad' : gp < 65 ? 'warn' : 'good');
    mgEl.textContent = money(ex - food); mgEl.className = 'gp';
    document.getElementById('gp-note').textContent = gp > 85
      ? `Food GP ${gp.toFixed(0)}% looks too good — what's missing? Oil, batter, sauce, garnish, lemon?`
      : `${money(ex)} ex-GST · prep labour is added from the Prep timer (last-4 average).`;
  } else {
    gpEl.textContent = '—'; gpEl.className = 'gp'; mgEl.textContent = '—'; mgEl.className = 'gp';
    document.getElementById('gp-note').textContent = '';
  }
}

// YAML-safe scalar: JSON string syntax is valid YAML, and escapes the quotes,
// colons and other characters that would otherwise produce a broken doc — one
// bad name (e.g. an ingredient literally called '"S&B" Curry') would corrupt the
// whole venue's recipe file and make every dish there fail to cost.
const ys = v => JSON.stringify(String(v ?? ''));

function buildYaml() {
  const dish = document.getElementById('dish').value.trim();
  const sell = +document.getElementById('sell').value || 0;
  const yq = +document.getElementById('yq').value || 0, yu = document.getElementById('yu').value;
  let s = `# generated by the recipe builder — review before committing\n- product: ${ys(dish)}\n`;
  if (sell) s += `  sell_incl_gst: ${sell.toFixed(2)}\n`;
  // UNIT_CONFIRMED: the person picked this, so it outranks the house rule.
  //
  // scripts/materialise_recipes.py defaults a batch's yield unit from the house
  // rule -- food is weighed, drinks are poured -- because nearly every scraped
  // yield carried a unit nobody had measured. That default has to be
  // overridable by a human standing in the kitchen, and this is the override:
  // touch the selector and the choice sticks, migration or no migration.
  if (yq && yu) s += `  yield_qty: ${yq}\n  yield_unit: ${ys(yu)}\n` +
                     (UNIT_TOUCHED ? `  unit_confirmed: true\n` : '');
  s += `  ingredients:\n`;
  for (const l of LINES) {
    if (l.kind === 'sub') s += `    - subrecipe: ${ys(l.product)}\n      qty: ${l.qty}\n      unit: ${ys(l.unit)}\n`;
    else if (l.kind === 'manual') s += `    - manual: true\n      desc: ${ys(l.name)}\n      qty: ${l.qty}\n      unit: ${ys(l.unit || 'ea')}\n      unit_cost_incl: ${(+l.cpu || 0).toFixed(6)}\n`;
    else {
      const i = ING.find(x => x.id === l.id);
      s += `    - id: ${ys(i.id)}\n      desc: ${ys(i.description)}\n      qty: ${l.qty}\n      unit: ${ys(i.pack_unit)}\n      unit_cost_incl: ${(+i.cost_per_base_unit).toFixed(6)}\n`;
    }
  }
  return s;
}

window.resetBuild = () => {
  LINES = [];
  ['dish', 'sell', 'yq', 'editq'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('yu').value = '';
  UNIT_TOUCHED = false;
  document.getElementById('save-result').textContent = '';
  document.getElementById('editlist').innerHTML = '';
  document.getElementById('editing').style.display = 'none';
  calc();
};

// True while a save is in flight. `btn.disabled` is NOT this guard: disabling a
// button stops the NEXT click, but every listener already queued on the current
// event still runs, and save() is also reachable from the console and from any
// future caller. The log it writes to is append-only, so a second entry is a
// permanent duplicate, not a harmless retry — worth one boolean.
let SAVING = false;

async function save() {
  if (SAVING) return;
  const dish = document.getElementById('dish').value.trim();
  const out = document.getElementById('save-result'), btn = document.getElementById('save-btn');
  if (!dish || !LINES.length) { out.textContent = 'Add a name + an ingredient.'; return; }
  let token; try { token = Auth.requireToken(); } catch (e) { out.textContent = e.message; return; }
  SAVING = true;
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch(`${WORKER_URL}/recipes`, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ venue: venueOf(USER), product: dish, yaml: buildYaml() }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    out.innerHTML = `<span style="color:var(--pos)">Saved ✓</span>`;
    // INSTANT AVAILABILITY. The authoritative sub-recipe list (recipes_index.json)
    // only rebuilds on the next deploy, so without this a batch you just saved
    // wouldn't appear as a sub-recipe until then. If it has a yield, upsert it into
    // SUBS now — with a cost computed from the very lines just entered — so it's
    // immediately selectable in a parent recipe (and in the prep timer). The deploy
    // later replaces this with the fully-costed feed entry (incl. prep labour).
    const yq = +document.getElementById('yq').value || 0, yu = document.getElementById('yu').value;
    if (yq > 0 && yu) {
      const food = LINES.reduce((a, l) => a + lineCost(l), 0);
      const entry = {
        product: dish, venue: venueOf(USER), yield_qty: yq, yield_unit: yu,
        usable_as_subrecipe: true,
        cost_per_yield_unit: food > 0 ? +(food / yq).toFixed(6) : null,
        cost_per_yield_unit_with_prep: null, prep_minutes_avg: null, prep_count: 0,
      };
      const ex = SUBS.find(s => s.product === dish);
      if (ex) Object.assign(ex, entry); else SUBS.push(entry);
      const exR = RECIPES.find(s => s.product === dish);
      if (exR) Object.assign(exR, entry); else RECIPES.push(entry);
      renderSubs();
      if (window.renderPrepList) window.renderPrepList();
    }
  } catch (e) { out.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
  finally { SAVING = false; btn.disabled = false; btn.textContent = 'Save recipe'; }
}
Object.assign(window, { renderPick, add, addSub, setQty, del, save, calc });

// ---------------- PREP TIMER ----------------
// Prep timing is for SUB-RECIPES only — the batches/sauces/doughs you prep ahead
// (a dish is plated to order, not "prepped"). So the picker lists sub-recipes.
window.renderPrepList = () => {
  const q = (document.getElementById('prep-q').value || '').toLowerCase().trim();
  const hits = SUBS.filter(r => !q || r.product.toLowerCase().includes(q));
  document.getElementById('prep-list').innerHTML = hits.length ? hits.map(r =>
    `<div class="ing" onclick="pickPrep('${encodeURIComponent(r.product)}')">
      <div><div class="n">${esc(r.product)}</div><div class="m">makes ${esc(r.yield_qty)} ${esc(r.yield_unit)}${
        r.prep_count ? ` · avg ${esc(r.prep_minutes_avg)} min` : ` · not timed yet`}</div></div>
      <div class="c">›</div></div>`).join('')
    : `<div class="empty">${SUBS.length ? 'No match.' : 'No batch recipes yet — give a batch a yield in Build, then time it here.'}</div>`;
};

window.pickPrep = (enc) => {
  prepRecipe = decodeURIComponent(enc);
  document.getElementById('prep-name').textContent = prepRecipe;
  const b = SUBS.find(s => s.product === prepRecipe) || {};
  document.getElementById('prep-avg').textContent = b.prep_count
    ? `Currently averaging ${b.prep_minutes_avg} min over the last ${Math.min(b.prep_count, 4)} of ${b.prep_count} prep${b.prep_count > 1 ? 's' : ''}.`
    : `No preps logged yet — this will be the first.`;
  document.getElementById('prep-panel').style.display = '';
  resetTimer();
  document.getElementById('log-result').textContent = '';
  document.getElementById('prep-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

window.toggleTimer = () => {
  const btn = document.getElementById('timerbtn');
  if (!timing) {
    timing = true; tStart = Date.now(); btn.textContent = 'Stop'; btn.className = 'bigbtn stop';
    tick = setInterval(showClock, 250);
  } else {
    timing = false; clearInterval(tick);
    tPrev += (Date.now() - tStart) / 1000;
    prepMin = +(tPrev / 60).toFixed(2);
    document.getElementById('mins').value = prepMin;
    btn.textContent = 'Resume'; btn.className = 'bigbtn start'; showClock(); armLog();
  }
};
function showClock() {
  const secs = tPrev + (timing ? (Date.now() - tStart) / 1000 : 0);
  const m = Math.floor(secs / 60), s = Math.floor(secs % 60);
  document.getElementById('clock').innerHTML = `${m}<small>:${String(s).padStart(2, '0')}</small>`;
}
window.setMins = (v) => { prepMin = +v || 0; tPrev = prepMin * 60; timing = false; if (tick) clearInterval(tick);
  document.getElementById('timerbtn').textContent = 'Start prepping'; document.getElementById('timerbtn').className = 'bigbtn start';
  showClock(); armLog(); };
function armLog() { document.getElementById('log-btn').disabled = !(prepRecipe && prepMin > 0); }
function resetTimer() { timing = false; if (tick) clearInterval(tick); tPrev = 0; prepMin = 0;
  document.getElementById('mins').value = ''; document.getElementById('clock').innerHTML = '0<small>:00</small>';
  document.getElementById('timerbtn').textContent = 'Start prepping'; document.getElementById('timerbtn').className = 'bigbtn start';
  armLog(); }

window.logPrep = async () => {
  const out = document.getElementById('log-result'), btn = document.getElementById('log-btn');
  if (!prepRecipe || !(prepMin > 0)) return;
  let token; try { token = Auth.requireToken(); } catch (e) { out.textContent = e.message; return; }
  btn.disabled = true; btn.textContent = 'Logging…';
  try {
    const r = await fetch(`${WORKER_URL}/prep`, {
      method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ venue: venueOf(USER), product: prepRecipe, minutes: prepMin }),
    });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || `HTTP ${r.status}`); }
    out.innerHTML = `<span style="color:var(--pos)">${prepMin} min logged ✓</span>`;
    resetTimer();
  } catch (e) { out.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; btn.disabled = false; }
  finally { btn.textContent = 'Log this prep'; }
};

/**
 * Load a recipe by NAME, for the Book tab and for a '#recipe=' deep link.
 *
 * -> true if the builder now holds it, false if no saved recipe matches. The
 * caller needs to know, because "the tab switched and nothing happened" is
 * indistinguishable from a broken build, and the answer is usually that the
 * feeds have not landed yet.
 *
 * Names come from data/lightspeed_recipes_costed.json (the book) and are looked
 * up in data/recipes_full.json (the builder). Both are keyed on the same POS
 * product name — build_recipe_feeds.recipes_full() copies the book's keys
 * verbatim — so an exact match is the right match. The case-insensitive second
 * pass is for a name typed by a human, not for the book's own rows.
 */
export function openRecipe(name) {
  const want = String(name || '').trim();
  if (!want) return false;
  const hit = EDITABLE.find(x => x.product === want)
           || EDITABLE.find(x => (x.product || '').toLowerCase() === want.toLowerCase());
  if (!hit) return false;
  window.loadRecipe(encodeURIComponent(hit.product));
  return true;
}

/** Whether the builder's feeds have landed — the page uses it to say why a
 *  click did nothing rather than doing nothing silently. */
export const builderReady = () => EDITABLE.length > 0;
