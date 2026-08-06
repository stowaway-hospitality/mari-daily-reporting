#!/usr/bin/env python3
"""
Score the deterministic parsers against the local corpus.

    python3 modules/invoices/parser_regression.py [supplier_key ...]

For each supplier with invoices in data/invoice_corpus/ (built by
build_corpus.py), parse every PDF and validate it. Reports, per supplier:

    PASS         parsed AND reconciled to the printed total  -> free & correct
    review       parsed but didn't reconcile                 -> falls to the LLM
    parse-fail   parser errored / returned nothing            -> falls to the LLM
    not-inv      a statement / remittance / direct-debit form -> skipped upstream
    scan         no text layer                                -> LLM (needs OCR)

The PASS rate is the number to drive up, and it is now computed over PARSEABLE
INVOICES ONLY — the not-inv and scan columns come out of the denominator.

That matters, because lumping them in was actively misleading. It reported
andrews_meat at 71% when all 10 "failures" were monthly STATEMENTS the parser is
right to refuse, and sun_circle at 0% when all 15 of its PDFs are scans with no
text layer for any parser to read. A previous run of the daily triage task could
spend its day building a parser for a supplier whose invoices are images.

The harness now applies run.py's own looks_like_statement first, exactly as
production does before it ever reaches a parser — so a supplier's PASS rate here
means what it says: of the real, readable invoices, how many parse for free.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices import pdf_text                         # noqa: E402
from modules.invoices.domains import DOMAIN_KEY               # noqa: E402
from modules.invoices.parsers import DOMAIN_TO_PARSER, parse_pdf  # noqa: E402
from modules.invoices.run import looks_like_statement         # noqa: E402
from modules.invoices.validator import Validator              # noqa: E402

CORPUS = ROOT / "data" / "invoice_corpus"
KEY_DOMAIN = {v: k for k, v in DOMAIN_KEY.items()}


def main() -> int:
    only = set(sys.argv[1:])
    cfg = yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text())
    V = Validator(cfg)

    keys = sorted(d.name for d in CORPUS.iterdir()) if CORPUS.exists() else []
    if not keys:
        print(f"no corpus at {CORPUS.relative_to(ROOT)} — run build_corpus.py first")
        return 1

    tot_p = tot_n = tot_skip = tot_scan = 0
    print(f"{'supplier':<18} {'pass':>11}   review  parsefail   not-inv   scan   parser")
    for key in keys:
        if only and key not in only:
            continue
        dom = KEY_DOMAIN.get(key, "")
        pdfs = sorted((CORPUS / key).glob("*.pdf"))
        p = r = f = skip = scan = 0
        for pf in pdfs:
            raw = pf.read_bytes()
            # Mirror production's order of operations (run.py): a scan has no
            # text for any parser to read, and a statement is refused before the
            # parser is ever called. Neither is a parser failure, so neither
            # belongs in the denominator.
            if not pdf_text.has_text_layer(raw):
                scan += 1
                continue
            if looks_like_statement(pdf_text.text(raw)):
                skip += 1
                continue
            try:
                inv = parse_pdf(raw, dom)
            except Exception:
                inv = None
            if inv is None:
                f += 1
            elif V.validate(inv).ok:
                p += 1
            else:
                r += 1
        n = p + r + f                      # real, readable invoices only
        tot_p += p
        tot_n += n
        tot_skip += skip
        tot_scan += scan
        pct = f"{p}/{n} ({100 * p // n if n else 0}%)"
        has = "yes" if dom in DOMAIN_TO_PARSER else "—"
        print(f"{key:<18} {pct:>11}   {r:>6}   {f:>8}   {skip:>7}   {scan:>4}   {has}")
    tpct = f"{tot_p}/{tot_n} ({100 * tot_p // tot_n if tot_n else 0}%)"
    print(f"{'TOTAL':<18} {tpct:>11}   {'':>6}   {'':>8}   {tot_skip:>7}   {tot_scan:>4}")
    print(f"\n{tot_skip} not-invoice PDF(s) and {tot_scan} scan(s) excluded from the rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
