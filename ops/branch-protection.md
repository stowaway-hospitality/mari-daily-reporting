# Wall 1 — branch-protect `main` (Zak, one-time)

> ## READ THIS FIRST — what is actually possible on THIS repo
>
> Settled by experiment on 2026-08-15 against the live repo, not by reading docs.
>
> **Tier 1 is ON.** Ruleset `main - no force-push, no delete` (`non_fast_forward`
> + `deletion`), enforcement active, NO bypass for anyone including Zak.
> Confirmed safe: nothing in `.github/workflows/` force-pushes or deletes `main`,
> so this cost nothing. `gh api repos/OWNER/REPO/rules/branches/main` lists it.
>
> **Tier 2 (require a PR) CANNOT be made safe while this repo is personal.**
> The bots push with `GITHUB_TOKEN`, i.e. as the GitHub Actions app - visible in
> the commit authors (`par-review-bot`, `Rebuild Wages`, `Roster Pull`). Adding
> that app as a bypass actor is REFUSED on a user-owned repo:
>
>     422  Actor GitHub Actions integration must be part of the ruleset
>          source or owner organization
>
> Only `RepositoryRole` and `DeployKey` are accepted here (both probed and
> confirmed). Neither covers `GITHUB_TOKEN`. So turning on a PR requirement today
> blocks every data commit - the daily pull, ingest, Xero, EatClub, Uber, health -
> which is the silent outage this file has always warned about.
>
> Three ways forward, in order of cost:
> 1. **Stay at Tier 1.** Force-push and deletion are the unrecoverable mistakes;
>    both are blocked. A bad-but-normal commit is revertable.
> 2. **Move the repo to a (free) GitHub organisation.** App bypass becomes legal
>    and Tier 2 works as designed. Note fine-grained PATs then need org approval.
> 3. **Rewire the ~18 pushing workflows to an SSH deploy key** and bypass that.
>    Most work, most risk, no org needed.
>
> One more thing that changes the maths: every Cowork session pushes with ZAK'S
> PAT, so any `RepositoryRole: admin` bypass makes a PR requirement decorative -
> the sessions it is meant to gate would bypass it too.


This is the single highest-leverage guardrail. Once it's on, **no** session — not a
support session, not even Zak's full-power one — can put broken or unreviewed code
live. The worst anything can do is open a pull request that waits for CI and your OK.

## Do it in the GitHub UI (recommended — it lists your real check names)
1. GitHub → the `mari-daily-reporting` repo → **Settings → Branches → Add branch
   ruleset** (or "Add rule" under Branch protection rules).
2. Branch name pattern: `main`.
3. Tick:
   - **Require a pull request before merging** → Require approvals: 1, and
     **Require review from Code Owners** (that's the CODEOWNERS file = you).
   - **Require status checks to pass before merging** → search and select the CI
     checks that this repo runs (the ones from `tests.yml` / the arch + schema
     guards). Also tick **Require branches to be up to date**.
   - **Block force pushes**.
   - **Restrict deletions**.
   - (Optional but recommended) **Do not allow bypassing the above** — applies the
     rules even to admins, so a mistaken direct push is impossible. Leave off if you
     want to keep an emergency direct-push escape hatch for yourself.
4. Save.

## Or via the CLI (advanced)
Fill in your actual status-check names, then:

    gh api -X PUT repos/zakstowaway/mari-daily-reporting/branches/main/protection \
      -H "Accept: application/vnd.github+json" --input - <<'JSON'
    {
      "required_status_checks": { "strict": true, "contexts": ["<CI-check-name>"] },
      "enforce_admins": false,
      "required_pull_request_reviews": { "required_approving_review_count": 1,
        "require_code_owner_reviews": true },
      "restrictions": null,
      "allow_force_pushes": false,
      "allow_deletions": false
    }
    JSON

## After it's on
- Your own workflow changes: work in a branch, open a PR, let CI pass, merge. (Or keep
  the admin escape hatch by leaving "do not allow bypassing" off.)
- The daily automation commits **data** to `main`. If you enforce PRs for *everyone*,
  those data-only bot commits need a carve-out — simplest is to leave `enforce_admins`
  off and let the automation's token bypass, OR scope the ruleset to code paths only
  (protect `dashboard/**`, `scripts/**`, `modules/**`, `core/**`, `.github/**`) so
  routine `data/**` commits still flow. Decide which; I can set the path scoping up.
