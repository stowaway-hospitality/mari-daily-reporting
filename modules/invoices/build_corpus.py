#!/usr/bin/env python3
"""
Build a local validation corpus of REAL invoices per supplier.

    python3 modules/invoices/build_corpus.py [--per 40] [--months 4]

The corpus is the test set the deterministic parsers must pass. More real
invoices = every layout variation (wrapped descriptions, odd units, credit
notes, multi-page, $0 substitutions) shows up, so a parser can be iterated
until it handles them — that is what drives error rates down.

Pulls up to `--per` invoices per known supplier domain from the last `--months`
months of the accounts@ inbox into data/invoice_corpus/<supplier_key>/, named by
content hash (natural dedup). Idempotent — re-run to grow the set; already-saved
PDFs are skipped. GITIGNORED: these are real invoices, never committed.

parser_regression.py measures the parsers against this corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices import pull_mailbox as P   # noqa: E402

CORPUS = ROOT / "data" / "invoice_corpus"

# Sender domain -> supplier key (matches the parser registry + build_cogs_list).
from modules.invoices.domains import DOMAIN_KEY   # static config, no heavy deps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=40, help="max invoices per supplier")
    ap.add_argument("--months", type=int, default=4, help="how far back to pull")
    args = ap.parse_args()

    token = P.get_token()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.months * 30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # THE CORPUS WAS A SURVIVOR SAMPLE. This scanned the INBOX only — but
    # pull_mailbox MOVES every message it handles out of the inbox: successes to
    # "Invoices Processed", failures to "Invoices Review". So a document could
    # only enter the corpus if it had already PARSED, and the one population the
    # corpus exists to measure — the failures — was structurally excluded.
    #
    # That is not theoretical. On 2026-08-15 Foodlink re-templated and every
    # invoice from 2026-07-29 on failed in production for 2.5 weeks, while this
    # corpus (60 PDFs, all of them pre-drift survivors) scored foodlink 59/59
    # (100%). The harness could not have caught it, because the broken documents
    # were sitting in a folder it never opened.
    #
    # Scanning all three folders makes the corpus a sample of what ARRIVES, not
    # of what happened to succeed. Newest-first (see below) so the drifted
    # layouts are the ones that get in.
    FOLDERS = ["inbox"]
    for name in (P.REVIEW_FOLDER, P.PROCESSED_FOLDER):
        try:
            FOLDERS.append(P.ensure_folder(token, name))
        except Exception as e:                     # folder may not exist yet
            print(f"  (skipping {name}: {e})")

    saved = {k: 0 for k in set(DOMAIN_KEY.values())}
    have = {k: len(list((CORPUS / k).glob("*.pdf"))) if (CORPUS / k).exists() else 0
            for k in saved}
    # --per is a per-supplier budget for THIS RUN's new saves, not a hard ceiling
    # on the directory. It used to be `have + saved >= per`, which meant a
    # supplier already at the cap was skipped outright — foodlink had been at 60
    # since July, so no new layout could ever enter its corpus no matter how many
    # times this ran. The corpus is gitignored and a PDF is ~100 KB, so letting it
    # grow is cheap; going blind is not.
    pages = 0
    for folder in FOLDERS:
        qs = urllib.parse.urlencode({
            "$filter": f"receivedDateTime ge {cutoff} and hasAttachments eq true",
            "$select": "id,subject,from,receivedDateTime",
            "$orderby": "receivedDateTime desc",   # NEWEST first: catch drift early
            "$top": "100",
        }, quote_via=urllib.parse.quote)
        url = f"/mailFolders/{folder}/messages?{qs}"
        while url and pages < 60:
            d = P._req(token, "GET", url)
            pages += 1
            for m in d.get("value", []):
                dom = ((m.get("from", {}).get("emailAddress", {}) or {}).get("address", "")).split("@")[-1].lower()
                key = DOMAIN_KEY.get(dom)
                if not key or saved[key] >= args.per:
                    continue
                if P.SKIP_SUBJECT.search(m.get("subject", "")):
                    continue
                for _, data in P.pdf_attachments(token, m["id"]):
                    dst = CORPUS / key
                    dst.mkdir(parents=True, exist_ok=True)
                    fn = dst / f"{hashlib.sha1(data).hexdigest()[:12]}.pdf"
                    if not fn.exists():
                        fn.write_bytes(data)
                        saved[key] += 1
                    break   # first PDF per email
            # stop paging once every supplier has had its fill THIS RUN
            if all(saved[k] >= args.per for k in saved):
                break
            url = d.get("@odata.nextLink")

    print(f"corpus at {CORPUS.relative_to(ROOT)} "
          f"({len(FOLDERS)} folder(s), pages scanned: {pages})")
    for k in sorted(saved):
        print(f"  {k:<18} +{saved[k]:>3} new  ({have[k] + saved[k]} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
