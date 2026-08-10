"""An escalation must never be able to vanish.

WHY this file exists: scripts/alert_check.py runs every 3 hours in Actions and
raises real, specific problems — "sales data is N days behind", "the STOW export
has been NARROWED, Harry Gatos revenue at risk", "the health snapshot is stale,
the office Mac may be down". It hands each one to notify().

Until 2026-08-10 notify() had exactly two backends, email and webhook, and BOTH
were gated on secrets that are not set in this repo (ALERT_EMAIL, ALERT_WEBHOOK
— confirmed absent). So notify() returned False, printed a line to the workflow
log, and the alert was gone. Alerting is deliberately on-screen only (Zak,
2026-08-09), but the on-screen panel is published from the office Mac — so the
one condition nobody could see was the Mac itself being down.

This repo has already paid for this exact mistake once. Eleven consecutive runs
detected the Uber 33%-estimate drift, wrote it to data/uber_pull.log, and the
numbers stayed wrong for four weeks because a log is not an alert.

The GitHub-issue backend needs no new secret, so it cannot be left unconfigured.
These tests hold that property.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import notify as N


class Recorder:
    """Stands in for the GitHub API so the tests never touch the real repo."""

    def __init__(self, existing=None):
        self.existing = existing or []
        self.calls = []

    def __call__(self, path, method="GET", body=None, token=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/issues?" in path:
            state = "open" if "state=open" in path else "closed"
            return [i for i in self.existing if i.get("state") == state]
        return {"number": 1}

    def created_issues(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith("/issues")]

    def comments_on(self, num):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(f"/issues/{num}/comments")]


def _run(monkeypatch, existing=None, token="t"):
    rec = Recorder(existing)
    monkeypatch.setattr(N, "_gh", rec)
    monkeypatch.setenv("GH_DISPATCH_PAT", token) if token else monkeypatch.delenv("GH_DISPATCH_PAT", raising=False)
    for v in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "zakstowaway/mari-daily-reporting")
    return rec


def test_a_fresh_problem_opens_an_issue(monkeypatch):
    rec = _run(monkeypatch)
    assert N._issue("pipeline needs a human", "sales data 3d behind") is True
    made = rec.created_issues()
    assert made, "no issue was created — the alert would have been lost"
    assert made[0][2]["title"] == "[auto] pipeline needs a human"
    assert "sales data 3d behind" in made[0][2]["body"]


def test_a_repeat_comments_instead_of_spawning_a_second_issue(monkeypatch):
    """Every 3 hours forever would bury the real signal under its own noise."""
    rec = _run(monkeypatch, existing=[{"title": "[auto] pipeline needs a human",
                                       "number": 7, "state": "open"}])
    assert N._issue("pipeline needs a human", "still behind") is True
    assert not rec.created_issues(), "opened a duplicate issue instead of commenting"
    assert rec.comments_on(7), "said nothing at all on the existing issue"


def test_closing_the_issue_does_not_silence_a_problem_that_is_still_there(monkeypatch):
    """Acknowledging is not fixing. If the condition persists it must come back."""
    rec = _run(monkeypatch, existing=[{"title": "[auto] pipeline needs a human",
                                       "number": 7, "state": "closed"}])
    assert N._issue("pipeline needs a human", "still behind") is True
    reopened = [c for c in rec.calls if c[0] == "PATCH" and c[2] == {"state": "open"}]
    assert reopened, "a closed issue stayed closed while the problem was still live"
    assert "Reopened" in rec.comments_on(7)[0][2]["body"]


def test_the_fallback_is_reached_when_email_and_webhook_are_unset(monkeypatch):
    """The actual production configuration of this repo: neither secret is set."""
    rec = _run(monkeypatch)
    for v in ("ALERT_EMAIL", "ALERT_WEBHOOK", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    assert N.notify("pipeline needs a human", "the office Mac may be down") is True, (
        "notify() reported failure with no email/webhook configured — this is the "
        "no-op that lost every escalation before 2026-08-10")
    assert rec.created_issues()


def test_notify_never_raises_into_its_caller(monkeypatch):
    """alert_check must finish its run even if alerting itself is broken."""
    def boom(*a, **k):
        raise RuntimeError("GitHub is down")
    monkeypatch.setattr(N, "_gh", boom)
    monkeypatch.setenv("GH_DISPATCH_PAT", "t")
    for v in ("ALERT_EMAIL", "ALERT_WEBHOOK"):
        monkeypatch.delenv(v, raising=False)
    assert N.notify("subject", "body") is False


def test_no_token_is_reported_not_swallowed(monkeypatch, capsys):
    """If even the fallback cannot run, that must be loud on stderr — an alert
    that is silently dropped is how the original bug survived."""
    monkeypatch.setattr(N, "_gh", Recorder())
    for v in ("GH_DISPATCH_PAT", "GH_TOKEN", "GITHUB_TOKEN", "ALERT_EMAIL", "ALERT_WEBHOOK"):
        monkeypatch.delenv(v, raising=False)
    assert N.notify("subject", "body") is False
    assert "NO CHANNEL ACCEPTED" in capsys.readouterr().err
