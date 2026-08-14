"""The panel must show a failing job.

WHY: scripts/alert_check.py has always detected failed Actions runs, retried the
safe ones and escalated the rest. Every OTHER thing it escalates already has a
home on the home-page health panel — sales behind is "Sales completeness", a
narrowed STOW export is "Pull integrity", and a stale snapshot is caught in the
browser from the snapshot's own timestamp, so it does not depend on the Mac to
report its own death. Failed workflow runs were the one class with nowhere on
screen to land, so they lived and died in a workflow log.

Alerting here is deliberately on-screen (Zak, 2026-08-09). So the fix for the
last uncovered class is a check on the panel, not another channel.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.health_monitor as hm


def _run(name, path, conclusion, hours_ago=1):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"name": name, "path": f".github/workflows/{path}", "conclusion": conclusion,
            "updated_at": when, "html_url": f"https://github.com/x/y/actions/runs/1"}


class FakeHTTP:
    def __init__(self, runs):
        self.payload = json.dumps({"workflow_runs": runs}).encode()

    def __call__(self, req, timeout=None):
        outer = self

        class R:
            def read(self):
                return outer.payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()


def _with(monkeypatch, runs, token="t"):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", FakeHTTP(runs))
    if token:
        monkeypatch.setenv("GH_TOKEN", token)
    else:
        for v in ("GH_TOKEN", "GITHUB_TOKEN", "GH_DISPATCH_PAT"):
            monkeypatch.delenv(v, raising=False)
        # ...and the PAT on disk, or this asserts nothing on the office Mac, which
        # is the only machine that has one. It failed there for days while passing
        # in CI — a test that only holds where the thing it guards cannot happen.
        monkeypatch.setattr(hm, "_pat_candidates", lambda: ())
    return hm._workflow_failures()


def test_all_green_reads_ok(monkeypatch):
    r = _with(monkeypatch, [_run("Daily Pull", "daily_pull.yml", "success"),
                            _run("Tests", "tests.yml", "success")])
    assert r["status"] == "ok"


def test_a_failing_critical_job_reads_down_and_names_it(monkeypatch):
    r = _with(monkeypatch, [_run("Daily Pull", "daily_pull.yml", "failure"),
                            _run("Tests", "tests.yml", "success")])
    assert r["status"] == "down", "a broken daily pull must not read as a minor issue"
    assert "Daily Pull" in r["detail"]
    assert r["action"] and r["meaning"], "the panel needs plain-English guidance, not just a status"


def test_a_failing_non_critical_job_is_only_a_warn(monkeypatch):
    """A monitor that cries wolf gets ignored, and then a real outage goes unseen."""
    r = _with(monkeypatch, [_run("Alias suggest", "alias_suggest.yml", "failure")])
    assert r["status"] == "warn"


def test_a_job_that_failed_then_recovered_is_not_reported(monkeypatch):
    """Judged on the LATEST run per workflow. Reporting an already-retried failure
    is exactly the noise that trains people to stop reading the panel."""
    r = _with(monkeypatch, [_run("Daily Pull", "daily_pull.yml", "success", hours_ago=1),
                            _run("Daily Pull", "daily_pull.yml", "failure", hours_ago=5)])
    assert r["status"] == "ok", "an old failure that has since succeeded was reported as live"


def test_stale_failures_outside_the_window_are_ignored(monkeypatch):
    r = _with(monkeypatch, [_run("Daily Pull", "daily_pull.yml", "failure", hours_ago=200)])
    assert r["status"] == "ok"


def test_no_token_is_unknown_not_a_false_all_clear(monkeypatch):
    """CI has no PAT. 'unknown' is honest; 'ok' would be a lie that hides outages."""
    r = _with(monkeypatch, [], token=None)
    assert r["status"] == "unknown"
    assert "token" in r["detail"].lower()


def test_a_broken_api_never_crashes_the_snapshot(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise RuntimeError("GitHub is down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setenv("GH_TOKEN", "t")
    assert hm._workflow_failures()["status"] == "unknown"
