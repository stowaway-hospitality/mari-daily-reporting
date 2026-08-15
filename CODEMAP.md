# CODEMAP — how `app.stowawaybar.com` actually works

A guided tour of this repo for a human or an agent landing here cold. Read
`ARCHITECTURE.md` for *why* the shape is what it is, `WORKING_HERE.md` for the
operational gotchas, `MODULES.md` for how modules stay independent, and this file
for *where everything is and how a number gets from a till to the screen*.

> **One-line summary:** a server-less P&L + ops platform for three venues
> (Stowaway `stow`, Harry Gatos `hg`, Marilyna's `mari`). Immutable facts land in
> `data/`, Python under test derives every number in GitHub Actions, and a dumb
> renderer draws it on GitHub Pages. No server. `data/` is the database, the audit
> log, and the API contract at once.

---

## The shape (memorise this)

    Insights CSV ─┐
    supplier PDF ─┼─► dispatcher ─► GitHub Actions ─► data/ ─► GitHub Pages
    Deputy API   ─┤   (email/IMAP    (the ONLY          │        (app.stowawaybar.com)
    Xero API     ─┘    or Graph)      runtime)          └── every commit IS the audit log

- **Actions is the only runtime.** Anything holding a secret runs there.
- **The dashboard has no logic worth testing** — it fetches JSON and draws.
- **All truth is computed upstream, in Python, under test.**

---

## Top-level layout

| path | what it is |
|---|---|
| `core/` | the domain. Depends on nothing; everything may import it. |
| `scripts/` | thin CLI entry points, one job each. Run by workflows or by hand. |
| `modules/` | capabilities that earned a package: `invoices/`, `recipes/`, `auth/`. |
| `dashboard/` | the site source. A shell + shared JS modules + one folder per page. |
| `data/` | THE CONTRACT. Facts, canon, authored files, and derived feeds. The DB. |
| `.github/workflows/` | the runtime. One workflow per pipeline. |
| `baselines/`, `_archive/`, `_site/` | golden snapshots, retired code, build output. |
| root `*.md` | the docs: ARCHITECTURE, MODULES, WORKING_HERE, INVOICES, COGS_ARCHITECTURE, EATCLUB_EXPANSION, HANDOFF_*. |
| `xero_pull.py` | top-level Xero P&L/overheads pull (Mac-run; token rotates). |

---

## `core/` — the domain (imports nothing)

- `venues.py` — THE domain module. Venue keys (`stow`/`hg`/`mari`), OU→department
  maps, `SUPER_RATE`, closed days. 8+ importers. Any function touching a
  Lightspeed ProductID must take a venue or it's a bug (0 shared IDs across venues).
- `domain.py` — identity (`Purchasable` vs `Ingredient`) and `CostSeries.as_of`.
- `pack_overrides.py`, `heartbeat.py` — pack-size overrides; liveness ping.

---

## `scripts/` — grouped by job

**Sales aggregation (the heart)**
- `daily_aggregator.py` — ingests a day's Insights CSV, splits the single Stow
  till into Stow / HG / Mari via `classify_product()`, pulls Deputy, writes the
  daily feeds. **`--venue` is REQUIRED** (positional silently defaults to Mari).
  Carries the Mari coverage guard + `STOW EXPORT LOOKS NARROWED` tripwire.
- `daily_deputy_pull.py`, `roster_pull.py` — Deputy timesheets + rosters (2 payroll weeks).
- `backfill_history.py`, `backfill_mari_food_split.py`, `backfill_mari_rg_split.py` — one-off history rebuilds.

**Wages (Deputy = who; Xero = how much)**
- `wage_model.py` — open-week estimate (salaried = annual/52/week).
- `pull_xero_pay_weekly.py` — Mac-only; writes `xero_pay_weekly.json` + super + leave.
- `rebuild_wages.py` — nightly; closed weeks from Xero actuals, open week estimated.
- `reconcile_wages.py` — proves every Xero dollar classifies. `check_salaried_roster.py` — catches new salary-earners (launchd, Mondays).
- `build_employee_map.py` / `match_xero_to_deputy.py` / `compute_wage_oncosts.py` — identity + on-costs. **DEPRECATED (exit immediately): `backfill_wages_deputy.py`, `backfill_dept_split.py`.**

**Deputy auto-approve**
- `deputy_triage.py` — the 3am approver. Approves the safe bucket, rounds meal
  breaks + start/end to 15 min, PARKS underpay-risk / missed-switch / forgotten
  clock-offs for Kris. `test_deputy_triage.py` (~40 cases). `deputy_break_probe.py`,
  `deputy_triage_eval.py`, `deputy_zero_cost_audit.py`, `deputy_day_dump.py` — read-only learning/audit.

**SPH / hourly / products API**
- `sph_from_email.py` — average-spend feed from the daily emails → `sph_daily.csv`.
- `ingest_insights_email.py` — reads Lightspeed emails (Gmail IMAP or M365 Graph app-only) and fires the `*-csv-arrived` dispatches. Pipedream replacement.
- `graph_mailbox.py` — app-only Microsoft Graph mailbox reader.
- `eatclub/ingest_hourly.py` — the Stow hour×RG feed → `stow_hourly_<date>.json`.
- `build_products_api.py` / `build_products_weekly.py` — build the **Sales Product API** (`dashboard/sales/products/{index,latest,rollup_*}.json` + `SCHEMA.md`).
- `build_products_daily.py` / `backfill_product_mix.py` — the **full daily product mix**: `data/product_mix/<prefix>_<date>.json` (per-day fact, every till line, written by `daily_aggregator.py`) rolled into `data/products_daily.csv`. This is what the stock ledger deducts from — NOT the daily record's `top_products`, which is the 20-row dashboard panel. See `INVENTORY_ARCHITECTURE.md`.

**Dashboard build + guards**
- `build_site.py` — builds `dashboard/` → the Pages site; stamps content-hash `?v=` cache-busts. Has an EXPLICIT page list.
- `arch_guard.py` — fails CI + deploy if business logic lands in `sales/index.html`; runs 3 JS suites + P&L conservation. `schema_guard.py` — guards history CSVs.
- `health_monitor.py` + `pull_integrity.py` — write `data/system_health.json` (feed freshness, pull integrity) for the home page.

**Xero / Uber / misc**
- `xero_reauth.py`, top-level `xero_pull.py` — Xero auth + P&L/overheads.
- `enter_uber_direct.py`, `uber_direct_upsert.py` — Uber Direct fees.
- `manage_users.py`, `build_dept_map.py`, `build_product_map.py`, `new_products.py`, `compute_baseline.py`, `measure_roster_realization.py`.

---

## `modules/` — capabilities that earned a package

- **`invoices/`** — supplier PDF → reconciled cost facts. `run.py` (entry),
  `extract.py` + `parsers/` + `pdf_text.py` (read the PDF), `models.py`,
  `validator.py` + `suppliers.yaml` (rules-as-config, each citing the invoice that
  proves it), `resolve.py` + `pack_size.py` (identity + pack maths), `xero_csv.py`
  / `xero_push.py` / `xero_process_approvals.py` (Xero coding), `graph_auth.py`
  (app-only Graph), `build_cogs_list.py` / `price_compare.py` (cost series + $1+
  increase flags). Writes `data/invoices/*.json` + `data/invoices_review/*.json`.
  Docs: `EXTRACTION.md`, `INVOICES.md`. Knows nothing about Lightspeed or recipes.
- **`recipes/`** — `cost.py`, `labour.py`, `pipeline/` (cost calc), `app/` (chef UI).
  Reads the `ingredients` feed, writes effective-dated `recipes`. See `COGS_ARCHITECTURE.md`.
- **`auth/`** — `set_role.py` + tests; the live privileged worker is the Supabase
  Edge Function `shg-auth` (`supabase/functions/shg-auth/index.ts`). `auth/pipedream/` is retired reference.

---

## `dashboard/` — shell + shared modules + pages

- **`_shared/`** (the ONLY shared code): `pnl.js` (pure P&L maths — WoW, delivery
  breakdown, actualCogs, wage/overhead rates), `render.js` (all DOM + handlers,
  incl. renderWoW, admin-only trend chart), `data.js` (feed loaders), `util.js`
  (format helpers), `feed.js` (fetch + cache-bust + missing-feed handling),
  `auth.js` (one login, six roles), `config.js` (`WORKER_URL` etc.), `tokens.css`
  (design system: `--stow`, `--hg`, `--ink`, `.card`). See `_shared/README.md`.
- **Pages** (each gated via `Auth.gate({roles, onOk})`): `sales/` (the P&L
  dashboard — `index.html` is a ~70KB shell, logic lives in `_shared/`; plus
  `eatclub.html`, `rg.html`, `products/` = the Sales Product API), `home/` (module
  picker, reads `system_health.json`), `recipes/`, `bookings/` (`bookings.js` —
  `roles:null` = open to all), `admin/`, `invoices/`, `pricing/`, `root/` (CNAME, logos).
- Roles: `admin, bigchef, stowfood, hgfood, bar, pizza`. Build list lives in `build_site.py`.

---

## `.github/workflows/` — the runtime

| workflow | runs | writes / does |
|---|---|---|
| `daily_pull.yml` | on `*-csv-arrived` dispatch + cron | aggregate stow+hg, tee integrity |
| `ingest_insights_email.yml` | cron (20-min AM window) | read sales emails → fire dispatches |
| `sph_from_email.yml` | cron 3×/day | update `sph_daily.csv` |
| `hourly_pull.yml` | on `stow-hourly-arrived` | Stow hour×RG feed |
| `rebuild_wages.yml` | nightly | closed+open payroll-week wages |
| `wages_backfill.yml`, `wage_*` , `employee_map.yml` | manual/dispatch | wage history + calibration |
| `deputy_triage.yml` | cron `0 17 * * *` (~3am AEST) | Deputy auto-approve (`DEPUTY_APPROVE=1`) |
| `deputy_break_probe.yml`, `deputy_triage_eval.yml`, `deputy_day_dump.yml` | manual | read-only Deputy learning |
| `invoice_pull.yml`, `invoice_ingest.yml` | dispatch | invoice extraction → costs |
| `roster_pull.yml`, `xero_to_deputy.yml`, `uber_direct_dispatch.yml` | dispatch | rosters / xero match / uber |
| `corp_payroll_audit.yml`, `zero_cost_audit.yml`, `wage_audit.yml` | manual | audits |
| `deploy_dashboard.yml` | push to `dashboard/**` or a data workflow | build + publish Pages |
| `tests.yml` | every push | pytest + JS suites + arch/schema guards |

---

## `data/` — the contract (feeds)

Facts (append-only, never edited): `invoices/`, cost observations, `insights_*` CSVs.
Canon (derived; CI must prove regeneration): `ingredients.json`, `product_map.csv`.
Authored (human, versioned, diff-reviewed): `recipes/{venue}.yaml`.
Derived outputs (safe to rebuild): `{venue}_daily_*.json`, `wages_*`, `sph_daily.csv`,
`stow_hourly_*.json`, `xero_pay_weekly.json` / `xero_leave_weekly.json`,
`xero_overheads_monthly.csv`, `system_health.json`, `deputy_triage.json` /
`deputy_approvals_log.json`, and the Sales Product API under `dashboard/sales/products/`.
**Schema changes are additive-only** — a live app + stale browser tabs read these.

---

## The data flows, end to end

**Sales → dashboard.** Lightspeed emails a "Sales by Product" CSV → lands in the
ingest Gmail (`zakbritton2@gmail.com`, IMAP) or M365 `reports@` (Graph app-only) →
`ingest_insights_email.yml` fires `repository_dispatch` (`stow-csv-arrived` /
`hg-csv-arrived` / `insights-csv-arrived`=Mari / `stow-hourly-arrived`) →
`daily_aggregator.py` splits the one Stow till into the three venues, pulls Deputy,
writes `data/{venue}_daily_*.json` → `deploy_dashboard.yml` rebuilds Pages. **No
sales API pull exists.** The Stow export is the single point of failure — Mari and
part of HG derive from it.

**Wages.** Deputy says who clocked on; Xero says what they were paid.
`pull_xero_pay_weekly.py` (Mac) → `rebuild_wages.py` (nightly) costs closed weeks
from Xero actuals (pro-rata across logged shifts) and the open week from
`wage_model.py`. Owners (Oliver, Bryony) are `_corp_payroll_only` — never on venue
lines. **Never map Deputy id 24 (Oliver).**

**Invoices → COGS.** `modules/invoices/run.py` extracts supplier PDFs → dated cost
observations → `ingredients.json` cost series (`cost_as_of`). Recipes (effective-
dated) × sales × cost-as-of = COGS. Invariant: recomputing any past day is
identical forever. See `ARCHITECTURE.md` Decision 2 and `COGS_ARCHITECTURE.md`.

**Deputy auto-approve.** `deputy_triage.yml` at 3am runs `deputy_triage.py`:
approve safe bucket, round to 15 min, park anything reconcile-worthy for Kris,
NEVER touch underpay-risk (a "no break" comment where the break was deducted).

---

## "I want to change X" — where to look

- **A dashboard number is wrong** → it's computed in `dashboard/_shared/pnl.js`
  (maths) or the feed that fed it in `data/`. Not in `index.html`.
- **Add/adjust a card or chart** → `dashboard/_shared/render.js` +
  `dashboard/sales/index.html` CARD_DEFS. Never put logic in the shell (arch_guard fails).
- **Change how a venue's revenue is split** → `daily_aggregator.py::classify_product()` + `MARILYNAS_RGS`.
- **Wage costing** → `rebuild_wages.py` (closed) / `wage_model.py` (open). Verify with `reconcile_wages.py`.
- **Timesheet approval behaviour** → `scripts/deputy_triage.py` (+ its tests).
- **Invoice parsing / a supplier rule** → `modules/invoices/` (`suppliers.yaml`, `parsers/`, `validator.py`).
- **The Sales Product API** → `scripts/build_products_api.py`; output + schema under `dashboard/sales/products/`.
- **Who can see a page** → the `Auth.gate({roles})` call in that page + `build_site.py`.
- **A pipeline schedule** → the matching `.github/workflows/*.yml`.

---

## Non-negotiable rules (each has a scar — see ARCHITECTURE.md)

1. Money is `Decimal`, never `float`. 2. Every derived number gets a guard whose
test holds real measured numbers. 3. Fail toward review (unresolved = 5 min of a
human; wrong-resolved = a bad cost smeared over a month). 4. Errors that flatter
you (too-high GP, low costs) are the dangerous ones — alarm on good news too.
5. `data/` is append-only for facts; derived files must regenerate in CI or
they're fossils. 6. Config is data (`suppliers.yaml`); code is generic.

## Operational guardrails (see WORKING_HERE.md)

- **Work in an isolated `/tmp` clone**, not the mounted working tree — the cron
  does `git pull --rebase` and will clobber in-progress edits to tracked files.
- **Cache trap:** Pages sends `max-age=600`; hard-refresh after deploy. `build_site.py` stamps `?v=` hashes.
- **Stow export must stay the FULL SITE report** — narrowing it silently deletes HG Mondays + blinds the Mari guard.
- **Python:** `/opt/homebrew/bin/python3.12` (system 3.9 lacks `str | None`); Actions uses 3.11.
- **Push auth:** PAT at `.secrets/github_pat_v2.txt` via the git credential helper.
- **Secrets never touched by the agent:** `GRAPH_CLIENT_SECRET`, Supabase `service_role` key.

_Last mapped: 2026-07-31._
