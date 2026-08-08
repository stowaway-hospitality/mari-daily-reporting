/**
 * The cost-book flags panel, as a PURE function: feed in, HTML out.
 *
 * WHY IT IS SPLIT FROM flags.js
 * -----------------------------
 * The same split the sales dashboard makes between pnl.js (the model, pure, no
 * DOM) and render.js (the page). scripts/arch_guard.py enforces it there — R5,
 * "the model never touches the page" — because a renderer that also fetches and
 * mutates the document cannot be tested without a browser, and what nobody can
 * test nobody checks.
 *
 * So everything that DECIDES anything lives here: which flags go in which
 * section, how a dollar figure is worded, what a flag with no dollar figure
 * says instead. It touches no document, imports nothing, and returns a string.
 * scripts/test_recipe_book_flags.mjs runs it under node against the real feed.
 *
 * IT COMPUTES NO MONEY. Every figure is read verbatim from
 * data/cost_book_flags.json, where scripts/build_cost_book_flags.py also wrote
 * the arithmetic that produced it (`impact_basis`). A flag with
 * `impact_per_year: null` renders as "not quantified" and must NEVER render as
 * $0 — those are different claims, and showing an unknown as a zero would sort
 * the queue wrong and read as "this one is free to ignore".
 */

export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const dollars = (n) => '$' + Math.round(Number(n || 0)).toLocaleString('en-AU');

const SEV = {
  high:   { label: 'high',   colour: 'var(--red)' },
  medium: { label: 'medium', colour: 'var(--amber)' },
  low:    { label: 'low',    colour: 'var(--ink-soft)' },
};

/**
 * The headline number for a flag, and what KIND of number it is.
 *
 * Three distinct states, deliberately, because collapsing them is how a work
 * queue lies:
 *   impact  — a measured $/yr under-cost, with its arithmetic in impact_basis
 *   revenue — no measured under-cost, but this much revenue is uncosted. That
 *             is revenue at stake, NOT an error, and it is labelled as such
 *   none    — nobody knows, and the panel says nobody knows
 */
export function weight(f) {
  if (f.impact_per_year) return { text: dollars(f.impact_per_year) + '/yr', kind: 'impact' };
  if (f.revenue_13wk) return { text: dollars(f.revenue_13wk) + ' 13wk', kind: 'revenue' };
  return { text: 'not quantified', kind: 'none' };
}

export function flagRow(f) {
  const sev = SEV[f.severity] || SEV.low;
  const w = weight(f);
  const evidence = (f.evidence || []).map(e => `<li>${esc(e)}</li>`).join('');
  // The arithmetic behind a dollar figure sits WITH the figure, folded away. A
  // number on a work queue that cannot be interrogated gets argued with once
  // and then ignored.
  const basis = f.impact_basis
    ? `<div class="fl-basis"><b>How that number is arrived at.</b> ${esc(f.impact_basis)}</div>` : '';
  const question = f.question ? `<div class="fl-q">${esc(f.question)}</div>` : '';
  return `<div class="fl" data-sev="${esc(f.severity)}" data-cat="${esc(f.category)}" id="${esc(f.id)}">
    <div class="fl-head">
      <span class="fl-pill" style="--pill:${sev.colour}">${esc(sev.label)}</span>
      <span class="fl-subj">${esc(f.subject)}</span>
      <span class="fl-w ${w.kind}">${esc(w.text)}</span>
    </div>
    <div class="fl-wrong">${esc(f.what_is_wrong)}</div>
    ${question}
    <div class="fl-why">${esc(f.why_it_matters)}</div>
    <div class="fl-act"><b>To close it:</b> ${esc(f.action)}
      <span class="fl-own" title="who has to do it">${esc(f.owner)}</span></div>
    ${(evidence || basis) ? `<details class="fl-more"><summary>evidence</summary>
       ${basis}${evidence ? `<ul>${evidence}</ul>` : ''}
       <div class="fl-src">${esc(f.derived ? 'derived from' : 'declared in')} ${esc(f.source)}</div>
     </details>` : ''}
  </div>`;
}

export function section(cat, flags) {
  const mine = (flags || []).filter(f => f.category === cat.key);
  if (!mine.length) return '';
  const known = mine.reduce((a, f) => a + (Number(f.impact_per_year) || 0), 0);
  return `<section class="fl-sec">
      <h3>${esc(cat.title)} <span class="fl-n">${mine.length}</span>
        ${known ? `<span class="fl-sum">${dollars(known)}/yr measured</span>` : ''}</h3>
      <p class="fl-why-sec">${esc(cat.why)}</p>
      ${mine.map(flagRow).join('')}
    </section>`;
}

/**
 * Uncosted revenue that will never have a recipe, kept VISIBLE rather than
 * silently filtered out. "Open Price is $2,194 of uncosted revenue" is true and
 * will be rediscovered by whoever looks next; saying out loud that it can never
 * be costed is the only thing that stops it being rediscovered every quarter.
 */
export function exemptSection(exempt) {
  if (!exempt || !exempt.length) return '';
  const byReason = new Map();
  for (const e of exempt) {
    if (!byReason.has(e.reason)) byReason.set(e.reason, []);
    byReason.get(e.reason).push(e);
  }
  const blocks = [...byReason.entries()].map(([reason, items]) => {
    const rev = items.reduce((a, i) => a + (Number(i.revenue_13wk) || 0), 0);
    const names = [...new Set(items.map(i => i.subject))].sort();
    return `<li><b>${esc(names.join(', '))}</b> — ${dollars(rev)} in 13wk.
      <span class="fl-why">${esc(reason)}</span></li>`;
  }).join('');
  return `<section class="fl-sec">
      <h3>Permanently exempt <span class="fl-n">${exempt.length}</span></h3>
      <p class="fl-why-sec">Uncosted revenue that will never have a recipe. Listed so
        it stops being re-found as work.</p>
      <ul class="fl-exempt">${blocks}</ul>
    </section>`;
}

export function headerHtml(d) {
  const c = d.counts || {};
  const sev = Object.entries(c.by_severity || {})
    .map(([k, v]) => `${v} ${esc(k)}`).join(' · ');
  const y = (d.assumptions || {}).cook_loss_yield;
  return `<div class="fl-top">
      <div>
        <h2>What the book still needs from a human</h2>
        <p class="fl-lede"><b>${c.total || 0}</b> open ${sev ? `(${sev})` : ''} —
          <b>${dollars(d.known_impact_per_year)}</b> a year of under-cost that has
          actually been measured, across ${c.with_a_dollar_figure || 0} of them.
          The rest are real and not yet quantified; none of them is guessed.</p>
        <p class="fl-note">${esc(d.note || '')} Built ${esc(d.generated_at || '')}.
          ${y ? `The ${esc(y)} cook-loss yield sizes those questions and is applied to no cost anywhere.` : ''}</p>
      </div>
      <div class="fl-filters">
        <label><input type="checkbox" id="fl-high"> high only</label>
      </div>
    </div>`;
}

/** The whole panel, as HTML. The only entry point flags.js needs. */
export function flagsHtml(d) {
  if (!d || !d.flags) {
    return '<div class="fl-loading">The flags feed has not been built yet '
      + '(scripts/build_cost_book_flags.py).</div>';
  }
  return headerHtml(d)
    + (d.categories || []).map(c => section(c, d.flags)).join('')
    + exemptSection(d.exempt);
}
