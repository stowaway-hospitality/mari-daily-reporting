/* QR Orders — the manage.meandu.app recreation. All logic for /qr/
 * (index.html is a shell, arch_guard style).
 *
 * Layout copies me&u's operator portal: left sidebar (Your Menu / Orders /
 * Venue), the green accepting-orders dot up top (tap = pause/resume, live),
 * and their Menu Items page — category rows with Edit buttons, drill into a
 * category to see its sections and items, exactly the hierarchy the backend
 * stores (menus -> sections -> items, with availability windows).
 *
 * Wiring is the house pattern: Supabase (Auth.gate) opens the page; the
 * service bearer token (Auth.bookingToken(), same app_config row as
 * /bookings/) is the real auth on every call. Nobody pastes a token.
 */
import { Auth } from '/_shared/auth.js';

const API = 'https://stowaway-bookings.onrender.com';
const TOKEN_KEY = 'stowaway_booking_token';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

let SVC = '';
let FULL = null;          // /api/admin/qr/menu/full payload
let ITEMMODS = {};        // item id -> [group ids] (from the effective doc)
let seen = new Set();
let pollTimer = null;
let VIEW = { page: 'menu', menuId: null, editItem: null, editMenu: null };

// ---------------------------------------------------------------- plumbing
async function ensureToken() {
  if (SVC) return SVC;
  try {
    const t = await Auth.bookingToken();
    if (t) { SVC = t; return SVC; }
  } catch (_) { /* fall through */ }
  SVC = localStorage.getItem(TOKEN_KEY) || '';
  return SVC;
}
const hdrs = () => ({ 'Authorization': 'Bearer ' + SVC, 'Content-Type': 'application/json' });
async function call(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
  if (r.status === 401) {
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

let audioCtx = null;
function ding() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.35, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);
    o.start(); o.stop(audioCtx.currentTime + 0.5);
  } catch (_) { /* flash is the primary signal */ }
}
const age = (iso) => {
  const m = Math.floor((Date.now() - new Date(iso)) / 60000);
  return m < 1 ? 'just now' : m + ' min';
};

// -------------------------------------------------------- the green dot
async function refreshDot() {
  try {
    const st = await call('/api/admin/qr/settings');
    const on = !!st.accepting_orders;
    $('acceptDot').classList.toggle('off', !on);
    $('acceptLbl').textContent = on ? 'Accepting orders' : 'Orders paused';
  } catch (_) { /* leave as-is */ }
}
async function toggleDot() {
  try {
    const st = await call('/api/admin/qr/settings');
    const to = !st.accepting_orders;
    if (!to && !confirm('Pause phone ordering? Guests can browse but not pay.')) return;
    await call('/api/admin/qr/settings', { method: 'PUT',
      body: JSON.stringify({ accepting_orders: to }) });
    refreshDot();
  } catch (_) {}
}

// ------------------------------------------------------------- data loads
async function loadFull() {
  FULL = await call('/api/admin/qr/menu/full');
  try {
    const eff = await call('/api/qr/menu');
    FULL.effective = eff;
    ITEMMODS = {};
    for (const it of eff.items || []) {
      const ids = (it.modifier_groups || []).map((g) => g.group_id).filter(Boolean);
      if (ids.length) ITEMMODS[it.id] = ids;
    }
    FULL.menus = await call('/api/admin/qr/menus');
  } catch (_) { FULL.effective = { menus: [] }; FULL.menus = []; }
}
const baseItem = (id) => (FULL.menu.items || []).find((i) => i.id === id);
const ovOf = (id) => (FULL.overrides || {})[id] || {};

// ================================================================ MENU ITEMS
// me&u's Menu Items page: category rows -> category detail -> item editor.
async function renderMenu() {
  const host = $('vMenu');
  host.innerHTML = '<div class="empty">Loading the menu…</div>';
  try { await loadFull(); } catch (e) {
    host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return;
  }
  if (VIEW.menuId) return renderCategory(host);

  const eff = FULL.effective;
  const cats = eff.menus || [];
  host.innerHTML = `
    <h1 class="mu">Menu</h1>
    <div class="mu-sub">Your menu, structured the me&u way: menus, their sections,
      their items. The till's products underneath never change — everything here
      is your layer over them.</div>
    ${cats.length === 0 || (cats.length === 1 && !cats[0].id) ? `
      <div class="mu-card pad" style="margin-bottom:14px">
        <strong>Bring your me&u structure across</strong>
        <p class="mu-sub" style="margin:6px 0 10px">One click rebuilds your real menu —
          KITCHEN MENU, PIZZA MENU, COCKTAILS and the rest, with their sections,
          exactly as your me&u portal has them.</p>
        <button class="btn green" id="seedbtn">Import structure from me&u</button>
        <span class="msg" id="seedmsg"></span>
      </div>` : ''}
    <div class="mu-card" id="catlist">
      ${cats.map((m) => `
        <div class="mrow" data-mid="${esc(m.id || '')}">
          <div class="thumb">${esc((m.name || '?').slice(0, 2))}</div>
          <div>
            <div class="ttl">${esc(m.name)} <span class="tag">Dine in</span></div>
            <div class="meta">${m.sections.length} section${m.sections.length === 1 ? '' : 's'} ·
              ${m.sections.reduce((n, s) => n + s.item_ids.length, 0)} items</div>
          </div>
          <div class="right">
            ${!m.open_now ? `<span class="chip sched">⏱ ${esc(m.window_note)}</span>` : ''}
            ${m.id ? `<button class="btn green sm" data-act="open">Edit</button>` : ''}
          </div>
        </div>`).join('') || '<div class="empty">No menu yet — import your structure above.</div>'}
    </div>
    <div class="btns" style="margin-top:12px">
      <button class="btn" id="addcat">+ New menu</button>
      ${cats.some((c) => c.id) ? '<button class="btn" id="reseed">Re-import from me&u</button>' : ''}
    </div>`;

  host.querySelectorAll('[data-act="open"]').forEach((b) => { b.onclick = () => {
    VIEW.menuId = b.closest('.mrow').dataset.mid; VIEW.editItem = null; renderMenu();
  }; });
  const seed = async (btn, msgId) => {
    btn.disabled = true;
    try { await call('/api/admin/qr/structure/seed', { method: 'POST' }); renderMenu(); }
    catch (e) { btn.disabled = false; if (msgId) $(msgId).textContent = e.message; }
  };
  const sb = $('seedbtn'); if (sb) sb.onclick = () => seed(sb, 'seedmsg');
  const rb = $('reseed'); if (rb) rb.onclick = () => {
    if (confirm('Re-import the me&u structure? Your custom menus/sections are rebuilt; item edits, modifiers and settings are untouched.')) seed(rb);
  };
  const ac = $('addcat'); if (ac) ac.onclick = async () => {
    const name = prompt('Menu name (e.g. LATE NIGHT FOOD MENU)');
    if (!name) return;
    await call('/api/admin/qr/menus', { method: 'POST', body: JSON.stringify({ name }) });
    renderMenu();
  };
}

function windowsEditor(windows) {
  const w = (windows && windows[0]) || null;   // one window covers the venue's real cases
  return `
    <label>Availability (leave days unticked for always available)</label>
    <div class="daysel">${DAYS.map((d, i) => `
      <label><input type="checkbox" data-day="${i}"
        ${w && w.days.includes(i) ? 'checked' : ''}>${d}</label>`).join('')}
    </div>
    <div class="row2" style="margin-top:8px"><div>
      <label>From</label><input type="text" data-wstart value="${esc(w ? w.start : '17:00')}" placeholder="17:00">
    </div><div>
      <label>Until</label><input type="text" data-wend value="${esc(w ? w.end : '23:00')}" placeholder="23:00">
    </div></div>`;
}
function readWindows(host) {
  const days = [...host.querySelectorAll('[data-day]:checked')].map((x) => +x.dataset.day);
  if (!days.length) return [];
  return [{ days, start: host.querySelector('[data-wstart]').value.trim() || '00:00',
            end: host.querySelector('[data-wend]').value.trim() || '23:59' }];
}

function renderCategory(host) {
  const cfg = (FULL.menus || []).find((m) => m.id === VIEW.menuId);
  const eff = (FULL.effective.menus || []).find((m) => m.id === VIEW.menuId);
  if (!cfg) { VIEW.menuId = null; return renderMenu(); }
  const secRows = (FULL.sections || []).filter((s) => s.menu_id === cfg.id);

  host.innerHTML = `
    <div class="crumb"><a id="backcats">‹ Menu</a> / ${esc(cfg.name)}</div>
    <h1 class="mu" style="margin-bottom:4px">${esc(cfg.name)}</h1>
    <div class="mu-sub">${cfg.open_now ? 'Available now'
      : '⏱ ' + esc(cfg.window_note)}</div>

    <div class="mu-card pad" style="margin-bottom:14px">
      <div class="fform" style="border-bottom:none;padding-bottom:0" id="catform">
        <div class="row2"><div>
          <label>Menu name</label><input type="text" data-cname value="${esc(cfg.name)}">
        </div><div>
          <label>Description (guests see this)</label>
          <input type="text" data-cdesc value="${esc(cfg.description || '')}">
        </div></div>
        ${windowsEditor(cfg.windows)}
        <div class="btns">
          <button class="btn green" id="catsave">Save menu</button>
          <button class="btn" id="addsec">+ Add section</button>
          <button class="btn danger" id="catdel">Delete menu</button>
          <span class="msg" id="catmsg"></span>
        </div>
      </div>
    </div>

    <div id="secblocks">
    ${(eff ? eff.sections : []).map((sx) => {
      const srow = secRows.find((r) => r.id === sx.id) || {};
      return `
      <div class="mu-card" style="margin-bottom:14px" data-sid="${esc(sx.id || '')}">
        <div class="sechead">
          <span>${esc(sx.name)}</span>
          ${sx.description ? `<span class="desc">${esc(sx.description)}</span>` : ''}
          <div class="right">
            <button class="btn sm" data-act="sup">↑</button>
            <button class="btn sm" data-act="sdown">↓</button>
            <button class="btn sm" data-act="sedit">Edit</button>
          </div>
        </div>
        <div class="fform" data-secform style="display:none">
          <div class="row2"><div>
            <label>Section name</label><input type="text" data-sname value="${esc(sx.name)}">
          </div><div>
            <label>Description</label><input type="text" data-sdesc value="${esc(sx.description || '')}">
          </div></div>
          <div class="btns">
            <button class="btn green sm" data-act="ssave">Save section</button>
            <button class="btn danger sm" data-act="sdel">Delete section</button>
          </div>
        </div>
        ${sx.item_ids.map((iid) => {
          const eit = (FULL.effective.items || []).find((i) => i.id === iid) || {};
          const ov = ovOf(iid);
          return `<div class="mrow" data-iid="${esc(iid)}">
            ${eit.image_url ? `<img class="thumb" src="${esc(eit.image_url)}" alt="">`
              : `<div class="thumb">${esc((eit.name || '?').slice(0, 2))}</div>`}
            <div>
              <div class="ttl">${esc(eit.name || iid)}</div>
              <div class="meta">${esc(eit.description || '').slice(0, 90) || 'no description yet'}</div>
            </div>
            <div class="right">
              ${ov.hidden ? '<span class="chip hid">hidden</span>'
                : ((FULL.overrides || {})[iid] ? '<span class="chip ok">edited</span>' : '')}
              <span class="chip">$${esc(eit.price_inc_gst || '?')}</span>
              <button class="btn green sm" data-act="iedit">Edit</button>
            </div>
          </div>
          <div data-itemform="${esc(iid)}"></div>`;
        }).join('') || '<div class="empty">No items in this section yet — place them from an item\'s editor.</div>'}
      </div>`;
    }).join('') || '<div class="mu-card"><div class="empty">This menu has no visible sections yet.</div></div>'}
    </div>`;

  $('backcats').onclick = () => { VIEW.menuId = null; renderMenu(); };
  $('catsave').onclick = async () => {
    try {
      await call(`/api/admin/qr/menus/${cfg.id}`, { method: 'PUT', body: JSON.stringify({
        name: host.querySelector('[data-cname]').value.trim(),
        description: host.querySelector('[data-cdesc]').value,
        windows: readWindows($('catform')) }) });
      renderMenu();
    } catch (e) { $('catmsg').textContent = e.message; }
  };
  $('catdel').onclick = async () => {
    if (!confirm(`Delete ${cfg.name}? Its items fall back to their till-derived sections.`)) return;
    await call(`/api/admin/qr/menus/${cfg.id}`, { method: 'DELETE' });
    VIEW.menuId = null; renderMenu();
  };
  $('addsec').onclick = async () => {
    const name = prompt('Section name (e.g. SMALLS)');
    if (!name) return;
    await call('/api/admin/qr/sections', { method: 'POST',
      body: JSON.stringify({ name, menu_id: cfg.id }) });
    renderMenu();
  };

  host.querySelectorAll('[data-sid]').forEach((card, idx, all) => {
    const sid = card.dataset.sid;
    if (!sid) return;
    const q = (sel) => card.querySelector(sel);
    q('[data-act="sedit"]').onclick = () => {
      const f = q('[data-secform]');
      f.style.display = f.style.display === 'none' ? '' : 'none';
    };
    q('[data-act="ssave"]').onclick = async () => {
      await call(`/api/admin/qr/sections/${sid}`, { method: 'PUT', body: JSON.stringify({
        name: q('[data-sname]').value.trim(), description: q('[data-sdesc]').value }) });
      renderMenu();
    };
    q('[data-act="sdel"]').onclick = async () => {
      if (!confirm('Delete this section? Its items fall back to their till-derived sections.')) return;
      await call(`/api/admin/qr/sections/${sid}`, { method: 'DELETE' });
      renderMenu();
    };
    q('[data-act="sup"]').onclick = async () => {
      await call(`/api/admin/qr/sections/${sid}`, { method: 'PUT',
        body: JSON.stringify({ position: Math.max(0, idx - 1) }) });
      renderMenu();
    };
    q('[data-act="sdown"]').onclick = async () => {
      await call(`/api/admin/qr/sections/${sid}`, { method: 'PUT',
        body: JSON.stringify({ position: idx + 2 }) });
      renderMenu();
    };
    card.querySelectorAll('[data-act="iedit"]').forEach((b) => { b.onclick = () => {
      const iid = b.closest('.mrow').dataset.iid;
      const slot = host.querySelector(`[data-itemform="${CSS.escape(iid)}"]`);
      if (slot.innerHTML) { slot.innerHTML = ''; return; }
      slot.innerHTML = itemForm(iid);
      wireItemForm(slot, iid);
    }; });
  });
}

// ------------------------------------------------------------ item editor
function itemForm(iid) {
  const i = baseItem(iid) || {};
  const ov = ovOf(iid);
  const secOptions = (FULL.menus || []).map((m) => {
    const secs = (FULL.sections || []).filter((s) => s.menu_id === m.id);
    return `<optgroup label="${esc(m.name)}">${secs.map((sx) => `
      <option value="${esc(sx.id)}" ${sx.item_ids.includes(iid) ? 'selected' : ''}>
        ${esc(sx.name)}</option>`).join('')}</optgroup>`;
  }).join('');
  return `<div class="fform">
    <div class="row2"><div>
      <label>Guest-facing name (blank = till's: ${esc(i.name || '')})</label>
      <input type="text" data-f="name" value="${esc(ov.name || '')}" placeholder="${esc(i.name || '')}">
    </div><div>
      <label>Guest price (blank = $${esc(i.price_inc_gst || '')})</label>
      <input type="text" data-f="price_inc_gst" value="${esc(ov.price_inc_gst || '')}"
        placeholder="${esc(i.price_inc_gst || '')}" inputmode="decimal">
    </div></div>
    <label>Description</label>
    <textarea data-f="description" placeholder="${esc(i.description || 'none yet')}">${esc(ov.description || '')}</textarea>
    <label>Image URL</label>
    <input type="text" data-f="image_url" value="${esc(ov.image_url || '')}" placeholder="${esc(i.image_url || 'none yet')}">
    <div class="row2"><div>
      <label>Section</label>
      <select data-sec><option value="">${esc(i.section || '')} (from the till)</option>${secOptions}</select>
    </div><div>
      <label>Featured row</label>
      <label style="display:flex;gap:8px;align-items:center;text-transform:none;font-size:13.5px">
        <input type="checkbox" data-feat
          ${((FULL.settings || {}).featured || []).includes(iid) ? 'checked' : ''}>
        Show in the guest "start with these" strip</label>
    </div></div>
    ${(FULL.mod_groups || []).length ? `<label>Modifier groups (yours replace the imported ones)</label>
      <div>${FULL.mod_groups.map((g) => `<label style="display:inline-flex;gap:6px;align-items:center;
        text-transform:none;font-size:13.5px;margin:2px 12px 2px 0">
        <input type="checkbox" data-grp value="${esc(g.id)}"
        ${(ITEMMODS[iid] || []).includes(g.id) ? 'checked' : ''}>${esc(g.name)}</label>`).join('')}</div>` : ''}
    <div class="btns">
      <button class="btn green" data-act="isave">Save</button>
      <button class="btn danger" data-act="ihide">${ov.hidden ? 'Put back on the menu' : 'Hide from the menu'}</button>
      <button class="btn" data-act="iclear">Clear all edits</button>
      <span class="msg"></span>
    </div>
  </div>`;
}

function wireItemForm(slot, iid) {
  const put = async (fn, btn) => {
    btn.disabled = true;
    try { await fn(); renderMenu(); }
    catch (e) { btn.disabled = false; slot.querySelector('.msg').textContent = e.message; }
  };
  slot.querySelector('[data-act="isave"]').onclick = (e) => put(async () => {
    const payload = {};
    slot.querySelectorAll('[data-f]').forEach((el) => { payload[el.dataset.f] = el.value.trim() || null; });
    await call(`/api/admin/qr/items/${encodeURIComponent(iid)}`,
               { method: 'PUT', body: JSON.stringify(payload) });
    const sec = slot.querySelector('[data-sec]');
    await call(`/api/admin/qr/items/${encodeURIComponent(iid)}/section`,
      { method: 'PUT', body: JSON.stringify({ section_id: sec.value || null }) });
    const grps = [...slot.querySelectorAll('[data-grp]:checked')].map((x) => x.value);
    await call(`/api/admin/qr/items/${encodeURIComponent(iid)}/modifiers`,
      { method: 'PUT', body: JSON.stringify({ group_ids: grps }) });
    const cur = new Set((FULL.settings || {}).featured || []);
    slot.querySelector('[data-feat]').checked ? cur.add(iid) : cur.delete(iid);
    await call('/api/admin/qr/settings',
      { method: 'PUT', body: JSON.stringify({ featured: [...cur] }) });
  }, e.target);
  slot.querySelector('[data-act="ihide"]').onclick = (e) => put(() =>
    call(`/api/admin/qr/items/${encodeURIComponent(iid)}`,
      { method: 'PUT', body: JSON.stringify({ hidden: !ovOf(iid).hidden }) }), e.target);
  slot.querySelector('[data-act="iclear"]').onclick = (e) => put(() =>
    call(`/api/admin/qr/items/${encodeURIComponent(iid)}`,
      { method: 'PUT', body: JSON.stringify({ hidden: false, name: null,
        description: null, image_url: null, price_inc_gst: null }) }), e.target);
}

// ================================================================ MODIFIERS
async function renderMods() {
  const host = $('vMods');
  let gs;
  try { gs = await call('/api/admin/qr/modgroups'); } catch (e) {
    host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return;
  }
  const form = (g) => `<div class="fform">
    <div class="row2"><div>
      <label>Group name</label><input type="text" data-gname value="${esc(g ? g.name : '')}">
    </div><div>
      <div class="row2"><div>
        <label>Min picks</label><input type="text" data-gmin inputmode="numeric" value="${g ? g.min_select : 0}">
      </div><div>
        <label>Max (blank = no limit)</label>
        <input type="text" data-gmax inputmode="numeric" value="${g && g.max_select != null ? g.max_select : ''}">
      </div></div>
    </div></div>
    <label>Options — one per line, "Name = surcharge" (blank surcharge = free)</label>
    <textarea data-gopts style="height:110px;font-family:ui-monospace,monospace">${g ? g.options.map((o) =>
      o.surcharge_inc_gst !== '0' ? `${o.name} = ${o.surcharge_inc_gst}` : o.name).join('\n') : ''}</textarea>
    <div class="btns"><button class="btn green" data-act="gsave">Save group</button>
    <span class="msg"></span></div>
  </div>`;
  const parseOpts = (txt) => txt.split('\n').map((l) => l.trim()).filter(Boolean)
    .map((l) => { const m = l.split('=');
      return { name: m[0].trim(), surcharge_inc_gst: (m[1] || '0').trim() || '0' }; });

  host.innerHTML = `
    <h1 class="mu">Modifiers</h1>
    <div class="mu-sub">Groups you create here replace the imported ones on any item
      you attach them to (from the item's editor). Min/max is enforced at payment,
      not just on screen.</div>
    <div class="mu-card" id="glist">
      ${gs.map((g) => `
        <div class="mrow" data-gid="${esc(g.id)}" style="flex-wrap:wrap">
          <div class="thumb">${esc(g.name.slice(0, 2))}</div>
          <div>
            <div class="ttl">${esc(g.name)}</div>
            <div class="meta">${g.min_select ? `min ${g.min_select}` : 'optional'}${g.max_select ? ` · max ${g.max_select}` : ''}
              · ${g.options.length} options · on ${g.attached_items} item${g.attached_items === 1 ? '' : 's'}</div>
            <div class="meta">${g.options.map((o) => esc(o.name) +
              (o.surcharge_inc_gst !== '0' ? ` +$${esc(o.surcharge_inc_gst)}` : '')).join(' · ')}</div>
          </div>
          <div class="right">
            <button class="btn green sm" data-act="gedit">Edit</button>
            <button class="btn danger sm" data-act="gdel">Delete</button>
          </div>
          <div data-gform style="display:none;width:100%"></div>
        </div>`).join('') || '<div class="empty">No groups yet.</div>'}
    </div>
    <div class="btns" style="margin-top:12px"><button class="btn green" id="addgrp">+ New modifier group</button></div>
    <div class="mu-card pad" id="newgwrap" style="display:none;margin-top:12px"></div>`;

  const wireSave = (hostEl, gid) => {
    hostEl.querySelector('[data-act="gsave"]').onclick = async (e) => {
      e.target.disabled = true;
      try {
        await call(gid ? `/api/admin/qr/modgroups/${gid}` : '/api/admin/qr/modgroups',
          { method: gid ? 'PUT' : 'POST', body: JSON.stringify({
            name: hostEl.querySelector('[data-gname]').value.trim(),
            min_select: parseInt(hostEl.querySelector('[data-gmin]').value || '0', 10) || 0,
            max_select: hostEl.querySelector('[data-gmax]').value.trim() || null,
            options: parseOpts(hostEl.querySelector('[data-gopts]').value) }) });
        renderMods();
      } catch (err) {
        e.target.disabled = false;
        hostEl.querySelector('.msg').textContent = err.message;
      }
    };
  };
  host.querySelectorAll('[data-gid]').forEach((row) => {
    const gid = row.dataset.gid;
    const g = gs.find((x) => x.id === gid);
    row.querySelector('[data-act="gedit"]').onclick = () => {
      const f = row.querySelector('[data-gform]');
      if (f.style.display !== 'none') { f.style.display = 'none'; return; }
      f.style.display = ''; f.innerHTML = form(g); wireSave(f, gid);
    };
    row.querySelector('[data-act="gdel"]').onclick = async (e) => {
      if (!confirm(`Delete "${g.name}"? It comes off every item it's attached to.`)) return;
      e.target.disabled = true;
      try { await call(`/api/admin/qr/modgroups/${gid}`, { method: 'DELETE' }); } catch (_) {}
      renderMods();
    };
  });
  $('addgrp').onclick = () => {
    const w = $('newgwrap');
    if (w.style.display !== 'none') { w.style.display = 'none'; return; }
    w.style.display = ''; w.innerHTML = form(null); wireSave(w, null);
  };
}

// ================================================================== QUEUE
async function renderQueue() {
  let d;
  try { d = await call('/api/admin/qr/live'); } catch (_) { return; }
  const badge = $('qcount');
  badge.style.display = d.count ? '' : 'none';
  badge.textContent = d.count;
  if (VIEW.page !== 'queue') { d.orders.forEach((o) => seen.add(o.id)); return; }
  const host = $('vQueue');
  let anyNew = false;
  host.innerHTML = `
    <h1 class="mu">Live queue</h1>
    <div class="mu-sub">While the till link is dry-run, this screen IS the docket —
      keep it open at the pass.</div>
    <div class="mu-card">${d.count === 0
      ? '<div class="empty">No open orders. They\'ll appear the moment a guest pays.</div>'
      : d.orders.map((o) => {
          const fresh = !seen.has(o.id); if (fresh) anyNew = true;
          return `<div class="order ${fresh ? 'fresh' : ''}" data-id="${esc(o.id)}">
            <div class="top">
              <span class="tbl">T${esc(o.table_no)}</span>
              <span class="meta">${age(o.created_at)} · $${(o.total_cents / 100).toFixed(2)} paid</span>
              <span class="chip ${o.status === 'failed' ? 'hid' : 'ok'}" style="margin-left:auto">
                ${o.status === 'injected' ? 'in till' : o.status === 'paid' ? 'new' : 'till failed'}</span>
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
              ${o.status === 'failed' ? '<button class="btn" data-act="retry">Retry till</button>' : ''}
              <button class="btn green" data-act="done">Done — sent out</button>
            </div>
          </div>`;
        }).join('')}</div>`;
  d.orders.forEach((o) => seen.add(o.id));
  if (anyNew && seen.size > d.count) ding();
  host.querySelectorAll('.acts .btn').forEach((b) => { b.onclick = async () => {
    const id = b.closest('.order').dataset.id;
    b.disabled = true;
    try {
      if (b.dataset.act === 'done') await call(`/api/admin/qr/orders/${id}/complete`, { method: 'POST' });
      else await call(`/api/admin/qr/orders/${id}/reinject`, { method: 'POST' });
    } catch (_) {}
    renderQueue();
  }; });
}

// ================================================================ 86 BOARD
async function render86() {
  const host = $('v86');
  let d, eff;
  try {
    d = await call('/api/admin/qr/live');
    eff = FULL ? FULL.effective : await call('/api/qr/menu');
  } catch (e) { host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const offIds = new Set((d.eighty_sixed || []).map((x) => x.item_id));
  const filter = (host.querySelector('#q86') || {}).value || '';
  const rows = (eff.items || [])
    .filter((i) => !filter || i.name.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => (offIds.has(b.id) - offIds.has(a.id)) || a.name.localeCompare(b.name))
    .slice(0, filter ? 60 : 40);
  host.innerHTML = `
    <h1 class="mu">86 board</h1>
    <div class="mu-sub">Sold out TODAY. It greys out on the guest menu straight away,
      and the till refuses payment even from a phone holding the old menu.
      For "not on our menu at all", hide the item from its editor instead.</div>
    <input class="mu-search" id="q86" placeholder="Find an item to 86…" value="${esc(filter)}">
    <div class="mu-card">${rows.map((i) => `
      <div class="mrow">
        <div><div class="ttl">${esc(i.name)}</div><div class="meta">${esc(i.section)}</div></div>
        <div class="right">
          <button class="btn sm ${offIds.has(i.id) ? 'danger' : ''}"
            data-id="${esc(i.id)}" data-to="${offIds.has(i.id) ? 1 : 0}">
            ${offIds.has(i.id) ? "86'd — bring back" : '86 it'}</button>
        </div>
      </div>`).join('') || '<div class="empty">Nothing matches.</div>'}</div>`;
  host.querySelector('#q86').oninput = () => render86();
  host.querySelectorAll('[data-id]').forEach((b) => { b.onclick = async () => {
    b.disabled = true;
    try {
      await call(`/api/admin/qr/86/${encodeURIComponent(b.dataset.id)}?available=${b.dataset.to === '1'}`,
                 { method: 'POST' });
    } catch (_) {}
    render86();
  }; });
}

// ================================================================= HISTORY
async function renderHist() {
  const host = $('vHist');
  let d;
  try { d = await call('/api/admin/qr/history?days=14'); } catch (e) {
    host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return;
  }
  host.innerHTML = `
    <h1 class="mu">Orders</h1>
    <div class="mu-card">${d.orders.length ? `<table class="simple">
      <tr><th>When</th><th>Table</th><th>Items</th><th class="r">Total</th><th>Status</th></tr>
      ${d.orders.map((o) => `<tr>
        <td>${esc(o.created_at.slice(0, 16).replace('T', ' '))}</td>
        <td>T${esc(o.table_no)}</td>
        <td>${o.lines.map((l) => `${l.qty}× ${esc(l.name)}`).join(', ')}</td>
        <td class="r">$${(o.total_cents / 100).toFixed(2)}</td>
        <td>${esc(o.status)}</td></tr>`).join('')}</table>`
      : '<div class="empty">No orders in the last fortnight.</div>'}</div>`;
}

// ================================================================ SETTINGS
async function renderSet() {
  const host = $('vSet');
  let st;
  try { st = await call('/api/admin/qr/settings'); } catch (e) {
    host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return;
  }
  const fee = st.service_fee || { enabled: false, rate: '0.02',
    label: 'Table service fee', note: 'Skip it by ordering at the bar.' };
  const tip = st.tipping || { enabled: true, presets: ['0', '0.05', '0.1'], allow_custom: true };
  host.innerHTML = `
    <h1 class="mu">Venue settings</h1>
    <div class="mu-card pad"><div class="fform" style="border-bottom:none">
      <label>Paused message (what guests read when the dot is red)</label>
      <input type="text" id="sPaused" value="${esc(st.paused_message)}">
      <label>Tables that can order (comma-separated; blank = every table)</label>
      <input type="text" id="sTables" value="${esc((st.tables || []).join(', '))}">
      <label style="display:flex;gap:8px;align-items:center;text-transform:none;font-size:14.5px;margin-top:16px">
        <input type="checkbox" id="sFeeOn" ${fee.enabled ? 'checked' : ''}> Table service fee</label>
      <div class="row2"><div>
        <label>Rate (0.02 = 2%)</label><input type="text" id="sFeeRate" value="${esc(fee.rate)}">
      </div><div>
        <label>Label (never mention cards)</label><input type="text" id="sFeeLabel" value="${esc(fee.label)}">
      </div></div>
      <label>Note under the fee</label><input type="text" id="sFeeNote" value="${esc(fee.note)}">
      <label style="display:flex;gap:8px;align-items:center;text-transform:none;font-size:14.5px;margin-top:16px">
        <input type="checkbox" id="sTipOn" ${tip.enabled ? 'checked' : ''}> Tipping</label>
      <label>Presets (rates, comma-separated — 0.05 = 5%)</label>
      <input type="text" id="sTipPre" value="${esc((tip.presets || []).join(', '))}">
      <div class="btns"><button class="btn green" id="sSave">Save settings</button>
        <span class="msg" id="sMsg"></span></div>
    </div></div>`;
  $('sSave').onclick = async () => {
    $('sSave').disabled = true; $('sMsg').textContent = '';
    try {
      await call('/api/admin/qr/settings', { method: 'PUT', body: JSON.stringify({
        paused_message: $('sPaused').value,
        tables: $('sTables').value.split(',').map((t) => t.trim()).filter(Boolean),
        service_fee: { enabled: $('sFeeOn').checked, rate: $('sFeeRate').value.trim(),
          label: $('sFeeLabel').value, note: $('sFeeNote').value },
        tipping: { enabled: $('sTipOn').checked,
          presets: $('sTipPre').value.split(',').map((t) => t.trim()).filter(Boolean),
          allow_custom: true } }) });
      renderSet();
    } catch (e) { $('sSave').disabled = false; $('sMsg').textContent = e.message; }
  };
}

// ================================================================ INSIGHTS
async function renderIns() {
  const host = $('vIns');
  let d;
  try { d = await call('/api/admin/qr/insights?days=30'); } catch (e) {
    host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return;
  }
  const money = (c) => '$' + (c / 100).toFixed(2);
  host.innerHTML = `
    <h1 class="mu">Insights</h1>
    <div class="statgrid">
      <div class="mu-card pad stat"><div class="n">${d.totals.orders}</div><div class="l">orders · 30d</div></div>
      <div class="mu-card pad stat"><div class="n">${money(d.totals.revenue_cents)}</div><div class="l">taken · inc GST</div></div>
      <div class="mu-card pad stat"><div class="n">${money(d.totals.tips_cents)}</div><div class="l">tips</div></div>
    </div>
    <div class="mu-card pad" style="margin-bottom:14px"><strong>By day</strong>
      ${d.daily.length ? `<table class="simple">
        <tr><th>Date</th><th class="r">Orders</th><th class="r">Taken</th><th class="r">Tips</th></tr>
        ${d.daily.map((x) => `<tr><td>${esc(x.date)}</td><td class="r">${x.orders}</td>
          <td class="r">${money(x.revenue_cents)}</td><td class="r">${money(x.tips_cents)}</td></tr>`).join('')}
      </table>` : '<div class="empty">Nothing yet.</div>'}</div>
    <div class="mu-card pad"><strong>Top sellers</strong>
      ${d.top_items.length ? `<table class="simple">
        <tr><th>Item</th><th class="r">Qty</th><th class="r">Revenue</th></tr>
        ${d.top_items.map((x) => `<tr><td>${esc(x.name)}</td><td class="r">${x.qty}</td>
          <td class="r">${money(x.revenue_cents)}</td></tr>`).join('')}
      </table>` : '<div class="empty">Nothing yet.</div>'}</div>`;
}

// ------------------------------------------------------------------- nav
const NAV = {
  menu:  { btn: 'navMenu',  view: 'vMenu',  render: renderMenu },
  mods:  { btn: 'navMods',  view: 'vMods',  render: renderMods },
  queue: { btn: 'navQueue', view: 'vQueue', render: renderQueue },
  '86':  { btn: 'nav86',    view: 'v86',    render: render86 },
  hist:  { btn: 'navHist',  view: 'vHist',  render: renderHist },
  set:   { btn: 'navSet',   view: 'vSet',   render: renderSet },
  ins:   { btn: 'navIns',   view: 'vIns',   render: renderIns },
};
function go(page) {
  VIEW.page = page;
  if (page !== 'menu') VIEW.menuId = null;
  for (const [k, n] of Object.entries(NAV)) {
    $(n.btn).classList.toggle('sel', k === page);
    $(n.view).style.display = k === page ? '' : 'none';
  }
  NAV[page].render();
}

// ------------------------------------------------------------------ boot
async function boot() {
  await ensureToken();
  if (!SVC) { $('tokenbox').style.display = ''; return; }
  try {
    const h = await call('/api/admin/qr/health');
    $('writerPill').textContent = h.writer === 'dry-run'
      ? 'no till link yet — the live queue is the docket' : 'till: ' + h.writer;
  } catch (_) { return; }
  refreshDot();
  go('menu');
  clearInterval(pollTimer);
  pollTimer = setInterval(renderQueue, 5000);   // queue badge stays live everywhere
}

Auth.gate($('gate'), {
  roles: null,
  onOk: (user) => {
    $('app').style.display = '';
    $('whotop').innerHTML = `<strong>${esc(user.name)}</strong>`;
    $('signout').onclick = async (e) => {
      e.preventDefault(); await Auth.logout(); location.href = '/';
    };
    for (const [k, n] of Object.entries(NAV)) $(n.btn).onclick = () => go(k);
    $('acceptWrap').onclick = toggleDot;
    $('tokgo').onclick = () => {
      const t = $('tok').value.trim();
      if (!t) return;
      localStorage.setItem(TOKEN_KEY, t);
      SVC = t; $('tokenbox').style.display = 'none'; $('tok').value = '';
      boot();
    };
    boot();
  },
});
