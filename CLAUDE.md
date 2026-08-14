# CLAUDE.md — read this first

This repo powers **app.stowawaybar.com**, the server-less P&L + operations platform
for Stowaway Hospitality Group (three venues: **Stowaway `stow`**, **Harry Gatos
`hg`**, **Marilyna's `mari`** — Northern Beaches, Sydney). GitHub repo:
`zakstowaway/mari-daily-reporting` (name is historical — it's a 3-venue platform now).

**Start with these docs, in order:**
- `CODEMAP.md` — where everything is and how a number gets from a till to the screen.
- `ARCHITECTURE.md` — the expensive decisions (identity, effective-dated time, dependency direction).
- `MODULES.md` — how modules stay independent (`data/` is the API contract).
- `WORKING_HERE.md` — the operational gotchas that have already cost money.
- `TROUBLESHOOTING.md` / `COWORK_FIRST_RESPONDER.md` — the management team's plain-language guides for triaging platform issues (and using Claude as first responder) without deep knowledge.
- `ops/support-agent/` — the locked-down guardrail profile that lets non-technical management troubleshoot via Claude safely (deny-list + PreToolUse guard); pairs with `ops/branch-protection.md` and `ops/scoped-token.md`.
- `INVOICES.md` / `COGS_ARCHITECTURE.md` / `EATCLUB_EXPANSION.md` — module deep-dives.

## The shape
Immutable facts (Insights CSV, supplier PDFs, Deputy, Xero) → **GitHub Actions**
(the only runtime; secrets live here) → files in **`data/`** → **GitHub Pages**.
No server. `data/` is the database, the audit log, and the API contract at once.
The dashboard fetches JSON and draws — no business logic in `dashboard/*/index.html`.

## The data model everyone re-learns the hard way
All three venues ring through **ONE Lightspeed till (the Stow POS)**. Mari has no
till; HG food rings through Stow too. `daily_aggregator.py::classify_product()`
splits the Stow export into the three venues. **The Stow export must stay the FULL
SITE report** — narrowing it in Lightspeed changes zero Stow numbers but silently
deletes HG's Monday revenue and blinds the Mari coverage guard (~6 figures/yr).

## Non-negotiable rules
- Money is `Decimal`, never `float`. Every derived number gets a guard whose test
  holds real measured numbers. **Fail toward review.** Errors that flatter you
  (too-high GP, low cost) are the dangerous ones.
- `data/` facts are append-only; derived files must regenerate in CI or they're fossils.
- Schema changes are **additive-only** (a live app + stale browser tabs read the feeds).
- Revenue is **ex-GST** everywhere (inc = ×1.1). Weeks are Mon–Sun, labelled by the Sunday.

## How to work here (or you will lose edits / break the live site)
- **Edit in an isolated `/tmp` clone**, never this mounted tree — the cron does
  `git pull --rebase` and clobbers in-progress edits to tracked files.
- `daily_aggregator.py` **requires `--venue`** (positional silently defaults to Mari).
- After any deploy, hard-refresh (Pages caches 10 min); `build_site.py` stamps `?v=` hashes.
- `arch_guard.py` fails CI + deploy if logic leaks into `sales/index.html`.
- Python: `/opt/homebrew/bin/python3.12` (system 3.9 lacks `str | None`); Actions uses 3.11.
- Push auth: git uses the **osxkeychain** helper, NOT a file. The live token is
  `.secrets/github_pat.txt` (expires **2026-10-07**); `.secrets/github_pat_v2.txt`
  is REVOKED despite the newer name — it 401s. If a push fails with "Invalid
  username or token", the keychain is serving the dead one; re-store the live one:
  `printf "protocol=https\nhost=github.com\n\n" | git credential-osxkeychain erase`
  then `printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$(tr -d ' \n\r' < .secrets/github_pat.txt)" | git credential-osxkeychain store`.
  Diagnose which token is in play by comparing `shasum -a 256` of the keychain
  password against each file — never print a token. (Fetch keeps working while
  broken because the repo is public, so a dead token shows up ONLY as failed pushes.)

## Standing constraints (still in force)
- Never handle `GRAPH_CLIENT_SECRET` or the Supabase `service_role` key — Zak pastes those.
- No permanent deletion. Never map **Deputy id 24 (Oliver)**; only Oliver + Bryony
  go to corp payroll (`_corp_payroll_only`), never on venue wage lines.
- The Sales Product API for any product-sales question lives at
  `app.stowawaybar.com/sales/products/{index,latest,rollup_stow,rollup_hg,rollup_mari}.json`
  (built by `scripts/build_products_api.py`). Use it, not a skill or Drive.

_Maintained 2026-08-14._
