/**
 * Bookings module — all logic for /bookings/ (the HTML is a shell).
 *
 * AUTH — two layers, and staff should never see a token box:
 *   1. Supabase gate decides who may open the page.
 *   2. The booking engine's bearer token is the REAL auth, verified by the
 *      service on every call. It is fetched automatically via
 *      Auth.bookingToken() from the Supabase app_config table (RLS: readable
 *      by authenticated users only), so a signed-in user is simply in.
 *      The manual paste box is a FALLBACK for when that lookup fails — it is
 *      not the normal path. (Regression note: earlier revisions of this file
 *      skipped bookingToken() and always prompted; don't do that again.)
 *
 * Cards come from /api/admin/overview: every upcoming PUBLIC event plus any
 * regular day holding staff bookings ("House bookings"). Any date is openable
 * via the date-picker card — house days take free start/end times.
 */
import { Auth } from '/_shared/auth.js';

const API = 'https://stowaway-bookings.onrender.com';
const TOKEN_KEY = 'stowaway_booking_token';

const $ = (id) => document.getElementById(id);
let DAY = null;
let EDITING = null;
let SEL = null;   // selected card {date, name, sittings, public}
let SVC = null;   // service token held in memory for this session

// ---------------------------------------------------------------- service io
const svcToken = () => SVC || localStorage.getItem(TOKEN_KEY) || '';

/** Supabase first (no paste), then any locally-saved fallback. */
async function ensureToken() {
  if (SVC) return SVC;
  try {
    const t = await Auth.bookingToken();
    if (t) { SVC = t; return SVC; }
  } catch (_) { /* fall through to the manual fallback */ }
  return localStorage.getItem(TOKEN_KEY) || '';
}

const hdrs = () => ({ 'Authorization': 'Bearer ' + svcToken(),
                      'Content-Type': 'application/json' });

async function call(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
  return r;
}

function fmtBooked(iso) {
  if (!iso) return '';
  return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString('en-AU',
    { timeZone: 'Australia/Sydney', day: '2-digit', month: 'short',
      hour: 'numeric', minute: '2-digit' });
}

const T12 = (t) => {
  const [h, m] = t.split(':').map(Number);
  const h12 = ((h + 11) % 12) + 1;
  return h12 + (m ? ':' + String(m).padStart(2, '0') : '') + (h >= 12 ? 'pm' : 'am');
};

function rowHtml(b) {
  const covers = b.adults + b.kids;
  const flags = [
    b.dogs ? `<span class="pill dog">${b.dogs > 1 ? b.dogs + ' dogs' : 'dog'}</span>` : '',
    b.kids ? `<span class="pill kid">${b.kids} kid${b.kids > 1 ? 's' : ''}</span>` : '',
    b.babies ? `<span class="pill kid">high chair</span>` : '',
    b.status === 'pending_deposit' ? `<span class="pill">deposit pending</span>` : '',
  ].join('');
  const note = b.notes ? `<span class="bnote">${b.notes}</span> · ` : '';
  const contact = [b.phone, b.email].filter(Boolean).join(' · ');
  return `<div class="brow${b.status === 'pending_deposit' ? ' pending' : ''}">
    <button class="btable${b.pinned_table ? ' pinnedchip' : ''}" data-pick="${b.id}"
      title="${b.pinned_table ? 'pinned — click to change' : 'click to choose a table'}">${b.suggested_table || '—'}</button>
    <div class="bmain">
      <div class="bname">${b.name} ${flags}</div>
      <div class="bsub">${note}${contact} · booked ${fmtBooked(b.created_at)}</div>
    </div>
    <div class="bpax">${covers}<small>pax</small></div>
    <div class="bacts">
      <button class="mini" data-edit="${b.id}">edit</button>
      <button class="mini danger" data-cancel="${b.id}">cancel</button>
    </div>
  </div>`;
}

function renderDay(d) {
  const wrap = $('daywrap');
  wrap.innerHTML = '';
  const active = d.bookings.filter(b => b.status !== 'cancelled');
  const cancelled = d.bookings.filter(b => b.status === 'cancelled');
  const times = d.sittings.length ? d.sittings
    : [...new Set(active.map(b => b.time))].sort();

  times.forEach(t => {
    const rows = active.filter(b => b.time === t)
      .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
    const covers = rows.reduce((n, b) => n + b.adults + b.kids, 0);
    const sizes = d.remaining[t] || {};
    const vals = Object.entries(sizes);
    const fitting = vals.filter(([, ok]) => ok === true).map(([s]) => +s);
    const unknown = vals.some(([, ok]) => ok === null || ok === undefined);
    const fit = !vals.length ? ''
      : fitting.length && vals.every(([, ok]) => ok === true)
        ? '<span class="yes">any party fits</span>'
      : fitting.length ? `<span class="yes">room up to ${Math.max(...fitting)}p</span>`
      : unknown ? '<span class="sit-meta">checking capacity…</span>'
      : '<span class="no">FULL</span>';
    const block = document.createElement('div');
    block.className = 'sitting-block';
    block.innerHTML = `<div class="sitting-head">
        <span class="sit-time">${T12(t)}</span>
        <span class="sit-meta">${rows.length} bookings · ${covers} covers</span>
        <span class="sit-fit">${fit}</span>
      </div>` + (rows.map(rowHtml).join('') ||
        '<div class="bsub" style="padding:14px 18px">No bookings yet.</div>');
    wrap.appendChild(block);
  });
  if (!times.length) {
    wrap.innerHTML = '<div class="bsub" style="padding:14px 4px">No bookings on this day yet — use + New booking.</div>';
  }

  if (cancelled.length) {
    const det = document.createElement('details');
    det.className = 'cancelled-list';
    det.innerHTML = `<summary>${cancelled.length} cancelled</summary>` +
      cancelled.map(b =>
        `<div class="crow">${T12(b.time)} · ${b.name} ×${b.adults + b.kids} · ${b.phone || ''} · ${fmtBooked(b.created_at)}</div>`).join('');
    wrap.appendChild(det);
  }

  wrap.querySelectorAll('[data-edit]').forEach(el =>
    el.addEventListener('click', () => openEdit(el.dataset.edit)));
  wrap.querySelectorAll('[data-cancel]').forEach(el =>
    el.addEventListener('click', () => cancelBooking(el.dataset.cancel)));
  wrap.querySelectorAll('[data-pick]').forEach(el =>
    el.addEventListener('click', () => pickTable(el.dataset.pick, el)));
}

function copyBtn(text, label) {
  const b = document.createElement('button');
  b.className = 'mini';
  b.textContent = label;
  b.onclick = async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = 'copied ✓'; }
    catch { b.textContent = 'copy failed'; }
    setTimeout(() => { b.textContent = label; }, 1600);
  };
  return b;
}

function showGuestLink() {
  const g = $('guestlink');
  g.innerHTML = '';
  const row = document.createElement('div');
  row.className = 'glrow';
  if (SEL.public === false) {
    row.textContent = 'Staff-only day — guests can’t see or book it.';
    g.appendChild(row);
    return;
  }
  const url = `${API}/?date=${SEL.date}`;
  const embed = `<iframe src="${API}/?bare=1&date=${SEL.date}" `
    + `style="width:100%;max-width:560px;height:780px;border:0" `
    + `title="Book ${SEL.name}"></iframe>`;
  const short = new Date(SEL.date + 'T12:00:00').toLocaleDateString('en-AU',
    { day: 'numeric', month: 'short' });
  row.innerHTML = `🔗 <a href="${url}" target="_blank" rel="noopener">Booking page — ${SEL.name}, ${short}</a>`;
  row.appendChild(copyBtn(url, 'copy link'));
  row.appendChild(copyBtn(embed, 'copy Wix embed'));
  g.appendChild(row);
}

async function pickTable(id, chip) {
  const original = chip.textContent;
  chip.textContent = '…';
  chip.disabled = true;
  let alts;
  try {
    alts = await (await call(`/api/admin/bookings/${id}/alternatives`)).json();
  } catch (e) {
    chip.textContent = original; chip.disabled = false;
    $('status').textContent = 'error: ' + e.message;
    return;
  }
  const sel = document.createElement('select');
  sel.className = 'tblselect';
  const current = alts.pinned || original;
  sel.innerHTML = `<option value="auto">auto — engine picks</option>` +
    alts.options.map(o =>
      `<option value="${o}" ${o === current ? 'selected' : ''}>${o}${o === alts.pinned ? ' (pinned)' : ''}</option>`).join('');
  chip.replaceWith(sel);
  sel.focus();
  let done = false;
  sel.addEventListener('change', async () => {
    done = true;
    try {
      await call(`/api/admin/bookings/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ pinned_table: sel.value }),
      });
    } catch (e) { $('status').textContent = 'move refused: ' + e.message; }
    loadDay();
  });
  sel.addEventListener('blur', () => { if (!done) loadDay(); });
}

async function loadDay() {
  $('status').textContent = 'loading…';
  try {
    DAY = await (await call('/api/admin/day/' + SEL.date)).json();
    $('status').textContent = DAY.event +
      (DAY.solvable ? ' · day solves ✓' : ' · ⚠ DAY DOES NOT SOLVE');
    showGuestLink();
    renderDay(DAY);
  } catch (e) {
    if (String(e.message).includes('bad admin token')) {
      SVC = null;
      localStorage.removeItem(TOKEN_KEY);
      showToken();
    } else {
      $('status').textContent = 'error: ' + e.message;
    }
  }
}

async function cancelBooking(id) {
  if (!confirm('Cancel this booking?')) return;
  await call(`/api/admin/bookings/${id}/cancel`, { method: 'POST' });
  refreshCards();
  loadDay();
}

function openEdit(id) {
  const b = (DAY?.bookings || []).find(x => x.id === id);
  if (!b) return;
  EDITING = id;
  $('edit_name').textContent = '— ' + b.name;
  const virt = !!DAY.virtual;
  $('ed_time').style.display = virt ? 'none' : '';
  $('ed_time2').style.display = virt ? '' : 'none';
  if (virt) {
    $('ed_time2').value = b.time;
  } else {
    $('ed_time').innerHTML = (DAY.sittings || []).map(t =>
      `<option ${t === b.time ? 'selected' : ''}>${t}</option>`).join('');
  }
  $('ed_adults').value = b.adults; $('ed_kids').value = b.kids;
  $('ed_babies').value = b.babies; $('ed_dogs').value = b.dogs;
  $('ed_phone').value = b.phone || ''; $('ed_notes').value = b.notes || '';
  $('editbox').style.display = 'block';
  $('editbox').scrollIntoView({ behavior: 'smooth' });
}

async function saveEdit() {
  try {
    const t = DAY.virtual ? $('ed_time2').value : $('ed_time').value;
    await call(`/api/admin/bookings/${EDITING}`, {
      method: 'PATCH',
      body: JSON.stringify({
        time: t, adults: +$('ed_adults').value,
        kids: +$('ed_kids').value, babies: +$('ed_babies').value,
        dogs: +$('ed_dogs').value, phone: $('ed_phone').value,
        notes: $('ed_notes').value,
      }),
    });
    $('editbox').style.display = 'none';
    loadDay();
  } catch (e) { $('status').textContent = 'edit refused: ' + e.message; }
}

// New booking. On event days: pick a sitting. On house days: free start time
// plus optional end time (becomes the booking's table hold).
function openAdd() {
  const virt = !SEL.public && (!DAY || DAY.virtual !== false);
  $('nb_timewrap').style.display = virt ? 'none' : '';
  $('nb_startwrap').style.display = virt ? '' : 'none';
  $('nb_endwrap').style.display = virt ? '' : 'none';
  if (!virt) {
    $('nb_time').innerHTML = ((DAY && DAY.sittings) || SEL.sittings || []).map(t =>
      `<option value="${t}">${T12(t)}</option>`).join('');
  }
  ['nb_name', 'nb_phone', 'nb_email', 'nb_notes'].forEach(id => { $(id).value = ''; });
  $('nb_start').value = '18:00'; $('nb_end').value = '';
  $('nb_adults').value = 2; $('nb_kids').value = 0;
  $('nb_babies').value = 0; $('nb_dogs').value = 0;
  $('editbox').style.display = 'none';
  $('addbox').style.display = 'block';
  $('addbox').scrollIntoView({ behavior: 'smooth' });
  $('nb_name').focus();
}

async function saveNew() {
  if (!$('nb_name').value.trim()) { $('status').textContent = 'name is required'; return; }
  const phone = ($('nb_phone').value || '').trim();
  const email = ($('nb_email').value || '').trim();
  if (!phone && !email) { $('status').textContent = 'a phone number or an email is required'; return; }
  const virt = !SEL.public;
  const time = virt ? $('nb_start').value : $('nb_time').value;
  if (!time) { $('status').textContent = 'start time is required'; return; }
  let hold = null;
  if (virt && $('nb_end').value) {
    const [sh, sm] = time.split(':').map(Number);
    const [eh, em] = $('nb_end').value.split(':').map(Number);
    hold = (eh * 60 + em) - (sh * 60 + sm);
    if (hold < 30) { $('status').textContent = 'end time must be at least 30 min after start'; return; }
  }
  try {
    const r = await (await call('/api/admin/bookings', {
      method: 'POST',
      body: JSON.stringify({
        date: SEL.date, time,
        name: $('nb_name').value.trim(),
        phone: phone || null,
        email: email || null,
        adults: +$('nb_adults').value, kids: +$('nb_kids').value,
        babies: +$('nb_babies').value, dogs: +$('nb_dogs').value,
        notes: $('nb_notes').value,
        hold_minutes: hold,
      }),
    })).json();
    $('addbox').style.display = 'none';
    $('status').textContent = `booked — ${r.covers} pax at ${T12(r.time)}`;
    refreshCards();
    loadDay();
  } catch (e) { $('status').textContent = 'booking refused: ' + e.message; }
}

async function downloadRunsheet() {
  const r = await call(`/api/admin/day/${SEL.date}/runsheet`);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = `Stowaway_Runsheet_${SEL.date}.pdf`;
  a.click();
}

// ---------------------------------------------------------------- boot
function showToken() {
  $('tokenbox').style.display = 'block';
  $('main').style.display = 'none';
}

function niceDate(iso) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-AU',
    { weekday: 'long', day: 'numeric', month: 'long' });
}

function selectEvent(ev, card) {
  SEL = ev;
  $('addbox').style.display = 'none';
  $('editbox').style.display = 'none';
  document.querySelectorAll('.event-card').forEach(c => c.classList.remove('sel'));
  if (card) card.classList.add('sel');
  loadDay();
}

function cardFor(ev, selected) {
  const dt = new Date(ev.date + 'T12:00:00');
  const dow = dt.toLocaleDateString('en-AU', { weekday: 'short' });
  const dm = dt.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
  const card = document.createElement('div');
  card.className = 'event-card' + (selected ? ' sel' : '') + (ev.public ? ' pub' : '');
  card.title = `${ev.name} — ${niceDate(ev.date)}`;
  const badge = ev.bookings ? `<span class="ec-count">${ev.bookings} bkg · ${ev.covers}p</span>` : '';
  card.innerHTML = `<div class="ec-dow">${dow}</div>
    <div class="ec-date">${dm}</div>
    <div class="ec-name">${ev.name}</div>${badge}`;
  card.addEventListener('click', () => selectEvent(ev, card));
  return card;
}

async function refreshCards() {
  const days = await (await call('/api/admin/overview')).json();
  $('eventline').textContent = 'Pick a day:';
  $('events').innerHTML = '';
  days.forEach(ev => $('events').appendChild(cardFor(ev, SEL && SEL.date === ev.date)));
  const add = document.createElement('div');
  add.className = 'event-card anyday';
  add.innerHTML = `<div class="ec-dow">any day</div>
    <input type="date" id="anydate" class="ec-datepick">
    <div class="ec-name">open a date</div>`;
  add.querySelector('#anydate').addEventListener('change', (e) => {
    if (!e.target.value) return;
    selectEvent({ date: e.target.value, name: 'House bookings',
                  sittings: [], public: false }, null);
  });
  $('events').appendChild(add);
  return days;
}

async function init() {
  // Signed in = authorised. The service token comes from Supabase; the paste
  // box only appears if that lookup fails.
  const t = await ensureToken();
  if (!t) { showToken(); return; }
  $('tokenbox').style.display = 'none';
  $('main').style.display = 'block';
  try {
    const days = await refreshCards();
    if (!days.length) {
      $('eventline').textContent = 'Nothing upcoming — open any date below.';
      return;
    }
    SEL = days[0];
    document.querySelector('.event-card')?.classList.add('sel');
    loadDay();
  } catch (e) {
    if (String(e.message).includes('bad admin token')) {
      SVC = null;
      localStorage.removeItem(TOKEN_KEY);
      showToken();
    } else {
      $('eventline').textContent = 'Booking engine unreachable: ' + e.message;
    }
  }
}

Auth.gate($('gate'), {
  roles: null,        // open to all signed-in staff (Zak, 2026-07: bookings is for
                      // everyone). NB: guest phone numbers are visible here.
  onOk: (user) => {
    $('app').style.display = '';
    $('whotop').innerHTML = `<strong>${user.name}</strong>`;
    $('signout').onclick = async (e) => {
      e.preventDefault(); await Auth.logout(); location.href = '/';
    };
    $('savetoken').addEventListener('click', () => {
      SVC = $('svc_token').value.trim();
      localStorage.setItem(TOKEN_KEY, SVC);
      init();
    });
    $('addbtn').addEventListener('click', openAdd);
    $('savenewbtn').addEventListener('click', saveNew);
    $('closenewbtn').addEventListener('click', () => { $('addbox').style.display = 'none'; });
    $('runsheetbtn').addEventListener('click', downloadRunsheet);
    $('saveeditbtn').addEventListener('click', saveEdit);
    $('closeeditbtn').addEventListener('click', () => { $('editbox').style.display = 'none'; });
    init();
  },
});
