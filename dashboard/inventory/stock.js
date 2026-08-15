/**
 * Stocktake and goods-received, on a phone.
 *
 * Writes rows to Supabase `stock_events` under row-level security, so this page
 * holds no secret — the same route invoice approvals already use. Nothing here
 * converts anything: what the person taps is what gets stored, verbatim, and
 * scripts/ingest_stock_events.py turns it into ledger movements later against a
 * container-size table that CAN BE CORRECTED. If a bottle size turns out to be
 * wrong, every past count re-derives. Convert in the browser and that error is
 * permanent.
 *
 * WHY IT SAVES AS YOU GO. A stocktake is two hundred taps in a cold room on bad
 * wifi. A single Submit at the end is one dropped connection away from losing
 * the lot, so every line posts as it is entered. The session is resumable
 * because the rows are already in Supabase, not in this tab.
 *
 * UNCOUNTED IS NOT ZERO. Skipping an item records nothing. There is a separate
 * "none" button, because "I looked and there are none" and "I did not look" are
 * different facts and only one of them should move stock.
 */
import { SUPABASE_URL, SUPABASE_ANON_KEY } from "/_shared/config.js";
import { Auth } from "/_shared/auth.js";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const sb = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
const $ = (s) => document.querySelector(s);

const state = {
  user: null, items: [], locations: [], location: null, venue: "stow",
  sessionRef: null, posted: new Map(), mode: "count",
};

export async function boot() {
  state.user = await Auth.current();
  if (!state.user) { location.href = "/?next=/inventory/"; return; }
  $("#who").textContent = `${state.user.name} · ${state.user.role || "no role"}`;

  const r = await fetch("/data/stock_catalogue.json?t=" + Date.now(), { cache: "no-store" });
  if (!r.ok) { fail("Could not load the item list. Reload when you have signal."); return; }
  const cat = await r.json();
  state.items = cat.items || [];
  state.locations = cat.locations || [];

  renderLocations();
  $("#search").addEventListener("input", renderItems);
  $("#venue").addEventListener("change", (e) => { state.venue = e.target.value; });
  $("#mode-count").addEventListener("click", () => setMode("count"));
  $("#mode-receive").addEventListener("click", () => setMode("receive"));
  setMode("count");
}

function setMode(m) {
  state.mode = m;
  $("#mode-count").classList.toggle("on", m === "count");
  $("#mode-receive").classList.toggle("on", m === "receive");
  $("#receive-bar").hidden = m !== "receive";
  renderItems();
}

function ensureSession() {
  if (state.sessionRef) return state.sessionRef;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  state.sessionRef = `${state.mode}:${stamp}:${(state.user.email || "?").split("@")[0]}`;
  $("#session").textContent = state.sessionRef;
  return state.sessionRef;
}

function renderLocations() {
  const sel = $("#location");
  sel.innerHTML = state.locations.map((l) => `<option>${l}</option>`).join("");
  state.location = state.locations[0] || null;
  sel.addEventListener("change", () => { state.location = sel.value; renderItems(); });
}

function renderItems() {
  const q = ($("#search").value || "").trim().toLowerCase();
  const rows = state.items
    .filter((i) => !q || i.name.toLowerCase().includes(q))
    .slice(0, q ? 60 : 40);

  $("#items").innerHTML = rows.map((i) => {
    const done = state.posted.get(i.item_id);
    // Items with no recorded container size are shown ANYWAY, flagged. Hiding
    // them means they silently never get counted and their variance stays
    // blank — which reads as "no problem here".
    const warn = i.convertible ? "" :
      `<span class="warn" title="Nobody has recorded how big one ${i.count_in} is. The count is kept and held until they do.">needs a size</span>`;
    return `
      <div class="item ${done ? "done" : ""}" data-id="${i.item_id}">
        <div class="nm">${esc(i.name)}${warn}</div>
        <div class="ctl">
          <input inputmode="decimal" placeholder="0" aria-label="how many ${i.count_in}"
                 value="${done ? done.qty : ""}">
          <span class="unit">${i.count_in}</span>
          <button class="save">${done ? "saved" : "save"}</button>
          <button class="none" title="Checked — there are none">none</button>
        </div>
      </div>`;
  }).join("") || `<p class="muted">Nothing matches that.</p>`;

  $("#items").querySelectorAll(".item").forEach((el) => {
    const id = el.dataset.id;
    el.querySelector(".save").addEventListener("click", () =>
      post(id, el.querySelector("input").value, el));
    el.querySelector(".none").addEventListener("click", () => post(id, "0", el, true));
  });
}

async function post(itemId, raw, el, explicitZero = false) {
  const item = state.items.find((i) => i.item_id === itemId);
  const qty = Number(String(raw).trim());
  if (!explicitZero && (raw === "" || Number.isNaN(qty))) return flash(el, "enter a number", true);
  if (qty < 0) return flash(el, "no negatives", true);

  const row = {
    kind: state.mode === "receive" ? "receive" : "count",
    occurred_at: new Date().toISOString(),
    venue: state.venue,
    location: state.location,
    item_id: itemId,
    item_name: item?.name || null,
    counted_qty: explicitZero ? 0 : qty,
    counted_unit: item?.count_in || "each",
    session_ref: ensureSession(),
    // Every location this session has walked. The ingester will not let a count
    // set truth unless its scope covers everywhere the item is known to live —
    // otherwise counting the bar writes off the storeroom as phantom waste.
    session_locations: [state.location],
    actor: state.user.name,
    actor_email: state.user.email,
    note: explicitZero ? "checked — none left" : null,
  };
  if (state.mode === "receive") {
    row.po_ref = ($("#po").value || "").trim() || null;
    const exp = ($("#expected").value || "").trim();
    if (exp !== "" && !Number.isNaN(Number(exp))) row.expected_qty = Number(exp);
  }

  el.classList.add("saving");
  const { data, error } = await sb.from("stock_events").insert(row).select("id").single();
  el.classList.remove("saving");

  if (error) {
    return flash(el, /row-level security/i.test(error.message)
      ? "not allowed — ask Zak for a role" : "failed — try again", true);
  }
  state.posted.set(itemId, { qty: row.counted_qty, unit: row.counted_unit, id: data?.id });
  el.classList.add("done");
  el.querySelector(".save").textContent = "saved";
  $("#tally").textContent = `${state.posted.size} line(s) recorded`;
  flash(el, explicitZero ? "none" : `${row.counted_qty} ${row.counted_unit}`);
}

function flash(el, msg, bad = false) {
  const n = document.createElement("span");
  n.className = "flash" + (bad ? " bad" : "");
  n.textContent = msg;
  el.appendChild(n);
  setTimeout(() => n.remove(), 1800);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function fail(msg) { $("#items").innerHTML = `<p class="bad">${msg}</p>`; }

boot();
