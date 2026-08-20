/* QR Orders module — all logic for /qr/ (index.html is a shell, arch_guard style).
 *
 * Fronts the QR table-ordering service on the bookings box, the same way
 * bookings.js fronts the seating engine:
 *
 *   Supabase (Auth.gate)  ->  who may OPEN the page
 *   service bearer token  ->  the real auth on every API call
 *
 * The token comes from Auth.bookingToken() — the same app_config row the
 * Bookings module uses, because it IS the same service and the same
 * STOWAWAY_ADMIN_TOKEN. Signed-in staff never paste a token; the #tokenbox
 * is a fallback for the day the app_config row is missing.
 *
 * While the Lightspeed till link is dry-run, the Queue IS the docket: new
 * orders flash and ding until someone marks them done. The 86 board flips
 * items off the guest menu live — next menu load greys them out, and the
 * server refuses payment for them even from a guest holding a pre-86 menu.
 */
import { Auth } from '/_shared/auth.js';

const API = 'https://stowaway-bookings.onrender.com';
const TOKEN_KEY = 'stowaway_booking_token';   // shared with /bookings/ — same service, same token
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let SVC = '';
let MENU = null;
let seen = new Set();
let timer = null;

// ---------------------------------------------------------------- token
/** Supabase first (no paste), then any locally-saved fallback. */
async function ensureToken() {
  if (SVC) return SVC;
  try {
    const t = await Auth.bookingToken();
    if (t) { SVC = t; return SVC; }
  } catch (_) { /* fall through to the manual fallback */ }
  SVC = localStorage.getItem(TOKEN_KEY) || '';
  return SVC;
}

const hdrs = () => ({ 'Authorization': 'Bearer ' + SVC, 'Content-Type': 'application/json' });

async function call(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
  if (r.status === 401) {
    // Wrong/missing service token: clear it and surface the fallback box.
    SVC = ''; localStorage.removeItem(TOKEN_KEY);
    $('tokenbox').style.display = '';
    throw new Error('service token rejected');
  }
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new Error(d && typeof d === 'object' ? JSON.stringify(d) : (d || ('HTTP ' + r.status)));
  }
  return r.json();
}

// ------------------------------------------------------------------ ding
// Synthesised — no audio file to host. The flash is the primary signal;
// kitchens are loud and tablets get muted, so this is best-effort backup.
let audioCtx = null;
function ding() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.35, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);
    o.start(); o.stop(audioCtx.currentTime + 0.5);
  } catch (_) { /* no audio permission — the flash still shows */ }
}

const age = (iso) => {
  const m = Math.floor((Date.now() - new Date(iso)) / 60000);
  return m < 1 ? 'just now' : m + ' min';
};

// ----------------------------------------------------------------- queue
async function refresh() {
  let d;
  try { d = await call('/api/admin/qr/live'); } catch (_) { return; }
  const q = $('queue');
  $('countPill').textContent = d.count + (d.count === 1 ? ' open order' : ' open orders');
  $('countPill').className = 'pill' + (d.count ? ' hot' : '');

  let anyNew = false;
  q.innerHTML = d.count === 0
    ? '<div class="card stow"><div class="empty">No open orders. They\'ll appear here the moment a guest pays.</div></div>'
    : d.orders.map((o) => {
        const fresh = !seen.has(o.id); if (fresh) anyNew = true;
        return `<div class="card ${o.status === 'failed' ? 'mari' : 'stow'} order ${fresh ? 'fresh' : ''}"
                     data-id="${esc(o.id)}">
          <div class="top">
            <span class="tbl">T${esc(o.table_no)}</span>
            <span class="meta">${age(o.created_at)} · $${(o.total_cents / 100).toFixed(2)} paid</span>
            <span class="flag ${o.status}">${o.status === 'injected' ? 'in till'
              : o.status === 'paid' ? 'new' : 'till failed'}</span>
          </div>
          <div class="lines">${o.lines.map((l) => `
            <div class="ln"><span class="q">${l.qty}×</span>
              <span>${esc(l.name)}
                ${l.modifiers.length ? `<div class="mods">${l.modifiers.map((m) => esc(m.name)).join(', ')}</div>` : ''}
                ${l.note ? `<div class="note">“${esc(l.note)}”</div>` : ''}
              </span></div>`).join('')}</div>
          ${o.status === 'failed' ? `<div class="err">Till rejected it: ${esc(o.last_error || '')}
             — ring it in by hand or retry.</div>` : ''}
          <div class="acts">
            ${o.status === 'failed' ? '<button class="retry" data-act="retry">Retry till</button>' : ''}
            <button class="done" data-act="done">Done — sent out</button>
          </div>
        </div>`;
      }).join('');
  d.orders.forEach((o) => seen.add(o.id));
  if (anyNew && seen.size > d.count) ding();   // don't ding on the first load

  q.querySelectorAll('button').forEach((b) => { b.onclick = async () => {
    const id = b.closest('.order').dataset.id;
    b.disabled = true;
    try {
      if (b.dataset.act === 'done') await call(`/api/admin/qr/orders/${id}/complete`, { method: 'POST' });
      else await call(`/api/admin/qr/orders/${id}/reinject`, { method: 'POST' });
    } catch (_) { /* surfaced on next refresh */ }
    refresh();
  }; });
  render86(d.eighty_sixed);
}

// -------------------------------------------------------------- 86 board
let OFF = [];
function render86(offList) {
  OFF = offList || OFF;
  if (!MENU) return;
  const offIds = new Set(OFF.map((x) => x.item_id));
  const filter = ($('q86').value || '').toLowerCase();
  const rows = MENU.items
    .filter((i) => !filter || i.name.toLowerCase().includes(filter))
    .sort((a, b) => (offIds.has(b.id) - offIds.has(a.id)) || a.name.localeCompare(b.name))
    .slice(0, filter ? 60 : 40);
  $('avlist').innerHTML = rows.map((i) => {
    const off = offIds.has(i.id);
    return `<div class="avrow"><span>${esc(i.name)}
        <div class="rg">${esc(i.section)}</div></span>
      <button class="${off ? 'off' : ''}" data-id="${esc(i.id)}" data-to="${off ? 1 : 0}">
        ${off ? "86'd — bring back" : '86 it'}</button></div>`;
  }).join('') || '<div class="empty">Nothing matches.</div>';
  document.querySelectorAll('#avlist button').forEach((b) => { b.onclick = async () => {
    b.disabled = true;
    try {
      await call(`/api/admin/qr/86/${encodeURIComponent(b.dataset.id)}?available=${b.dataset.to === '1'}`,
                 { method: 'POST' });
    } catch (_) { /* surfaced on next refresh */ }
    refresh();
  }; });
}

// ------------------------------------------------------------------ tabs
function setTab(queue) {
  $('queue').style.display = queue ? '' : 'none';
  $('board86').style.display = queue ? 'none' : '';
  $('tabQ').classList.toggle('sel', queue);
  $('tab86').classList.toggle('sel', !queue);
}

// ------------------------------------------------------------------ boot
async function init() {
  await ensureToken();
  if (!SVC) { $('tokenbox').style.display = ''; return; }
  try {
    const h = await call('/api/admin/qr/health');
    $('writerPill').textContent = h.writer === 'dry-run'
      ? 'no till link yet — this screen is the docket' : 'till: ' + h.writer;
    $('writerPill').className = 'pill' + (h.writer === 'dry-run' ? ' hot' : '');
  } catch (_) { return; }
  // The menu (for the 86 board). The admin Bearer passes the service's
  // preview gate, so this needs no preview key.
  try { MENU = await call('/api/qr/menu'); } catch (_) { /* board stays empty */ }
  refresh();
  clearInterval(timer);
  timer = setInterval(refresh, 5000);
}

Auth.gate($('gate'), {
  roles: null,   // open to all signed-in staff — the pass, the bar, the office
  onOk: (user) => {
    $('app').style.display = '';
    $('whotop').innerHTML = `<strong>${esc(user.name)}</strong>`;
    $('signout').onclick = async (e) => {
      e.preventDefault(); await Auth.logout(); location.href = '/';
    };
    $('tabQ').onclick = () => setTab(true);
    $('tab86').onclick = () => setTab(false);
    $('q86').oninput = () => render86();
    $('tokgo').onclick = () => {
      const t = $('tok').value.trim();
      if (!t) return;
      localStorage.setItem(TOKEN_KEY, t);
      SVC = t; $('tokenbox').style.display = 'none'; $('tok').value = '';
      init();
    };
    init();
  },
});
