# Wall 1 — branch-protect `main` (Zak, one-time)

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
