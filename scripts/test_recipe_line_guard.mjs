/* The recipe BUILDER's plausibility guard, under node.
   Run: node scripts/test_recipe_line_guard.mjs

   WHAT THIS IS FOR
   ----------------
   Two wrong lines reached the "BUILD A RECIPE" panel at /recipes/ and nothing
   in this repo noticed, because every check we own runs on the OUTPUT — costs.csv,
   lightspeed_recipes_costed.json, audit_book.py, the P&L — and the mistake is
   made on the INPUT.

     * "Lettuce Cos Baby Twin Pack [Each]" at qty 1, $2.75/ea, putting $2.75 of
       baby cos on an $8.20 burger. The saved book has that same ingredient at
       0.083 ($0.228) in BOTH burgers that use it (American Standard Burger and
       Beef Burger D), so the cost book was never wrong — the builder accepted a
       12x quantity in silence.

     * "T2 Milk Bun Sliced White Sesame [85g]" drawing its rate as $980.00/L.
       $0.98 is right (85 g x $11.53/kg); per LITRE is not. A bun is not a
       liquid. That is the same typo family that made Peking Sauce a $10,530.95
       batch (data/recipe_line_unit_fixes.yaml).

   Both are named tests below. The rest of the file holds the calibration
   honest: the guard is measured against the REAL book on every run, and if a
   rule starts flagging the saved recipes it is crying wolf and this goes red.

   Measured 2026-08-08 over 892 recipes / 3,041 saved lines and 1,091
   ingredients: whole-multi-serve-pack 0, count-pack-at-a-weight-qty 0,
   qty-unit-contradicts-pack 24 (all real), rate-unit-contradicts-name 4 (all
   real), never-used-this-much 0.
*/
import fs from 'fs'; import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const G = await import('file://' + path.join(ROOT, 'dashboard/_shared/recipe_line_guard.js'));

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};
const codes = (line, ctx) => G.lineWarnings(line, ctx || {}).map(w => w.code);
const readJson = (rel) => {
  const p = path.join(ROOT, rel);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
};

/* ------------------------------------------------------------------ THE LETTUCE
   The exact line off the screenshot: pack unit "ea", one "ea" IS the twin pack
   at $2.75, entered as 1 on a burger whose other lines total $5.45. Two rules
   should have something to say — the pack plainly serves several, and the book
   has never used more than 0.083 of it. Neither may block the save. */
{
  const lettuce = {
    name: 'Lettuce Cos Baby Twin Pack [Each]',
    packUnit: 'ea', qty: 1, qtyUnit: 'ea', ratePerBaseUnit: 2.75,
  };
  const w = G.lineWarnings(lettuce, { isBatch: false, peerQuantities: [0.083, 0.083] });
  const c = w.map(x => x.code);
  ok('lettuce twin pack at qty 1 warns that the pack serves several',
     c.includes('whole-multi-serve-pack'), c.join(','));
  ok('lettuce twin pack at qty 1 warns it is 12x anything the book uses',
     c.includes('never-used-this-much'), c.join(','));
  ok('...and the 12x wording quotes the real multiple',
     (w.find(x => x.code === 'never-used-this-much') || {}).title === '12× more than any saved recipe uses',
     JSON.stringify(w.map(x => x.title)));
  ok('...and it tells the chef the number to type instead',
     w.some(x => x.detail.includes('0.083')));
  // The correct entry, which is what both saved burgers actually hold.
  ok('the same line at the book value of 0.083 is silent',
     G.lineWarnings({ ...lettuce, qty: 0.083 },
       { isBatch: false, peerQuantities: [0.083, 0.083] }).length === 0);
  // A batch legitimately eats whole packs: Guacamole [4kg] takes a whole tray
  // of avocados, Pico de Gallo a whole bunch of coriander. 9 such lines exist.
  ok('a BATCH taking a whole pack is not warned about',
     !codes(lettuce, { isBatch: true, peerQuantities: [0.083, 0.083] })
       .includes('whole-multi-serve-pack'));
}

/* ---------------------------------------------------------------------- THE BUN
   $980.00/L for a bun. The dollars are right and the dimension is not: the name
   declares an 85 g pack and the ingredient record prices it per ml, so the
   builder renders g->kg... as ml->L. The guard reads the name's own bracket. */
{
  const bunBroken = {
    name: 'T2 Milk Bun Sliced White Sesame [85g]',
    packUnit: 'ml', qty: 1, qtyUnit: 'ml', ratePerBaseUnit: 0.98,
  };
  const w = G.lineWarnings(bunBroken, {});
  ok('the $980.00/L bun is flagged as a per-litre rate on a per-weight good',
     w.map(x => x.code).includes('rate-unit-contradicts-name'), JSON.stringify(w));
  ok('...and the warning names both sides of the contradiction',
     /per L/.test(w[0].title) && /weight/.test(w[0].title), w[0] && w[0].title);
  ok('the rate unit the page draws is per L for ml and per kg for g',
     G.rateUnit('ml') === 'L' && G.rateUnit('g') === 'kg' && G.rateUnit('ea') === 'ea');
  // The record as it stands in data/ingredients.json today: pack_unit "g",
  // $0.011529/g = $11.53/kg. Correct, and must stay silent.
  ok('the same bun priced per g (as the live feed has it) is silent',
     G.lineWarnings({ name: 'T2 Milk Bun Sliced White Sesame [85g]', packUnit: 'g',
                      qty: 85, qtyUnit: 'g', ratePerBaseUnit: 0.011529 }, {}).length === 0);
}

/* ------------------------------------------------------- the 0.5 ml whole chicken */
{
  const c = codes({ name: 'Chicken Whole Free Range', packUnit: 'g', qty: 0.5, qtyUnit: 'ml' }, {});
  ok('a mass-priced good measured in ml is flagged',
     c.includes('qty-unit-contradicts-pack'), c.join(','));
  ok('a volume-priced good measured in g is flagged',
     codes({ name: 'Lemon [Sliced]', packUnit: 'ml', qty: 13.604, qtyUnit: 'g' }, {})
       .includes('qty-unit-contradicts-pack'));
  ok('ml out of a [Bottle] — 426 saved lines do this and all are right — is silent',
     G.lineWarnings({ name: 'Bombay Dry [Bottle]', packUnit: 'ml', qty: 45, qtyUnit: 'ml',
                      ratePerBaseUnit: 0.0701 }, {}).length === 0);
  ok('a whole bottle sold as 1 ea is silent (Dom Pérignon, San Giorgio, Barolo)',
     G.lineWarnings({ name: 'Dom Pérignon Champagne - Bottle', packUnit: 'ml', qty: 1,
                      qtyUnit: 'ea', ratePerBaseUnit: 0.4434 }, {}).length === 0);
}

/* -------------------------------------------------- grams typed into a pack box */
{
  // 250 g of shallot against a $1.93 BUNCH is $482.50 on one line.
  const c = codes({ name: 'Shallot [bunch]', packUnit: 'bunch', qty: 250, qtyUnit: 'bunch',
                    ratePerBaseUnit: 1.93 }, { isBatch: false });
  ok('250 packs on one plate is flagged', c.includes('count-pack-at-a-weight-qty'), c.join(','));
  ok('a six-pack (6 cans, peer max 1) is NOT flagged — 7 such lines are real',
     !codes({ name: 'Peroni', packUnit: 'can', qty: 6, qtyUnit: 'can', ratePerBaseUnit: 2.2 },
            { isBatch: false, peerQuantities: [1, 1, 1] }).length);
  ok('Brownie Prep at 125.99 eggs is a BATCH and stays silent',
     !codes({ name: 'Eggs 700 Grams [12x]', packUnit: 'ea', qty: 125.99, qtyUnit: 'ea' },
            { isBatch: true }).includes('count-pack-at-a-weight-qty'));
}

/* ----------------------------------------------------------------- name reading */
{
  ok('a trailing [85g] declares weight', G.nameDeclaredDimension('T2 Milk Bun [85g]') === 'mass');
  ok('a trailing [700ml] declares volume', G.nameDeclaredDimension('Havana 3yr [700ml]') === 'volume');
  ok('a bare [kg] declares weight', G.nameDeclaredDimension('Onion Brown [kg]') === 'mass');
  ok('[Each] declares a countable thing', G.nameDeclaredDimension('Lettuce Cos Baby Twin Pack [Each]') === 'count');
  ok('[Bottle] declares NOTHING — a bottle holds ml', G.nameDeclaredDimension('Campari [Bottle]') === null);
  ok('[Keg] declares nothing either', G.nameDeclaredDimension('Guinness [Keg]') === null);
  // "Coke 1.25L" is bought and priced BY THE CAN and is correct. Reading a size
  // from anywhere in the name flagged it and four siblings; only the trailing
  // bracket is trusted, so it reads as "the name does not say".
  ok('an unbracketed size says nothing (Coke 1.25L is priced per can, correctly)',
     G.nameDeclaredDimension('Coke 1.25L') === null);
  ok('a mixed multiplier bracket says nothing',
     G.nameDeclaredDimension('Pizza Base Gluten Free 11in [10x2 CTN]') === null);
  ok('unit dimensions', G.unitDimension('kg') === 'mass' && G.unitDimension('L') === 'volume'
     && G.unitDimension('punnet') === 'count' && G.unitDimension('splash') === null);
}

/* ------------------------------------------------------------------- rendering */
{
  const html = G.warningsHtml(G.lineWarnings(
    { name: 'Fancy <b>"Pants"</b> Twin Pack [Each]', packUnit: 'ea', qty: 1, qtyUnit: 'ea',
      ratePerBaseUnit: 2.75 }, { isBatch: false }));
  ok('a warning renders', html.includes('lwarn'));
  ok('the ingredient name is escaped', html.includes('&lt;b&gt;') && !html.includes('<b>Pants'));
  ok('no warnings renders the empty string, so the slot CLEARS', G.warningsHtml([]) === '');
  ok('null renders the empty string', G.warningsHtml(null) === '');
}

/* --------------------------------------------------------------- the peer index */
{
  const idx = G.peerIndex([
    { product: 'A', lines: [{ id: 'x', qty: '0.083' }, { id: 'y', qty: 5 }] },
    { product: 'B', lines: [{ id: 'x', qty: 0.083 }, { manual: true, qty: 3 }] },
  ]);
  ok('quantities are collected per ingredient id', JSON.stringify(idx.get('x')) === '[0.083,0.083]');
  ok('manual lines carry no id and are not indexed', idx.size === 2);
  ok('one prior use is not enough to judge by',
     !codes({ name: 'Anything', packUnit: 'g', qty: 900, qtyUnit: 'g' },
            { peerQuantities: [1] }).includes('never-used-this-much'));
}

/* ============================================================================
   CALIBRATION AGAINST THE REAL BOOK
   A rule that fires on the recipes we already believe is a rule people will
   learn to click past. These run over every saved line and hold the measured
   counts. data/ingredients.json is built, not committed, so a clean checkout
   skips the halves that need it rather than failing.
   ============================================================================ */
const book = readJson('data/lightspeed_recipes_costed.json');
const ingFeed = readJson('data/ingredients.json');
const full = readJson('data/recipes_full.json');

if (book && ingFeed) {
  const byId = new Map((ingFeed.ingredients || []).map(i => [i.id, i]));
  const tally = {};
  let lines = 0;
  for (const [dish, r] of Object.entries(book.recipes || {})) {
    for (const ln of r.ingredients || []) {
      lines++;
      const ing = ln.kind === 'id' ? byId.get(ln.ref) : null;
      const ws = G.lineWarnings({
        name: ln.name, packUnit: ing && ing.pack_unit, qty: Number(ln.qty),
        qtyUnit: ln.unit, ratePerBaseUnit: ing && Number(ing.cost_per_base_unit),
      }, { isBatch: !!r.is_prep, peerQuantities: [] });
      for (const w of ws) (tally[w.code] = tally[w.code] || []).push(`${dish} | ${ln.name}`);
    }
  }
  const cnt = (k) => (tally[k] || []).length;
  ok(`the book has not shrunk (${lines} lines)`, lines > 2900, String(lines));
  ok('no saved line trips the whole-multi-serve-pack rule',
     cnt('whole-multi-serve-pack') === 0, (tally['whole-multi-serve-pack'] || []).slice(0, 5).join(' / '));
  ok('no saved line trips the count-pack-at-a-weight-qty rule',
     cnt('count-pack-at-a-weight-qty') === 0, (tally['count-pack-at-a-weight-qty'] || []).slice(0, 5).join(' / '));
  // These 24 are real and known: Lemon [Sliced] and Spiced Sour Cream [Batch]
  // are priced per ml and used in g; Oregano Leaves Rubbed and Dehydrated Lime
  // Garnish are priced per g and used in ml. Every one is a unit-label defect
  // worth a chef seeing. The bound stops it growing into noise.
  ok(`the mass<->volume clash stays at the 24 known lines (got ${cnt('qty-unit-contradicts-pack')})`,
     cnt('qty-unit-contradicts-pack') <= 30, (tally['qty-unit-contradicts-pack'] || []).slice(0, 3).join(' / '));

  // The ingredient FEED — the surface the bun defect actually lives on.
  const feedHits = (ingFeed.ingredients || []).filter(i => i.pack_unit && G.lineWarnings(
    { name: i.description, packUnit: i.pack_unit, qty: 1, qtyUnit: i.pack_unit,
      ratePerBaseUnit: Number(i.cost_per_base_unit) }, { isBatch: true })
    .some(w => w.code === 'rate-unit-contradicts-name'));
  // Three of the four were FIXED on 2026-08-14 by pinning the pack in
  // data/pack_overrides.yaml at the unit each one's price was already sane for:
  // Onion Brown 1000 g ($1.54/kg, which is Fresh Fruit Team's own invoiced rate
  // to the cent), Potato Peeled 1000 g, and Ponzu Dashi 1 mL ($0.0153/mL = $5.51
  // for the 360 mL bottle the name declares, against Foodlink's Mizkan ponzu at
  // $0.0167/mL). None moved money.
  //
  // PEARS GREEN IS STILL HERE ON PURPOSE and must stay. Its price is right and
  // its NAME is wrong: $0.65 is not a kilo of pears (FFT bill PGKG at $3.96/kg)
  // but it is an ordinary price for ONE pear (Select Fresh bill PGRE at $0.40
  // each), and the book has always treated it that way — Rocket Man takes 0.25
  // and is charged $0.1625. The pack is pinned at 1 ea so the book states
  // something true, and the rule keeps reporting it because the fix that closes
  // it is a rename to "Pears Green [ea]" in Back Office, not another override.
  ok(`the ingredient feed's rate/name contradictions are down to the naming one (got ${feedHits.length})`,
     feedHits.length <= 2, feedHits.map(i => i.description).join(' / '));
  ok('...and Pears Green [kg] — price right, name wrong — is still said out loud',
     feedHits.some(i => /Pears Green \[kg\]/.test(i.description)),
     feedHits.map(i => i.description).join(' / '));
  ok('...while the three that were pinned stay fixed',
     !feedHits.some(i => /Onion Brown \[kg\]|Potato Peeled \[kg\]|Ponzu Dashi/.test(i.description)),
     feedHits.map(i => i.description).join(' / '));
} else {
  console.log('  (skipped the real-book calibration — data/ingredients.json is built, not committed)');
}

if (full) {
  // never-used-this-much, over every saved non-batch recipe, each line judged
  // against the whole book INCLUDING itself (which is what the builder does
  // when you open a recipe to edit it). Opening a saved recipe must be silent.
  const idx = G.peerIndex(full.recipes || []);
  const isBatch = (r) => !!r.yield_qty
    || /\[(batch|prep|recipe|\d+\s*(kg|g|l|ml))\]|\b(prep|batch|blend|mix)\b/i.test(r.product || '')
    || (Number(r.sell_incl_gst) || 0) < 3;
  let noisy = [], checked = 0;
  for (const r of full.recipes || []) {
    for (const ln of r.lines || []) {
      if (!ln.id) continue;
      checked++;
      const w = G.lineWarnings({ name: ln.id, packUnit: ln.unit, qty: Number(ln.qty), qtyUnit: ln.unit },
        { isBatch: isBatch(r), peerQuantities: idx.get(ln.id) || [] });
      if (w.some(x => x.code === 'never-used-this-much')) noisy.push(`${r.product} | ${ln.id}`);
    }
  }
  ok(`opening any of the ${checked} saved id lines raises no "never used this much"`,
     noisy.length === 0, noisy.slice(0, 5).join(' / '));

  // The harder question: type a saved quantity in FRESH, so the line is judged
  // against the OTHER recipes only. That is the real cry-wolf rate. At 10x it
  // is 0; at 5x it is 7 (six-packs: 6 cans against a peer max of 1) and at 3x
  // it is 17 (four- and six-packs). The lettuce sits at 12x.
  let fresh = [];
  for (const r of full.recipes || []) {
    if (isBatch(r)) continue;
    for (const ln of r.lines || []) {
      if (!ln.id) continue;
      const peers = (idx.get(ln.id) || []).slice();
      peers.splice(peers.indexOf(Number(ln.qty)), 1);       // judge against the others
      const w = G.lineWarnings({ name: ln.id, packUnit: ln.unit, qty: Number(ln.qty), qtyUnit: ln.unit },
        { isBatch: false, peerQuantities: peers });
      if (w.some(x => x.code === 'never-used-this-much')) fresh.push(`${r.product} | ${ln.id} @ ${ln.qty}`);
    }
  }
  ok('re-entering any saved non-batch quantity from scratch raises no false alarm',
     fresh.length === 0, fresh.slice(0, 6).join(' / '));
} else {
  console.log('  (skipped the peer calibration — data/recipes_full.json is built, not committed)');
}

/* ======================================================================= WIRING
   The guard is worth nothing if the page stops calling it, and that failure is
   silent: the builder keeps working, it just stops warning. So the binding is
   asserted as text, the same way arch_guard.py asserts the sales dashboard's
   behaviour markers. And the DECIDING must not migrate back into the HTML —
   the rule words below belong to the module and nowhere else. */
{
  // The builder's script left index.html when the book and the builder became
  // one module with tabs; it is dashboard/_shared/recipe_builder.js now, moved
  // verbatim. The wiring is asserted where the code is, and the "no logic in
  // the HTML" half is asserted against the shell — which is now a stronger
  // claim than it was, because the shell holds no builder code at all.
  const page = fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_builder.js'), 'utf8');
  const shell = fs.readFileSync(path.join(ROOT, 'modules/recipes/app/index.html'), 'utf8');
  ok('the builder imports the guard', page.includes("from '/_shared/recipe_line_guard.js'"));
  ok('every line has a warning slot', page.includes('id="lw-${k}"'));
  ok('the slot is painted on every recompute', page.includes('warningsHtml(builderLineWarnings('));
  ok('a batch is passed through, so batch rules can switch off', page.includes('isBatch: isPrep'));
  ok('the peer history is passed through', page.includes('peerQuantities:'));
  for (const token of ['twin\\s*pack', 'PEER_MAX_MULTIPLE', 'COUNT_PACK_QTY_CEILING', 'punnet']) {
    ok(`no rule logic in index.html — "${token}" stays in the module`, !shell.includes(token));
  }
  ok('...and the shell holds no builder logic either, after the merge',
     !shell.includes('function lineCost') && !shell.includes('buildYaml'));
  ok('the module is the only place the thresholds live',
     fs.readFileSync(path.join(ROOT, 'dashboard/_shared/recipe_line_guard.js'), 'utf8')
       .includes('PEER_MAX_MULTIPLE = 10'));
}

console.log(`\n${n} builder-guard assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
