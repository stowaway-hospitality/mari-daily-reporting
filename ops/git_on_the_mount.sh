#!/usr/bin/env bash
# Git that works on the Cowork mount, where unlink(2) is forbidden.
#
# THE CONSTRAINT
# --------------
# /Users/Shared/ClaudeShared/... is mounted such that files can be CREATED and
# RENAMED but never REMOVED ("Operation not permitted"). Git assumes it can
# delete, so:
#
#   commit / push / add / fetch   WORK      (they only write and rename)
#   checkout / reset / merge      IMPOSSIBLE (they must remove files)
#   clearing its own *.lock       IMPOSSIBLE (git leaves one behind every run)
#
# The lock is the nasty one: every git command that touches the index or a ref
# leaves a .lock it cannot delete, so the NEXT command dies with "File exists.
# Another git process seems to be running". It is not another process. It is
# the mount.
#
# WHAT NOT TO DO (learned the hard way, 2026-08-08)
# -------------------------------------------------
# Do NOT rename locks in place — `mv refs/…/branch.lock refs/…/branch.stale-N`
# leaves the junk INSIDE .git/refs/, where git reads it as a ref and every
# later fetch dies with "fatal: bad object refs/remotes/origin/….stale-N".
# 43 of those accumulated before anyone noticed. Quarantine OUTSIDE refs/.
#
# USAGE
#   . ops/git_on_the_mount.sh     # source it, then:
#   unlock                        # clear stale locks safely
#   g <any git args>              # git, with locks cleared first and after
#   sandbox_merge <branch> <into> # do what the mount cannot: merge + push
#
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JUNK="$REPO/.git/_lockjunk"

unlock() {
  mkdir -p "$JUNK"
  find "$REPO/.git" -name "*.lock" -type f 2>/dev/null | while read -r f; do
    # move OUT of .git/refs — never rename in place. See above.
    mv "$f" "$JUNK/$(basename "$f").$RANDOM" 2>/dev/null
  done
}

g() { unlock; git -C "$REPO" "$@"; local rc=$?; unlock; return $rc; }

_cred() {
  local p="$REPO/.secrets/github_pat_v2.txt"
  [ -r "$p" ] || { echo "no PAT at $p" >&2; return 1; }
  # The credential helper in .git/config reads a HOST path that does not exist
  # inside the sandbox, so supply the token explicitly for this invocation.
  printf '!f() { echo username=zakstowaway; echo "password=$(tr -d "\\n" < %q)"; }; f' "$p"
}

# push from the mount — this DOES work, it just needs the locks cleared
gpush() {
  local br="${1:-$(git -C "$REPO" rev-parse --abbrev-ref HEAD)}"
  unlock
  git -C "$REPO" -c credential.helper= -c credential.helper="$(_cred)" push origin "$br"
  unlock
}

# THE ESCAPE HATCH: anything that must remove files happens in a real
# filesystem, then comes back over the wire. This is how the COGS audit was
# merged to main on 2026-08-08 after checkout/reset/merge all failed here.
sandbox_merge() {
  local from="${1:?branch to merge}" into="${2:-main}" msg="${3:-Merge $1 into $2}"
  local tmp; tmp="$(mktemp -d)"
  local tok; tok="$(tr -d '\n' < "$REPO/.secrets/github_pat_v2.txt")"
  git clone -q --no-local "https://zakstowaway:${tok}@github.com/zakstowaway/mari-daily-reporting.git" "$tmp" || return 1
  ( cd "$tmp" \
    && git config user.name zakstowaway && git config user.email zak@stowawaybar.com \
    && git checkout -q "$into" \
    && git merge --no-ff "origin/$from" -m "$msg" \
    && python3 -m pytest -q \
    && python3 scripts/arch_guard.py >/dev/null \
    && python3 scripts/build_site.py >/dev/null \
    && git push -q origin "$into" \
    && echo "merged $from -> $into : $(git rev-parse --short HEAD)" )
  local rc=$?
  [ $rc -eq 0 ] || echo "sandbox_merge FAILED (nothing pushed); clone kept at $tmp" >&2
  return $rc
}
