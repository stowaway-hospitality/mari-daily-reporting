# Working here with more than one Cowork chat

**If you are a Claude session: read this before you touch anything. It is not
advice, it is the protocol.**

## Why this exists — the failure it prevents

On 2026-08-14 two sessions worked this repo at once. One pushed a commit with two
hunks in `.github/workflows/daily_pull.yml`. The other rebased the same file. The
rebase kept one hunk and dropped the other.

Nothing conflicted. Nothing failed. CI stayed green. The commit was still in the
log — the change just was not in the file. The dropped hunk was the one that made
a sales backfill write to the correct DAY, so three days of missing sales silently
would not heal, and finding it took hours.

**That is the danger: not conflicts — git shouts about those — but SILENT LOSS.**
Everything below exists to catch that one thing.

---

# For Zak — how to run it

## Starting a new chat

Say this:

> Read SESSIONS.md and claim <area>. I want you to <the job>.

Areas: `cost-book`, `inventory`, `sales-pipeline`, `ops`, `dashboard`, `bookings`, `par`.

## Seeing what is running

Ask any chat: **"session status"**. It prints live claims and whether CI is green.
Claims expire after 12 hours, so a dead chat never blocks the repo.

## The rules you enforce

1. **One area per chat.** Two chats in the same area is how the above happened.
2. **`ops` is exclusive.** Workflows, `health_monitor.py`, `build_site.py`,
   `CLAUDE.md`, `costs.csv`, `cogs_list.csv` affect every session. If a chat
   claims `ops`, others should not push at all until it is done.
3. **Never start on a red CI.** If it is already red you cannot tell whose break
   is whose. Ask the chat to find out who broke it first.
4. **Finish before you switch.** A chat that stops mid-area still holds the claim
   until it expires. Tell it "release the claim" when you move on.

## When something looks wrong

Ask: **"verify <file> landed on main"**. If it says DIFFERS, someone rebased over
it — the fix is to re-apply and push again.

---

# For Claude sessions — the protocol

## 1. Before you touch anything

    python3 scripts/session.py status
    python3 scripts/session.py start <area> --who "<one line: what you are doing>"

If it BLOCKS, do not proceed and do not "just be careful". Tell Zak who holds it.

`start` and `end` write the register straight to `main` themselves, so the claim
is visible to every other session the moment you make it. **Do not commit
`ops/session_claims.json` yourself** — an older copy from your clone would
silently un-claim whoever claimed after you. (It used to say "commit it with your
first push". That left a released claim sitting on `main` blocking the repo
within an hour of the tool shipping, because the release never got committed.)

## 2. Work in a /tmp clone. Never the mount.

    git clone --depth 1 https://x-access-token:$PAT@github.com/zakstowaway/mari-daily-reporting.git /tmp/<topic>

`/Users/Shared/ClaudeShared/...` is shared state, cannot delete files, and is
where the cron pulls — editing a tracked file there gets swept into an autostash
mid-rebase and reverts. See WORKING_HERE.md.

## 3. Stay inside your area

The paths for each area are in `scripts/session.py::AREAS`. If the job needs a
file outside it, STOP and ask — do not widen silently. Wanting one file from
another area is usually a sign the change belongs to that area's session.

**Derived files have one owner at a time.** `data/costs.csv` must reproduce
byte-identically and `data/cost_book_flags.json` is rebuilt every run; two
sessions regenerating them from different states will fight. If you do not own
the area, let CI regenerate them.

## 4. Before you push

    git pull --rebase -q origin main
    # then the full gate for what you touched:
    python3 -m pytest -q                 # NOT python3.12 — no pytest there
    node scripts/test_<the ones you touched>.mjs
    python3 scripts/arch_guard.py
    python3 scripts/schema_guard.py

A green suite is not proof if a feed failed to build: check the flags feed exists
(`test -f data/cost_book_flags.json`) or the node tests silently SKIP their
real-data checks and report 0 failures on nothing.

## 5. After you push — ALWAYS

    python3 scripts/session.py verify <every file you changed>

`on main` for all of them, or you have not really shipped. This is the step that
catches the silent rebase loss. **The commit log is not evidence; the file on
origin/main is.**

## 6. When you finish

    python3 scripts/session.py end <area>

...and write a handoff if the work continues (`HANDOFF_<date>_<topic>.md`, in the
style of the existing ones: one job, the blocker stated in full, what is done).

## 7. Never do these

* Rebase a file another session is holding. Squash-merge your own branch instead.
* `git push --force` / `--force-with-lease` on `main`. Ever.
* Regenerate derived data you do not own.
* Edit `.github/workflows/` without the `ops` claim — the 2026-08-14 loss was
  exactly this.
* Leave `main` red at the end of a session without telling Zak.

---

# Branch protection (Zak, one-time, at a keyboard)

`ops/branch-protection.md` has the full steps. **Read the warning there first:**
this repo's bots push to `main` dozens of times a day (daily pull, ingest ledger,
Xero pull, EatClub, Uber, health snapshot). Requiring pull requests without adding
the bot as a **bypass actor** stops all of them — and a silent automation outage
is worse than the problem being solved. Turn it on when you can watch the next
pull run.

_Written 2026-08-15, after the day it would have saved._
