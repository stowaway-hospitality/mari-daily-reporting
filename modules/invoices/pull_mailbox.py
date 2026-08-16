#!/usr/bin/env python3
"""
Capture supplier invoices from the accounts@stowawaybar.com mailbox — the
Dext-free ingestion step.

    python3 modules/invoices/pull_mailbox.py            # process new invoices
    python3 modules/invoices/pull_mailbox.py --dry-run  # list only, no changes

FLOW
  Graph (accounts@ inbox, token auth)  ->  every message with a PDF
    ->  run.py (extract via ANTHROPIC_API_KEY, then validate)
    ->  PASS   -> data/invoices/         + move email to "Invoices Processed"
    ->  REVIEW -> data/invoices_review/  + move email to "Invoices Review"
    ->  build_cogs_list + build_costs    ->  git commit

WHY A DEDICATED INBOX, NOT A MAIL RULE
  Suppliers send to accounts@. We process EVERY PDF and let the validator decide
  what's a real invoice (it must reconcile to the printed total). No sender
  matching to rot silently; anything that isn't a clean invoice lands in the
  visible Review folder. Moving each message out of the inbox is the "done"
  marker — idempotent, and you can see exactly what needs a human.

AUTH  Microsoft Graph, delegated, same public client as the functions system.
  One-time: python3 modules/invoices/graph_auth.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices.graph_auth import get_token   # noqa: E402

MAILBOX = "accounts@stowawaybar.com"
GRAPH = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(MAILBOX)}"
PROCESSED_FOLDER = "Invoices Processed"
REVIEW_FOLDER = "Invoices Review"
BATCH = 20        # messages per run; the schedule catches the rest
RETRY_BATCH = 200  # the Review retry sweeps the WHOLE folder — see main()
GRAPH_TIMEOUT = 60   # seconds per Graph call; see the note in _req()
GRAPH_TRIES = 3      # total attempts per Graph call; see _req()
GRAPH_BACKOFF = 3    # seconds, multiplied by the attempt number
# Transient Graph failures. 429 is throttling (Retry-After is honoured); the 5xx
# family is Graph having a moment. NOT 4xx — a 401/403/404 is deterministic, and
# retrying it just delays a real error and can mask an expired token.
GRAPH_RETRY_STATUS = {429, 500, 502, 503, 504}
# Methods safe to repeat. GET is trivially idempotent. PATCH is here because the
# only PATCH this module makes is move_message, whose body names an ABSOLUTE
# destination folder — moving a message to the folder it is already in is a
# no-op, so a repeat cannot compound. POST is deliberately absent: the only POST
# is folder creation, and a duplicate "Invoices Review" folder would split the
# backlog across two folders, which is far worse than one failed run.
GRAPH_RETRY_METHODS = {"GET", "PATCH"}
WINDOW_WEEKS = 12  # ~3 months. Widened from 6 for a one-off deep backfill (Zak:
                   # "do another month of invoices to ensure we've caught
                   # everything, incl. beverage purchases"). The daily pass still
                   # only sees UNprocessed inbox mail (processed = moved out), so a
                   # wide window is harmless day-to-day and just lets a backfill
                   # reach older suppliers (liquor especially) that invoice monthly.

# Clearly NOT an invoice — statements, reminders, remittances, receipts. We skip
# these before spending an extraction on them. Conservative on purpose: only
# obvious non-invoices; anything ambiguous still goes through the validator,
# which is the real relevance gate. A real invoice rarely carries these words.
import re as _re  # noqa: E402
SKIP_SUBJECT = _re.compile(
    r"\b(statement|remittance|payment\s+reminder|reminder|overdue|thank\s+you\s+for\s+your\s+payment"
    r"|account\s+balance|past\s+due|receipt\s+of\s+payment)\b", _re.I)


# ── Graph helpers ──────────────────────────────────────────────────────────
def _req(token, method, path, body=None):
    """
    One Graph call, with a hard timeout and a bounded retry on transient failure.

    TIMEOUT IS NOT OPTIONAL. urlopen() with no timeout blocks FOREVER on a
    half-open socket, and this call is the bottom of every mail path:
    pull_mailbox, the Review retry, and build_corpus all reach Graph through
    here. On 2026-08-15 a pull_mailbox.py was found still wedged after ~21 hours
    and a build_corpus.py after 1h45m (2 open sockets, 1.75s of CPU, zero files
    written) — both had to be killed by hand, and a wedged run blocks the next
    scheduled one.

    THE TIMEOUT ALONE WAS NOT ENOUGH, which is what 2026-08-16 showed. A single
    transient "Graph 504 UnknownError" on one message's attachments raised
    straight out of the Review sweep and abandoned the remaining messages —
    twice: at message 120 of 200 on 08-15, and again on 08-16 at 76 of ~200
    (that one an `[Errno 32] Broken pipe` on an attachment fetch). Both times
    the run was doing its job and one flaky call threw the rest away. A 5xx from
    Graph is not exceptional; it is Tuesday. So a transient failure is now
    retried a few times before it is allowed to become an error, and only then
    for the methods it is safe to repeat (see GRAPH_RETRY_METHODS).

    A genuinely stuck socket still fails loudly after the last attempt, and the
    next scheduled run picks the work up.
    """
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    can_retry = method.upper() in GRAPH_RETRY_METHODS
    where = f"{method} {url.split('?')[0]}"

    for attempt in range(1, GRAPH_TRIES + 1):
        last = attempt == GRAPH_TRIES
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=GRAPH_TIMEOUT) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if last or not can_retry or e.code not in GRAPH_RETRY_STATUS:
                raise RuntimeError(f"Graph {e.code} on {where}: {detail}") from None
            # Graph asks for a specific wait when it throttles; honour it.
            try:
                pause = max(0, int(e.headers.get("Retry-After", "")))
            except (TypeError, ValueError):
                pause = 0
            reason = f"Graph {e.code}"
        except (TimeoutError, socket.timeout):
            if last or not can_retry:
                raise RuntimeError(
                    f"Graph TIMEOUT after {GRAPH_TIMEOUT}s on {where} — no response"
                    f"{'' if can_retry else ' (not retried: unsafe to repeat)'}. "
                    f"Nothing was promoted; the next run retries."
                ) from None
            pause, reason = 0, f"timeout after {GRAPH_TIMEOUT}s"
        except urllib.error.URLError as e:
            # Connection-level: broken pipe, connection reset, DNS blip. This is
            # the class that produced the 08-16 "[Errno 32] Broken pipe".
            if last or not can_retry:
                raise RuntimeError(f"Graph connection error on {where}: {e.reason}") from None
            pause, reason = 0, f"connection error ({e.reason})"

        pause = pause or GRAPH_BACKOFF * attempt
        print(f"    (transient {reason} on {where} — attempt {attempt}/{GRAPH_TRIES},"
              f" retrying in {pause}s)")
        time.sleep(pause)

    # Unreachable: the final attempt either returns or raises above.
    raise RuntimeError(f"Graph: exhausted {GRAPH_TRIES} attempts on {where}")


def ensure_folder(token, name) -> str:
    """Folder id for `name`, creating it at the mailbox root if missing."""
    q = urllib.parse.quote(f"displayName eq '{name}'")
    found = _req(token, "GET", f"/mailFolders?$filter={q}").get("value", [])
    if found:
        return found[0]["id"]
    return _req(token, "POST", "/mailFolders", {"displayName": name})["id"]


def messages_with_attachments(token, folder="inbox", oldest_first=False, top=BATCH):
    # RECENT ONLY. Filter on receivedDateTime (indexed -> efficient) for the
    # last WINDOW_WEEKS; check hasAttachments client-side. Filtering on BOTH
    # receivedDateTime and hasAttachments trips Graph's "InefficientFilter", so
    # we don't — and we never reach back past the window regardless of how much
    # history sits in the folder. `folder` is a well-known name (inbox) or a
    # folder id (for the Review-retry pass).
    #
    # ORDER matters for a BACKLOG. The daily pass takes the NEWEST first (desc):
    # today's invoices should never wait behind a backlog. But if a backlog built
    # up (the poller started after mail had already accumulated), newest-first
    # STARVES the old tail — new mail keeps landing at the top and the 6-week-old
    # invoices at the bottom never reach the batch. `oldest_first` drains from the
    # bottom instead, so a backfill run claws the old invoices back. Pages past
    # $top via @odata.nextLink so a backfill can gather more than one batch.
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=WINDOW_WEEKS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qs = urllib.parse.urlencode({
        "$filter": f"receivedDateTime ge {cutoff}",
        "$select": "id,subject,from,receivedDateTime,hasAttachments",
        "$orderby": "receivedDateTime asc" if oldest_first else "receivedDateTime desc",
        "$top": str(min(top, 50)),
    }, quote_via=urllib.parse.quote)
    out, path = [], f"/mailFolders/{folder}/messages?{qs}"
    while path and len(out) < top:
        page = _req(token, "GET", path)
        out.extend(m for m in page.get("value", []) if m.get("hasAttachments"))
        path = page.get("@odata.nextLink")        # None on the last page
    return out[:top]


def pdf_attachments(token, msg_id):
    out = []
    for a in _req(token, "GET", f"/messages/{msg_id}/attachments").get("value", []):
        name = (a.get("name") or "").lower()
        ctype = (a.get("contentType") or "").lower()
        is_pdf = ctype == "application/pdf" or name.endswith(".pdf")
        if is_pdf and a.get("contentBytes"):
            out.append((a["name"], base64.b64decode(a["contentBytes"])))
    return out


def move_message(token, msg_id, folder_id):
    # A move can 404 (ErrorItemNotFound) if the message was already moved or
    # deleted between the list and now — a stale id, not a real failure. Swallow
    # it so ONE vanished message can't abort a whole backfill run (it did once:
    # extraction failed on an out-of-credit key, the move-to-review then 404'd and
    # crashed the run before it committed the 25 invoices it had already ingested).
    try:
        _req(token, "POST", f"/messages/{msg_id}/move", {"destinationId": folder_id})
        return True
    except RuntimeError as e:
        if "ItemNotFound" in str(e) or " 404 " in str(e):
            print(f"    (message already gone — skipping move)")
            return False
        raise


# ── invoice handling ───────────────────────────────────────────────────────
def run_invoice(pdf_bytes, source, sender="", no_llm=False) -> int:
    """run.py on one PDF. Its exit codes: 0 PASS, 1 ERROR, 2 REVIEW,
    3 STATEMENT (not an invoice), 4 IMAGE-ONLY (no text layer -> manual entry).
    Passes the sender domain so run.py tries a free deterministic parser first.
    no_llm forbids the API call — parser or review, nothing spent."""
    # keep the original PDF so the app can show the actual invoice for review
    try:
        from modules.invoices.invoice_store import upload_pdf
        upload_pdf(pdf_bytes)
    except Exception as e:
        print(f"    (pdf store skipped: {str(e)[:80]})")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        cmd = [sys.executable, "modules/invoices/run.py", "--pdf", tmp, "--source", source]
        if sender:
            cmd += ["--sender", sender.split("@")[-1].lower()]
        if no_llm:
            cmd += ["--no-llm"]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode == 1:
            print(f"    extract error: {r.stderr.strip()[:200]}")
        return r.returncode
    finally:
        Path(tmp).unlink(missing_ok=True)


def aggregate_and_commit(dry_run: bool):
    subprocess.run([sys.executable, "modules/invoices/build_cogs_list.py"], cwd=ROOT, check=False)
    subprocess.run([sys.executable, "modules/recipes/pipeline/build_costs.py"], cwd=ROOT, check=False)
    # refresh the app's review queue so newly-ingested bills show up at /invoices
    subprocess.run([sys.executable, "modules/invoices/build_invoice_queue.py"], cwd=ROOT, check=False)
    # refresh cross-supplier price comparison (/pricing) from the updated cogs
    subprocess.run([sys.executable, "modules/invoices/build_price_compare.py"], cwd=ROOT, check=False)
    if dry_run:
        return
    subprocess.run(["git", "add", "data/invoices", "data/invoices_review",
                    "data/cogs_list.csv", "data/costs.csv", "modules/invoices/learned_overrides.json",
                    "dashboard/invoices/queue.json", "dashboard/invoices/accounts.json",
                    "dashboard/pricing/compare.json"], cwd=ROOT, check=False)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode
    if staged == 0:
        print("nothing new to commit")
        return
    subprocess.run(["git", "commit", "-m", "Invoice ingest from accounts@ mailbox"], cwd=ROOT, check=False)
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=ROOT, check=False)
    p = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True)
    print("push:", (p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip() else "ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list only; no extract, move or commit")
    ap.add_argument("--source-folder", default="inbox",
                    help="'inbox' (default) or 'Invoices Review' to re-run stuck ones (retry pass)")
    ap.add_argument("--oldest-first", action="store_true",
                    help="drain the OLDEST unprocessed mail first — for backfilling a backlog "
                         "the newest-first daily pass has starved (still within WINDOW_WEEKS)")
    ap.add_argument("--max", type=int, default=None,
                    help=f"messages to gather this run (default {BATCH} for the inbox pass, "
                         f"{RETRY_BATCH} for the Review retry; raise for a backfill)")
    ap.add_argument("--no-llm", action="store_true",
                    help="parse deterministically only — no API credit needed; unparseable "
                         "invoices go to Review for a later LLM pass")
    args = ap.parse_args()
    retry = args.source_folder.lower() != "inbox"   # Review-retry pass
    # THE RETRY PASS WAS STARVING ITS OWN BACKLOG. Both passes defaulted to the
    # newest BATCH=20 messages, which is right for the inbox (today's invoices
    # must not queue behind a backlog) and wrong for Review, where the whole
    # point is to re-try things that have ALREADY been sitting there. Review
    # holds ~60 messages, so the newest 20 are permanently in front and anything
    # older is never re-tried: after the Foodlink parser was fixed on 2026-08-15
    # the retry pass recovered only the 3 newest invoices and left 7 stuck, which
    # took a manual --max 60 to clear. A retry pass should sweep the whole
    # folder — it is cheap under --no-llm, where a miss costs nothing.
    #
    # ONLY under --no-llm. A retry pass WITHOUT it pays for an extraction per
    # message, so sweeping the folder would turn a 20-message run into a
    # 200-message bill. Deterministic sweeps are free and get the whole backlog;
    # billed sweeps stay on the small batch. --max still overrides either way.
    if args.max is None:
        args.max = RETRY_BATCH if (retry and args.no_llm) else BATCH

    token = get_token()
    processed_id = review_id = None
    if not args.dry_run:
        processed_id = ensure_folder(token, PROCESSED_FOLDER)
        review_id = ensure_folder(token, REVIEW_FOLDER)
    source = review_id if retry else "inbox"
    msgs = messages_with_attachments(token, source, oldest_first=args.oldest_first, top=args.max)
    label = "Review folder (retry)" if retry else "accounts@ inbox"
    print(f"{len(msgs)} message(s) with attachments in {label}"
          + (f"  [model={os.environ.get('INVOICE_MODEL','haiku')}]" if retry else ""))
    # DELIBERATELY NOT refreshing the system-health snapshot here — see below.
    #
    # This used to run scripts/health_monitor.py every cycle with cwd=ROOT, which
    # writes data/system_health.json INTO THE MOUNTED TREE. The comment justified
    # it as "the monitor's clock: if system_health.json goes stale, this poller
    # (which writes it) has stopped". Both halves of that stopped being true when
    # ops/publish_health.py was introduced:
    #
    #   * The poller is no longer "the poller which writes it". publish_health.py
    #     builds the snapshot hourly from a main-pinned clone and PUTs it to main
    #     through the Contents API. The published artefact — the only one Pages
    #     serves and the dashboard reads — carries publish_health's timestamp,
    #     never this one. The local write reached nothing.
    #   * It is not the clock either. health_monitor measures this poller with
    #     _log_age_min("invoice_poller.log"), a file this process writes on every
    #     cycle whether or not there is mail. Poller liveness is unaffected.
    #
    # What the local write DID do was dirty a tracked file every 30 minutes
    # without ever committing it, so `git pull --rebase --autostash` collided on
    # it on essentially every run — twice during the 2026-08-16 triage alone,
    # once as an unresolved UU left over from an earlier rebase. Both sides were
    # stale regenerations of a file that is authored elsewhere. Removing the
    # write removes the conflict at its source; nothing else changes.
    #
    # If a local snapshot is ever wanted for offline debugging, run
    # `python3 scripts/health_monitor.py` by hand — it is safe, it is just not
    # something an automated job should do inside the shared working tree.
    if not msgs:
        return 0

    # ONE BAD MESSAGE MUST NOT COST THE REST OF THE SWEEP.
    #
    # _req now retries transient Graph failures, which removes most of this
    # risk — but "most" is not "all", and the failure mode is expensive out of
    # proportion to its cause. On 2026-08-15 a 504 at message 120 of 200 threw
    # away the remaining 80; on 2026-08-16 a broken pipe at 76 of ~200 threw
    # away ~124, and BOTH runs were sweeping a Review backlog that only shrinks
    # when a sweep completes. A message that cannot be read is a fact about that
    # message, not about the other 199.
    #
    # So each message is now isolated: anything unhandled is reported against
    # the subject it belongs to and the sweep moves on. The message stays where
    # it is (Review), which is the correct resting place for something we could
    # not process — nothing is promoted, nothing is lost, and the next run
    # retries it. Failures are counted and surfaced at the end rather than
    # scrolling past, so a supplier that fails EVERY run is visible as a pattern
    # instead of looking like one-off noise.
    any_change = False
    failed: list[tuple[str, str]] = []
    for m in msgs:
        subj = m.get("subject", "(no subject)")
        sender = (m.get("from", {}).get("emailAddress", {}) or {}).get("address", "?")
        try:
            any_change |= _handle_message(m, subj, sender, token, args, retry,
                                          review_id, processed_id)
        except Exception as e:                       # noqa: BLE001 — deliberate, see above
            failed.append((subj, f"{type(e).__name__}: {e}"))
            print(f"    !! FAILED, left in place and skipped: {type(e).__name__}: {e}")

    if failed:
        print(f"\n{len(failed)} message(s) could not be processed this run "
              f"(left where they were; the next run retries):")
        for subj, err in failed:
            print(f"  • {subj[:70]}  — {err}")

    if any_change or args.dry_run:
        aggregate_and_commit(args.dry_run)
    return 0


def _handle_message(m, subj, sender, token, args, retry, review_id, processed_id) -> bool:
    """
    Process one message; return True if it changed anything on disk.

    Raises on failure — the caller isolates it so one bad message cannot end the
    sweep. Every early exit here is a `return`, and each returns whether this
    message wrote anything, because that is what decides if the run aggregates
    and commits at the end.
    """
    # On the first pass, skip obvious non-invoices. On a retry we process
    # everything already in Review (they were flagged for a reason).
    if not retry and SKIP_SUBJECT.search(subj):
        print(f"\n• {subj}  <{sender}>  — skip (statement/reminder, not an invoice)")
        if not args.dry_run:
            move_message(token, m["id"], review_id)
        return False
    pdfs = pdf_attachments(token, m["id"])
    print(f"\n• {subj}  <{sender}>  — {len(pdfs)} PDF(s)")
    if args.dry_run:
        return False
    if not pdfs:
        if not retry:
            move_message(token, m["id"], review_id)   # attachment but no PDF -> human
        return False

    worst = 0
    saw_invoice = False
    needs_manual = False
    changed = False
    for name, data in pdfs:
        code = run_invoice(data, f"{subj} / {name}", sender=sender, no_llm=args.no_llm)
        if code == 3:                                  # statement / not an invoice
            print("    (statement / not an invoice — skipped)")
            continue
        if code == 4:
            # Image-only: no parser can ever read it (see run.py). Kept in
            # Review for a human to key, but labelled so it does not read as
            # a parser gap — otherwise every future triage re-investigates
            # the same 15 Sun Circle scans and "concludes" they need OCR.
            print("    (image-only scan — needs MANUAL ENTRY, not a parser)")
            needs_manual = True
            continue
        saw_invoice = True
        worst = max(worst, 1 if code == 1 else (2 if code == 2 else 0))
        changed = True
    if not saw_invoice:                                # statement / manual-entry only
        if needs_manual:
            if not retry:
                move_message(token, m["id"], review_id)
            print("    -> Review (manual entry — no text layer)")
        elif not retry:
            move_message(token, m["id"], review_id)
            print("    -> Review (statement, no invoice)")
    elif worst == 0:
        move_message(token, m["id"], processed_id)    # rescued -> Processed
        print("    -> Processed")
    elif not retry:
        move_message(token, m["id"], review_id)
        print("    -> Review")
    else:
        print("    -> still stuck (left in Review)")   # retry couldn't rescue it
    return changed


if __name__ == "__main__":
    raise SystemExit(main())
