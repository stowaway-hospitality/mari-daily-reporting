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

TRIAGE LOG — 2026-08-08: every remaining non-PASS was opened and identified.
None of them is a parser defect, so do NOT spend a day writing a parser for one.
The corpus is at 407/415 (98%), and the 8 shortfalls are:

  * be_foods d02385290774 — a $0.00 "PICK UP RETURN FOR CREDIT" docket.
  * ilg      e23ce69fe899 — a $0.00 WOS invoice (qty column literally "WOS").
  * ilg      b46bfb0a542a — a "TAX ADJUST" buy-back note, not a tax invoice;
                            its only line is "Stock / Quantity - 4 / Buy at -
                            $65.89" with no product code.
  * paramount 670685f29215 — the NSW April 2026 craft-beer PRICE LIST.
  * farmer_joes 4444676 — parses fine; SANITY_BOUNDS correctly fires because
                            CHICKEN BONES at $0.80/kg is under the $1.00/kg
                            per_kg floor. Real price. Fixing it means raising a
                            GLOBAL floor downward, which weakens the net for
                            every supplier in the too-cheap direction. Left.
  * reward_dist (2), vanguard (1) — no parser, but both are dormant: 0 invoices
                            in data/invoices, corpus copies date from 2020-2024,
                            and neither domain is in domains.py. Not worth one.

TRIAGE LOG — 2026-08-09: paramount was found at 13/20 (65%), six invoices below
the reading above. Not drift, and not a new supplier layout: all six parsed and
reconciled TO THE CENT, then failed SANITY_BOUNDS on a single line — WHITE LIGHT
VODKA ORIGINAL "1/20000 ml" at $1,012.78. That is one 20 L drum, and the price
is right; it was being bound-checked against per_unit's $500 ceiling because the
parser called every proved line PER_UNIT. Fixed by giving a single-unit pack of
>= 4 L its own basis (CostBasis.PER_BULK) and its own bounds, so per_unit keeps
its $500 for the other ~40 stock lines. paramount 13/20 -> 19/20, TOTAL
401/415 -> 407/415, and paramount's only remaining shortfall is the price list
above. Note the platform had already diagnosed this itself: the cost-book
"config" flag named the exact product and price and said it "goes to review on
every delivery". Closing the gap emptied that flag.

The three zero-total documents can never PASS by construction: validator's
_check_required_fields treats total_incl <= 0 as a BAD_TOTAL ERROR, deliberately.
So no parser can promote them — the only way to stop them costing an LLM call on
every retry pass forever is to classify a STATED $0.00 total as not-an-invoice in
run.py::looks_like_statement. That is a shared, cross-supplier gate, so it wants
Zak's eyes before it ships, not an unattended daily run's.
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
