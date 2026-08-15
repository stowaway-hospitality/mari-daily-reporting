#!/usr/bin/env python3
"""Catch a committed Insights export that is really another day's data.

HOW THIS BIT US. `insights_stow_2026-08-10.csv` holds 2026-08-03's rows, and
`insights_stow_2026-08-13.csv` holds 2026-08-11's — identical to the cent,
product for product. Nothing complained for a week: the file existed, parsed,
and totalled a plausible number for a quiet Monday. It was only found by
re-pulling the days from Lightspeed and comparing.

The damage is not confined to Stow. The Stow export is the whole site, so
Marilyna's revenue and Harry Gatos' food-on-Stow both come out of it — one
stale file moves all three venues' published P&L.

THE TEST. Two different trading days never produce byte-identical product
lines. Same products, same quantities, same cents, across a whole day, is not
something a restaurant does twice. So: fingerprint each export by its
(product, qty, sales) rows and shout if two dates share a fingerprint.

Deliberately content-based, not size-based: a duplicate has the same byte
count too, and comparing sizes would just find the same thing less precisely.

Run: python3 scripts/duplicate_export_guard.py [--quiet]
Exit 1 if any duplicate is found, so CI stops on it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
NAME_RE = re.compile(r"^insights_(stow|hg|mari)_(\d{4}-\d{2}-\d{2})\.csv$")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not members:
                return ""
            largest = max(members, key=lambda n: zf.getinfo(n).file_size)
            return zf.read(largest).decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def fingerprint(path: Path) -> tuple[str, int, float]:
    """(hash of the day's product lines, row count, total sales)."""
    rows = list(csv.DictReader(io.StringIO(read_text(path))))
    parts, total = [], 0.0
    for r in rows:
        name = (r.get("Product Name") or r.get("Product") or "").strip()
        if not name:
            continue                      # footer
        qty = (r.get("Product Quantity") or r.get("Quantity") or "").strip()
        sales = (r.get("$ Sales") or r.get("Sale Amount") or r.get("Sales") or "").strip()
        parts.append(f"{name}|{qty}|{sales}")
        try:
            total += float(sales.replace("$", "").replace(",", "") or 0)
        except ValueError:
            pass
    parts.sort()
    h = hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
    return h, len(parts), round(total, 2)


ACKNOWLEDGED = DATA / "known_duplicate_exports.txt"


def acknowledged() -> dict[str, str]:
    """<prefix>:<date> -> the reason it cannot be repaired yet.

    An allow-list with reasons rather than a switch to turn the guard off. A
    set that cannot be repaired today is recorded and skipped; anything NEW
    still fails. Same shape as schema_guard's --allow.
    """
    out: dict[str, str] = {}
    if not ACKNOWLEDGED.exists():
        return out
    for line in ACKNOWLEDGED.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, why = line.partition("#")
        entry = entry.strip()
        if re.match(r"^(stow|hg|mari):\d{4}-\d{2}-\d{2}$", entry):
            out[entry] = why.strip()
    return out


def main() -> int:
    quiet = "--quiet" in sys.argv
    known = acknowledged()
    by_print: dict[tuple[str, str], list[tuple[str, int, float]]] = defaultdict(list)
    checked = 0

    for path in sorted(DATA.glob("insights_*.csv")):
        m = NAME_RE.match(path.name)
        if not m:
            continue                      # legacy unprefixed Mari export
        prefix, day = m.group(1), m.group(2)
        h, n, total = fingerprint(path)
        if not n:
            continue                      # not a product export (RG-level report)
        by_print[(prefix, h)].append((day, n, total))
        checked += 1

    all_dupes = {k: v for k, v in by_print.items() if len(v) > 1}

    # A set is acknowledged only if EVERY later copy in it is on the list. The
    # earliest date is the real day and is never the thing being excused.
    dupes, excused = {}, {}
    for k, days in all_dupes.items():
        prefix = k[0]
        later = sorted(d for d, _, _ in days)[1:]
        if all(f"{prefix}:{d}" in known for d in later):
            excused[k] = days
        else:
            dupes[k] = days

    if not quiet:
        print(f"duplicate export guard: checked {checked} product export(s)")
        for k, days in sorted(excused.items()):
            listed = ", ".join(d for d, _, _ in days)
            print(f"  acknowledged: {k[0]} {listed} — {known[f'{k[0]}:{sorted(d for d,_,_ in days)[1]}']}"[:160])

    if not dupes:
        if not quiet:
            print("  ok — no unacknowledged duplicates")
        return 0

    print(f"\n*** {len(dupes)} SET(S) OF DUPLICATE EXPORTS")
    print("    Two trading days cannot ring identical products, quantities and cents.")
    print("    One of these files is another day's data, and because the Stow export")
    print("    is the whole site, it moves Stowaway, Marilyna's AND Harry Gatos.\n")
    for (prefix, h), days in sorted(dupes.items()):
        listed = ", ".join(d for d, _, _ in days)
        n, total = days[0][1], days[0][2]
        print(f"    {prefix}: {listed}")
        print(f"         {n} product lines, ${total:,.2f} inc-GST, fingerprint {h}")
        print(f"         The EARLIEST date is normally the real one; the later file is")
        print(f"         the stale copy. Re-pull it before trusting that day.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
