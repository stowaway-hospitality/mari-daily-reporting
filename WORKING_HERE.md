# Where this project lives

**Canonical working copy:** `/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting`
(also reachable as `~/Documents/STOW/Sales Reports/Daily Reporting` — symlink, same folder.
ACLs grant both `zak` and `stowaway` full access.)

As of 2026-07-15 this folder is a **real git clone** of `zakstowaway/mari-daily-reporting`.
It had previously drifted ~3 days stale because it was a plain folder with no
git, so nobody could see it had fallen behind. Don't let that happen again:

    git status      # drift is now visible
    git pull        # before you start
    git push        # when you're done

## ⚠️ CRITICAL DATA MODEL — read before touching sales data (2026-07-24)

**All three venues share ONE Lightspeed till — the Stowaway POS.**
- **Marilyna's has NO till of its own.** Her sales ring through the Stow POS.
- Harry Gatos food also rings through the Stow POS.
- The Stow "Sales by Product" export `data/insights_stow_<date>.csv` is the WHOLE
  site — it carries Stow **+ HG + Mari** rows.
- `daily_aggregator.py` splits it by `classify_product()`: Stow keeps its own,
  HG gets `'hg'` rows, **Marilyna's = the `'m'` rows carved off the Stow till**
  (`classify_product(...) == 'm'`). Mari's own export is a CROSS-CHECK only,
  NEVER a source. There is NO separate Marilyna's Kounta login to hunt for.

**Sales enter ONLY via the emailed Insights CSV** (Lightspeed scheduled report →
Pipedream → `repository_dispatch` with `csv_base64`). There is NO sales API pull.

**To fix a day whose Stow export email never fired:**
1. From the logged-in Lightspeed tab, fetch the Stow export:
   `https://my.kounta.com/report/salesummarybyproduct?DateFrom=<d>&DateTo=<d>&CategoryID=0&SiteID=0&TerminalID=0&TabId=week&TypeId=product&tags=&noTax=0&export=true&txtDateFrom=<d>&txtDateTo=<d>`
   (`credentials:'include'`). The aggregator handles this format: no tax column →
   revenue_ex = revenue_inc / 1.1.
2. Ingest through the production path — `repository_dispatch` `stow-csv-arrived`,
   payload `{venue:"stowaway", csv_base64:<b64>}`. This writes the Stow export,
   pulls Deputy, aggregates **stow + hg**.
3. **THEN run the Mari aggregation** so her rows get carved from the same export:
   `workflow_dispatch daily_pull.yml {venue:"marilynas", target_date:"<d>"}`.
   (The stow-csv-arrived run does NOT re-aggregate Mari automatically.)

**The Stow export is a SINGLE POINT OF FAILURE for the whole group.** Sales flow:
Lightspeed scheduled "Sales by Product" email → a Pipedream workflow (NOT in this
repo; separate from `pipedream/uber_direct_ingest.js`) → `repository_dispatch`
(`stow-csv-arrived` / `hg-csv-arrived` / `insights-csv-arrived`=mari). Pipedream
works when an email arrives (HG's fired 2026-07-23); when Stow's doesn't fire,
Mari + part of HG starve too. 2026-07-24: only HG's email came for 23 Jul, so the
Group showed HG-only ($1,685) until I hand-pulled Stow and ingested it. If a day
looks wrong/partial, FIRST check whether the Stow export landed
(`ls data/insights_stow_<date>.csv`); the dashboard now also flags venues
"awaiting import" on the group day view. Root cause when Stow's is missing is
upstream (Lightspeed schedule disabled, or email not reaching Pipedream) — not
the aggregator.

**Closed-week wages/leave = Xero** (Mac-only pull; leave from payslip
LeaveEarningsLines). Owners (Oliver, Bryony) = corp payroll, never on Deputy/venue lines.

### Sales email ingestion — FREE, no Pipedream (2026-07-24)

Pipedream's free tier ran out mid-morning 2026-07-24, so HG's 05:00 email got
through but Stow's 05:30 + Mari's 06:00 didn't — starving the whole group (Mari +
part of HG derive from the Stow export). We do NOT pay $45/mo for an email
forwarder. Replacement: `.github/workflows/ingest_insights_email.yml` +
`scripts/ingest_insights_email.py` — a GitHub Action that reads the Insights
"Daily Sales Auto" emails and fires the SAME `{stow,hg,insights}-csv-arrived`
dispatches the daily pull already consumes. Polls every 20 min in the morning
window, so a late email is caught next run and re-runs are no-ops (only UNSEEN
mail is processed, then marked \Seen).

**Why Gmail, not M365.** This tenant blocks every self-serve Microsoft Graph
path: Zak's account can't register apps (401); user consent is disabled (the
Graph CLI client hits an admin-approval wall, AADSTS65001-style); and the Office
public client isn't preauthorised for Graph (AADSTS65002). So we route the three
Lightspeed schedules to a **dedicated free Gmail** and read THAT over IMAP with a
Google **app password** — no admin anywhere.

**One-time setup:**
1. Create a dedicated Gmail (nobody reads it), e.g. `stowawaysales@gmail.com`.
   Turn on 2-Step Verification, then generate an **App password**
   (myaccount.google.com → Security → App passwords) — 16 chars.
2. Point the three Lightspeed schedules at that Gmail: Insights → Reports →
   "Product sales" → Schedules → each Daily auto → recipient = the Gmail.
3. Repo secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (the 16-char app password),
   `GH_DISPATCH_PAT` (PAT with repo scope — fires repository_dispatch).

Dedupe is the IMAP `\Seen` flag (dedicated inbox, so "unseen" is reliable) — no
token rotation, no ledger. $0, always-on, no admin. Note: M365 IMAP is NOT an
option here (basic-auth/app-passwords disabled by the tenant) — Gmail is.


### Uber Direct fees — FREE, no Pipedream (2026-08-09)

The LAST integration still on Pipedream, and it died with it. Uber Direct
(Mari's own online orders, delivered by Uber's fleet) was billed by a daily
invoice email parsed by `pipedream/uber_direct_ingest.js`, which fired
`uber_direct_dispatch.yml`. When Pipedream's credits ran out on 2026-07-24 the
sales ingest and the auth worker were migrated off; this one was missed. It then
sat dead for **22 days with zero workflow runs and no alert**, because pnl.js
degrades safely — `uberDirectActual` reports `covered:false` and the caller
estimates that slice, so no number was ever wrong. The cost was just never
recorded, and nothing said so.

Replacement: the daily `uber-eats-daily-fees` task now reads the fees from
**direct.uber.com** (Deliveries list, per-order `A$` totals summed by local
date; cancelled orders are A$0.00 and carry no fee). No email, no Pipedream, no
secrets. The portal figures reconciled **to the cent** against all six days the
email path had captured, which is what makes it a safe substitute. $327.44 of
missed fees were recovered on changeover.

`pipedream/uber_direct_ingest.js` and `.github/workflows/uber_direct_dispatch.yml`
are RETIRED but left in place — the workflow still accepts a manual
`workflow_dispatch` if you ever want to key a fee in by hand. Staleness is now
watched by health_monitor's "Uber Direct ingest" check (warn 7d, down 21d),
which is what would have caught this on day seven.

## Auth / admin worker — Supabase Edge Function (2026-07-24, off Pipedream)

The privileged auth worker (list/invite/set-role users, and commit recipes/prep)
used to be a Pipedream HTTP workflow. Pipedream died (out of credits) so the Team
page 400'd ("Is SUPABASE_SERVICE_KEY set?") and chefs couldn't save recipes.

Now it's a **Supabase Edge Function `shg-auth`** (code in `supabase/functions/
shg-auth/index.ts`; deployed on the Supabase project fyqhvyvwbedoowjkrxyj). URL:
`https://fyqhvyvwbedoowjkrxyj.supabase.co/functions/v1/shg-auth`, wired via
`WORKER_URL` in `dashboard/_shared/config.js`. Same routes as before:
`/admin/users|invite|role` (admin) + `/recipes`, `/prep` (kitchen).

- **verify_jwt = OFF** (the function verifies the caller's Supabase token itself
  via /auth/v1/user and must answer CORS preflight). Don't turn it on.
- Admin routes use `SUPABASE_SERVICE_ROLE_KEY` — **auto-injected** by Supabase,
  no secret to set.
- Recipe/prep commits use a `GITHUB_TOKEN` Edge Function secret (the repo PAT),
  set in Supabase -> Edge Functions -> Secrets.
- To change the function: edit the .ts, redeploy via Supabase dashboard
  (Functions -> shg-auth -> Code/Editor) or `supabase functions deploy shg-auth`.
- Old Pipedream code kept for reference at modules/auth/pipedream/auth_component.js
  (NO LONGER USED). Login/signup/reset and invoice approvals already talk to
  Supabase directly and never used this worker.

## Running the tests locally

`/opt/homebrew/bin/python3.12` has pyyaml but not pytest; the Homebrew `python3`
has pytest but not pyyaml. Neither can run the suite alone, which is why it was
only ever green in CI. Use the repo venv:

    python3.12 -m venv .venv                     # once
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m pytest -q                # 371 passed, 3 skipped

Then the same gate CI runs, before you push:

    .venv/bin/python -m pytest -q
    .venv/bin/python modules/recipes/pipeline/build_costs.py   # costs.csv must not change
    .venv/bin/python scripts/convert_lightspeed_recipes.py     # nor the costed book
    .venv/bin/python scripts/arch_guard.py
    .venv/bin/python scripts/build_site.py

`.venv/` is gitignored; CI installs requirements.txt directly and is unaffected.
Do NOT `pip install --break-system-packages` into 3.12 — the 6am cron runs on it.

## Deploying the dashboard (modularised 2026-07-23 — see dashboard/_shared/README.md)

The site is built by `scripts/build_site.py` and served at app.stowawaybar.com:
`/` = `dashboard/home/` (module picker), `/sales/` = the P&L dashboard,
`/recipes/`, `/bookings/`, `/admin/`.

**The sales dashboard is NO LONGER one big index.html.** `dashboard/sales/index.html`
is a ~70KB SHELL (markup + config + bootstrap); ALL logic lives in modules:
`dashboard/_shared/pnl.js` (pure P&L maths), `util.js` (helpers/formatting),
`data.js` (feed loaders), `render.js` (DOM + handlers). **Do not put business
logic in index.html** — `scripts/arch_guard.py` fails CI *and* the deploy if you
do (it also runs 3 JS test suites + the P&L conservation check). `scripts/schema_guard.py`
guards the history CSVs. `reconcile_wages.py` proves every Xero dollar classifies.

**Deploy trap — work in an ISOLATED clone, never this mounted folder.** The cron
(Daily Pull / Rebuild Wages) does `git pull --rebase` on THIS working tree and
will silently clobber in-progress edits mid-session. Pattern: `git clone` to /tmp,
edit + commit + push there, so automation can't stomp you. A push to `dashboard/**`
(or a Daily Pull / Wages Backfill / Rebuild Wages / Roster Pull run) triggers the
`deploy_dashboard` GitHub Action which rebuilds Pages. Data-only commits need a
`dashboard/**` touch OR one of those workflows to redeploy.

The old patch_index_v*.py / push_*.py scripts are archived in
`_archive/patch-scripts-2026-07/` — do not use them.

## Git on the Cowork mount — it cannot delete files (2026-08-08)

`/Users/Shared/ClaudeShared/...` allows CREATE and RENAME but **never `unlink`**.
Everything below follows from that one fact:

| operation | on the mount |
|---|---|
| `add` / `commit` / `push` / `fetch` | **work** (write + rename only) |
| `checkout` / `reset` / `merge` / `stash` | **impossible** — they must remove files |
| clearing git's own `*.lock` | **impossible** — git leaves one behind every run |

The lock is the one that bites. Every command touching the index or a ref leaves
a `.lock` git cannot delete, so the *next* command dies with *"File exists.
Another git process seems to be running."* **It is not another process. It is the
mount.**

    . ops/git_on_the_mount.sh     # then:
    unlock                        # quarantine stale locks
    g <git args>                  # git, locks cleared before and after
    gpush [branch]                # push (supplies the PAT explicitly, see below)
    sandbox_merge <from> <into>   # clone to /tmp, merge, gate, push — the escape hatch

**Do NOT rename locks in place.** `mv refs/…/branch.lock refs/…/branch.stale-N`
leaves the junk *inside* `.git/refs/`, where git reads it as a ref and every later
fetch dies with `fatal: bad object refs/remotes/origin/….stale-N`. 43 of those
accumulated on 2026-08-08 before anyone noticed and it broke fetching entirely.
`unlock` quarantines to `.git/_lockjunk/`, outside `refs/`. Safe to empty that
directory from a real terminal.

**Anything that removes files must happen off the mount.** Clone to `/tmp`, do the
merge/checkout there, run the gate, push, then `git checkout` on the Mac itself
from Terminal. `sandbox_merge` does exactly this; it is how the COGS audit reached
`main`.

**Push auth from a sandbox:** the credential helper in `.git/config` reads a
*host* path (`/Users/Shared/...`) that does not exist inside the sandbox, so a
push there fails with "Invalid username or token" even though the PAT is fine.
Pass it explicitly — `gpush` does.

**Keep the Mac tree on `main`.** If it sits on a feature branch, the invoice
poller, daily pull and wage rebuild all commit *there*, and their data never
reaches the dashboard. The home page's system-status card shouts about this.

## Auth

`git push` authenticates via a credential helper configured in `.git/config`
that reads the PAT from `.secrets/github_pat_v2.txt` (gitignored). The token is
not stored in git config itself. If pushes start failing, check that file first.

Note: git needs a safe.directory exception because the folder is owned by `zak`:

    git config --global --add safe.directory "/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting"

## Known stale copies (do not use)

- `.../local_5ea388ea-.../outputs/push/`  — session scratch, was the only copy
  of v16.4/v16.5 work until 2026-07-15. Now redundant.
- `.../local_5ea388ea-.../outputs/repo/`  — Jul 12 snapshot, no git.
- `../\_daily-reporting-backup-2026-07-15.tgz` — pre-adoption safety snapshot.

## The Lightspeed email reports: what each one must contain

**There is one till.** Stowaway's POS rings up all three brands. Marilyna's has no
till of its own; Harry Gatos food is rung on the Stow till too. Every venue's
"own" CSV is a *filter over the same POS data*. That has one consequence people
keep re-discovering the hard way:

> **Stow's export must stay the FULL SITE report.** It is not "dirty" — two other
> venues read their revenue out of it.

    Stow's export ──┬── 'm'   rows ──► Marilyna's   (coverage guard cross-checks
                    │                                her report against these)
                    └── 'hgf' rows ──► Harry Gatos  (~$585/day, ~$213k/yr,
                                                     concentrated on MONDAYS:
                                                     07-06 $3,233, 07-13 $2,544)

`daily_aggregator.py` **strips both off Stow's own totals** (line ~310). So
narrowing Stow's report to "only Stow RGs" *does not change a single Stow
number* — it just deletes Harry Gatos' Monday revenue and blinds the Mari guard.
It looks like a tidy-up from inside Lightspeed and costs six figures a year in
silence. This was nearly shipped on 2026-07-16. A tripwire now shouts
`STOW EXPORT LOOKS NARROWED` if the export ever arrives with zero cross-venue
rows (Mari rings through Stow every trading day, so zero means the filter moved,
not that nobody ordered pizza).

**Mari's export** (`Mari Daily Sales Auto`) must include `Dine-in Pizza` and
`Add-ons - Pizza`. When it doesn't, Stow strips those rows and Mari never
receives them, so the revenue reaches **no venue at all** — $612.70 on 07-14,
$375.84 on 07-11. The aggregator now recovers them and prints `*** RECOVERED`;
that is a **net, not a repair** — the filter is the fix. The recovery is derived
from the gap, so it goes inert on its own once the filter is right.

**Mari's RG set is deliberately wider** than the weekly-report skill's
`Marilynas-strict` (which excludes Dine-in Pizza). Strict answers "what would we
lose if Mari closed?"; this answers "whose revenue is it?". Both correct. Don't
reconcile them.

## Running the aggregator by hand

    python3 scripts/daily_aggregator.py --venue stowaway 2026-07-14

**The `--venue` flag is required.** Venue is NOT positional — `daily_aggregator.py
stowaway 2026-07-14` silently aggregates *Marilyna's* (the default at line 223)
and looks like it worked. Some older notes have it wrong.

Re-running the aggregator **rewrites `wages_*` from the daily Deputy JSON using
the provisional model**, undoing the Xero-actuals rebuild for any day it touches.
Always follow it with a Rebuild Wages over **whole payroll weeks** (Mon–Sun).

## Wages: how they're costed (2026-07-15 rebuild)

Deputy knows who clocked on. Only Xero knows what they were paid. So:

  * **Closed weeks** — costed from `data/xero_pay_weekly.json` (what payroll
    actually paid), allocated pro-rata across the shifts each person logged.
    Hours decide WHERE the money lands; Xero decides how much.
  * **The open week** — estimated via `scripts/wage_model.py`: a salaried person
    costs annual/52 per week regardless of hours logged. This is an estimate
    standing in for Xero until the pay run posts.

`rebuild_wages.py` runs nightly over the current + previous payroll week. That's
load-bearing, not belt-and-braces: salaried cost is only knowable once a week is
known, and Deputy's Cost lands on APPROVAL (often days later), so re-reading the
fortnight is the only way approvals ever land.

Refresh the Xero side on the Mac (the token rotates, so Actions would burn it):

    python3 scripts/pull_xero_pay_weekly.py     # -> xero_pay_weekly.json + xero_super_weekly.json + xero_leave_weekly.json
    # then dispatch the Employee Map + Rebuild Wages workflows

**Closed-week LEAVE (added 2026-07-24).** `pull_xero_pay_weekly.py` now also writes
`data/xero_leave_weekly.json` from each payslip's LeaveEarningsLines (endpoint is
`/Payslip/{id}` SINGULAR, wrapped in `"Payslip"`; leave $ = NumberOfUnits x
RatePerUnit — there is no Amount field). `rebuild_wages.py` splits that leave OUT
of the venue wage line into `leave_dollars` on the register's leave days, so
"operational wages" excludes leave and the group leave toggle shows what payroll
paid. It is INERT until `xero_leave_weekly.json` exists (no change to any number).
The dashboard's leave figure is $0 until you run the Xero pull for those weeks.

**Do not** use `backfill_wages_deputy.py` or `backfill_dept_split.py` — both are
deprecated and exit immediately. They cost salaried staff at hours x rate.

New salary-earners are caught by `check_salaried_roster.py` (launchd:
com.stowaway.salariedcheck, Mondays 10:40). Owners live in `_corp_payroll_only`
and reach the P&L via the residual precisely because they're absent from Deputy.

## Lightspeed / Insights accounts (IMPORTANT — remember this)

Average-spend / SPH data comes from Lightspeed Insights, spread across **three
separate logins**:

- **`zak@stowawaybar.com` Insights (this is the "group" Lite login).** Has the
  standard **Lite dashboards** (Snapshot, Product sales, etc.) covering the sites
  **"Stowaway Bar"** (= Stow **+ Marilyna's**, shared till) and **"Harry Gatos"**.
  **Does NOT have Custom Insights** — "Build From" is locked with "Upgrade to
  Custom". So from here you can only schedule the standard Snapshot report
  (site-level), not a custom reporting-group explore.
- **Stow + Mari detailed = a DIFFERENT Lightspeed login (has Custom Insights).**
  This is where the reporting-group split comes from that produces the
  `Stowaway` / `Marilynas` / `Marilynas-Uber` lines in `sph_daily.csv`. Custom
  Insights lets you build + schedule a `date × reporting-group × [sales, txns,
  guests]` report. (Login not recorded here — ask Zak / he logs it in.)
- **Harry Gatos = its own separate Kounta account, NO Custom Insights.** Feed HG
  via the free **Snapshot** report scheduled to email.

**Ongoing self-sustaining feed (no Claude task):** Lightspeed scheduled emails →
ingest Gmail (`zakbritton2@gmail.com`) → the GitHub Action that already polls that
inbox parses them → updates `data/sph_daily.csv` → dashboard refreshes. HG via
Snapshot (site-level); Stow+Mari via the Custom-Insights login (reporting-group
split). `sph_daily.csv` venue labels: Stowaway, Marilynas, Marilynas-Uber,
HarryGatos.

## Average-spend (SPH) ongoing feed — SOLVED without extra logins

The Average Spend card reads `data/sph_daily.csv`. History (to 2026-07-05) was
backfilled from the weekly SPH pull. Ongoing days now come **automatically** from
the daily "Daily Sales Auto" emails already arriving in the ingest Gmail — those
ZIPs carry a `sales_by_staff (line added)` grand-total row with `# of Sales`, and
`reporting_groups`/`sales_by_category` per-group counts.

`scripts/sph_from_email.py` (workflow `SPH from Email`, cron 3×/day) reads those
emails over IMAP, extracts transactions + inc-GST sales per venue, and upserts
sph_daily.csv:
  HarryGatos = HG email total · Marilynas = Mari email total ·
  Stowaway (bar) = Stow email total − Mari email total (the Stow email is the
  whole till, incl Marilyna's reporting groups).
Deploy trigger updated so an sph_daily.csv change publishes.

**HG guests / spend-per-head:** the daily product-mix emails have NO guest count,
so the HG "Snapshot" schedule (daily, previous-day, CSV -> ingest Gmail) is the
guest source and must be KEPT (do NOT delete it). `sph_from_email.py` also parses
the Snapshot email (`atv__avg._guest_spend` -> Total Guest Count) and writes
Guests onto the HarryGatos rows. The HG card then leads with spend-per-GUEST
(dine-in metric); Stow/Mari/group lead with per-transaction.

## Stow hourly feed — revived off Pipedream (2026-07-25)

The "Stow Hourly RG Auto" Custom Insights report (a Look: hour × reporting-group,
in the zak.britton@hotmail Custom Insights login → My Reports → Zak B) used to
email a dead `@upload.pipedream.net` address, so the hourly feed stopped 22 Jul.
Fixed WITHOUT Pipedream:
  1. Re-pointed the Look's schedule to email the ingest Gmail (zakbritton2@gmail.com),
     CSV, daily, "1 day ago for 1 day" filter (removed the pipedream recipient).
  2. Poller (ingest_insights_email.py) now routes any "Hourly" subject to
     `stow-hourly-arrived` BEFORE the generic "stow" daily rule — so the hour×RG
     CSV never lands in the daily pipeline. Fires the same dispatch the hourly
     pull already consumes → scripts/eatclub/ingest_hourly.py → stow_hourly_<date>.json.
  3. deploy_dashboard triggers on "Hourly Pull" so it publishes promptly.
Verified end-to-end 2026-07-24 (164 rows, dinner window $9,330 inc-GST, live).

HG hourly is NOT possible: HG's Kounta account has no Custom Insights, so there's
no HG hour×RG report to schedule. Hourly stays Stowaway-only unless HG upgrades.

## Par model v3 — two things that will cost money if you forget them (2026-08-09)

**1. The net stock variance lies. Never read it.**
Lightspeed's stock count export nets the losses against the gains. On the 28 Jul
2026 count the gross NEGATIVE was **-$1,598** and the gross positive **+$1,636**,
so the net printed **+$37** and the count read clean. It was not clean: $1,598 of
stock left the building without a sale (Rooster Rojo -18.8%, San Pellegrino
-19.4%, Coke Zero Can -67%) and an unrelated pile of miscounts happened to cover
it. `modules/par/shrinkage.py` only ever takes `max(0, -Variance)`. If you are
ever eyeballing a count by hand, do the same — sum the negatives on their own.

Related: 16 Stowaway SKUs are flagged `shrinkage_without_demand_mapping` — the
stock counts can see them moving but `products_weekly.csv` shows zero demand for
them. That is a POS product/name mapping gap (Coke Can, Coke Zero Can, Sprite
Can, the 1.25L bottles, the Fever-Tree tonics, St. Germain). Their pars are HELD
at the live value rather than zeroed, but nobody is forecasting them until the
mapping is fixed. Coke Can alone is 3.05 units a week the till never saw.

**2. Christmas 2026 is a 14-day delivery gap, not a long weekend.**
Last normal ILG run **Wed 23 Dec 2026**. The 30 Dec run slips to Fri 1 Jan
(holiday Monday 28 Dec) and Fri 1 Jan is New Year's Day, so it does not happen —
next realistic delivery **Wed 6 Jan 2027**. That is 14 days / 21 weighted
day-units = **2.10× a normal cycle**, over peak summer trade, and it all has to
be on the truck on 23 Dec. The model computes this itself now (no more ×1.3
fudge at order time) and prints it in every weekly par build.
**Late November: collect supplier Christmas cutoffs** into
`data/par_calendar.json → supplierShutdowns`. Until then the 6 Jan resumption is
the optimistic case. Full note: `data/_par_review/christmas_2026.md`.

Also: bookings uplift is computed but **shadow only**
(`modules/par/bookings.py`, `BOOKINGS_LIVE = False`) — the admin endpoints need
a bearer token the build box does not have, and it should be watched for a few
cycles before it moves a par either way.
