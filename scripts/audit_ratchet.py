#!/usr/bin/env python3
"""Cost-book ratchet — audit_book's findings may not get WORSE.

audit_book.py finds money-misstating defects (SEVERE) and suspect numbers
(WARN), and its own docstring says it exits 1 "so CI can hold the line" —
but until 2026-08-15 no workflow ever ran it. SEVERE 7 stood for days with
CI green, and the queue grew (flags 65->67, WARN 112->116 in one day).

This gate pins the CURRENT findings as a ceiling instead of demanding zero:

  * a NEW SEVERE rule appearing            -> fail
  * MORE findings under a pinned SEVERE rule -> fail
  * FEWER findings than the baseline       -> pass, with a nudge to
    `--rebase` so the ceiling ratchets DOWN and stays down
  * WARN / INFO totals                     -> reported, never enforced.
    They move with the calendar (stale-seed findings age in through the
    90-day ingredients window), and a red main on a Saturday bot commit
    helps nobody. SEVERE is the line CI holds.

Why per-rule counts and not per-finding IDs: a finding's detail line embeds
prices and a 13-week sales tail, both of which move without any defect
appearing or disappearing — ID-pinning on those strings would flap weekly.
Rule-level counts are stable. The full ID-pinning ambition is recorded as
T5 in COST_BOOK_ARCHITECTURE_PLAN.md, for when findings carry stable
product identities.

Usage:
  python3 scripts/audit_ratchet.py            # gate (CI runs this)
  python3 scripts/audit_ratchet.py --rebase   # pin current state as baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines" / "audit_baseline.json"

sys.path.insert(0, str(ROOT / "scripts"))
import audit_book  # noqa: E402


def current_state() -> dict:
    F = audit_book.audit()
    severe: dict[str, int] = {}
    warn = info = 0
    for (sev, rule), items in F.items():
        if sev == "SEVERE":
            severe[rule] = severe.get(rule, 0) + len(items)
        elif sev == "WARN":
            warn += len(items)
        else:
            info += len(items)
    return {"severe_rules": dict(sorted(severe.items())),
            "warn_total": warn, "info_total": info}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebase", action="store_true",
                    help="pin the current findings as the new baseline")
    args = ap.parse_args()

    state = current_state()

    if args.rebase:
        BASELINE.parent.mkdir(exist_ok=True)
        payload = {"pinned_on": date.today().isoformat(), **state}
        BASELINE.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"baseline pinned: {sum(state['severe_rules'].values())} SEVERE "
              f"across {len(state['severe_rules'])} rules -> {BASELINE}")
        return 0

    if not BASELINE.exists():
        print(f"::error::no baseline at {BASELINE} — run scripts/audit_ratchet.py --rebase and commit it")
        return 1

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    pinned: dict[str, int] = base.get("severe_rules", {})
    failures: list[str] = []
    improvements: list[str] = []

    for rule, n in state["severe_rules"].items():
        ceiling = pinned.get(rule)
        if ceiling is None:
            failures.append(f"NEW SEVERE rule ({n}): {rule}")
        elif n > ceiling:
            failures.append(f"SEVERE grew {ceiling} -> {n}: {rule}")
        elif n < ceiling:
            improvements.append(f"SEVERE shrank {ceiling} -> {n}: {rule}")
    for rule, ceiling in pinned.items():
        if rule not in state["severe_rules"]:
            improvements.append(f"SEVERE cleared ({ceiling} -> 0): {rule}")

    n_sev = sum(state["severe_rules"].values())
    n_pin = sum(pinned.values())
    print(f"SEVERE {n_sev} (baseline {n_pin}, pinned {base.get('pinned_on', '?')}) | "
          f"WARN {state['warn_total']} (was {base.get('warn_total', '?')}) | "
          f"INFO {state['info_total']} (was {base.get('info_total', '?')})")

    for line in improvements:
        print(f"  better: {line}")
    if improvements and not failures:
        print("  -> lock it in: python3 scripts/audit_ratchet.py --rebase  (and commit the baseline)")

    if failures:
        for line in failures:
            print(f"::error::cost book got worse — {line}")
        print("A defect the book didn't have when the baseline was pinned. Fix it, "
              "or if it is a consciously accepted state, --rebase with the reason in the commit message.")
        return 1

    print("cost book is no worse than the pinned baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
