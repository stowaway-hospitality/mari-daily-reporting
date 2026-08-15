#!/usr/bin/env python3
"""Verify the fetched daily history against the committed Insights exports.

The history CSV was pulled day-by-day from the Lightspeed report endpoint. 40 of
those days we already hold as committed facts. If the pull reproduces all 40 —
same product lines, same quantities, same sale amounts — then the other 570 days
were fetched the same way and can be trusted. If it doesn't, nothing downstream
should be built on it.

Run: python3 scripts/verify_history_export.py <history.csv>
"""
from __future__ import annotations

import csv
import io
import os
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"


def parse_num(x) -> float:
    s = str(x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            m = max((n for n in zf.namelist() if n.lower().endswith(".csv")),
                    key=lambda n: zf.getinfo(n).file_size)
            return zf.read(m).decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def committed_day(path: Path) -> dict[str, tuple[float, float]]:
    """product -> (qty, sale_inc) from a committed export."""
    rows = list(csv.DictReader(io.StringIO(read_csv_text(path))))
    out: dict[str, tuple[float, float]] = {}
    for r in rows:
        name = (r.get("Product Name") or r.get("Product") or "").strip()
        if not name:
            continue          # footer
        qty = parse_num(r.get("Product Quantity") or r.get("Quantity"))
        sale = parse_num(r.get("$ Sales") or r.get("Sale Amount") or r.get("Sales"))
        prev = out.get(name, (0.0, 0.0))
        out[name] = (prev[0] + qty, prev[1] + sale)
    return out


def main() -> int:
    hist_path = Path(sys.argv[1])
    by_day: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    with hist_path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = (r.get("Product") or "").strip()
            if not name:
                continue
            d = r["Date"]
            qty = parse_num(r.get("Quantity"))
            sale = parse_num(r.get("Sale Amount"))
            prev = by_day[d].get(name, (0.0, 0.0))
            by_day[d][name] = (prev[0] + qty, prev[1] + sale)

    print(f"history: {len(by_day)} dates, {sum(len(v) for v in by_day.values()):,} product-days")

    committed = sorted(DATA.glob("insights_stow_*.csv"))
    print(f"checking against {len(committed)} committed Stow exports\n")

    ok = bad = 0
    for path in committed:
        day = path.name.replace("insights_stow_", "").replace(".csv", "")
        theirs = committed_day(path)
        ours = by_day.get(day)
        if ours is None:
            print(f"  {day}  MISSING from history pull")
            bad += 1
            continue

        problems = []
        if set(ours) != set(theirs):
            only_h = sorted(set(ours) - set(theirs))[:3]
            only_c = sorted(set(theirs) - set(ours))[:3]
            problems.append(f"product sets differ (+{len(set(ours)-set(theirs))} "
                            f"-{len(set(theirs)-set(ours))}) {only_h} {only_c}")
        for name in set(ours) & set(theirs):
            if abs(ours[name][0] - theirs[name][0]) > 0.001:
                problems.append(f"{name}: qty {ours[name][0]} vs {theirs[name][0]}")
            if abs(ours[name][1] - theirs[name][1]) > 0.011:
                problems.append(f"{name}: sale {ours[name][1]} vs {theirs[name][1]}")

        t_sale = sum(v[1] for v in theirs.values())
        o_sale = sum(v[1] for v in ours.values())
        if problems:
            bad += 1
            print(f"  {day}  MISMATCH  ${o_sale:,.2f} vs ${t_sale:,.2f}")
            for p in problems[:4]:
                print(f"       {p}")
        else:
            ok += 1
            print(f"  {day}  ok  {len(ours):3d} products  ${o_sale:,.2f}")

    print(f"\n{ok} matched, {bad} mismatched")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
