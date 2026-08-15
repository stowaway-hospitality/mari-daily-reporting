"""No committed Insights export may be another day's data.

WHAT HAPPENED. insights_stow_2026-08-10.csv held 2026-08-03's rows and
insights_stow_2026-08-13.csv held 2026-08-11's — identical product for product,
cent for cent. insights_hg_2026-08-10.csv was a copy of 2026-08-03 too. Nothing
noticed for a week, because a stale file is not a broken file: it exists, it
parses, and a quiet Monday's total looks like a quiet Monday.

The published damage was $3,467 ex-GST across two days and all three venues —
Stowaway's Thursday understated by $3,070, Harry Gatos' Monday overstated by
$1,258 — because the Stow export is the whole site and every venue reads it.

The check is cheap and total: two different trading days never ring identical
products, quantities and cents. This test is the reason that class of failure
cannot come back quietly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "duplicate_export_guard.py"


def test_no_duplicate_insights_exports():
    r = subprocess.run([sys.executable, str(GUARD)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, (
        "a committed Insights export is a duplicate of another day.\n"
        "The later file is normally the stale one — re-pull it from\n"
        "my.kounta.com/report/salesummarybyproduct before trusting that day,\n"
        "and remember it moves all three venues.\n\n" + r.stdout)
