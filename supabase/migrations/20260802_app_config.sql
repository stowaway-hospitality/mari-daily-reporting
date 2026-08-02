-- app_config: small key/value store for shared runtime config that must NOT live
-- in the public repo/page (the dashboard is public GitHub Pages). Read by
-- authenticated staff only (RLS); written only via the Supabase console/service
-- role. First use: the bookings engine's shared bearer token, so signed-in staff
-- get it automatically instead of pasting it per device.
create table if not exists public.app_config (
  key        text primary key,
  value      text not null,
  updated_at timestamptz not null default now()
);
alter table public.app_config enable row level security;

-- Any signed-in user may READ. No browser write policy exists, so INSERT/UPDATE
-- are only possible with the service role (console/CLI) — the token can never be
-- changed from the browser.
drop policy if exists "authenticated read app_config" on public.app_config;
create policy "authenticated read app_config"
  on public.app_config for select to authenticated using (true);

-- One-time, run with the REAL token in place of the placeholder:
-- insert into public.app_config (key, value)
--   values ('booking_token', 'PASTE_THE_BOOKING_TOKEN_HERE')
--   on conflict (key) do update set value = excluded.value, updated_at = now();
