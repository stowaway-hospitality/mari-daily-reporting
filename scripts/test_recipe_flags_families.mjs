/* Does the flags panel actually SHOW everything?  Run: node scripts/test_recipe_flags_families.mjs

   WHAT THIS IS FOR
   ----------------
   scripts/test_recipe_book_flags.mjs checks that the panel words a flag
   honestly. This checks a different thing, and the one Zak has now asked for
   twice: that every family of open question is ON it, and that the rendered
   HTML contains them — not just the JSON.

   The failure mode is specific and it has already happened. The feed had nine
   families and two whole classes were missing from it:

     * the uncosted LONG TAIL. 299 of the 334 coverage gaps sit under the $500
       a product needs to earn its own flag — every kitchen add-on, every "Add
       Prawns", Sticky Chicken Wings at $41.36 — $20,113 of 13-week revenue
       that the panel did not mention at all.
     * the UNIT defects. A lemon priced $0.375 per millilitre, a cauliflower
       and a Turkish bread priced per "can", an avocado priced per tray beside
       the same avocado priced per each, and the recipe lines that take
       "0.083 ml" of a twin pack of cos. Every one of these was found by a
       human reading a screen, and none of them was in any feed.

   And a third failure, which is why the panel is now a TAB: it used to render
   BELOW 913 rows of table. Nobody scrolls 913 rows, so the correct conclusion
   from looking at the page was that the flags were not there.

   THE NAMES BELOW ARE REAL. Every dish and product named is one Zak listed or
   one the derivation produced; they are asserted by name so that a rule which
   quietly stops firing goes red instead of going quiet.
*/
import fs from 'fs'; import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const view = await import('file://' + path.join(ROOT, 'dashboard/_shared/flags_view.js'));

let fails = 0, n = 0;
const ok = (label, cond, extra = '') => {
  n++;
  if (!cond) { fails++; console.log(`✗ ${label}${extra ? '\n    ' + extra : ''}`); }
};

// --- the third kind of number, on fixtures ---------------------------------
// A unit defect has no measurable under-cost (we do not know which side of the
// contradiction is wrong) and it is not uncosted revenue either. Showing it as
// "not quantified" would drop the whole family to the bottom of a queue sorted
// by money; showing it as an impact would make the panel's headline false.
{
  const w = view.weight({ impact_per_year: null, revenue_13wk: null,
                          cost_at_stake_per_year: 516.3 });
  ok('cost at stake is its own kind of number', w.kind === 'stake', w.kind);
  ok('...and says "at stake", not "/yr" alone', w.text === '$516/yr at stake', w.text);
  ok('...and an impact still wins over it',
     view.weight({ impact_per_year: 2725.8, cost_at_stake_per_year: 99 }).kind === 'impact');
  ok('...and revenue still wins over it',
     view.weight({ revenue_13wk: 4707.62, cost_at_stake_per_year: 99 }).kind === 'revenue');
  ok('...and with none of the three it is still honestly unquantified',
     view.weight({}).kind === 'none');
}
{
  // The arithmetic behind an at-stake figure has to be interrogable for the
  // same reason an impact's does: a number on a work queue that cannot be
  // argued with gets argued with once and then ignored.
  const html = view.flagRow({
    id: 'x', severity: 'high', category: 'feed_defect', subject: 'Lemon',
    what_is_wrong: 'w', why_it_matters: 'y', action: 'a', owner: 'Dev',
    cost_at_stake_per_year: 206.29,
    cost_at_stake_basis: '$206.29 of recipe cost a year is drawn through this record',
    evidence: [], source: 's', derived: true });
  ok('an at-stake figure shows its working', html.includes('How that number is arrived at'));
  ok('...and is not dressed as a measured impact', !html.includes('class="fl-w impact"'));
}

// --- against the REAL feed ---------------------------------------------------
const p = path.join(ROOT, 'data/cost_book_flags.json');
if (!fs.existsSync(p)) {
  console.log('  (skipped: data/cost_book_flags.json not built — run scripts/build_cost_book_flags.py)');
} else {
  const d = JSON.parse(fs.readFileSync(p, 'utf8'));
  const html = view.flagsHtml(d);
  const cats = new Set(d.flags.map(f => f.category));
  const text = html.replace(/<[^>]*>/g, ' ');
  const inFeed = (s) => d.flags.some(f =>
    (f.subject || '').includes(s)
    || (f.evidence || []).some(e => String(e).includes(s))
    || (f.what_is_wrong || '').includes(s));

  // Every family, by category key.
  // batch_yield is NOT in this list: every one of its findings has been settled
  // (real Lightspeed yields + the chilli rate), and a family that is fully
  // resolved should read as empty, not as a broken build.
  //
  // `decision` left the list on 2026-08-10 for the same reason. Its only member
  // was decision-ilg-reparse — "re-parsing the historical ILG invoices is blocked
  // on a unit question" — and the question was answered and the re-parse ran
  // (063d9b7, b2ef362). A decision flag exists to ask something; when it has an
  // answer in the code there is nothing left to render. Asserting it is still
  // there would demand the repo keep a settled question open forever, which is
  // exactly the trap the batch_yield note above records.
  // bad_seed left this list on 2026-08-14 for the reason stated just above:
  // both its members were fixed (the Gulli carton-as-each seeds, corrected in
  // data/cogs_list.csv to $1.4952 and $0.11055), so demanding the family would
  // demand the defect. The fix is guarded at section 5 instead.
  for (const c of ['cook_loss', 'structure', 'price_conflict',
                   'no_recipe', 'back_office',
                   'feed_defect']) {
    ok(`family present: ${c}`, cats.has(c), [...cats].join(','));
  }
  // ...and every family that is present is DRAWN. A category missing from
  // `categories` renders nothing at all, silently — the flags are in the JSON
  // and on nobody's screen.
  for (const c of cats) {
    ok(`family is rendered, not just carried: ${c}`,
       (d.categories || []).some(x => x.key === c), c);
  }
  ok('every flag in the feed reaches the HTML',
     d.flags.every(f => html.includes(`id="${f.id}"`)),
     d.flags.filter(f => !html.includes(`id="${f.id}"`)).map(f => f.id).join(', '));

  // 1. yields nobody has measured. Lamb Roast is a PLATED portion, so the loss
  //    is on the plate, not on a batch.
  for (const s of ['Cooked Beef Brisket', 'Achiote Chicken']) {
    ok(`missing yield flagged: ${s}`, inFeed(s));
  }
  // LAMB IS ANSWERED — Zak weighed it 2026-08-15, "raw lamb 2.7kg was 2.3kg
  // cooked". 2.3/2.7 = 85.19%, applied in data/cook_yields.yaml, which scales the
  // plated 220 g to the 258.3 g of raw leg behind it. So the question is gone and
  // this guards that it stays gone — while the two roasts nobody has weighed stay
  // open, because the three proteins do not shrink at the same rate.
  ok('the lamb cook-loss question is settled, not merely quiet',
     !d.flags.some(f => f.id === 'yield-lamb-roast'),
     'yield-lamb-roast is back — check data/cook_yields.yaml still applies');
  for (const s of ['yield-pork-roast', 'yield-beef-roast']) {
    ok(`still open, nobody has weighed it: ${s}`, d.flags.some(f => f.id === s));
  }
  ok('the remaining yield questions are still sized off a plated portion',
     d.flags.some(f => f.category === 'cook_loss' && /plated|serve/i.test(f.impact_basis || '')));

  // 2. dishes with NO costed recipe — derived from audit_book.coverage(), never
  //    a hardcoded list, so a recipe landing removes its own flag.
  for (const s of ['Shredded Beef', 'Miso', 'Shoyu', 'Unlimited BBQ',
                   'Chicken Karaage', 'BBQ Meat Platter', 'Edamame',
                   'Arancini Balls', 'Baked Camembert', 'Pie', 'Roast Turkey',
                   'Beef Cheek']) {
    ok(`uncosted dish flagged: ${s}`, inFeed(s));
  }
  ok('the uncosted long tail is not silently dropped',
     d.flags.some(f => f.id.startsWith('no-recipe-tail')));
  ok('...the kitchen add-ons are named as a group',
     d.flags.some(f => f.id.startsWith('no-recipe-tail') && /Add-ons - Kitchen/.test(f.subject)),
     d.flags.filter(f => f.id.startsWith('no-recipe-tail')).map(f => f.subject).join(' | '));
  ok('...and Sticky Chicken Wings, at $41, is inside one of them',
     inFeed('Sticky Chicken Wings'));
  ok('...with every member listed, so nothing hides behind the total',
     d.flags.filter(f => f.id.startsWith('no-recipe-tail'))
            .every(f => (f.evidence || []).length >= 3));

  // 3. the reconcile findings. Assert the FAMILIES are carried, not a frozen
  //    total: the count legitimately falls as findings get settled (Cauliflower
  //    Burrito's cheese was resolved on 2026-08-09 — it carries vegan cheese at
  //    the same 55 g), and a pinned number turns every real fix into a red build.
  for (const c of ['structure', 'price_conflict']) {
    ok(`reconcile family carried: ${c}`, d.flags.some(f => f.category === c));
  }
  // No total pinned here on purpose. The per-family checks above are the real
  // contract; a count would go red every time a finding is SETTLED, which is the
  // trap this file just fell into twice (Cauliflower's cheese, then three of the
  // four batch yields). Settling work should never break the build.

  // 4. Back Office duplicates.
  // CLOSED 2026-08-09: HG's Back-Office copy seeded Angostura at $1.34 a 200 mL
  // bottle against an ILG invoice of $17.305 (13x low) and four cocktails costed
  // off it. The seed now carries the invoiced figure, so the duplicate is gone.
  // Guard the fix rather than demand the flag: re-break the seed and this reds.
  ok('the Angostura Back Office duplicate stays closed',
     !d.flags.some(f => f.category === 'back_office' && /Angostura/i.test(f.subject || '')));
  // WAS: 'Plantation is flagged as a Back Office duplicate'. Back Office held the
  // one bottle three times and Harry Gatos' copy said 4500 ML — not a size
  // Plantation 3 Stars is sold in — against the SAME $60.83 its 700 ML twins
  // carry, so it read $0.013518/ml where they read $0.0869. Same price, same
  // product, a size that does not exist: the size was the typo. Pinned to 700 ml
  // in data/pack_overrides.yaml on 2026-08-10, so the duplicate is resolved and
  // the flag is correctly gone. This now guards the FIX — re-break the override
  // and the duplicate comes back and this reds.
  ok('Plantation duplicate is resolved, not merely reported',
     !inFeed('Plantation'),
     'Plantation is flagged again — the 4500ML pack override has been lost');

  // 5. the two case-price-as-each seeds — FIXED 2026-08-14, so this guards the
  //    fix. Both were recipe-bridge-seed rows holding a Gulli PACK price in a
  //    per-each field: garlic bread $59.81 (carton of 40) and pizza box inserts
  //    $11.055 (box of 100). Neither mispriced a dish — costs.csv already had the
  //    invoiced rate — but a seed is the fallback for any ProductID that reaches
  //    a recipe before its first invoice, so a $59.81 garlic bread was a loaded
  //    gun with the safety on. Re-break either row and the family comes back.
  ok('the case-priced-as-each seeds stay fixed',
     !d.flags.some(f => f.category === 'bad_seed'),
     d.flags.filter(f => f.category === 'bad_seed').map(f => f.subject).join(' | '));

  // 6. the config and decision families. The 20 L vodka drum gap this used to
  //    flag was CLOSED by adding the per_bulk bound to suppliers.yaml (a14a5818) —
  //    exactly what the flag's own action asked for — so config is now an empty,
  //    resolved family. Guard the fix stays in rather than demand the flag: remove
  //    the bound and the drum re-appears here, and this goes red.
  ok('the 20 L vodka drum bounds gap stays closed',
     !d.flags.some(f => f.category === 'config' && /vodka|20000ml/i.test(f.subject || '')));
  ok('the pending ILG re-parse decision is flagged', inFeed('ILG'));

  // 7. the feed defects. Every one of these was found by eye.
  // THE LEMON IS FIXED (2026-08-21) — same rule as the burger lettuce below: a
  // test that pins a defect expires the day someone fixes it, so both halves
  // are inverted rather than deleted. $0.375 was never a volume rate, it is
  // what ONE LEMON costs: Select Fresh bill LEME "LEMON EA" at $0.45 each and
  // LEMK "LEMON KG" at $3.20/kg, and at $3.20/kg a 117 g lemon is $0.375
  // exactly. The seed is stated per ea now, so the 117x two-dimensions gap is
  // closed and what remains is an honest weighing question.
  ok('the lemon two-dimensions gap stays closed', !inFeed('Lemon'));
  ok('...and no panel still says $0.3750 per ml',
     !(/0\.3750/.test(text) && /per ml/i.test(text)));
  ok('Cauliflower [ea] carrying pack unit "can" is flagged', inFeed('Cauliflower [ea]'));
  ok('Turkish Bread [ea] carrying pack unit "can" is flagged', inFeed('Turkish Bread [ea]'));
  ok('Avocado priced per tray is flagged', inFeed('Avocado'));
  // The burger lettuce was this rule's canonical example — 0.083 "ml" of a twin
  // pack of baby cos, a twelfth of the pack at $0.228, the cost right and the
  // unit meaningless. FIXED 2026-08-14 by a label-only entry in
  // data/recipe_line_unit_fixes.yaml (ml -> ea, NO cost_factor), so it is gone
  // from the family and the money did not move. Guard both halves.
  ok('the American Standard Burger lettuce line stays fixed',
     !d.flags.some(f => f.category === 'feed_defect'
                     && /Lettuce Cos Baby Twin Pack/.test(f.subject || '')),
     d.flags.filter(f => f.category === 'feed_defect').map(f => f.subject).join(' | '));
  // ...and the rule is still alive: the ingredients it has NOT been told about
  // are still reported, and still say the cost is not in dispute.
  ok('the line-unit rule still fires on what is left',
     d.flags.some(f => f.category === 'feed_defect'
                    && /line\(s\) in the wrong unit/.test(f.subject || '')
                    && /quantity/i.test(f.why_it_matters || '')),
     d.flags.filter(f => f.category === 'feed_defect').map(f => f.subject).join(' | '));

  // 8. ranked by money, inside each severity band.
  {
    const dollars = (f) => (Number(f.impact_per_year) || 0)
      || (Number(f.revenue_13wk) || 0) || (Number(f.cost_at_stake_per_year) || 0);
    const bands = {};
    for (const f of d.flags) (bands[f.severity] ||= []).push(f);
    for (const [sev, list] of Object.entries(bands)) {
      const quantified = list.filter(dollars);
      const idx = list.map((f, i) => [f, i]).filter(([f]) => dollars(f)).map(([, i]) => i);
      ok(`the ${sev} band leads with the quantified ones`,
         idx.length === 0 || Math.max(...idx) < list.length,
         `${quantified.length} of ${list.length}`);
    }
    ok('high severity comes before medium',
       d.flags.findIndex(f => f.severity === 'medium')
         > d.flags.findLastIndex(f => f.severity === 'high'));
  }

  // 9. every flag says what closes it, and who. A queue without an owner is a
  //    list of complaints.
  ok('every flag has an action', d.flags.every(f => (f.action || '').length > 10),
     d.flags.filter(f => !(f.action || '').length).map(f => f.id).join(', '));
  ok('every flag has an owner', d.flags.every(f => (f.owner || '').length > 1));
  ok('every flag says where it came from', d.flags.every(f => (f.source || '').length > 5));
  ok('at-stake dollars are NEVER added to the measured headline',
     Math.abs(d.known_impact_per_year
              - d.flags.reduce((a, f) => a + (Number(f.impact_per_year) || 0), 0)) < 0.02);
  ok('...and no flag claims both an impact and an at-stake figure',
     !d.flags.some(f => f.impact_per_year && f.cost_at_stake_per_year));

  console.log(`  (real feed: ${d.flags.length} flags across ${cats.size} families,`
    + ` $${Math.round(d.known_impact_per_year).toLocaleString()}/yr measured)`);
}

// --- MUTATION CHECK -----------------------------------------------------------
// A feed with the two new families removed must fail the family assertions.
{
  const stripped = { categories: [{ key: 'no_recipe', title: 'x', why: 'y' }], flags: [] };
  const html = view.flagsHtml(stripped);
  ok('MUTATION: a feed without feed_defect renders no unit defects',
     !/Lemon|Cauliflower/.test(html));
  ok('MUTATION: a feed with no flags at all still renders its header, not a crash',
     html.includes('What the book still needs from a human'));
  ok('MUTATION: a missing feed says so instead of drawing an empty queue',
     view.flagsHtml(null).includes('has not been built yet'));
}

console.log(`\n${n} flags-family assertions, ${fails} failures`);
process.exit(fails ? 1 : 0);
