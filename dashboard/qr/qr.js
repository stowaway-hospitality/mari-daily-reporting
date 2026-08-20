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

// ------------------------------------------------------------- menu editor
// The operator-portal layer: what the GUEST sees for each item. Edits are
// stored server-side as an overlay (they survive menu rebuilds) and the
// service enforces them at pricing time, so nothing here is display-only.
let ED = null;   // { menu, overrides } from /api/admin/qr/menu/full
let edOpen = '';

function itemGroupIds(itemId) {
  // which operator groups an item has attached, derived from mod_groups list
  const out = [];
  for (const g of (ED && ED.mod_groups) || []) {
    // menu/full doesn't ship per-item attachment; read it off the EFFECTIVE
    // modifier groups on the base item when they carry group_id
  }
  const it = ED && ED.itemMods && ED.itemMods[itemId];
  return it || [];
}

async function loadEditor() {
  try { ED = await call('/api/admin/qr/menu/full'); } catch (_) { return; }
  // effective doc carries group_id per attached group — build item->groups
  try {
    const eff = await call('/api/qr/menu');
    ED.itemMods = {};
    for (const it of eff.items || []) {
      const ids = (it.modifier_groups || []).map((g) => g.group_id).filter(Boolean);
      if (ids.length) ED.itemMods[it.id] = ids;
    }
  } catch (_) { ED.itemMods = {}; }
  renderSections();
  renderEditor();
}

function renderSections() {
  const el = $('seclist');
  if (!el || !ED) return;
  el.innerHTML = (ED.sections || []).map((sx) => `
    <div class="secrow" data-id="${esc(sx.id)}">
      <input type="text" data-name value="${esc(sx.name)}">
      <span style="font-size:12px;color:var(--ink-soft)">${sx.item_ids.length} items</span>
      <button class="mini" data-act="up">↑</button>
      <button class="mini" data-act="down">↓</button>
      <button class="mini save" data-act="ren">Save</button>
      <button class="mini danger" data-act="del">Delete</button>
    </div>`).join('') ||
    '<div class="hint">No custom sections yet — the menu shows the till\'s own.</div>';
  el.querySelectorAll('.secrow').forEach((row, idx) => {
    const sid = row.dataset.id;
    const act = async (fn, btn) => {
      btn.disabled = true;
      try { await fn(); await loadEditor(); } catch (e) { btn.disabled = false; }
    };
    row.querySelector('[data-act="ren"]').onclick = (e) => act(() =>
      call(`/api/admin/qr/sections/${sid}`, { method: 'PUT',
        body: JSON.stringify({ name: row.querySelector('[data-name]').value }) }), e.target);
    row.querySelector('[data-act="del"]').onclick = (e) => act(() =>
      call(`/api/admin/qr/sections/${sid}`, { method: 'DELETE' }), e.target);
    row.querySelector('[data-act="up"]').onclick = (e) => act(() =>
      call(`/api/admin/qr/sections/${sid}`, { method: 'PUT',
        body: JSON.stringify({ position: Math.max(0, idx - 1) }) }), e.target);
    row.querySelector('[data-act="down"]').onclick = (e) => act(() =>
      call(`/api/admin/qr/sections/${sid}`, { method: 'PUT',
        body: JSON.stringify({ position: idx + 2 }) }), e.target);
  });
}

function renderEditor() {
  if (!ED) return;
  const filter = ($('qM').value || '').toLowerCase();
  const ovs = ED.overrides || {};
  const rows = ED.menu.items
    .filter((i) => !filter || i.name.toLowerCase().includes(filter)
                 || (ovs[i.id] && ovs[i.id].name || '').toLowerCase().includes(filter))
    .sort((a, b) => ((ovs[b.id] ? 1 : 0) - (ovs[a.id] ? 1 : 0)) || a.name.localeCompare(b.name))
    .slice(0, filter ? 80 : 40);

  $('edlist').innerHTML = rows.map((i) => {
    const ov = ovs[i.id] || {};
    const shownName = ov.name || i.name;
    const shownPrice = ov.price_inc_gst || i.price_inc_gst;
    const open = edOpen === i.id;
    return `<div class="edrow" data-id="${esc(i.id)}">
      <div class="edhead" data-act="toggle">
        <span>${esc(shownName)}<div class="rg">${esc(i.section)}</div></span>
        ${ov.hidden ? '<span class="badge hiddenb">hidden</span>'
          : (ovs[i.id] ? '<span class="badge edited">edited</span>' : '')}
        <span class="pr">$${esc(shownPrice)}</span>
      </div>
      <div class="edform ${open ? 'on' : ''}">
        <label>Guest-facing name <span style="text-transform:none">(blank = till's own: ${esc(i.name)})</span></label>
        <input data-f="name" value="${esc(ov.name || '')}" placeholder="${esc(i.name)}">
        <label>Description</label>
        <textarea data-f="description" placeholder="${esc(i.description || 'none yet')}">${esc(ov.description || '')}</textarea>
        <label>Image URL</label>
        <input data-f="image_url" value="${esc(ov.image_url || '')}" placeholder="${esc(i.image_url || 'none yet')}">
        <div class="row2"><div>
          <label>Guest price (blank = $${esc(i.price_inc_gst)})</label>
          <input data-f="price_inc_gst" value="${esc(ov.price_inc_gst || '')}" placeholder="${esc(i.price_inc_gst)}" inputmode="decimal">
        </div><div>
          <label>Section</label>
          <select data-sec style="width:100%;padding:9px;font:inherit;font-size:14px;
            border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink)">
            <option value="">${esc(i.section)} (from the till)</option>
            ${(ED.sections || []).map((sx) => `<option value="${esc(sx.id)}"
              ${sx.item_ids.includes(i.id) ? 'selected' : ''}>${esc(sx.name)}</option>`).join('')}
          </select>
        </div></div>
        <label style="display:flex;gap:8px;align-items:center;text-transform:none;
          font-size:13.5px;margin-top:12px"><input type="checkbox" data-feat
          ${(ED.settings && (ED.settings.featured || []).includes(i.id)) ? 'checked' : ''}>
          Featured — show in the guest "start with these" row</label>
        ${(ED.mod_groups || []).length ? `<label>Modifier groups (yours replace the imported ones)</label>
        <div>${ED.mod_groups.map((g) => `<label style="display:inline-flex;gap:6px;
          align-items:center;text-transform:none;font-size:13.5px;margin:2px 12px 2px 0">
          <input type="checkbox" data-grp value="${esc(g.id)}"
          ${(itemGroupIds(i.id) || []).includes(g.id) ? 'checked' : ''}>${esc(g.name)}</label>`).join('')}
        </div>` : ''}
        <div class="btns">
          <button class="save" data-act="save">Save</button>
          <button class="hide" data-act="hide">${ov.hidden ? 'Put back on the menu' : 'Hide from the menu'}</button>
          <button data-act="clear">Clear all edits</button>
        </div>
        <div class="msg"></div>
      </div>
    </div>`;
  }).join('') || '<div class="empty">Nothing matches.</div>';

  document.querySelectorAll('#edlist .edrow').forEach((row) => {
    const id = row.dataset.id;
    row.querySelector('[data-act="toggle"]').onclick = () => {
      edOpen = edOpen === id ? '' : id; renderEditor();
    };
    const put = async (payload, btn) => {
      btn.disabled = true;
      try {
        await call(`/api/admin/qr/items/${encodeURIComponent(id)}`,
                   { method: 'PUT', body: JSON.stringify(payload) });
        await loadEditor();
      } catch (e) {
        btn.disabled = false;
        row.querySelector('.msg').textContent = e.message;
      }
    };
    const saveBtn = row.querySelector('[data-act="save"]');
    if (saveBtn) saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try {
        const payload = {};
        row.querySelectorAll('[data-f]').forEach((el) => {
          payload[el.dataset.f] = el.value.trim() || null;
        });
        await call(`/api/admin/qr/items/${encodeURIComponent(id)}`,
                   { method: 'PUT', body: JSON.stringify(payload) });
        const sec = row.querySelector('[data-sec]');
        if (sec) await call(`/api/admin/qr/items/${encodeURIComponent(id)}/section`,
          { method: 'PUT', body: JSON.stringify({ section_id: sec.value || null }) });
        const grps = [...row.querySelectorAll('[data-grp]:checked')].map((x) => x.value);
        await call(`/api/admin/qr/items/${encodeURIComponent(id)}/modifiers`,
          { method: 'PUT', body: JSON.stringify({ group_ids: grps }) });
        const feat = row.querySelector('[data-feat]');
        if (feat) {
          const cur = new Set((ED.settings && ED.settings.featured) || []);
          feat.checked ? cur.add(id) : cur.delete(id);
          await call('/api/admin/qr/settings',
            { method: 'PUT', body: JSON.stringify({ featured: [...cur] }) });
        }
        await loadEditor();
      } catch (e) {
        saveBtn.disabled = false;
        row.querySelector('.msg').textContent = e.message;
      }
    };
    const hideBtn = row.querySelector('[data-act="hide"]');
    if (hideBtn) hideBtn.onclick = () => {
      const ov = (ED.overrides || {})[id] || {};
      put({ hidden: !ov.hidden }, hideBtn);
    };
    const clearBtn = row.querySelector('[data-act="clear"]');
    if (clearBtn) clearBtn.onclick = () => put(
      { hidden: false, name: null, description: null, image_url: null, price_inc_gst: null },
      clearBtn);
  });
}

// -------------------------------------------------------- modifier groups
async function loadGroups() {
  let gs;
  try { gs = await call('/api/admin/qr/modgroups'); } catch (_) { return; }
  const el = $('glist');
  el.innerHTML = gs.map((g) => `
    <div class="grow" data-id="${esc(g.id)}">
      <strong>${esc(g.name)}</strong>
      <span style="font-size:12px;color:var(--ink-soft)">
        ${g.min_select ? `min ${g.min_select}` : 'optional'}${g.max_select ? ` · max ${g.max_select}` : ''}
        · ${g.options.length} options · on ${g.attached_items} item${g.attached_items === 1 ? '' : 's'}</span>
      <button class="mini" data-act="edit" style="margin-left:auto">Edit</button>
      <button class="mini danger" data-act="del">Delete</button>
      <div class="gopts">${g.options.map((o) => esc(o.name) +
        (o.surcharge_inc_gst !== '0' ? ` +$${esc(o.surcharge_inc_gst)}` : '')).join(' · ')}</div>
      <div class="gform" style="display:none"></div>
    </div>`).join('') || '<div class="hint">No groups yet.</div>';

  const form = (g) => `
    <label style="font-size:11.5px;text-transform:uppercase;color:var(--ink-soft)">Name</label>
    <input type="text" data-gname value="${esc(g ? g.name : '')}" style="width:100%;
      box-sizing:border-box;padding:8px;font:inherit;font-size:14px;
      border:1px solid var(--line);border-radius:8px;margin:3px 0 8px">
    <div class="row2"><div>
      <label style="font-size:11.5px;text-transform:uppercase;color:var(--ink-soft)">Min picks</label>
      <input type="text" data-gmin inputmode="numeric" value="${g ? g.min_select : 0}" style="width:100%;
        box-sizing:border-box;padding:8px;font:inherit;border:1px solid var(--line);border-radius:8px">
    </div><div>
      <label style="font-size:11.5px;text-transform:uppercase;color:var(--ink-soft)">Max picks (blank = no limit)</label>
      <input type="text" data-gmax inputmode="numeric" value="${g && g.max_select != null ? g.max_select : ''}" style="width:100%;
        box-sizing:border-box;padding:8px;font:inherit;border:1px solid var(--line);border-radius:8px">
    </div></div>
    <label style="font-size:11.5px;text-transform:uppercase;color:var(--ink-soft);
      display:block;margin-top:8px">Options — one per line, "Name = surcharge" (blank surcharge = free)</label>
    <textarea data-gopts>${g ? g.options.map((o) =>
      o.surcharge_inc_gst !== '0' ? `${o.name} = ${o.surcharge_inc_gst}` : o.name).join('\n') : ''}</textarea>
    <div style="margin-top:8px"><button class="mini save" data-act="gsave">Save group</button>
    <span class="msg" style="color:var(--red);font-size:13px"></span></div>`;

  const parseOpts = (txt) => txt.split('\n').map((l) => l.trim()).filter(Boolean)
    .map((l) => { const m = l.split('=');
      return { name: m[0].trim(), surcharge_inc_gst: (m[1] || '0').trim() || '0' }; });

  const wireSave = (host, gid) => {
    host.querySelector('[data-act="gsave"]').onclick = async (e) => {
      e.target.disabled = true;
      const payload = {
        name: host.querySelector('[data-gname]').value.trim(),
        min_select: parseInt(host.querySelector('[data-gmin]').value || '0', 10) || 0,
        max_select: host.querySelector('[data-gmax]').value.trim() || null,
        options: parseOpts(host.querySelector('[data-gopts]').value),
      };
      try {
        await call(gid ? `/api/admin/qr/modgroups/${gid}` : '/api/admin/qr/modgroups',
                   { method: gid ? 'PUT' : 'POST', body: JSON.stringify(payload) });
        loadGroups();
      } catch (err) {
        e.target.disabled = false;
        host.querySelector('.msg').textContent = err.message;
      }
    };
  };

  el.querySelectorAll('.grow').forEach((row) => {
    const gid = row.dataset.id;
    const g = gs.find((x) => x.id === gid);
    row.querySelector('[data-act="edit"]').onclick = () => {
      const f = row.querySelector('.gform');
      const open = f.style.display !== 'none';
      f.style.display = open ? 'none' : 'block';
      if (!open) { f.innerHTML = form(g); wireSave(f, gid); }
    };
    row.querySelector('[data-act="del"]').onclick = async (e) => {
      e.target.disabled = true;
      try { await call(`/api/admin/qr/modgroups/${gid}`, { method: 'DELETE' }); } catch (_) {}
      loadGroups();
    };
  });
  $('addgrp').onclick = () => {
    let host = document.getElementById('newgform');
    if (host) { host.remove(); return; }
    host = document.createElement('div');
    host.id = 'newgform'; host.className = 'gform'; host.style.display = 'block';
    host.innerHTML = form(null);
    $('addgrp').before(host);
    wireSave(host, null);
  };
}

// --------------------------------------------------------------- settings
async function loadSettings() {
  let st;
  try { st = await call('/api/admin/qr/settings'); } catch (_) { return; }
  const fee = st.service_fee || { enabled: false, rate: '0.02',
    label: 'Table service fee', note: 'Skip it by ordering at the bar.' };
  const tip = st.tipping || { enabled: true, presets: ['0', '0.05', '0.1'], allow_custom: true };
  $('setform').innerHTML = `
    <div class="switch"><input type="checkbox" id="sAccept" ${st.accepting_orders ? 'checked' : ''}>
      Accepting orders <span class="hint" style="margin:0">— the pause button; guests can browse but not pay</span></div>
    <div class="setrow"><label>Paused message (what guests read while paused)</label>
      <input type="text" id="sPaused" value="${esc(st.paused_message)}"></div>
    <div class="setrow"><label>Tables that can order (comma-separated; blank = every table)</label>
      <input type="text" id="sTables" value="${esc((st.tables || []).join(', '))}"></div>
    <div class="setrow"><div class="switch" style="font-size:14px">
      <input type="checkbox" id="sFeeOn" ${fee.enabled ? 'checked' : ''}> Table service fee</div>
      <div class="row2" style="margin-top:8px"><div>
        <label>Rate (0.02 = 2%)</label><input type="text" id="sFeeRate" value="${esc(fee.rate)}">
      </div><div>
        <label>Label (never mention cards)</label><input type="text" id="sFeeLabel" value="${esc(fee.label)}">
      </div></div>
      <label style="margin-top:8px">Note under the fee</label>
      <input type="text" id="sFeeNote" value="${esc(fee.note)}"></div>
    <div class="setrow"><div class="switch" style="font-size:14px">
      <input type="checkbox" id="sTipOn" ${tip.enabled ? 'checked' : ''}> Tipping</div>
      <label style="margin-top:8px">Presets (rates, comma-separated — 0.05 = 5%)</label>
      <input type="text" id="sTipPre" value="${esc((tip.presets || []).join(', '))}"></div>
    <button class="mini save" id="sSave" style="font-size:14.5px;padding:10px 20px">Save settings</button>
    <span class="msg" id="sMsg" style="color:var(--red);font-size:13px;margin-left:10px"></span>`;
  $('sSave').onclick = async () => {
    $('sSave').disabled = true; $('sMsg').textContent = '';
    try {
      await call('/api/admin/qr/settings', { method: 'PUT', body: JSON.stringify({
        accepting_orders: $('sAccept').checked,
        paused_message: $('sPaused').value,
        tables: $('sTables').value.split(',').map((t) => t.trim()).filter(Boolean),
        service_fee: { enabled: $('sFeeOn').checked, rate: $('sFeeRate').value.trim(),
          label: $('sFeeLabel').value, note: $('sFeeNote').value },
        tipping: { enabled: $('sTipOn').checked,
          presets: $('sTipPre').value.split(',').map((t) => t.trim()).filter(Boolean),
          allow_custom: true },
      }) });
      loadSettings();
    } catch (e) { $('sSave').disabled = false; $('sMsg').textContent = e.message; }
  };
}

// ----------------------------------------------------------------- orders
async function loadOrders() {
  let d;
  try { d = await call('/api/admin/qr/history?days=14'); } catch (_) { return; }
  $('olist').innerHTML = d.orders.length ? `<table class="simple">
    <tr><th>When</th><th>Table</th><th>Items</th><th class="r">Total</th><th>Status</th></tr>
    ${d.orders.map((o) => `<tr>
      <td>${esc(o.created_at.slice(0, 16).replace('T', ' '))}</td>
      <td>T${esc(o.table_no)}</td>
      <td>${o.lines.map((l) => `${l.qty}× ${esc(l.name)}`).join(', ')}</td>
      <td class="r">$${(o.total_cents / 100).toFixed(2)}</td>
      <td>${esc(o.status)}</td></tr>`).join('')}</table>`
    : '<div class="empty">No orders in the last fortnight.</div>';
}

// --------------------------------------------------------------- insights
async function loadInsights() {
  let d;
  try { d = await call('/api/admin/qr/insights?days=30'); } catch (_) { return; }
  const money = (c) => '$' + (c / 100).toFixed(2);
  $('insights').innerHTML = `
    <div class="statgrid">
      <div class="card stow stat"><div class="n">${d.totals.orders}</div><div class="l">orders · 30d</div></div>
      <div class="card stow stat"><div class="n">${money(d.totals.revenue_cents)}</div><div class="l">taken · inc GST</div></div>
      <div class="card stow stat"><div class="n">${money(d.totals.tips_cents)}</div><div class="l">tips</div></div>
    </div>
    <div class="card stow"><strong style="font-size:14px">By day</strong>
      ${d.daily.length ? `<table class="simple">
        <tr><th>Date</th><th class="r">Orders</th><th class="r">Taken</th><th class="r">Tips</th></tr>
        ${d.daily.map((x) => `<tr><td>${esc(x.date)}</td><td class="r">${x.orders}</td>
          <td class="r">${money(x.revenue_cents)}</td><td class="r">${money(x.tips_cents)}</td></tr>`).join('')}
      </table>` : '<div class="empty">Nothing yet.</div>'}</div>
    <div class="card stow"><strong style="font-size:14px">Top sellers</strong>
      ${d.top_items.length ? `<table class="simple">
        <tr><th>Item</th><th class="r">Qty</th><th class="r">Revenue</th></tr>
        ${d.top_items.map((x) => `<tr><td>${esc(x.name)}</td><td class="r">${x.qty}</td>
          <td class="r">${money(x.revenue_cents)}</td></tr>`).join('')}
      </table>` : '<div class="empty">Nothing yet.</div>'}</div>`;
}

// ------------------------------------------------------------------ tabs
const TABS = { q: 'queue', '86': 'board86', m: 'boardM', g: 'boardG',
               s: 'boardS', o: 'boardO', i: 'boardI' };
const TABBTN = { q: 'tabQ', '86': 'tab86', m: 'tabM', g: 'tabG',
                 s: 'tabS', o: 'tabO', i: 'tabI' };
function setTab(which) {
  for (const [k, id] of Object.entries(TABS)) $(id).style.display = k === which ? '' : 'none';
  for (const [k, id] of Object.entries(TABBTN)) $(id).classList.toggle('sel', k === which);
  if (which === 'm') loadEditor();
  if (which === 'g') loadGroups();
  if (which === 's') loadSettings();
  if (which === 'o') loadOrders();
  if (which === 'i') loadInsights();
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
    for (const [k, id] of Object.entries(TABBTN)) $(id).onclick = () => setTab(k);
    $('q86').oninput = () => render86();
    $('qM').oninput = () => renderEditor();
    $('addsec').onclick = async () => {
      const name = $('newsec').value.trim();
      if (!name) return;
      try {
        await call('/api/admin/qr/sections', { method: 'POST',
          body: JSON.stringify({ name }) });
        $('newsec').value = '';
        loadEditor();
      } catch (_) {}
    };
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
