/**
 * Restatements — the numbers that changed AFTER you were shown them.
 *
 * WHY THIS PANEL EXISTS
 * ---------------------
 * Zak, 2026-08-10: "if my reported numbers are going to be changing, i need to
 * know what's happened." Marilyna's July is the case that prompted it: as each
 * weekly Xero pull landed, July overheads went $1,313 -> $5,663 and July Uber
 * fees $2,130 -> $8,529, over eight and six revisions respectively. Every one of
 * those figures was correct on the day. July profit still fell by thousands after
 * the month ended, and nothing on screen said so.
 *
 * A number that moves silently is worse than a number that is wrong: you cannot
 * argue with what you cannot see. So this does not freeze anything — bills arrive
 * late, that is normal accrual and the figures SHOULD get more accurate — it just
 * says out loud what moved, by how much, and how settled it is now.
 *
 * Pure. Takes the feed, returns strings. scripts/test_restatements.mjs drives it.
 */

const MATURITY = {
  provisional: { label: 'provisional', tone: 'warn',
                 why: 'still moving — costs are arriving' },
  settling:    { label: 'settling',    tone: 'mute',
                 why: 'last change was small' },
  final:       { label: 'final',       tone: 'good',
                 why: 'nothing new for a fortnight' },
};

const VENUE = { stow: 'Stowaway', hg: 'Harry Gatos', mari: "Marilyna's", group: 'Group' };

const esc = (t) => String(t == null ? '' : t)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const money = (n) => (n < 0 ? '−$' : '$') + Math.abs(Number(n) || 0)
  .toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const signed = (n) => (Number(n) >= 0 ? '+' : '−') + '$'
  + Math.abs(Number(n) || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });

/** Series that actually moved, biggest dollar swing first. */
export function restated(feed, opts) {
  const o = opts || {};
  const all = Object.values((feed && feed.series) || {});
  let rows = all.filter((s) => (s.revision_count || 0) > 1);
  if (o.venue && o.venue !== 'group') rows = rows.filter((s) => s.venue === o.venue);
  if (o.sinceDays != null) rows = rows.filter((s) => (s.quiet_days ?? 999) <= o.sinceDays);
  return rows.sort((a, b) => Math.abs(b.delta || 0) - Math.abs(a.delta || 0));
}

/** One line a human can read without a key. */
export function line(s) {
  const m = MATURITY[s.maturity] || MATURITY.provisional;
  const pct = (s.pct == null) ? '' : ` (${s.pct >= 0 ? '+' : ''}${s.pct}%)`;
  return `${VENUE[s.venue] || s.venue} ${s.period} ${s.label}: `
       + `${money(s.first)} → ${money(s.latest)} ${signed(s.delta)}${pct}`
       + ` · ${s.revision_count} revisions · ${m.label}`;
}

export function rowHtml(s) {
  const m = MATURITY[s.maturity] || MATURITY.provisional;
  const pct = (s.pct == null) ? '' : `${s.pct >= 0 ? '+' : ''}${s.pct}%`;
  return `<tr>
    <td>${esc(VENUE[s.venue] || s.venue)}</td>
    <td>${esc(s.period)}</td>
    <td>${esc(s.label)}</td>
    <td class="r">${money(s.first)}</td>
    <td class="r">${money(s.latest)}</td>
    <td class="r ${Number(s.delta) >= 0 ? 'up' : 'down'}">${signed(s.delta)}${pct ? ` <small>${esc(pct)}</small>` : ''}</td>
    <td class="r">${esc(s.revision_count)}</td>
    <td><span class="rs-chip ${esc(m.tone)}" title="${esc(m.why)}">${esc(m.label)}</span></td>
  </tr>`;
}

/**
 * The panel. `venue` scopes it; null shows every venue.
 *
 * Deliberately shows the DIRECTION of travel (first -> latest) rather than only
 * the last hop: the last hop on Marilyna's July was +$527, which reads as noise.
 * The whole move was +$4,350, which does not.
 */
export function panelHtml(feed, opts) {
  const o = opts || {};
  const rows = restated(feed, o);
  if (!rows.length) {
    return `<div class="rs-empty">No reported figure has changed since it was
      first published. Numbers here move when late supplier bills land in Xero —
      when that happens it is recorded, not hidden.</div>`;
  }
  const still = rows.filter((s) => s.maturity === 'provisional').length;
  const top = rows.slice(0, o.limit || 12);
  return `<p class="rs-note">${rows.length} reported figure${rows.length === 1 ? '' : 's'}
      changed after first publication${still ? `, and <b>${still}</b> ${still === 1 ? 'is' : 'are'} still moving` : ''}.
      Late bills coded to a closed period are the usual cause — the figure gets
      more accurate, so this says what moved rather than freezing it.</p>
    <table class="rs-table">
      <thead><tr><th>Venue</th><th>Period</th><th>What</th><th class="r">First said</th>
        <th class="r">Now</th><th class="r">Change</th><th class="r">Revisions</th><th>Status</th></tr></thead>
      <tbody>${top.map(rowHtml).join('')}</tbody>
    </table>
    ${rows.length > top.length ? `<p class="rs-note">…and ${rows.length - top.length} smaller.</p>` : ''}`;
}

/** Mount into an element id, fetching the feed. Never throws into the page. */
export async function mountRestatements(el, opts) {
  const host = typeof el === 'string' ? document.getElementById(el) : el;
  if (!host) return;
  try {
    const r = await fetch('/data/restatements.json?t=' + Date.now());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    host.innerHTML = panelHtml(await r.json(), opts);
  } catch (e) {
    host.innerHTML = '<div class="rs-empty">Restatement history is not built yet.</div>';
  }
}
