#!/usr/bin/env python3
"""
Invoice run — the entry point.

    email → Outlook rule → Pipedream → repository_dispatch → THIS → data/

Mirrors the daily_pull.yml / Pipedream pattern already proven in this repo
(see PIPEDREAM_BRIDGE.md): Pipedream extracts the PDF attachment as base64 and
POSTs a repository_dispatch; the workflow decodes it and calls this.

Usage
-----
    # from a Pipedream dispatch (what the workflow does)
    python3 scripts/invoice_run.py --pdf-base64-file payload.b64 --source "ILG inv.pdf"

    # from a local file, e.g. re-running one by hand
    python3 scripts/invoice_run.py --pdf /path/to/invoice.pdf

    # parse a saved extraction without calling the API (cheap; for debugging)
    python3 scripts/invoice_run.py --json extraction.json

Exit codes
----------
    0  PASS    — written to data/invoices/
    2  REVIEW  — written to data/invoices_review/, findings printed
    1  ERROR   — could not extract at all
    3  SKIP    — not an invoice (a statement / remittance); nothing written

REVIEW IS NOT FAILURE. An invoice that lands in review cost five minutes.
An invoice that silently passes with a wrong number costs a wrong margin on a
dish for a month (skill Rule 8: Average Cost Price is computed from receive
transactions, so a bad number persists ~30 days regardless of later fixes).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root

from modules.invoices.extract import ExtractionError, extract, parse          # noqa: E402
from modules.invoices.validator import Status, Validator                       # noqa: E402

ROOT = Path(__file__).resolve().parents[2]   # repo root (NOT modules/) — must
# match build_cogs_list / build_invoice_queue / pull_mailbox, which all read
# data/invoices at the repo root. Was .parent.parent = modules/, so run.py wrote
# every invoice to modules/data/invoices where nothing downstream ever looked —
# emails got consumed and moved to Processed but nothing flowed to cogs or Xero.
CONFIG = Path(__file__).parent / "suppliers.yaml"
OUT_PASS = ROOT / "data" / "invoices"
OUT_REVIEW = ROOT / "data" / "invoices_review"

# An ageing bucket: "7 Days", "14 DAYS", "21 +Days", "28 Days+". Statement-only.
_AGEING = re.compile(r"\d{1,3}\s*\+?\s*days\+?")


def _letters(t: str) -> str:
    """Lowercase a-z0-9 only — spacing and punctuation dropped."""
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def _deshift(t: str) -> str:
    """
    Undo the broken font map some PDFs render with, where every glyph extracts 29
    codepoints low, so "DIRECT DEBIT REQUEST" comes out "',5(&7'(%,75(48(67".
    Paramount's and Foodlink's direct-debit authority forms do this. Shifting the
    letters back is only ever used to RECOGNISE a title we want to throw away —
    never to read a number off a document we intend to keep.
    """
    return _letters("".join(chr(ord(c) + 29) if c.isprintable() else c for c in t))


def looks_like_statement(text: str) -> bool:
    """
    True if the PDF is a STATEMENT / remittance, not an invoice.

    A statement lists other invoices with a running balance and has no products
    to cost. The mailbox already skips these by SUBJECT, but one whose subject
    doesn't say "statement" (Inalca's didn't) slips through to the LLM, which then
    reads the statement's summary rows ("Order SO26-024600  $307.99") as if they
    were line items. Catch it by content instead. Conservative: a real Tax Invoice
    is never treated as a statement, and we require both a masthead hit AND a
    balance-style column, so a mere mention of the word can't trip it.

    EVERY phrase test below runs on WHITESPACE-COLLAPSED text, and it has to.
    PDF extraction puts whatever spacing the layout used between words, so Select
    Fresh's masthead comes out "tax  invoice" with two spaces. That slipped past
    the `"tax invoice"` escape hatch, and their footer line "please email
    remittance advice to ..." then matched the remittance rule below — so the
    guard classified EVERY Select Fresh invoice as a statement and run.py threw
    all 60 of the last four months away (exit 3, email filed to Processed,
    nothing written to data/invoices, nothing in COGS). Their produce, herbs and
    citrus quietly stopped costing. Collapsing runs of whitespace first is what
    makes the "a real tax invoice is never a statement" promise actually hold.
    """
    t = re.sub(r"\s+", " ", (text or "").lower())
    if "tax invoice" in t:
        return False
    # Payment notices — a direct-debit / remittance advice that references an
    # invoice but carries NO line items (ILG debits its members and emails one of
    # these per invoice; it leaked into review as a 1-line "invoice"). A real tax
    # invoice says "tax invoice" (returned above), so these titles are safe.
    if ("direct debit advice" in t or "remittance advice" in t
            or "payment advice" in t):
        return True
    # A DIRECT DEBIT REQUEST / authority form is an onboarding form, not a bill:
    # no line items, no total to reconcile. Paramount and Foodlink both mail
    # these; they were reaching the LLM. (Some render with a broken font map, so
    # match the de-shifted spelling too — see _deshift below.)
    if "directdebitrequest" in _letters(t) or "directdebitrequest" in _deshift(t):
        return True
    # A PAYMENT RECEIPT that lists other invoices is a remittance, not an
    # invoice. Gulli mails these; "statement" never appears near the top so the
    # titled+strong test below can't see it. Require the invoice-listing header
    # so a genuine one-off receipt can't trip it.
    if "payment receipt" in t and "invoice number" in t and "payment amount" in t:
        return True
    # A PROOF OF DELIVERY is a carrier's docket, not a bill. CartonCloud emails one
    # per consignment on behalf of the brewers ("MOUNTAIN CULTURE / Proof Of
    # Delivery ... KEG: 2 | Value: $0.00"); 13 of them were sitting in Review as
    # though a parser were missing. They carry quantities and no prices at all, so
    # there is nothing to cost and nothing a parser could ever reconcile.
    if "proof of delivery" in t:
        return True
    # A STATEMENT LEDGER names the columns of a running account. "Invoice Amount"
    # as a COLUMN alongside a "Balance Due" is a statement construct — an invoice
    # states its own total, it does not tabulate other invoices' amounts against a
    # balance. Xero's statement template does exactly this and slipped through
    # because it prints neither "amount enclosed" nor an ageing spread (Speed Gas,
    # Grifter, Cordless Filter).
    if "balance due" in t and "invoice amount" in t:
        return True
    # AGEING BUCKETS ARE SUFFICIENT ON THEIR OWN. Foodlink's monthly Statement of
    # Account prints no masthead in the text layer at all — it opens straight into
    # the ledger ("15079 stowaway ... invoice si4500784 340.80 ... 1,441.20"), so
    # the `titled` test below never fired and the document cycled through Review
    # on every retry pass, unparseable by construction (it has no line items).
    #
    # A "Current | 7 Days | 14 Days | 21 Days+" spread is a statement-only
    # construct: an invoice states ONE set of terms, never a spread of buckets.
    # Measured against the whole corpus on 2026-08-15 — of 418 PASSing invoices,
    # ZERO match this rule, so promoting it from a `strong` signal to a standalone
    # one costs nothing and cannot swallow a real bill. The "tax invoice" escape
    # hatch above still runs first and covers 417 of those 418 outright.
    if "current" in t and len(set(_AGEING.findall(t))) >= 2:
        return True
    titled = "statement" in t[:600] or "statement of account" in t  # word up top
    strong = ("running total" in t or "remaining amount" in t
              or ("opening balance" in t and "closing balance" in t)
              or ("starting date" in t and "ending date" in t)
              or "amount outstanding" in t
              # Fresh Fruit Team (and others) email single-invoice STATEMENTS as
              # "Invoice(s)" — a payment advice with a "balance due" and a remittance
              # slip, no line items. They leaked into review as 0-line invoices. A
              # real tax invoice never says "payment advice", and "tax invoice"
              # already returned False above, so these are safe statement markers.
              or "payment advice" in t
              or ("balance due" in t and "amount enclosed" in t)
              # AGEING BUCKETS. Andrews Meat and Farmer Joes head their monthly
              # statements "STATEMENT" but print none of the phrases above — they
              # age the balance across columns instead ("Current | 7 Days |
              # 14 Days | 21 Days+ | 28 Days+"). That row is a statement-only
              # construct; an invoice states one set of terms, never a spread of
              # buckets. Requiring the word "current" AND two DISTINCT day
              # buckets keeps a plain "7 DAYS NET" terms line from matching.
              or ("current" in t and len(set(_AGEING.findall(t))) >= 2))
    return titled and strong


def looks_like_credit_note(text: str) -> bool:
    """
    True if the PDF is a supplier CREDIT / adjustment note, not a bill.

    A credit note refunds us (returned stock, an overcharge). Parsers read its
    amount as positive like any invoice, so left alone a $48 credit posts as a
    $48 payable — money owed the wrong way. We route these to Review and block the
    push. Match the Australian phrasings ("credit note", "adjustment note") as a
    PHRASE so "credit terms" / "credit card" can't trip it.
    """
    t = (text or "").lower()
    # "credit note" catches "Tax Credit Note" (Gulli) by substring. Add the other
    # AU/US phrasings, kept as PHRASES so "credit terms"/"credit card" can't trip it.
    return ("credit note" in t or "adjustment note" in t
            or "credit memo" in t or "rcti credit" in t)


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)          # money is Decimal; serialise as string, never float
    if isinstance(o, Enum):
        return o.value
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"unserialisable: {type(o)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract, validate and file one supplier invoice.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", type=Path, help="local PDF")
    src.add_argument("--pdf-base64-file", type=Path, help="file containing base64 PDF (Pipedream payload)")
    src.add_argument("--json", type=Path, help="pre-extracted JSON (skips the API call)")
    ap.add_argument("--source", default="", help="original filename / email subject, for provenance")
    ap.add_argument("--sender", default="", help="sender email domain — picks a free deterministic parser before the LLM")
    ap.add_argument("--no-llm", action="store_true",
                    help="NEVER call the LLM — parse deterministically or route to review. "
                         "Free (no API credit needed); a backfill can run entirely on parsers.")
    ap.add_argument("--dry-run", action="store_true", help="validate but write nothing")
    args = ap.parse_args()

    # ---- extract -----------------------------------------------------------
    try:
        if args.json:
            inv = parse(args.json.read_text(), source=args.source or str(args.json))
        else:
            if args.pdf_base64_file:
                pdf = base64.b64decode(args.pdf_base64_file.read_text())
                name = args.source or args.pdf_base64_file.stem
            else:
                pdf = args.pdf.read_bytes()
                name = args.source or args.pdf.name
            # A statement is not an invoice — don't spend an LLM call turning its
            # balance rows into fake line items; exit 3 so the mailbox files it.
            from modules.invoices import pdf_text as _pt
            _txt = _pt.text(pdf)
            if looks_like_statement(_txt):
                print("[skipped — statement / not an invoice]")
                return 3
            # AN IMAGE-ONLY PDF NEVER GOES TO THE EXTRACTOR. No text layer means
            # no parser can read it, and the fallback was to let the LLM look at
            # the picture — which is where this gets dangerous rather than merely
            # unhelpful.
            #
            # All 17 scans in the corpus were opened on 2026-08-15 and NOT ONE is
            # a printed invoice:
            #   * 15 are Sun Circle, a PRE-PRINTED order form filled in BY HAND.
            #     The product names are printed, but qty, unit price, amount and
            #     the total are handwritten in pen ("48 x 4.50  216", total
            #     "540.-"). Those handwritten numbers are the only data we need.
            #     A model reading them is GUESSING at money, and because it
            #     guesses the line amounts AND the total from the same strokes it
            #     can guess consistently and still reconcile — a wrong number that
            #     validates. That is precisely the "errors that flatter you"
            #     failure this codebase is built to refuse.
            #   * 1 is a blank ILG Direct Debit Request; 1 is a blank B&E Credit
            #     Card Authorisation form. Neither is a bill at all, and the
            #     latter is a card-details form that should not be shipped to an
            #     extractor on principle.
            #
            # So this costs zero automation today and removes the whole class.
            # It stays in Review, where a human keys it — Sun Circle is ~3-4
            # lines an invoice, about one a week. The real fix is upstream:
            # ask Sun Circle to email a digital invoice. OCR is NOT the answer —
            # Tesseract reads print, not handwriting, and would return confident
            # nonsense for exactly the fields that matter.
            # Exit 4, not 2, so the mailbox can tell "needs a parser" from "needs
            # a human with a keyboard". A 2 invites tomorrow's triage to go
            # looking for a parser to write; there isn't one to write.
            if not _pt.has_text_layer(pdf):
                print("[image-only PDF — no text layer; needs manual entry, never guessed]")
                return 4
            _is_credit = looks_like_credit_note(_txt)
            # FREE FIRST, BUT ONLY IF IT RECONCILES. A recurring supplier with a
            # known layout is parsed deterministically (no API). We TRUST it only
            # when it validates against the printed total — otherwise (no parser,
            # a scan, a layout change, a parser bug) we fall to the LLM. So a
            # partial parser is pure upside: free when it's right, LLM when not.
            inv = None
            if args.sender:
                from modules.invoices.parsers import parse_pdf
                cand = parse_pdf(pdf, args.sender)
                if cand is not None:
                    if Validator(yaml.safe_load(CONFIG.read_text())).validate(cand).ok:
                        inv = cand
                        print(f"[parsed deterministically — {args.sender}, reconciled, no API]")
                    else:
                        print(f"[{args.sender} parser did not reconcile — using LLM]")
            if inv is None and args.no_llm:
                # Parser absent or didn't reconcile, and we're forbidden the LLM —
                # route to review rather than spend (or fail on) an API call. Exit 2
                # so the mailbox files it in Review for a later LLM pass.
                print(f"[no-llm: no reconciling parser for {args.sender or 'sender'} — routed to review]")
                return 2
            if inv is None:
                inv = extract(pdf, filename=name)
            inv.is_credit_note = _is_credit    # a credit note reads as a positive invoice
    except ExtractionError as e:
        print(f"EXTRACTION FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        # a corrupt / empty / non-PDF attachment (pymupdf FileDataError etc.) must
        # not crash with a traceback — fail cleanly so the mailbox files it for a
        # human instead of the poller choking on one bad file.
        print(f"EXTRACTION FAILED (unreadable file): {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # payment due date, read off the invoice itself (never inferred from history)
    if inv.due_date is None:
        try:
            from modules.invoices.due_terms import read_due
            inv.due_date = read_due(pdf, inv.invoice_date)
        except Exception:
            pass

    # provenance: key of the original PDF in Supabase Storage, so the app can
    # open the actual invoice for review.
    try:
        from modules.invoices.invoice_store import pdf_key
        inv.source_pdf = pdf_key(pdf)
    except Exception:
        pass

    # canonical pack size ($/kg, $/L, $/each) so costs flow into the recipe builder
    try:
        from modules.invoices.models import CostBasis
        from modules.invoices.pack_size import parse_pack
        for ln in inv.lines:
            if ln.pack_qty is None:
                ln.pack_qty, ln.pack_unit = parse_pack(
                    ln.description, ln.raw_uom, is_weight_priced=(ln.cost_basis == CostBasis.PER_KG))
    except Exception:
        pass

    # ---- validate — the gate. No model involved. ---------------------------
    result = Validator(yaml.safe_load(CONFIG.read_text())).validate(inv)

    print(f"{inv.supplier_name_raw} · {inv.invoice_ref} · {inv.invoice_date} · ${inv.total_incl}")
    print(result.report())
    if result.extras_total:
        print(f"  note: LS receive should be ${result.expected_ls_receive_total} "
              f"(${result.extras_total} of extras excluded — that gap is expected)")

    # ---- suggest Xero coding (the Dext replacement) — a hint, never a decision -
    from collections import Counter

    from modules.invoices.account_map import ACCOUNT_NAME, suggest_coding
    coding = suggest_coding(inv)
    acct_split = Counter(l.account_code for l in coding.lines if l.account_code)
    split_str = ", ".join(f"{ACCOUNT_NAME.get(c, c)} ({c})×{n}" for c, n in acct_split.most_common())
    print(f"  Xero: {split_str or 'no codeable lines'}"
          f"  |  tracking: {coding.tracking_category}/{coding.tracking_option} ({coding.tracking_confidence})")

    # A CREDIT NOTE reads as a positive invoice and can reconcile perfectly, so it
    # would otherwise PASS and post as a $X payable — the wrong direction. Never
    # let it pass: force Review with a loud finding. (Full ACCPAYCREDIT support can
    # come later; for now a human enters the credit.)
    credit_hold = inv.is_credit_note
    passed = result.ok and not credit_hold
    if credit_hold:
        print("  ** CREDIT NOTE — forcing REVIEW; enter as a supplier credit, NOT a payable **")

    if args.dry_run:
        return 0 if passed else 2

    # ---- file it -----------------------------------------------------------
    out_dir = OUT_PASS if passed else OUT_REVIEW
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{inv.invoice_date}_{inv.supplier_key or 'unknown'}_{inv.invoice_ref or 'noref'}".replace("/", "-")
    findings = [
        {"code": f.code, "severity": f.severity.value, "message": f.message,
         "line_index": f.line_index, "expected": f.expected, "actual": f.actual}
        for f in result.findings
    ]
    if credit_hold:
        findings.insert(0, {"code": "CREDIT_NOTE", "severity": "error",
                            "message": "This is a supplier credit note, not a bill — enter as a "
                                       "credit in Xero, do not post as a payable.",
                            "line_index": None, "expected": None, "actual": None})
    payload = {
        "invoice": asdict(inv),
        "validation": {
            "status": "review" if credit_hold else result.status.value,
            "findings": findings,
            "expected_ls_receive_total": result.expected_ls_receive_total,
            "extras_total": result.extras_total,
        },
        "xero_coding": {
            "tracking_category": coding.tracking_category,
            "tracking_option": coding.tracking_option,
            "tracking_confidence": coding.tracking_confidence,
            "primary_account": coding.primary_account,
            "lines": [
                {"description": l.description, "account_code": l.account_code,
                 "account_name": l.account_name, "reason": l.reason}
                for l in coding.lines
            ],
        },
    }
    path = out_dir / f"{stem}.json"
    path.write_text(json.dumps(payload, indent=2, default=_json_default))
    print(f"-> {path.relative_to(ROOT)}")

    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
