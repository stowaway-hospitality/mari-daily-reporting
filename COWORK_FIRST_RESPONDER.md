# Using Claude as the platform's first responder

The management team does **not** troubleshoot this platform by reading code. You
troubleshoot it by **describing the problem to Claude in plain English** and letting it
investigate and fix the safe things — or write up exactly what Zak needs to do. Claude
already understands the whole system (it's described in the global instructions and in
the repo's `CLAUDE.md` / `CODEMAP.md`). This is your main tool. `TROUBLESHOOTING.md` is
the quick paper reference; this is the "get it sorted" path.

---

## How to use it

**Where:** open the **Claude desktop app on the office Mac** — that's where the
platform folder is connected and Claude can actually look at logs, the dashboard, and
GitHub. (You can also message it from your phone if **Dispatch** is turned on — see the
safety note at the end.)

**Start a chat and just say what's wrong.** You don't need any special commands. Good
openers:

- "Yesterday's sales aren't showing on the dashboard — can you check the Stow export
  landed and re-run the ingest if it didn't?"
- "Harry Gatos revenue looks way too low for Monday — can you check the pull integrity?"
- "Steph can't log in to bookings — can you check her role?"
- "The System status panel is red on 'Invoice poller' — what's wrong and what should I do?"
- "A chef says they can't save a recipe — can you check the auth service?"

**Give it three things:** what you saw (and which page), when it started, and what you
already tried (hard-refresh, waited). A screenshot helps a lot.

**Let it work, then read the summary.** Claude will either fix the safe class of problem
and tell you what it did, or hand you a clear "here's what's broken and exactly what Zak
needs to do." If it says it needs Zak, forward that summary to him — it's already written up.

---

## What Claude may do on its own vs. what it escalates

**May handle autonomously (all safe / reversible):**
- Read the dashboard, the health snapshot, and the job logs to diagnose.
- Check whether a GitHub Action succeeded or failed, and **re-run a failed pull/ingest**.
- Hand-pull a missing day's Stowaway sales export and re-ingest it through the normal path.
- Diagnose a login/role problem and **set a user's role** on the Team page.
- Explain any number on the dashboard and where it came from.
- Nudge a stalled but safe job back into life; read the logs to confirm it recovered.

**Must escalate to Zak (won't do without him):**
- Editing pipeline **code** and deploying it.
- Anything involving **secrets, tokens, or keys** (GitHub secrets, Xero login, the
  Microsoft/Graph secret, Supabase keys) — Zak handles these himself.
- **Microsoft 365 / Entra admin, DNS, or domain** changes.
- **Lightspeed sales-report schedule/filter** changes (the single-till trap).
- Anything that **deletes data**, force-pushes git, or changes Deputy's live approval config.
- Renaming the repository.

This boundary is deliberate: the "may do" list can't harm the business (data is
append-only and every change is in version history); the "escalate" list is where a
wrong move is expensive, so it stays with Zak.

---

## Least-privilege access checklist (Zak sets this up once)

Give each first-responder manager exactly what they need to see and safely act — no more:

- **Claude access** — they use the Claude desktop app on the office Mac (the shared
  Stowaway account), or their own signed-in account with Dispatch to that Mac. This is
  the one that lets Claude actually investigate.
- **Dashboard admin role** — so they can see the System status panel and use the Team
  page to fix roles. Set on the Team page.
- **GitHub: read + re-run only** — add them to the repo as a member who can *read* and
  *re-run a failed Action*, not admin. They never edit code or see secrets.
- **Shared password vault (break-glass only)** — a sealed entry in the team password
  manager with the couple of emergency logins, for genuine "Zak is unreachable and it's
  urgent" moments. Access is logged; it is not for day-to-day use.

**Do NOT hand out:** GitHub admin, any GitHub/Supabase/M365 secret or key, Microsoft 365
global admin, or the Xero login. If a task needs one of those, it's a Zak task by design.

---

## Copy-paste starting prompts

Keep these somewhere handy (a pinned note, the fridge in the office):

- *"Check app.stowawaybar.com system health and tell me in plain English if anything is
  wrong and what I should do."*
- *"Yesterday's numbers look missing/partial — check whether the Stowaway sales export
  landed and re-run the ingest if needed."*
- *"[Name] can't access [tool] — check and fix their role if that's the issue."*
- *"Something looks off with [venue] [metric] on [date] — investigate and explain."*
- *"Write up what's wrong for Zak — what happened, what you checked, and what he needs
  to do."*

---

## One safety note

If you turn on **Dispatch** (driving the office Mac from your phone), understand that a
phone message can trigger real actions on that computer. Only enable it for people you'd
trust to act on the Mac in person, and keep the "escalate to Zak" boundary above. For
day-to-day, working in the desktop app on the office Mac is the simplest and safest.

_Maintained 2026-07-31._
