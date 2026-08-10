#!/usr/bin/env python3
"""Loud-failure notifier — sends a short alert to a shared channel so the team
learns a pipeline is broken before a customer does.

Three backends. The first ALWAYS works in CI; the other two are optional and
inert if unconfigured (safe to ship before anyone sets them up):
  * GitHub issue — the fallback of last resort, and the only one that needs no
    new secret: it reuses GH_DISPATCH_PAT (already set) or the workflow's own
    GITHUB_TOKEN. WHY it exists: ALERT_EMAIL and ALERT_WEBHOOK are both unset,
    so before 2026-08-10 notify() was a NO-OP and every escalation
    scripts/alert_check.py raised — including "the health snapshot is stale, the
    office Mac may be down" — printed to a workflow log nobody opens and then
    vanished. That is the same failure this repo already paid for once with
    data/uber_pull.log: eleven consecutive runs detected the 33%-estimate drift,
    wrote it to a log, and the numbers stayed wrong for four weeks. A guard that
    logs is not a guard. An issue is visible, persistent and de-duplicated.
  * Email   — set ALERT_EMAIL (comma-separated recipients). Sends via the same
    Gmail the ingest already uses: GMAIL_ADDRESS + GMAIL_APP_PASSWORD (an app
    password, SMTP over TLS). No new account, no admin consent.
  * Webhook — set ALERT_WEBHOOK to a Slack or Teams incoming-webhook URL to post
    there as well.

If neither is set, notify() is a no-op returning False — nothing breaks. Never
raises into the caller; returns True if at least one backend accepted.

CLI:  python3 scripts/notify.py "subject" "body text"
"""
import json
import os
import smtplib
import ssl
import sys
import urllib.request
from email.message import EmailMessage


def _email(subject: str, body: str) -> bool:
    to = [a.strip() for a in os.environ.get("ALERT_EMAIL", "").split(",") if a.strip()]
    user = os.environ.get("GMAIL_ADDRESS")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not (to and user and pw):
        return False
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg["Subject"] = f"[Stowaway platform] {subject}"
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)
    return True


def _webhook(subject: str, body: str) -> bool:
    url = os.environ.get("ALERT_WEBHOOK")
    if not url:
        return False
    payload = {"text": f"*{subject}*\n{body}"}  # Slack & classic Teams both accept {text}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    return True


ISSUE_MARKER = "<!-- stowaway-auto-alert -->"


def _gh(path, method="GET", body=None, token=None):
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "stowaway-notify"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or "null")


def _issue(subject: str, body: str) -> bool:
    """Open (or comment on) a GitHub issue in this repo.

    De-duplicated by title: a recurring problem gets ONE issue with a comment per
    occurrence, not a new issue every three hours. Closing the issue by hand is
    how you acknowledge it; if the problem is still there the next run reopens it
    and says so, so acknowledging without fixing does not silence it.
    """
    token = (os.environ.get("GH_DISPATCH_PAT") or os.environ.get("GH_TOKEN")
             or os.environ.get("GITHUB_TOKEN"))
    repo = os.environ.get("GITHUB_REPOSITORY", "zakstowaway/mari-daily-reporting")
    if not token:
        return False
    title = f"[auto] {subject}"
    found = None
    for state in ("open", "closed"):
        for it in (_gh(f"/repos/{repo}/issues?state={state}&per_page=50", token=token) or []):
            if it.get("title") == title and "pull_request" not in it:
                found = it
                break
        if found:
            break
    stamp = f"{ISSUE_MARKER}\n\n{body}"
    if found is None:
        _gh(f"/repos/{repo}/issues", "POST", {"title": title, "body": stamp}, token=token)
        return True
    num = found["number"]
    if found.get("state") == "closed":
        _gh(f"/repos/{repo}/issues/{num}", "PATCH", {"state": "open"}, token=token)
        stamp = (f"{ISSUE_MARKER}\n\nReopened — this was closed but the condition is "
                 f"still present.\n\n{body}")
    _gh(f"/repos/{repo}/issues/{num}/comments", "POST", {"body": stamp}, token=token)
    return True


def notify(subject: str, body: str) -> bool:
    ok = False
    for fn in (_webhook, _email, _issue):
        try:
            ok = fn(subject, body) or ok
        except Exception as e:  # never let alerting crash the caller
            print(f"notify: {fn.__name__} failed: {e}", file=sys.stderr)
    if not ok:
        print("notify: NO CHANNEL ACCEPTED — not even the GitHub-issue fallback, which means "
              "no token was in the environment. This alert has been lost; set ALERT_EMAIL or "
              "ALERT_WEBHOOK, or make sure GH_DISPATCH_PAT/GITHUB_TOKEN reaches this step.",
              file=sys.stderr)
    return ok


if __name__ == "__main__":
    sub = sys.argv[1] if len(sys.argv) > 1 else "test alert"
    bod = sys.argv[2] if len(sys.argv) > 2 else "test body"
    notify(sub, bod)
    raise SystemExit(0)
