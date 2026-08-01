# TROUBLESHOOTING — for the management team

**You do not need to understand how the platform is built to use this.** This is a
plain-language guide to the handful of things that actually go wrong, what each one
means, and what you can safely do. When in doubt, the last section tells you what to
**never touch** and when to **call Zak**.

The golden rule: **almost everything here fixes itself or is safe to retry.** The
data is never lost — every day's figures are stored permanently and every change is
reversible. You cannot break the business by following the "safe to do" steps below.

Fastest first move for *any* weirdness on the dashboard: **hard-refresh the page**
(hold Shift and click reload, or Cmd+Shift+R). The site caches for ~10 minutes, so a
stale tab is the #1 cause of "it looks wrong."

---

## Most things now fix themselves

An always-on monitor watches the pipelines and **auto-fixes the safe, reversible
problems** — it re-runs a job that failed transiently, and re-triggers the sales
ingest if a day's data is running late — silently, with nobody needed. You'll only
get an alert when something genuinely needs a human (a supplier-report change in
Lightspeed, an expired login, a code fix). So no alert usually means it either
didn't happen or already self-healed; when you *do* get one, it says what it is.

## How to read the health panel first

Open **app.stowawaybar.com** (signed in as an admin) — the top of the home page
shows **System status**. Green = all good. Amber = something needs attention but
isn't an outage. Red = something's actually down. Each amber/red item now spells out
**what it means and what to do** right there. Check this before doing anything else —
it usually tells you whether to act or just wait.

If the panel itself says the snapshot is hours old, that means the Mac that runs the
background jobs is asleep or off — wake it / turn it on, and it catches up on its own.

---

## The things that actually go wrong

### 1. The dashboard shows old or partial numbers
**What you see:** yesterday isn't there yet, or a venue is blank / says "awaiting import."
**What it usually means:** the sales email from Lightspeed hasn't landed yet. The
system re-checks every 20 minutes through the morning.
**Safe to do:** hard-refresh. If it's before ~10am, just wait — it's still coming.
If it's after 10am and still missing, ask Claude (see the Cowork guide) to
"check whether yesterday's Stow sales export landed and re-run the ingest if not," or
tell Zak. **Nothing is lost** — the day fills in once the email arrives.

### 2. Harry Gatos revenue looks wrong / a Monday is way too low
**What you see:** HG (or the group total) is missing a big chunk, especially on a Monday.
**What it usually means:** this is the one to take seriously. All three venues ring
through **one** till (Stowaway's). If someone "tidied" the Stowaway sales report inside
Lightspeed, Harry Gatos' revenue silently drops out. The health panel flags this as
**"Pull integrity — STOW export narrowed."**
**Safe to do:** don't try to fix it in Lightspeed. **Call Zak, or ask Claude** — the
Stowaway export must be the *full site* report again. Flag it the same day.

### 3. Someone can't log in, or can't see a tool
**What you see:** "not authorised," or a tool tile is missing for them.
**What it usually means:** their account has no role, or the wrong role, set.
**Safe to do:** an admin opens the **Team** page (app.stowawaybar.com/admin) and sets
their role. If the whole login is failing for everyone, that's the auth service —
ask Claude to check it, or tell Zak.

### 4. A chef can't save a recipe / the Team page shows an error
**What it usually means:** the small background service that saves recipes and manages
users has hiccupped.
**Safe to do:** try again in a few minutes. If it persists, ask Claude to
"check the shg-auth service," or tell Zak — it's a known component with a known fix.

### 5. Supplier invoices have stopped flowing
**What you see:** the health panel shows **Invoice poller** amber/red, or new bills
aren't appearing.
**What it usually means:** the Mac that reads the invoice inbox is asleep/off, or its
Xero login expired.
**Safe to do:** make sure the office Mac is on and awake — the poller resumes by
itself. Bills are never lost; they queue. If the panel points at the **Xero token**,
that's a Zak job (re-login to Xero).

### 6. Wages or COGS look wrong or missing
**What it usually means:** the weekly Xero pull is behind (it needs the Mac and Zak's
Xero login), or approvals haven't landed yet. The dashboard keeps showing the last
good week meanwhile.
**Safe to do:** it's rarely urgent. Ask Claude to explain the specific number, or tell
Zak the weekly Xero pull may need re-running.

### 7. The timesheets didn't auto-approve
**What it means:** the 3am auto-approval parks anything unusual for Kris to review — by
design. Parked ≠ broken.
**Safe to do:** Kris's normal weekly review is the backstop. If it clearly didn't run
at all, ask Claude to check the "Deputy Auto-Approve" job.

### 8. The whole site won't load
**What it usually means:** rare — a hosting/DNS blip.
**Safe to do:** hard-refresh, try a different network/device. If it's genuinely down
for everyone for more than a few minutes, call Zak (it's hosting-level, not something
to fix from inside).

---

## The escalation ladder (do these in order)

1. **Look at the System status panel** on the home page — it usually tells you if it's
   self-healing or needs action.
2. **Hard-refresh** the page (Cmd+Shift+R). Fixes most "looks wrong" moments.
3. **Wait, if it's inside a self-heal window** (e.g. a missing morning sales figure
   before ~10am). The panel says when.
4. **Ask Claude (Cowork)** to investigate and fix — see `COWORK_FIRST_RESPONDER.md`.
   This is your main tool. Claude knows the whole system and can safely handle most of
   the list above.
5. **Call Zak** for anything in the "never touch" list below, or anything Claude tells
   you needs him.

---

## Never touch — call Zak (or let Claude escalate to him)

These are the few things where a well-meaning fix can cause real harm. Leave them:

- **Anything inside Lightspeed's sales report settings / schedules** — narrowing the
  Stowaway export drops Harry Gatos revenue (see #2).
- **The code / the repository / GitHub Actions workflows** — don't edit or delete.
  (Re-running a failed job from the Actions page is fine *if* Zak has shown you; nothing
  else.)
- **Any passwords, secrets, API keys or tokens** — GitHub secrets, the Xero login, the
  Microsoft/Graph secret, the Supabase keys. Never enter, change, or share these.
- **Microsoft 365 / Entra admin, DNS, or domain settings.**
- **Deputy's live approval settings** or bulk-editing timesheets outside the normal flow.

For all of these: describe what you're seeing to Zak (a screenshot helps), or ask Claude
— Claude will either handle the safe part or write up exactly what Zak needs to do.

---

## What to tell Zak (or Claude) when you escalate

A good report is three lines: **what you saw** (and where — which page), **when** it
started, and **what you already tried** (hard-refresh, waited, etc.). A screenshot of
the dashboard or the health panel beats a long description. That's enough for Claude or
Zak to pick it up fast.

_Maintained 2026-07-31. If a fix in here turns out to be wrong or out of date, tell Zak
so this file gets corrected — it's the team's shared source of truth._
