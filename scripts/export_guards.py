"""Guards on a Lightspeed Insights export, before any number is believed.

Three things can arrive in the mailbox and none of them announces itself:

  1. a real product export                      -> aggregate it
  2. a header-only report (the venue was shut)  -> record CLOSED, not blank
  3. LAST WEEK'S report, re-sent                -> refuse, loudly

The third is the dangerous one, and it is not hypothetical. Harry Gatos'
exports for Monday 3 Aug and Monday 10 Aug 2026 are byte-identical: 51 rows,
"Unlimited Dumplings, 29, $684.00" on both, every figure equal to the cent. Two
trading Mondays do not produce identical files. One of those days is a copy,
and a copied day is worse than a missing one - a missing day is visible and
heals itself, a copied day looks like trade and sums into the week.

The second matters for a different reason: an empty export and a failed ingest
looked the same on screen. HG's nine closed Tuesdays and Sundays all delivered
the same header-only Reporting-Group file, and the week rendered "6 days" with
nothing saying why. Closed is a fact worth recording.

This module is deliberately separate from daily_aggregator.py, which executes
its work at import time and so cannot be imported by a test.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

# The two shapes Lightspeed emails. A product export names the product; the
# reporting-group one does not, and carries no product detail at all.
PRODUCT_KEYS = ("Product Name", "Product")
RG_KEY = "Reporting Group Name"


class StaleExport(RuntimeError):
    """This export is byte-identical to another DATE's export."""


def read_rows(path: Path) -> tuple[list[dict], list[str]]:
    """(rows, fieldnames). No rows is a legitimate answer: the venue was shut."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader), (reader.fieldnames or [])


def is_closed_day(path: Path) -> bool:
    """True when the export has a header and nothing else."""
    rows, _ = read_rows(path)
    return not rows


def is_product_level(path: Path) -> bool:
    _, fields = read_rows(path)
    return any(k in fields for k in PRODUCT_KEYS)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_not_a_copy(path: Path, prefix: str, data_dir: Path | None = None) -> None:
    """Raise StaleExport if an identical file exists for a different date.

    Closed-day exports are exempt: they are identical to each other by nature
    and carry no revenue to duplicate.
    """
    if is_closed_day(path):
        return
    data_dir = data_dir or path.parent
    mine = fingerprint(path)
    for other in sorted(data_dir.glob(f"insights_{prefix}_*.csv")):
        if other.name == path.name:
            continue
        try:
            if fingerprint(other) == mine:
                raise StaleExport(
                    f"{path.name} is byte-identical to {other.name} — that is a "
                    f"re-sent report, not this day's trade. Writing it would "
                    f"duplicate a day's revenue into the week. Re-export this "
                    f"date from Lightspeed Insights.")
        except OSError:
            continue
