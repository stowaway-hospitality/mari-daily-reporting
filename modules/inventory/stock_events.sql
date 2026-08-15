-- Stock events — what a human saw, captured on a phone.
--
-- The same shape as invoice_approvals: a signed-in app writes a row here under
-- row-level security, so THE BROWSER HOLDS NO SECRET, and a poller with the
-- service key reads pending rows and acts on them. Do not invent a second
-- route; that one is proven and its RLS is already understood.
--
-- TWO EVENTS, ONE TABLE. A stocktake line and a goods-received line are the
-- same thing from the ledger's point of view: a person, at a time, in a place,
-- saying how much of something there is. Splitting them would duplicate both
-- the units problem and the scope problem.
--
-- WHAT IS STORED IS WHAT THE PERSON SAID. `counted_qty` + `counted_unit` are
-- the observation — "0.75", "bottle". Conversion to millilitres happens
-- downstream in scripts/ingest_stock_events.py, against a container-size table
-- that CAN BE CORRECTED LATER. If a bottle size turns out to be wrong, every
-- historical event re-derives; had we stored only millilitres, the error would
-- be permanent and invisible.
--
-- Run once in Supabase -> SQL Editor.

create table if not exists public.stock_events (
  id                uuid primary key default gen_random_uuid(),

  kind              text not null check (kind in ('count', 'receive', 'waste', 'transfer')),
  occurred_at       timestamptz not null default now(),
  venue             text not null,            -- who was doing it: stow | hg | mari
  location          text,                     -- Bar & Kegroom | Storeroom - Bar | ...
  item_id           text not null,            -- lightspeed:<id> or <supplier>:<CODE>
  item_name         text,                     -- what the app showed, for the audit trail

  -- The observation, in the unit a human actually uses.
  counted_qty       numeric(12,4) not null,
  counted_unit      text not null,            -- bottle | keg | each | kg | ...

  -- A stocktake SESSION. Several hundred rows share one, and the ledger must
  -- know which locations it covered before any of them may set truth: counting
  -- the bar while stock sits in the storeroom must NOT supersede.
  session_ref       text,
  session_locations text[],                   -- every location this session walked

  -- Goods-received only. Ordered vs turned up is the whole point: the
  -- difference is a supplier credit claim with the evidence attached.
  po_ref            text,
  expected_qty      numeric(12,4),
  supplier_key      text,

  reason            text,                     -- waste: spill | spoil | comp | staff
  note              text,
  actor             text,                     -- who counted it
  actor_email       text,

  status            text not null default 'pending',  -- pending | booked | needs_conversion | rejected
  booked_at         timestamptz,
  ledger_note       text,                     -- why it could not be booked, if it could not

  created_at        timestamptz not null default now()
);

create index if not exists stock_events_pending on public.stock_events (status, occurred_at);
create index if not exists stock_events_session on public.stock_events (session_ref);

alter table public.stock_events enable row level security;

-- Staff may record what they see. The role lives in the JWT's app_metadata,
-- which only the service key can set, so it cannot be spoofed from a browser.
create policy stock_events_staff_insert on public.stock_events
  for insert to authenticated
  with check ((auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'staff'));

create policy stock_events_staff_select on public.stock_events
  for select to authenticated
  using ((auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'staff'));

-- Corrections are NEW ROWS, never edits — the same rule data/ runs on. Someone
-- who miscounts records it again and the ledger takes the later one. Only the
-- poller (service key, bypasses RLS) writes status back.
create policy stock_events_admin_update on public.stock_events
  for update to authenticated
  using ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
  with check ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- No delete policy, deliberately. Nothing here is ever deleted: a wrong count
-- is still a fact about what somebody saw, and the audit trail is the point.
