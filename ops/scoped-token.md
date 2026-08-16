# Scoped token for the support session (Zak, one-time)

The support session must be able to **re-run pipelines** but **not push code**. Give it
a fine-grained GitHub token scoped exactly to that — and never give it your push PAT.

## Create it
GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained
tokens → Generate new token**:
- **Resource owner:** your account/org · **Repository access:** only
  `mari-daily-reporting`.
- **Repository permissions:**
  - **Actions: Read and write** — lets it re-run a failed job / dispatch a pull.
  - **Contents: Read-only** — can read code, **cannot push**.
  - **Metadata: Read-only** (auto).
  - Everything else: **No access** — especially *Secrets*, *Administration*,
    *Workflows*, *Environments*, *Webhooks* = none.
- **Expiry:** 90 days (rotate on a reminder).

## Give it to the support session (not your push token)
On the management account/machine, authenticate the CLI with THIS token:

    echo "<the-fine-grained-token>" | gh auth login --with-token

Confirm the boundary — both of these should be true in the support session:

    gh run list -R stowaway-hospitality/mari-daily-reporting        # works (read)
    gh run rerun <id> -R stowaway-hospitality/mari-daily-reporting  # works (re-run)
    git push                                               # FAILS (no contents:write)

## Notes
- This is belt-and-braces with the deny-list in `support-agent/settings.json`: even if
  the guard hook were bypassed, the token still can't push.
- Keep this token out of the repo and out of `.secrets/**`; it lives only in that
  account's `gh` auth. Rotate it if a manager leaves.
