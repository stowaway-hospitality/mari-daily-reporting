#!/usr/bin/env python3
"""
Per-supplier parser drift guard — runs every supplier's parser over its whole
real-invoice corpus and asserts each one is still HEALTHY, naming any that isn't.

Why this and not the battletest's corpus check: that one asserts a GLOBAL count
(`tot >= 40` parses returned), so a single supplier's parser could silently break
— return nothing, or return lines that no longer reconcile — and the others would
carry the number over the line. A supplier changing their invoice layout is the
exact failure this project exists to prevent, and it happens at the front door.

For each supplier this measures, over its corpus:
  - parse rate     — how many PDFs the deterministic parser returned an Invoice for
  - reconcile rate — of those, how many pass the validator (lines sum to the total)

A healthy parser reconciles the large majority of a supplier's standard invoices.
When a format changes, the parser either stops returning (parse rate craters) or
returns lines that don't add up (reconcile rate craters) — either way the supplier
drops below threshold and is named. Statements / credit notes / odd one-offs go to
the LLM path in production, so the thresholds are loose smoke-alarms, not a
thermostat: they catch a BROKEN parser, not a few legitimately-skipped invoices.

    python3 scripts/test_parser_corpus.py      # exit 0 = every parser healthy

The corpus is gitignored (real supplier invoices), so this SKIPS cleanly where it
isn't present (CI, a fresh checkout). It is meant to run where invoices actually
land — the Mac, via the healthcheck.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE = 20          # invoices per supplier (enough to be representative, fast)
MIN_PARSE_RATE = 0.50     # a working parser returns for at least half its corpus
MIN_RECON_RATE = 0.60     # and of those, most reconcile

fails: list[str] = []


def main() -> int:
    pdfs = glob.glob(str(ROOT / "data/invoice_corpus/*/*.pdf"))
    if not pdfs:
        print("no invoice corpus present — skipping parser drift guard (runs on the Mac)")
        return 0

    import yaml
    from modules.invoices.domains import DOMAIN_KEY
    from modules.invoices.parsers import parse_pdf, DOMAIN_TO_PARSER
    from modules.invoices.validator import Validator

    V = Validator(yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text()))
    dom_for = {sup: dom for dom, sup in DOMAIN_KEY.items()}
    # a supplier has a DETERMINISTIC parser only if its domain is registered;
    # otherwise every invoice goes to the LLM path — expected, not drift.
    has_parser = {sup: (dom in DOMAIN_TO_PARSER) for sup, dom in dom_for.items()}

    llm_only = []
    print(f"{'supplier':22} {'parsed':>12} {'reconciled':>14}   health")
    print("-" * 64)
    for sup in sorted(dom_for):
        files = sorted(glob.glob(str(ROOT / f"data/invoice_corpus/{sup}/*.pdf")))[:SAMPLE]
        if not files:
            continue
        if not has_parser[sup]:
            llm_only.append(sup)
            print(f"{sup:22} {'—':>12} {'—':>14}   LLM-only (no deterministic parser)")
            continue
        parsed = recon = 0
        for f in files:
            try:
                inv = parse_pdf(open(f, "rb").read(), dom_for[sup])
            except Exception:
                inv = None
            if inv is None:
                continue          # deterministic parser passed; LLM handles it in prod
            parsed += 1
            if V.validate(inv).ok:
                recon += 1
        n = len(files)
        p_rate = parsed / n if n else 0
        r_rate = recon / parsed if parsed else 0
        ok = (p_rate >= MIN_PARSE_RATE) and (r_rate >= MIN_RECON_RATE)
        if not ok:
            fails.append(sup)
        print(f"{sup:22} {parsed:>3}/{n:<3} ({p_rate:>4.0%})  "
              f"{recon:>3}/{parsed if parsed else 0:<3} ({r_rate:>4.0%})   "
              f"{'ok' if ok else 'DRIFT — parser needs a look'}")

    print()
    if llm_only:
        print(f"note: {', '.join(llm_only)} rely on the LLM path (no deterministic parser).")
    if fails:
        print(f"{len(fails)} registered parser(s) below threshold: {', '.join(fails)}")
        return 1
    print("all registered parsers healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
