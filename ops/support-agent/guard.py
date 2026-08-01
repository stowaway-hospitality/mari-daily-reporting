#!/usr/bin/env python3
"""PreToolUse guardrail for the management SUPPORT agent.

Claude Code runs this before every Bash / file-write tool call in a support
session (see settings.json). It HARD-BLOCKS the actions that could cause
irreversible harm to the platform — regardless of what the user asked or what a
poisoned log/email/web page tried to steer the model into. It fails CLOSED:
on a block it both prints a deny decision AND exits 2, so older and newer Claude
Code versions all stop the call.

This is a safety net, not the only wall: the support session should also hold a
read + re-run token (no code-write) and `main` should be branch-protected, so
even if this guard were bypassed, prod still can't be broken. See
ops/support-agent/README.md.

Allowed (not blocked here): read/inspect anything except secrets, git status/
pull/log/diff, running diagnostic scripts, `gh run rerun` / `gh workflow run`
(re-run a stuck pull — reversible). Blocked: pushing code, rewriting/discarding
git state, deleting, touching secrets or workflow files, sudo/chmod/chown,
launchctl, privileged gh writes, piping the web into a shell.
"""
import json
import re
import sys

PROTECTED_PATH = re.compile(r"(^|/)\.secrets(/|$)|(^|/)\.github/workflows/|(^|/)\.git/(config|hooks)")

# (regex, human reason) — matched against the FULL bash command, case-insensitive,
# so chained forms (`a && git push`, `X=1 git push`) are caught too.
BASH_BLOCK = [
    (r"\bgit\s+push\b", "pushing to the repository"),
    (r"\bgit\s+reset\s+--hard\b", "a hard git reset (discards commits)"),
    (r"\bgit\s+rebase\b", "a git rebase (rewrites history)"),
    (r"\bgit\s+clean\b", "git clean (deletes untracked files)"),
    (r"\bgit\s+restore\b", "git restore (discards changes)"),
    (r"\bgit\s+checkout\s+(--|\.)", "discarding working changes"),
    (r"\bgit\s+branch\s+-D\b", "force-deleting a branch"),
    (r"--force\b|--hard\b|--no-verify\b", "a --force / bypass flag"),
    (r"\brm\s+-[a-z]*[rf]", "a recursive/forced delete"),
    (r"\.secrets\b", "touching the secrets folder"),
    (r"\bsudo\b", "a sudo command"),
    (r"\bchmod\b|\bchown\b", "changing file permissions/ownership"),
    (r"\blaunchctl\b|\bcrontab\b", "changing scheduled jobs"),
    (r"\bkillall\b|\bpkill\b", "killing processes"),
    (r"\bgh\s+secret\b", "a GitHub secret operation"),
    (r"\bgh\s+api\b[^\n]*-X\s*(POST|PUT|PATCH|DELETE)", "a privileged GitHub API write"),
    (r"\bgh\s+(repo|workflow)\s+(delete|disable)\b", "deleting/disabling a repo or workflow"),
    (r"\|\s*(sudo\s+)?(bash|sh|zsh)\b", "piping input into a shell"),
    (r"\bcurl\b[^\n]*\|\s*(bash|sh)\b", "piping the web into a shell"),
    (r"\bdd\s+if=|\bmkfs\b|\b>\s*/dev/", "a raw disk operation"),
    (r">\s*\S*\.env\b|xero_token|graph_app_secret|service_role", "writing near a credential"),
]


def deny(reason: str):
    msg = ("Blocked by the support-mode guardrail: " + reason + ". "
           "This is reserved for Zak. In support mode you diagnose and do reversible "
           "fixes (re-run a pull, set a role); propose any code change as a diff for Zak "
           "to apply — never push it. See ops/support-agent/SUPPORT.md.")
    # newer Claude Code: structured deny on stdout
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": msg}}))
    # older Claude Code: exit 2 + stderr also blocks. Fail closed.
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    raw = sys.stdin.read()
    try:
        d = json.loads(raw)
    except Exception:
        sys.exit(0)  # can't parse -> don't interfere
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""
        if path and PROTECTED_PATH.search(path):
            deny("writing to a protected file (" + path + ")")

    if tool == "Bash":
        cmd = ti.get("command", "") or ""
        for pat, reason in BASH_BLOCK:
            if re.search(pat, cmd, re.I):
                deny(reason)

    sys.exit(0)


if __name__ == "__main__":
    main()
