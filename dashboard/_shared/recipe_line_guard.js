/**
 * Plausibility guard for a single line in the recipe BUILDER (/recipes/).
 *
 * WHY THIS EXISTS
 * ---------------
 * Every check this platform owns runs on the OUTPUT — data/costs.csv,
 * data/lightspeed_recipes_costed.json, audit_book.py, the P&L. Nothing ran on
 * the INPUT SURFACE, which is where the mistake is actually made, so the two
 * defects below were caught by Zak's eye on a screenshot and by nothing else:
 *
 *   1. "Lettuce Cos Baby Twin Pack [Each]" entered at qty 1. The pack unit is
 *      "ea" and one "ea" IS the twin pack, at $2.75 — so one line put $2.75 of
 *      baby cos on an $8.20 burger. The saved book has the same ingredient at
 *      0.083 ($0.228) in both burgers that use it, so this was never a costing
 *      bug: the builder accepted a 12x quantity in silence.
 *
 *   2. "T2 Milk Bun Sliced White Sesame [85g]" rendering its rate as $980.00/L.
 *      The dollar figure was right ($0.98 = 85 g x $11.53/kg); the RATE was
 *      drawn per LITRE, because the ingredient's pack_unit said "ml". A bun is
 *      not a liquid. Same family as a whole chicken logged as "0.5 ml" —
 *      data/recipe_line_unit_fixes.yaml exists because that class of typo has
 *      already cost this book a $10,530 batch (Peking Sauce).
 *
 * It WARNS. It never blocks. A chef must still be able to save a genuinely
 * unusual recipe, and a guard that stops the save gets worked around within a
 * week. Every rule below is a visible caution next to the offending line.
 *
 * CALIBRATION — measured against the real book, 892 recipes / 3,041 saved
 * lines (data/lightspeed_recipes_costed.json) and 1,091 ingredient records
 * (data/ingredients.json), 2026-08-08. Flagged / true positive / false positive:
 *
 *   whole-multi-serve-pack        0 / 0 / 0   of 3,041 lines
 *   count-pack-at-a-weight-qty    0 / 0 / 0   of 3,041 lines
 *   rate-unit-contradicts-name    4 / 4 / 0   of 1,091 ingredients
 *   qty-unit-contradicts-pack    24 / 24 / 0  of 3,041 lines
 *   never-used-this-much          0 / 0 / 0   of 2,421 id lines
 *
 * DELIBERATELY NOT IMPLEMENTED — "this line is an outsized share of the dish".
 * Measured on the same book: >30% of dish cost flags 380 of 3,041 lines. The
 * tightest defensible form (non-batch, 4+ lines, >75% of dish) flags 23, and
 * all 23 are correct: single-spirit cocktails (Tommy's Margarita, 95%), a $4.44
 * gluten-free pizza base in a $5.37 pizza, a $3.84 pack of har gow in a $4.40
 * serve. 23 flagged, 0 true positives. It also would not have caught the
 * lettuce, which was 34% of its dish. A rule that is wrong every time it fires
 * teaches people to ignore the ones that are right.
 *
 * PURE. No document, no fetch, no imports — scripts/test_recipe_line_guard.mjs
 * runs it under node against the real feeds. Same split as
 * dashboard/recipes-book/flags_view.js (decides) vs flags.js (draws), and as
 * pnl.js vs render.js, which scripts/arch_guard.py enforces.
 */

const MASS = new Set(['g', 'gm', 'gms', 'gr', 'gram', 'grams', 'kg', 'kgs', 'kilo', 'kilogram', 'kilograms']);
const VOLUME = new Set(['ml', 'mls', 'millilitre', 'l', 'lt', 'ltr', 'litre', 'liter', 'litres', 'cl']);
const COUNT = new Set(['ea', 'each', 'unit', 'units', 'pc', 'pcs', 'piece', 'pieces', 'portion',
  'slice', 'slices', 'bunch', 'punnet', 'can', 'cans', 'bottle', 'bottles', 'tray', 'box',
  'pack', 'packet', 'sheet', 'leaf', 'clove', 'egg', 'tin', 'jar', 'block', 'roll', 'bag',
  'case', 'carton', 'dozen', 'keg', 'tub', 'pkt', 'serve', 'serves', 'head', 'loaf', 'fillet']);

/** mass / volume / count — or null for a unit we have never seen and will not guess. */
export function unitDimension(unit) {
  const u = String(unit ?? '').trim().toLowerCase().replace(/\.$/, '');
  if (MASS.has(u)) return 'mass';
  if (VOLUME.has(u)) return 'volume';
  if (COUNT.has(u)) return 'count';
  return null;
}

const BARE_UNIT = /^\s*(kgs|kg|gms|gm|g|mls|ml|litres|litre|ltr|lt|l)\s*$/i;
const SIZED = /([0-9]*\.?[0-9]+)\s*(kgs|kg|gms|gm|gr|g|mls|ml|litres|litre|ltr|lt|l)\b/gi;
// Countable THINGS, not liquid containers. "[Bottle]", "[Keg]", "[Can]" say
// nothing about how the contents are measured — 426 saved lines draw ml out of
// a "[Bottle]" and every one of them is correct — so those words are silent here.
const THING = /^\s*(each|ea|bunch|punnet|head|dozen|twin\s*pack)\s*$/i;

/**
 * What dimension the ingredient's OWN NAME claims its pack is measured in, read
 * from the trailing bracket only — "[85g]" -> mass, "[700ml]" -> volume,
 * "[Each]" -> count. null when the name does not say.
 *
 * The bracket is load-bearing. Reading a size anywhere in the name flags "Coke
 * 1.25L" (pack unit "can") and four of its siblings, all of which are bought
 * and priced by the can and are perfectly correct. The cost book's own naming
 * convention puts the pack in a trailing bracket, so that is all we trust.
 */
export function nameDeclaredDimension(name) {
  const m = /\[([^\]]*)\]\s*$/.exec(String(name ?? ''));
  if (!m) return null;
  const inner = m[1];
  if (BARE_UNIT.test(inner)) return unitDimension(inner.trim());
  if (THING.test(inner)) return 'count';
  const dims = new Set();
  SIZED.lastIndex = 0;
  let hit;
  while ((hit = SIZED.exec(inner)) !== null) {
    const d = unitDimension(hit[2]);
    if (d) dims.add(d);
  }
  return dims.size === 1 ? [...dims][0] : null;   // "10x2" style mixes say nothing
}

// A pack noun that means the selling unit holds SEVERAL serves. "Box" and
// "case" are absent on purpose: a pizza box is one box per pizza, and the
// carton note on "Pizza Base Gluten Free 11in [10x2 CTN]" describes the
// delivery, not the unit — including them flagged 266 correct lines.
const MULTI_PORTION = /(twin\s*pack|multi[\s-]*pack|\bbunch\b|\bpunnet\b|\btray\b|\bdozen\b|\bcarton\b)/i;

const sameUnit = (a, b) => String(a ?? '').trim().toLowerCase() === String(b ?? '').trim().toLowerCase();
const isWhole = (n) => Number.isFinite(n) && Math.abs(n - Math.round(n)) < 1e-9;
const money = (n) => '$' + (Math.round(Number(n) * 100) / 100).toFixed(2);
/** g -> kg, ml -> L: the unit a rate is actually shown in on the page. */
export const rateUnit = (u) => (u === 'g' ? 'kg' : u === 'ml' ? 'L' : String(u || 'ea').replace(/^per\s+/i, ''));

// Quantity of countable packs above which "you typed grams into a box that
// counts packs" is the likelier reading. 250 g of shallot entered against a
// $1.93 BUNCH is $482.50 on one line. Threshold 10: of the 3,041 saved lines,
// exactly 2 sit above it (125.99 and 323.99 eggs) and both are batch preps,
// which this rule excludes. Zero non-batch lines.
const COUNT_PACK_QTY_CEILING = 10;
// How far above every quantity the book has ever used for this ingredient a new
// entry must sit before we say anything. 10x flags 0 of the 2,421 saved id
// lines in non-batch recipes; 5x flags 7 (six-packs: 6 cans against a peer max
// of 1) and 3x flags 17 (four- and six-packs). The lettuce is 1 / 0.083 = 12x.
const PEER_MAX_MULTIPLE = 10;
const PEER_MIN_SAMPLES = 2;

/**
 * Zero or more warnings for one builder line. Pure; order is stable.
 *
 * line = { name, packUnit, qty, qtyUnit, ratePerBaseUnit }
 *   packUnit         the unit the ingredient is PURCHASED and priced in
 *   qtyUnit          the unit the chef's quantity is denominated in (for a
 *                    picked ingredient the builder makes this the pack unit)
 *   ratePerBaseUnit  cost of one packUnit — only used to word a message
 * ctx  = { isBatch, peerQuantities }
 *   isBatch          the dish has a yield / reads as a prep. A batch really
 *                    does consume a whole bunch of thyme or a tray of avocados
 *                    (9 such lines in the book), so the quantity rules are off.
 *   peerQuantities   every quantity the saved book uses for this same
 *                    ingredient, any recipe. Absent/short -> that rule is silent.
 */
export function lineWarnings(line, ctx) {
  const l = line || {};
  const c = ctx || {};
  const out = [];
  const name = String(l.name ?? '');
  const qty = Number(l.qty);
  const packDim = unitDimension(l.packUnit);
  const qtyDim = unitDimension(l.qtyUnit);
  const nameDim = nameDeclaredDimension(name);
  const countedInPacks = packDim === 'count' && sameUnit(l.qtyUnit, l.packUnit);

  // --- the bun: the rate we are about to draw is in the wrong dimension -----
  if (nameDim && packDim && nameDim !== packDim) {
    out.push({
      code: 'rate-unit-contradicts-name',
      title: `Priced per ${rateUnit(String(l.packUnit ?? '').toLowerCase())}, but the name says ${DIM_WORD[nameDim]}`,
      detail: `“${name}” is bought by ${DIM_WORD[nameDim]}, and this rate is per `
        + `${rateUnit(String(l.packUnit ?? '').toLowerCase())}. The dollar figure may still be right, `
        + `but the pack unit on the ingredient is not, so anything derived from it is a guess.`,
    });
  }

  // --- the 0.5 ml chicken: the quantity is in the wrong dimension -----------
  if (qtyDim && packDim && qtyDim !== packDim
      && ((qtyDim === 'mass' && packDim === 'volume') || (qtyDim === 'volume' && packDim === 'mass'))) {
    out.push({
      code: 'qty-unit-contradicts-pack',
      title: `Measured in ${DIM_WORD[qtyDim]}, bought by ${DIM_WORD[packDim]}`,
      detail: `This line is ${fmtQty(qty)} ${l.qtyUnit} of something priced per ${l.packUnit}. `
        + `One of the two labels is wrong, and the cost follows whichever one wins.`,
    });
  }

  // --- the lettuce: a whole multi-serve pack on one plate -------------------
  if (!c.isBatch && countedInPacks && qty >= 1 && isWhole(qty) && MULTI_PORTION.test(name)) {
    const each = Number(l.ratePerBaseUnit);
    const noun = (MULTI_PORTION.exec(name) || [])[0] || String(l.packUnit);
    out.push({
      code: 'whole-multi-serve-pack',
      title: `${fmtQty(qty)} whole ${noun} — that pack holds several serves`,
      detail: `“${name}” is one pack${Number.isFinite(each) && each > 0 ? ` at ${money(each)}` : ''}, `
        + `and the name says the pack holds more than one serve. If one serve is a fraction `
        + `of the pack, enter that fraction rather than ${fmtQty(qty)}.`,
    });
  }

  // --- grams typed into a box that counts packs ----------------------------
  if (!c.isBatch && countedInPacks && qty >= COUNT_PACK_QTY_CEILING) {
    out.push({
      code: 'count-pack-at-a-weight-qty',
      title: `${fmtQty(qty)} × ${String(l.packUnit)} on one serve`,
      detail: `This box counts ${String(l.packUnit)}, not grams. ${fmtQty(qty)} of them is `
        + `${fmtQty(qty)} whole packs on a single plate.`,
    });
  }

  // --- the book has never used anything like this much ---------------------
  const peers = (c.peerQuantities || []).map(Number).filter(q => Number.isFinite(q) && q > 0);
  if (!c.isBatch && peers.length >= PEER_MIN_SAMPLES && qty > 0) {
    const most = Math.max(...peers);
    if (qty > PEER_MAX_MULTIPLE * most) {
      out.push({
        code: 'never-used-this-much',
        title: `${Math.round(qty / most)}× more than any saved recipe uses`,
        detail: `Every other recipe using “${name}” takes at most ${fmtQty(most)} `
          + `${String(l.qtyUnit ?? '')}. This one takes ${fmtQty(qty)}.`,
      });
    }
  }
  return out;
}

const DIM_WORD = { mass: 'weight', volume: 'volume', count: 'the piece' };

function fmtQty(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '?';
  return String(Math.round(v * 1000) / 1000);
}

export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, ch => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));

/**
 * The warnings as inline HTML for the line they belong to. Empty string when
 * there is nothing to say — the caller writes it straight into the line's slot,
 * so "no warnings" has to clear the slot rather than leave the last one up.
 */
export function warningsHtml(warnings) {
  if (!warnings || !warnings.length) return '';
  return warnings.map(w => `<div class="lwarn" data-code="${esc(w.code)}">`
    + `<b>${esc(w.title)}</b> ${esc(w.detail)}</div>`).join('');
}

/**
 * The builder's own line shape, adapted to `lineWarnings` — so the page never
 * has to know which fields a warning reads.
 *
 * A picked ingredient (`kind: 'ing'`) is always denominated in its PACK unit:
 * that is what the builder's placeholder says ("0 ea") and what `lineCost`
 * multiplies. A sub-recipe or an imported manual line carries its own unit and
 * per-unit cost, and no ingredient record, so the pack-unit rules stay quiet
 * for it and only the quantity rules apply.
 */
export function builderLineWarnings(line, ingredient, ctx) {
  const l = line || {};
  const input = (l.kind === 'ing' && ingredient)
    ? { name: ingredient.description, packUnit: ingredient.pack_unit, qty: Number(l.qty),
        qtyUnit: ingredient.pack_unit, ratePerBaseUnit: Number(ingredient.cost_per_base_unit) }
    : { name: l.product || l.name || l.id, packUnit: l.unit, qty: Number(l.qty),
        qtyUnit: l.unit, ratePerBaseUnit: Number(l.cpu) };
  return lineWarnings(input, ctx);
}

/**
 * id -> every quantity the saved book uses for it, from data/recipes_full.json.
 * Built once at boot; feeds `peerQuantities`. Manual and sub-recipe lines have
 * no id and are not indexed, so those lines simply never get this warning.
 */
export function peerIndex(recipes) {
  const idx = new Map();
  for (const r of recipes || []) {
    for (const ln of r.lines || []) {
      if (!ln || !ln.id) continue;
      const q = Number(ln.qty);
      if (!Number.isFinite(q) || q <= 0) continue;
      if (!idx.has(ln.id)) idx.set(ln.id, []);
      idx.get(ln.id).push(q);
    }
  }
  return idx;
}
