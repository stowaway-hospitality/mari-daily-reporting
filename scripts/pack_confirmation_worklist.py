#!/usr/bin/env python3
"""What to confirm next, ranked by the money it unblocks.

73.5% of invoice stock lines book into the ledger. The rest refuse because a
pack size is unprovable — "1 box" of something, with no count anywhere on the
invoice. That is the right behaviour (guessing is wrong on every future
movement, in the direction that flatters), but a refusal is only useful if
someone can clear it.

data/pack_overrides.yaml is the clearing mechanism, and it already works: 208
lines book today because a human wrote down what a pack actually holds, with
their name and the date against it. This script says which confirmation is
worth the most, and prints it as a ready-to-paste stub.

Nothing here decides anything. It ranks, it shows the evidence — the supplier's
own description, the units seen, how many times, how many dollars — and leaves
the number to the person who can open the box.

Run: python3 scripts/pack_confirmation_worklist.py [--top 25] [--stubs]
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_receive_movements import (collect_lines, dec,          # noqa: E402
                                     derive_base_units)
from ledger import UnprovableUnit, load_base_units, to_base       # noqa: E402
from core.pack_overrides import load_pack_overrides               # noqa: E402

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))


_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml)\b", re.I)
_MULTI = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|g|l|ml)\b", re.I)


def stated_size(descs: set[str]) -> str:
    """Any pack size the supplier printed in the description, as a hint only."""
    for d in sorted(descs):
        m = _MULTI.search(d)
        if m:
            return f"{m.group(1)} x {m.group(2)}{m.group(3).lower()}"
        m = _SIZE.search(d)
        if m:
            return f"{m.group(1)}{m.group(2).lower()}"
    return ""


def main() -> int:
    top = 25
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    overrides = load_pack_overrides(ROOT / "data" / "pack_overrides.yaml")
    rows = collect_lines()
    base_units, conflicts = derive_base_units(rows, load_base_units())

    # item -> [$ blocked, lines, {descriptions}, {units}, reason]
    blocked: dict[str, list] = defaultdict(
        lambda: [Decimal(0), 0, set(), set(), ""])

    for r in rows:
        L, item = r["line"], r["item"]
        if not item:
            continue
        value = dec(L.get("line_total_incl")) or Decimal(0)
        if overrides.get(r["purchasable"] or "") or overrides.get(item):
            continue                       # already confirmed

        pack_qty, pack_unit = dec(L.get("pack_qty")), (L.get("pack_unit") or "")
        reason = ""
        try:
            if pack_qty is None:
                raise UnprovableUnit("no pack size")
            _, base = to_base(pack_qty, pack_unit)
            want = base_units.get(item)
            if want is None:
                reason = ("delivered in two dimensions" if item in conflicts
                          else "no base unit derivable")
            elif want != base:
                reason = f"recipes use {want}, this delivers {base}"
            else:
                continue                   # books fine
        except UnprovableUnit:
            reason = f"pack unit {str(pack_unit).lower()!r} has no provable size"

        e = blocked[item]
        e[0] += value
        e[1] += 1
        e[2].add((L.get("description") or "")[:44])
        e[3].add(str(pack_unit).lower())
        e[4] = reason

    ranked = sorted(blocked.items(), key=lambda kv: -kv[1][0])
    total = sum(v[0] for v in blocked.values())
    print(f"{len(ranked)} item(s) blocked, ${total:,.2f} incl of deliveries unbooked")
    print(f"clearing the top {min(top, len(ranked))} would unblock "
          f"${sum(v[0] for _, v in ranked[:top]):,.2f}\n")

    print(f"{'$ blocked':>10} {'lines':>6}  {'item':34} why / what the supplier calls it")
    for item, (value, n, descs, units, reason) in ranked[:top]:
        print(f"{value:10,.2f} {n:6}  {item[:34]:34} {reason}")
        for d in sorted(descs)[:2]:
            print(f"{'':18}  {'':34} {d}")
        hint = stated_size(descs)
        if hint:
            # The supplier sometimes prints the size and the parser still can't
            # use it: "BARRAMUNDI FILLETS ... S/OFF 5KG (I)" comes in on a UOM
            # token of 'box', and the bulk-label branch returns "1 box" without
            # reading the text. Surfaced as EVIDENCE for the person confirming,
            # never applied — the description is a free-text field that wraps,
            # truncates and carries substitution notes.
            print(f"{'':18}  {'':34} ^ description states {hint} — confirm before trusting")

    if "--stubs" in sys.argv:
        print("\n\n# --- paste into data/pack_overrides.yaml, one per item, after")
        print("# --- opening one and counting what is actually in it.")
        for item, (value, n, descs, units, reason) in ranked[:top]:
            d = sorted(descs)[0] if descs else ""
            print(f'- id: "{item}"')
            print(f"  pack_qty:            # how many base units in ONE {sorted(units)[0] if units else 'pack'}?")
            print(f"  pack_unit:           # g | ml | ea")
            print(f'  by: ""')
            print(f"  on: {date.today().isoformat()}")
            print(f'  by_email: ""')
            print(f"  # {d}  (${value:,.2f} blocked across {n} line(s); {reason})")
    else:
        print("\n(pass --stubs to print ready-to-fill pack_overrides.yaml entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
