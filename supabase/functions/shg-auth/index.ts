// SHG Auth — Supabase Edge Function. Free, always-on replacement for the
// Pipedream worker (eotwefx7cim9jou.m.pipedream.net) which died with Pipedream's
// credits. Same contract, same routes:
//
//   POST /shg-auth/admin/users    list users            (admin only)
//   POST /shg-auth/admin/invite   invite + set role     (admin only)
//   POST /shg-auth/admin/role     set role/venue/link   (admin only)
//   POST /shg-auth/recipes        commit data/recipes/<venue>.yaml  (kitchen)
//   POST /shg-auth/prep           append data/prep_sessions/<venue>.yaml (kitchen)
//
// Every request is authenticated by the caller's Supabase token (verified via
// /auth/v1/user). PRIVILEGE (role, venue) is read ONLY from app_metadata.
//
// The admin routes use the service role key — AUTO-INJECTED by Supabase as
// SUPABASE_SERVICE_ROLE_KEY, so there is no secret to set. The repo-write routes
// use GITHUB_TOKEN (set once as a function secret). Deploy with verify_jwt=false
// (we verify the token ourselves and must answer CORS preflight).

import { checkRecipeSave, stampEffectiveFrom } from "./recipe_guard.ts";

const KITCHEN = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];
const ROLES = ["admin", "bigchef", "stowfood", "hgfood", "bar", "pizza"];
const VENUES = ["stowaway", "harry_gatos", "marilynas"];
const REPO = "stowaway-hospitality/mari-daily-reporting";

const CORS = {
  "access-control-allow-origin": "https://app.stowawaybar.com",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const GITHUB_TOKEN = Deno.env.get("GITHUB_TOKEN") || "";

const reply = (status: number, body: unknown) =>
  new Response(status === 204 ? null : (typeof body === "string" ? body : JSON.stringify(body)), {
    status,
    headers: { "content-type": "application/json", ...CORS },
  });

// utf8 <-> base64 (chunked; recipe/prep files are small but be safe)
function toB64(str: string): string {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode(...bytes.subarray(i, i + CH));
  return btoa(bin);
}
function fromB64(b64: string): string {
  const bin = atob((b64 || "").replace(/\n/g, ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return reply(204, "");
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return reply(500, { error: "Supabase env vars not set" });

  // who is calling? verify their token
  const token = (req.headers.get("authorization") || "").replace(/^Bearer /, "");
  if (!token) return reply(401, { error: "Not signed in" });
  const who = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: SUPABASE_ANON_KEY, authorization: `Bearer ${token}` },
  });
  if (!who.ok) return reply(401, { error: "Invalid or expired session" });
  const user = await who.json();
  const app = user.app_metadata || {};
  const usr = user.user_metadata || {};
  const role = app.role || null;
  const allowedVenue = app.venue || null;
  const name = usr.name || app.name || user.email;

  const path = new URL(req.url).pathname.replace(/\/+$/, "");
  let body: Record<string, any> = {};
  try { body = await req.json(); } catch { /* empty body ok */ }

  // ── admin routes — service role key, admin caller only ───────────────────
  if (path.endsWith("/admin/users") || path.endsWith("/admin/invite") || path.endsWith("/admin/role")) {
    if (role !== "admin") return reply(403, { error: "Admins only" });
    if (!SERVICE_KEY) return reply(500, { error: "service role key not available" });
    const sbAdmin = (method: string, p: string, payload?: unknown) =>
      fetch(`${SUPABASE_URL}/auth/v1/${p}`, {
        method,
        headers: { apikey: SERVICE_KEY, authorization: `Bearer ${SERVICE_KEY}`, "content-type": "application/json" },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
      });
    const findId = async (email: string) => {
      const r = await sbAdmin("GET", "admin/users?per_page=200");
      const j = await r.json();
      return (j.users || []).find((u: any) => (u.email || "").toLowerCase() === email.toLowerCase())?.id;
    };
    const getUser = async (uid: string) => {
      const r = await sbAdmin("GET", `admin/users/${uid}`);
      return r.ok ? r.json() : null;
    };
    const shapeUser = (u: any) => ({
      id: u.id, email: u.email, name: u.user_metadata?.name || "",
      role: u.app_metadata?.role || null, venue: u.app_metadata?.venue || null,
      employee: u.app_metadata?.employee_id || null,
      confirmed: !!u.email_confirmed_at, last_sign_in: u.last_sign_in_at || null,
    });

    if (path.endsWith("/admin/users")) {
      const r = await sbAdmin("GET", "admin/users?per_page=200");
      if (!r.ok) return reply(502, { error: "list failed", detail: await r.text() });
      const j = await r.json();
      return reply(200, { users: (j.users || []).map(shapeUser) });
    }

    if (path.endsWith("/admin/invite")) {
      const email = String(body.email || "").trim().toLowerCase();
      const newRole = body.role || null;
      const newVenue = body.venue || null;
      if (!email) return reply(400, { error: "email required" });
      if (newRole && !ROLES.includes(newRole)) return reply(400, { error: "bad role" });
      if (newVenue && !VENUES.includes(newVenue)) return reply(400, { error: "bad venue" });
      const inv = await sbAdmin("POST", "invite", { email });
      if (!inv.ok) return reply(502, { error: "invite failed", detail: await inv.text() });
      const invited = await inv.json();
      const meta: Record<string, unknown> = {};
      if (newRole) meta.role = newRole;
      if (newVenue) meta.venue = newVenue;
      if (body.employee) meta.employee_id = String(body.employee);
      if (Object.keys(meta).length) await sbAdmin("PUT", `admin/users/${invited.id}`, { app_metadata: meta });
      return reply(200, { ok: true, invited: email, role: newRole, venue: newVenue });
    }

    if (path.endsWith("/admin/role")) {
      const email = String(body.email || "").trim().toLowerCase();
      const newRole = body.role || null;
      const newVenue = body.venue ?? null;
      if (newRole && !ROLES.includes(newRole)) return reply(400, { error: "bad role" });
      if (newVenue && !VENUES.includes(newVenue)) return reply(400, { error: "bad venue" });
      const uid = body.id || (email ? await findId(email) : null);
      if (!uid) return reply(404, { error: "user not found" });
      const cur = await getUser(uid);
      const meta: Record<string, unknown> = { ...(cur?.app_metadata || {}) };
      if ("role" in body) meta.role = newRole;
      if ("venue" in body) meta.venue = newVenue;
      if ("employee" in body) meta.employee_id = body.employee ? String(body.employee) : null;
      const upd = await sbAdmin("PUT", `admin/users/${uid}`, { app_metadata: meta });
      if (!upd.ok) return reply(502, { error: "update failed", detail: await upd.text() });
      return reply(200, { ok: true });
    }
  }

  // ── repo writes — kitchen role, committed AS the person ──────────────────
  if (!KITCHEN.includes(role)) return reply(403, { error: "Your role cannot edit recipes" });
  if (!GITHUB_TOKEN) return reply(500, { error: "GITHUB_TOKEN not set on the function" });

  const { venue, product } = body;
  const noVenue = path.endsWith("/alias");   // /alias is a whole-book action, not per-venue
  if (!venue && !noVenue) return reply(400, { error: "venue required" });
  if (venue && !/^[a-z_]+$/.test(venue)) return reply(400, { error: "bad venue" });
  if (!product && !path.endsWith("/pack") && !noVenue) return reply(400, { error: "product required" });
  if (!["admin", "bigchef"].includes(role) && allowedVenue && allowedVenue !== venue) {
    return reply(403, { error: `You can only edit ${allowedVenue}` });
  }

  const gh = (method: string, url: string, payload?: unknown) =>
    fetch(`https://api.github.com/repos/${REPO}/${url}`, {
      method,
      headers: {
        authorization: `Bearer ${GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "user-agent": "shg-auth",
        ...(payload ? { "content-type": "application/json" } : {}),
      },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
  const stamp = new Date().toISOString().slice(0, 10);
  type CommitResult = {
    ok: boolean;
    path?: string;
    deduped?: boolean;
    detail?: string;
    status?: number;
    /** A save guard refused this write. Operator-facing text, safe to show. */
    guard?: string;
  };
  const appendCommit = async (
    path_: string,
    block: string,
    message: string,
    guard?: (current: string) => string | null,
  ): Promise<CommitResult> => {
    let sha: string | undefined, current = "";
    const existing = await gh("GET", `contents/${path_}`);
    if (existing.ok) {
      const j = await existing.json();
      sha = j.sha;
      current = fromB64(j.content);
    }
    // Save guards read the book AS IT STANDS, so they can see the product's
    // previous block. Deliberately BEFORE the idempotence check below: a block
    // the guard refuses is refused even when an identical one was the last
    // thing written, because "we already stored it" is not a reason to store
    // it again.
    if (guard) {
      const problem = guard(current);
      if (problem) return { ok: false, guard: problem, status: 400 };
    }
    // IDEMPOTENCE. This is an append-only log with at-least-once delivery: if
    // GitHub accepts the PUT but the response never reaches us, the client shows
    // an error and the operator saves again — and the log gets the same block
    // twice. That is exactly how Romesco Sauce came to sit in stowaway.yaml
    // byte-for-byte identical, saved twice on 2026-08-03 by the same person, and
    // then quietly became "which of these two is live?".
    //
    // The block carries a timestamp in its comment header, so compare the BODY
    // only: if what we are about to append is already the last thing in the file,
    // the previous attempt landed. Report success rather than writing it again.
    // Deliberately only the TAIL — an identical block further up is a genuine
    // revert-to-an-earlier-spec, which is a real edit and must still append.
    const bodyOf = (s: string) =>
      s.split("\n").filter((l) => !l.trimStart().startsWith("#")).join("\n").trim();
    const incoming = bodyOf(block);
    if (incoming && bodyOf(current).endsWith(incoming)) {
      return { ok: true, path: path_, deduped: true };
    }
    const put = await gh("PUT", `contents/${path_}`, {
      message,
      content: toB64(current + block),
      author: { name, email: user.email },
      ...(sha ? { sha } : {}),
    });
    if (!put.ok) return { ok: false, detail: await put.text(), status: put.status };
    return { ok: true, path: path_ };
  };

  if (path.endsWith("/alias")) {
    // Self-service ingredient merge/split for /pricing. A senior chef says "these
    // two ARE the same thing" (merge one canonical key onto another) or undoes a
    // prior merge (unmerge). Read-modify-write of data/ingredient_aliases.json —
    // JSON, so it can't be a blind append like the YAML logs. Restricted to
    // admin/bigchef: a wrong merge silently corrupts a price comparison book-wide.
    if (!["admin", "bigchef"].includes(role)) {
      return reply(403, { error: "Only a senior chef or admin can merge ingredients" });
    }
    const action = String(body.action || "merge");
    const from = String(body.from || "").trim();
    const into = String(body.into || "").trim();
    if (!from) return reply(400, { error: "from key required" });
    if (from.length > 200 || into.length > 200) return reply(400, { error: "key too long" });
    if (action === "merge" && !into) return reply(400, { error: "into key required" });
    if (action === "merge" && from === into) return reply(400, { error: "cannot merge a key onto itself" });
    if (!["merge", "unmerge"].includes(action)) return reply(400, { error: "action must be merge or unmerge" });

    let sha: string | undefined;
    let doc: Record<string, any> = { merge: {} };
    const existing = await gh("GET", "contents/data/ingredient_aliases.json");
    if (existing.ok) {
      const j = await existing.json();
      sha = j.sha;
      try {
        const parsed = JSON.parse(fromB64(j.content));
        // keep the whole doc (e.g. the _comment) — only mutate .merge below
        doc = (parsed && typeof parsed === "object") ? parsed : { merge: {} };
        if (!doc.merge || typeof doc.merge !== "object") doc.merge = {};
      } catch { /* corrupt/empty -> start fresh */ }
    }
    if (action === "merge") {
      // guard against a 2-step cycle (a->b then b->a)
      if (doc.merge[into] === from) return reply(400, { error: "that merge would create a loop" });
      doc.merge[from] = into;
    } else {
      if (!(from in doc.merge)) return reply(404, { error: "no such merge to undo" });
      delete doc.merge[from];
    }
    const put = await gh("PUT", "contents/data/ingredient_aliases.json", {
      message: `Alias ${action}: ${from}${action === "merge" ? " -> " + into : ""} - ${name}`,
      content: toB64(JSON.stringify(doc, null, 2) + "\n"),
      author: { name, email: user.email },
      ...(sha ? { sha } : {}),
    });
    if (!put.ok) return reply(502, { error: `GitHub ${put.status}`, detail: await put.text() });
    return reply(200, { ok: true, action, from, into: action === "merge" ? into : undefined });
  }

  if (path.endsWith("/prep")) {
    const minutes = Number(body.minutes);
    if (!(minutes > 0) || minutes > 600) return reply(400, { error: "minutes must be 0-600" });
    const whoId = app.employee_id || name;
    const safe = String(product).replace(/"/g, "'");
    const block =
      `- product: "${safe}"\n  who: "${whoId}"\n  who_name: "${name}"\n` +
      `  minutes: ${minutes}\n  recorded_on: ${stamp}\n  recorded_by: "${user.email}"\n`;
    const res = await appendCommit(`data/prep_sessions/${venue}.yaml`, block,
      `Prep: ${safe} ${minutes}min (${venue}) - ${name}`);
    if (!res.ok) return reply(502, { error: `GitHub ${res.status}`, detail: res.detail });
    return reply(200, { ok: true, path: res.path, minutes, who: whoId });
  }

  if (path.endsWith("/pack")) {
    // a chef confirms the pack size of an ingredient the parser couldn't read.
    // Append-only log keyed by purchasable_id; the build takes the latest.
    const id = String(body.id || "").trim();
    const packQty = Number(body.pack_qty);
    const packUnit = String(body.pack_unit || "").trim().toLowerCase();
    if (!id) return reply(400, { error: "id required" });
    if (!(packQty > 0) || packQty > 1e7) return reply(400, { error: "pack_qty must be 0-1e7" });
    if (!["g", "ml", "ea"].includes(packUnit)) return reply(400, { error: "pack_unit must be g, ml or ea" });
    const safe = id.replace(/"/g, "'");
    const block =
      `- id: "${safe}"\n  pack_qty: ${packQty}\n  pack_unit: ${packUnit}\n` +
      `  by: "${name}"\n  on: ${stamp}\n  by_email: "${user.email}"\n`;
    const res = await appendCommit(`data/pack_overrides.yaml`, block,
      `Pack confirm: ${safe} = ${packQty}${packUnit} - ${name}`);
    if (!res.ok) return reply(502, { error: `GitHub ${res.status}`, detail: res.detail });
    return reply(200, { ok: true, id, pack_qty: packQty, pack_unit: packUnit });
  }

  const { yaml } = body;
  if (!yaml) return reply(400, { error: "yaml required" });
  // Stamp the save with the day it was made. Two blocks for one product are
  // normal — a chef fixing a typo writes a second one — and without a date the
  // reader had only file position to tell which is live. modules/recipes/cost.py
  // says it plainly: "the builder should stamp effective_from on every save, so
  // a correction supersedes explicitly instead of relying on the order of lines
  // in a file". Guarded first, then stamped, so a refusal never depends on
  // something this endpoint added.
  const dated = stampEffectiveFrom(String(yaml).trim(), stamp);
  const block = `\n# ${product} - entered by ${name} (${user.email}) on ${stamp}\n${dated}\n`;
  const res = await appendCommit(
    `data/recipes/${venue}.yaml`,
    block,
    `Recipe: ${product} (${venue}) - ${name}`,
    (current) => {
      const v = checkRecipeSave(current, String(yaml), String(product));
      return v.ok ? null : (v.error || "refused by a save guard");
    },
  );
  if (!res.ok) {
    // A guard refusal is the chef's to fix, so it comes back as a 400 with the
    // reason rather than a 502 that reads like the server broke.
    if (res.guard) return reply(400, { error: res.guard });
    return reply(502, { error: `GitHub ${res.status}`, detail: res.detail });
  }
  return reply(200, { ok: true, path: res.path, committed_as: name });
});
