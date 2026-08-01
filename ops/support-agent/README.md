# The management support agent — a bomb-proof troubleshooting profile

This folder turns a Cowork/Claude session into a **safe troubleshooting responder**
that a non-technical manager can drive without being able to break the platform. It
is the "Wall 2" of a two-wall design (see `../branch-protection.md` for Wall 1).

## Why this exists
A normal Cowork session on the office Mac is powerful: a shell, a push token, deploy
rights. That's fine for Zak, dangerous for a shared responder. This profile removes
the dangerous powers so that **no instruction — however confused, or injected via a
log/email the model reads — can cause irreversible harm.**

## The two walls
1. **`main` is branch-protected** (Wall 1): nothing reaches production without a pull
   request that passes CI. The worst any session can do is *propose* a change.
2. **This profile** (Wall 2): the support session is structurally unable to push,
   deploy, touch secrets, or run destructive commands.
Behind both: the data is append-only and in git history, so everything is reversible.

## What's in here
- `settings.json` — Claude Code settings: permission mode + a deny-list (no push,
  commit, reset, rm, sudo, secret/workflow writes …) + the PreToolUse guard hook.
- `guard.py` — the hook. Runs before every Bash/file-write call and HARD-BLOCKS the
  dangerous patterns (fails closed), even chained or disguised ones. Self-tested.
- `SUPPORT.md` — the behavioural posture the session follows (diagnose + reversible
  fixes; propose code as a diff for Zak; escalate the rest).

## Install it for a management session (Zak, once per machine/account)
1. Put the management user on a **separate account/profile** from yours, on the office
   Mac (or their own signed-in Claude desktop app).
2. Point that session at this settings file — either copy it to the account's
   `~/.claude/settings.json`, or launch with `--settings "<repo>/ops/support-agent/settings.json"`.
   (Do NOT commit it as the repo's shared `.claude/settings.json` — that would also
   constrain your own full-power session.)
3. Give that session **`SUPPORT.md` as its context** (paste it in, or add it to that
   account's memory) so the behavioural posture is loaded too.
4. Give that session the **scoped token** (`../scoped-token.md`) — read + re-run
   workflows only, no code-write — and make sure it does **not** have your push PAT.
5. Turn **computer-use / browser control OFF** for that session unless a task needs it.
6. Confirm the guard runs: in that session, ask it to `git push` — it must refuse with
   the support-mode message.

## The layers, stacked
| layer | stops | enforced by |
|---|---|---|
| branch protection | broken code reaching prod | GitHub (Wall 1) |
| scoped token | pushing / privileged API | GitHub token scopes |
| deny-list + guard hook | destructive shell, secrets, deploys | this profile |
| permissions ON | silent surprising writes | Claude Code prompts |
| append-only + git history | permanent loss | the data model |
| SUPPORT.md posture | drift into risky actions | the model, softly |

No single layer is trusted alone. That's the point.
