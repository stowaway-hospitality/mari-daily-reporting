#!/usr/bin/env python3
"""Loud-failure notifier — sends a short alert to a shared channel so the team
learns a pipeline is broken before a customer does.

Two backends, both OPTIONAL and both INERT if unconfigured:
  * Email   — set ALERT_EMAIL (comma-separated recipients). Sends via the same
    Gmail the ingest already uses: GMAIL_ADDRESS + GMAIL_APP_PASSWORD (an app
    password, SMTP over TLS). No new account, no admin consent.
  * Webhook — set ALERT_WEBHOOK to a Slack or Teams incoming-webhook URL to post
    there as well.

NEITHER IS SET in this repo, so notify() is currently a no-op — and that is a
deliberate choice, not an oversight (Zak, 2026-08-09): alerting is ON SCREEN, on
the home-page health panel, where a problem is seen and fixed on the spot.

Read this before concluding an alert can vanish. Every condition
scripts/alert_check.py escalates has a home on that panel:
  * a failed workflow run        -> "Automation jobs"      (health_monitor)
  * sales data behind            -> "Sales completeness" / "Daily sales pull"
  * STOW export NARROWED         -> "Pull integrity"
  * the snapshot itself is stale -> the home page computes the snapshot's age in
    the BROWSER from its own timestamp and shows a red "Monitoring" card past
    30h, so it does not rely on the office Mac to report its own death.
A GitHub-issue backend was added here on 2026-08-10 on the mistaken belief that
the last of those was uncovered. It was not. Removed the same day — one surface
that people actually read beats a second channel nobody asked for.

If push alerts are ever wanted, setting ALERT_EMAIL is the whole change; the
Gmail sender secrets already exist.

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
        print("notify: no push channel configured (ALERT_EMAIL / ALERT_WEBHOOK unset) — by "
              "design; this condition is surfaced on the home-page health panel instead. "
              "The full text is written to the job summary by alert_check.log().",
              file=sys.stderr)
    return ok


if __name__ == "__main__":
    sub = sys.argv[1] if len(sys.argv) > 1 else "test alert"
    bod = sys.argv[2] if len(sys.argv) > 2 else "test body"
    notify(sub, bod)
    raise SystemExit(0)
