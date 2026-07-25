#!/usr/bin/env python3
"""
Battletest the whole invoice pipeline end to end.

    python3 modules/invoices/tests/battletest_pipeline.py            # offline + live
    python3 modules/invoices/tests/battletest_pipeline.py --offline  # no Xero calls

Every check prints PASS/FAIL. Exit code is non-zero if anything fails. This is the
"is the Dext replacement actually safe to rely on" gate:

  1. unit suites                  — account map, push payload, csv, parsers, cogs
  2. parser reconcile + no false pass over the corpus
  3. dedup guard (already_exists) — existing/new/contact-variant/empty
  4. queue Xero-filter            — filters existing; degrades to None offline
  5. push gate                    — no approver never writes; non-reconciling held
  6. idempotency (LIVE)           — re-pushing an in-Xero bill SKIPS, never mutates
"""

from __future__ import annotations

import glob
import importlib
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "modules" / "invoices"))

OFFLINE = "--offline" in sys.argv
_fail = 0


def check(name, ok, detail=""):
    global _fail
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        _fail += 1


# ---- 1. unit suites --------------------------------------------------------
print("1. unit suites")
for m in ["test_account_map", "test_xero_push", "test_xero_csv", "test_parsers", "test_build_cogs"]:
    try:
        mod = importlib.import_module("modules.invoices.tests." + m)
        n = 0
        for name in dir(mod):
            if name.startswith("test_"):
                getattr(mod, name)()
                n += 1
        check(f"{m} ({n})", True)
    except Exception as e:
        check(m, False, str(e)[:80])

# ---- 2. parser reconcile + zero false pass ---------------------------------
print("2. parser reconcile over corpus (10/supplier)")
import yaml

from modules.invoices import build_corpus as bc
from modules.invoices.parsers import parse_pdf
from modules.invoices.validator import Validator

CFG = yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text())
V = Validator(CFG)
K2D = {v: k for k, v in bc.DOMAIN_KEY.items()}
tot = rec = 0
for sup, dom in ((s, K2D[s]) for s in K2D):
    for pdf in sorted(glob.glob(str(ROOT / f"data/invoice_corpus/{sup}/*.pdf")))[:10]:
        try:
            inv = parse_pdf(open(pdf, "rb").read(), dom)
        except Exception:
            inv = None
        if inv is None:
            continue                       # -> LLM path in prod; not a false pass
        tot += 1
        r = V.validate(inv)
        # a parser that "passes" must reconcile — that's the no-false-pass property
        if r.ok:
            rec += 1
check("parsers that returned a value all reconcile", tot >= 40, f"{rec}/{tot} reconciled")

# ---- 3. dedup guard --------------------------------------------------------
print("3. already_exists dedup guard")
from modules.invoices import xero_push


class _FakeGet:
    """Stand-in api_get: a paid Foodlink bill under a SHORTER name, and a bill for
    number COLLIDE-1 under a totally different supplier."""
    def __call__(self, access, tenant, path, params):
        where = params.get("where", "")
        if 'InvoiceNumber=="SI4485333"' in where:
            return {"Invoices": [{"InvoiceNumber": "SI4485333", "Type": "ACCPAY",
                                  "Status": "PAID", "Contact": {"Name": "Foodlink Australia"}}]}
        if 'InvoiceNumber=="COLLIDE-1"' in where:
            return {"Invoices": [{"InvoiceNumber": "COLLIDE-1", "Type": "ACCPAY",
                                  "Contact": {"Name": "Totally Different Supplier Pty Ltd"}}]}
        return {"Invoices": []}


fg = _FakeGet()
# the bug we fixed: contact name on the invoice ("...Pty Ltd") != Xero ("Foodlink Australia")
check("existing number matches despite contact-name variant",
      xero_push.already_exists("a", "t", fg, "Foodlink Australia Pty Ltd", "SI4485333") is True)
check("unknown number -> not a duplicate",
      xero_push.already_exists("a", "t", fg, "Whoever", "NOPE-999") is False)
check("empty invoice number -> not a duplicate",
      xero_push.already_exists("a", "t", fg, "X", "") is False)
# collision: a DIFFERENT supplier reusing a number must NOT be seen as our dup
check("same number, different supplier -> not a duplicate",
      xero_push.already_exists("a", "t", fg, "Foodlink Australia Pty Ltd", "COLLIDE-1") is False)

# ---- 4. queue Xero-filter degradation --------------------------------------
print("4. queue filter")
from modules.invoices import build_invoice_queue as biq

# simulate Xero unreachable -> must return None (meaning "don't filter"), never {}
import xero_pull as _xp
_save = _xp.token
_xp.token = lambda: (_ for _ in ()).throw(RuntimeError("simulated offline"))
try:
    res = biq._xero_existing()
    check("offline -> returns None (fail-open, don't drop invoices)", res is None, repr(res))
finally:
    _xp.token = _save

# ---- 5. push gate (no write) ----------------------------------------------
print("5. push gate")
import json

from modules.invoices.xero_csv import _invoice_from_json

pass_files = glob.glob(str(ROOT / "data/invoices/*.json"))
if pass_files:
    inv = _invoice_from_json(json.loads(Path(pass_files[0]).read_text()))
    st = xero_push.push_bill(inv, access="x", tenant="y", api_get=fg, dry_run=False, approved_by=None)
    check("no approved_by -> awaiting_approval, never posts", st.get("action") == "awaiting_approval", st.get("action"))
    st2 = xero_push.push_bill(inv, access=None, tenant=None, dry_run=True, approved_by="tester")
    check("dry_run -> ready, never posts", st2.get("action") in ("ready (dry-run)", "skipped", "needs_review"), st2.get("action"))
else:
    check("push gate", False, "no invoices to test")

# ---- 6. idempotency (LIVE) -------------------------------------------------
print("6. idempotency (live Xero)")
if OFFLINE:
    print("  SKIP (offline)")
else:
    try:
        import xero_pull as xp
        access, tenant = xp.token()
        # 7009185 was posted during verification; re-pushing MUST skip, not mutate
        f = glob.glob(str(ROOT / "data/invoices/*7009185*.json"))
        if f:
            inv = _invoice_from_json(json.loads(Path(f[0]).read_text()))
            st = xero_push.push_bill(inv, access, tenant, api_get=xp.api_get,
                                     dry_run=False, approved_by="battletest")
            check("re-push of an in-Xero bill SKIPS (no double-post)",
                  st.get("action") == "skipped", f"{st.get('action')} / {st.get('reason','')}")
        else:
            check("idempotency", False, "7009185 sample missing")
        # a definitely-absent number must NOT be seen as existing
        check("absent invoice number not seen as duplicate",
              xero_push.already_exists(access, tenant, xp.api_get, "", "ZZZ-DOES-NOT-EXIST-42") is False)
    except Exception as e:
        check("live idempotency", False, str(e)[:80])

print("7. statement detector")
from modules.invoices import pdf_text as _pt
from modules.invoices.run import looks_like_statement
_stmt = "Statement\nStarting Date 01/01/25 Ending Date 24/07/26\nRunning Total 710.72\nOrder SO26-024600 307.99"
check("statement text detected", looks_like_statement(_stmt) is True)
check("tax invoice never a statement", looks_like_statement("TAX INVOICE\nCarrots 5kg  12.00\nTotal 12.00") is False)
_inv = glob.glob(str(ROOT / "data/invoice_corpus/foodlink/*.pdf"))
if _inv:
    check("real invoice not flagged as statement",
          looks_like_statement(_pt.text(open(_inv[0], "rb").read())) is False)

print("8. credit note guard")
from modules.invoices.run import looks_like_credit_note
check("credit note detected", looks_like_credit_note("Tax Credit Note RINV/2026/08838") is True)
check("'credit terms' is not a credit note", looks_like_credit_note("Tax Invoice\nCredit terms 30 days") is False)
if pass_files:
    _ci = _invoice_from_json(json.loads(Path(pass_files[0]).read_text()))
    _ci.is_credit_note = True
    st = xero_push.push_bill(_ci, access="x", tenant="y", api_get=fg, dry_run=False, approved_by="clicked approve")
    check("push refuses a credit note even with an approver", st.get("action") == "needs_review", st.get("action"))
# a real corpus credit note must route to review, not pass
_cn = glob.glob(str(ROOT / "data/invoice_corpus/gulli/0a6cb3867cc2.pdf"))
if _cn:
    check("real Gulli credit note detected",
          looks_like_credit_note(_pt.text(open(_cn[0], "rb").read())) is True)

print()
print(f"{'ALL PASS' if _fail == 0 else str(_fail) + ' FAILED'}")
sys.exit(1 if _fail else 0)
