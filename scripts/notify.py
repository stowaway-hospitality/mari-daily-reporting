#!/usr/bin/env python3
"""Loud-failure notifier — sends a short alert to a shared channel so the team
learns a pipeline is broken before a customer does.

Two backends, both OPTIONAL and both INERT if unconfigured (safe to ship before
anyone sets it up):
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


def notify(subject: str, body: str) -> bool:
    ok = False
    for fn in (_webhook, _email):
        try:
            ok = fn(subject, body) or ok
        except Exception as e:  # never let alerting crash the caller
            print(f"notify: {fn.__name__} failed: {e}", file=sys.stderr)
    if not ok:
        print("notify: no channel configured (set ALERT_EMAIL or ALERT_WEBHOOK) — skipped")
    return ok


if __name__ == "__main__":
    sub = sys.argv[1] if len(sys.argv) > 1 else "test alert"
    bod = sys.argv[2] if len(sys.argv) > 2 else "test body"
    notify(sub, bod)
    raise SystemExit(0)
