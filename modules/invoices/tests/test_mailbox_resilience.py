"""
The Review sweep must survive a flaky Graph, and must not dirty the working tree.

WHY THIS FILE EXISTS
--------------------
The Review backlog only shrinks when a sweep RUNS TO COMPLETION, and twice in
two days it did not:

  * 2026-08-15 — "Graph 504 UnknownError" while fetching one message's
    attachments, at message 120 of 200. The remaining 80 were never tried.
  * 2026-08-16 — "[Errno 32] Broken pipe" on an attachment fetch, at message 76
    of ~200. ~124 were never tried, on a run whose entire purpose was to work
    the backlog down.

Neither was a bug in the invoice logic. Both were one transient network error
being allowed to end everything after it. These tests pin the two defences:
a bounded retry inside _req, and per-message isolation in the sweep loop.
"""

import io
import sys
import types
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.invoices import pull_mailbox as pm  # noqa: E402


def _http_error(code, body=b'{"error":"boom"}', headers=None):
    return urllib.error.HTTPError(
        "https://graph.microsoft.com/v1.0/x", code, "err",
        headers or {}, io.BytesIO(body))


class _Resp:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, payload=b'{"value":[]}'):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries must not actually wait during tests."""
    monkeypatch.setattr(pm.time, "sleep", lambda *_: None)


# ── _req: bounded retry on transient failure ────────────────────────────────

def test_a_transient_504_is_retried_and_then_succeeds(monkeypatch):
    # The exact 2026-08-15 failure. One 504 must not become an error.
    calls = []

    def fake(req, timeout=None):
        calls.append(req.get_method())
        if len(calls) == 1:
            raise _http_error(504)
        return _Resp(b'{"ok":true}')

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    assert pm._req("tok", "GET", "/messages") == {"ok": True}
    assert len(calls) == 2, "the 504 should have been retried exactly once"


def test_a_broken_pipe_is_retried_and_then_succeeds(monkeypatch):
    # The exact 2026-08-16 failure: URLError wrapping OSError(32).
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError(OSError(32, "Broken pipe"))
        return _Resp(b'{"ok":true}')

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    assert pm._req("tok", "GET", "/messages") == {"ok": True}
    assert len(calls) == 2


def test_a_timeout_is_retried(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError()
        return _Resp(b"{}")

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    assert pm._req("tok", "GET", "/messages") == {}
    assert len(calls) == 3


def test_retries_are_bounded_and_then_it_fails_loudly(monkeypatch):
    # A genuinely dead endpoint must still fail — the point is bounded retry,
    # not infinite patience. An unbounded retry would recreate the 21-hour wedge
    # this module's timeout exists to prevent.
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(503)

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="Graph 503"):
        pm._req("tok", "GET", "/messages")
    assert len(calls) == pm.GRAPH_TRIES


def test_a_401_is_never_retried(monkeypatch):
    # Deterministic failures must fail fast. Retrying an expired token wastes
    # three timeouts and, worse, buries the one error that tells Zak to re-auth.
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(401, b"token expired")

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="Graph 401"):
        pm._req("tok", "GET", "/messages")
    assert len(calls) == 1, "a 401 is not transient and must not be retried"


def test_a_404_is_never_retried(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(404)

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="Graph 404"):
        pm._req("tok", "GET", "/messages")
    assert len(calls) == 1


def test_post_is_not_retried_because_folder_creation_must_not_double(monkeypatch):
    # The only POST here creates a mail folder. A duplicate "Invoices Review"
    # would split the backlog across two folders — strictly worse than one
    # failed run, so POST is deliberately excluded from GRAPH_RETRY_METHODS.
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        raise _http_error(503)

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError):
        pm._req("tok", "POST", "/mailFolders", {"displayName": "X"})
    assert len(calls) == 1
    assert "POST" not in pm.GRAPH_RETRY_METHODS


def test_patch_is_retried_because_a_move_names_an_absolute_destination(monkeypatch):
    # move_message PATCHes to a named folder, so repeating it cannot compound —
    # moving a message to the folder it is already in is a no-op.
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(502)
        return _Resp(b"{}")

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    assert pm._req("tok", "PATCH", "/messages/1", {"destinationId": "x"}) == {}
    assert len(calls) == 2


def test_throttling_honours_the_retry_after_header(monkeypatch):
    slept = []
    monkeypatch.setattr(pm.time, "sleep", lambda s: slept.append(s))
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, b"slow down", {"Retry-After": "7"})
        return _Resp(b"{}")

    monkeypatch.setattr(pm.urllib.request, "urlopen", fake)
    pm._req("tok", "GET", "/messages")
    assert slept == [7], "Graph told us how long to wait; that must be honoured"


# ── the sweep: one bad message must not end the run ─────────────────────────

def test_one_failing_message_does_not_abandon_the_rest(monkeypatch, capsys):
    # The heart of both incidents. Message 2 of 3 explodes; 1 and 3 must still
    # be processed, and the run must still reach its aggregate step.
    seen = []

    # Signature tracks _handle_message. It gained not_bills_id on 2026-08-17,
    # when statements and credit notes stopped being filed in Review; **kwargs is
    # deliberately NOT used here, so a future argument breaks this loudly rather
    # than letting the stub drift out of step with the thing it stands in for.
    def fake_handle(m, subj, sender, token, args, retry, review_id, processed_id,
                    not_bills_id):
        seen.append(subj)
        if subj == "boom":
            raise RuntimeError("Graph 504 UnknownError")
        return True

    monkeypatch.setattr(pm, "_handle_message", fake_handle)
    monkeypatch.setattr(pm, "get_token", lambda: "tok")
    monkeypatch.setattr(pm, "ensure_folder", lambda *a, **k: "fid")
    monkeypatch.setattr(pm, "messages_with_attachments",
                        lambda *a, **k: [{"id": "1", "subject": "first"},
                                         {"id": "2", "subject": "boom"},
                                         {"id": "3", "subject": "third"}])
    aggregated = []
    monkeypatch.setattr(pm, "aggregate_and_commit", lambda dry: aggregated.append(dry))
    monkeypatch.setattr(sys, "argv", ["pull_mailbox.py", "--no-llm"])

    assert pm.main() == 0
    assert seen == ["first", "boom", "third"], "the sweep stopped at the failure"
    assert aggregated, "a partial sweep must still aggregate what it did rescue"
    out = capsys.readouterr().out
    assert "1 message(s) could not be processed" in out
    assert "boom" in out, "the failing message must be named, not swallowed"


def test_a_failing_message_is_reported_with_its_error(monkeypatch, capsys):
    def fake_handle(*a, **k):
        raise urllib.error.URLError(OSError(32, "Broken pipe"))

    monkeypatch.setattr(pm, "_handle_message", fake_handle)
    monkeypatch.setattr(pm, "get_token", lambda: "tok")
    monkeypatch.setattr(pm, "ensure_folder", lambda *a, **k: "fid")
    monkeypatch.setattr(pm, "messages_with_attachments",
                        lambda *a, **k: [{"id": "1", "subject": "Inalca invoice"}])
    monkeypatch.setattr(pm, "aggregate_and_commit", lambda dry: None)
    monkeypatch.setattr(sys, "argv", ["pull_mailbox.py", "--no-llm"])

    assert pm.main() == 0
    out = capsys.readouterr().out
    assert "Inalca invoice" in out and "Broken pipe" in out


# ── the working tree must stay clean ────────────────────────────────────────

def test_the_poller_no_longer_writes_system_health_into_the_working_tree():
    """
    data/system_health.json is authored by ops/publish_health.py, which builds it
    from a main-pinned clone and PUTs it to main through the Contents API. The
    poller used to ALSO write it locally every 30 minutes and never commit it,
    so `git pull --rebase --autostash` collided on it on essentially every run
    (twice during the 2026-08-16 triage alone, once as a leftover unresolved UU).

    Both sides of every one of those conflicts were stale regenerations of a
    file authored somewhere else entirely, so the local write bought nothing.
    """
    import inspect
    src = inspect.getsource(pm)
    # The comment block explaining the removal is expected; a live call is not.
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "health_monitor" not in code, (
        "the poller is writing data/system_health.json into the shared working "
        "tree again — that is the rebase-conflict source removed on 2026-08-16")
