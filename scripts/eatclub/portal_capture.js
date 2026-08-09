/* EatClub partner portal — the canonical row extractor.
 *
 * Paste into the browser console (or run via the Chrome MCP) on
 * partners.eatclub.com.au/transactions. It exists because the capture used to be
 * ad-hoc JS retyped every morning, and two of its failure modes are silent:
 *
 *  1. WRONG STORE. One login serves three stores and the portal ALWAYS opens on
 *     Stowaway Bar. The store name appears only in the sidebar and document.title.
 *     A pull that skips the switch writes another venue's tables into the file.
 *     This happened on 22-23 Jul 2026 and contaminated the Harry Gatos master.
 *
 *  2. TRUNCATED TABLE. The transactions table renders a capped page (50 rows as
 *     at 2026-08-09). Reading it as if it were the whole period silently drops
 *     the oldest rows. Harmless for a last-night pull because the table is
 *     date-descending; corrupting for any backfill.
 *
 * extractNight() refuses to return data unless BOTH are checked.
 */

const ROW_CAP_HINT = 50;   // observed page size; treat >= this as possibly capped

function currentStore() {
  const m = (document.title || '').match(/\|\s*(.+?)\s*\|/);
  return m ? m[1].trim() : null;
}

function readRows() {
  return [...document.querySelectorAll('tr')]
    .filter(r => r.querySelectorAll('td').length >= 6)
    .map(r => [...r.querySelectorAll('td')].map(c => c.innerText.replace(/\n+/g, '|').trim()));
}

/* expectedStore: 'Stowaway Bar' | 'Harry Gatos' | 'Marilynas Famous Pizza'
 * nightDDMMYY  : the arrival date as the portal prints it, e.g. '08/08/26'
 * Returns {store, rows, total, capped} or throws.                              */
function extractNight(expectedStore, nightDDMMYY) {
  const store = currentStore();
  if (store !== expectedStore) {
    throw new Error(
      `WRONG STORE: page is "${store}", expected "${expectedStore}". ` +
      `Switch store (sidebar -> Change venue) and confirm document.title before reading.`);
  }

  const all = readRows();
  const rows = all.filter(r => r[1] && r[1].includes(nightDDMMYY));
  const dates = [...new Set(all.map(r => (r[1] || '').slice(0, 8)))];
  const capped = all.length >= ROW_CAP_HINT;

  // Truncation only matters if the night we want could be beyond the cut. The
  // table is date-descending, so a capped page is safe iff our night is not the
  // oldest date on it.
  const oldest = dates[dates.length - 1];
  if (capped && oldest === nightDDMMYY) {
    throw new Error(
      `TABLE MAY BE TRUNCATED: ${all.length} rows rendered (cap ~${ROW_CAP_HINT}) and ` +
      `${nightDDMMYY} is the oldest date shown, so earlier rows for that night may be ` +
      `cut off. Narrow the date filter to that night and re-read before trusting this.`);
  }

  return { store, rows, total: all.length, capped, datesOnPage: dates.slice(0, 8) };
}

/* Blank status cell == UNREDEEMED (the portal renders nothing for an unclaimed
 * offer). Normalise it so giveaway.py's status guard sees a known value. */
function normaliseStatus(cell) {
  const s = (cell || '').trim().toUpperCase();
  return s === '' ? 'UNREDEEMED' : s;
}
