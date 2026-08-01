#!/usr/bin/env python3
"""PreToolUse guardrail for the management SUPPORT agent — HARDENED.

Runs before every Bash / file-write tool call in a support session (see
settings.json) and HARD-BLOCKS actions that could cause irreversible harm,
regardless of what the user asked or what a poisoned log/email/page tried to
steer the model into. Fails CLOSED (prints a deny decision AND exits 2).

Two layers here work together:
  * coarse regexes over the FULL command — catch dangerous strings even when
    embedded (python -c "... git push", curl | bash, base64 | sh);
  * a git-subcommand parser — catches option-prefixed evasions the regex can't
    (`git -C /x push`, `git --git-dir=… push`, `GIT_DIR=… git push`) WITHOUT
    false-positiving on safe flags like `git pull --rebase`.

This guard is one wall of several: the support session also holds a read+re-run
token (no code-write) and `main` is branch-protected, so even a guard bypass
can't reach production. Battle-tested by ops/support-agent/test_guard.py.
"""
import json
import os
import re
import shlex
import sys

PROTECTED_PATH = re.compile(r"(^|/)\.secrets(/|$)|(^|/)\.github/workflows/|(^|/)\.git/(config|hooks)")

# mutating/destructive git subcommands a support session must never run
DANGER_GIT = {
    "push", "commit", "reset", "rebase", "merge", "revert", "cherry-pick",
    "clean", "restore", "checkout", "switch", "branch", "stash", "remote",
    "config", "tag", "filter-branch", "filter-repo", "update-ref", "reflog",
    "am", "apply", "gc", "prune", "worktree", "submodule",
}

# coarse patterns matched against the FULL command (case-insensitive)
BASH_BLOCK = [
    (r"\bgit\s+(push|commit|reset|rebase|merge|revert|clean|restore|checkout|switch|branch|stash|remote|config|tag|filter-branch|filter-repo|update-ref|reflog|am|apply|gc|prune|worktree|submodule)\b", "a mutating/destructive git command"),
    (r"\bcherry-pick\b", "a mutating git command"),
    (r"--force\b|--hard\b|--no-verify\b", "a --force / bypass flag"),
    (r"\brm\s+\S", "a file delete (rm)"),
    (r"\brmdir\b|\bunlink\b|\bshred\b", "a file delete"),
    (r"\.secrets\b", "touching the secrets folder"),
    (r"(^|/|\s)\.github/workflows/", "touching a workflow file"),
    (r"\.git/(config|hooks)\b", "touching git internals"),
    (r"\bsudo\b|\bdoas\b", "a privilege escalation"),
    (r"\bchmod\b|\bchown\b|\bchgrp\b", "changing permissions/ownership"),
    (r"\blaunchctl\b|\bcrontab\b", "changing scheduled jobs"),
    (r"\bkillall\b|\bpkill\b|\bkill\s+-9\b", "killing processes"),
    (r"\bgh\s+secret\b", "a GitHub secret operation"),
    (r"\bgh\s+api\b", "a raw GitHub API call"),
    (r"\bgh\s+(repo|workflow)\s+(delete|disable|rename|archive|edit|transfer|create|fork|sync)\b", "a repo/workflow mutation"),
    (r"\|\s*(sudo\s+)?(\S*/)?(bash|zsh|ash|dash|ksh|csh|fish|sh|ssh|python3?|perl|ruby|node|php)\b", "piping into a shell/interpreter"),
    (r"\bdd\s+if=|\bmkfs\b|\b>\s*/dev/|\bdiskutil\b", "a raw disk operation"),
    (r">\s*\S*\.env\b|xero_token|graph_app_secret|service_role|github_pat|app_password", "writing near a credential"),
]


def git_subcommand(tokens):
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens) or os.path.basename(tokens[i]) != "git":
        return None
    i += 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return None


def git_danger(cmd):
    for seg in re.split(r"&&|\|\||[;\n|]", cmd):
        try:
            toks = shlex.split(seg)
        except Exception:
            continue
        for start in range(len(toks)):
            sub = git_subcommand(toks[start:])
            if sub and sub in DANGER_GIT:
                return True
    return False


def deny(reason):
    msg = ("Blocked by the support-mode guardrail: " + reason + ". This is reserved "
           "for Zak. Diagnose and do reversible fixes (re-run a pull, set a role); "
           "propose any code change as a diff for Zak to apply. See ops/support-agent/SUPPORT.md.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": msg}}))
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    raw = sys.stdin.read()
    try:
        d = json.loads(raw)
    except Exception:
        sys.exit(0)
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
        if git_danger(cmd):
            deny("a mutating/destructive git command")
    sys.exit(0)


if __name__ == "__main__":
    main()
