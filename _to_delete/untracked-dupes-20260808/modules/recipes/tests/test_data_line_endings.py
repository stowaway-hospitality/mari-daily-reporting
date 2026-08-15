"""No tracked data CSV may mix CRLF and LF rows.

WHY THIS EXISTS
---------------
27 of the 154 tracked CSVs under data/ are CRLF — costs.csv and cogs_list.csv
among them, because they are written by tools that came from the Lightspeed /
Excel side of the world. The other 127 are LF. Either is fine. What is not fine
is one file being both.

The way a file becomes both is appending a row to it in Python text mode:
read_text() folds CRLF to LF on the way in, and if you then write only the new
row back the file grows an LF line among CRLF ones. The near-miss that opened
this file was the whole-file version of the same mistake — a one-row append to
product_map.csv came back as a 349-line diff because every existing line had
been silently re-terminated. Nothing failed. csv handles both, so the numbers
were right; the DIFF was the casualty, and in a repo where data/ is the audit
log, a change that buries itself in 348 lines of noise is the expensive kind.

A wholesale conversion is at least visible in review. A HALF-converted file is
not, and it is what breaks a naive reader downstream. So this guards the half.

Today: 0 mixed. This test starts green and can only go red on a regression.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _tracked_csvs():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "data/*.csv",
                          "data/**/*.csv"], capture_output=True, text=True)
    if out.returncode:
        return []
    return [p for p in out.stdout.split("\n") if p.strip()]


def test_no_tracked_data_csv_mixes_line_endings():
    files = _tracked_csvs()
    if not files:
        return          # not a git checkout (tarball, vendored copy) — nothing to say
    mixed = []
    for rel in files:
        path = ROOT / rel
        if not path.exists():
            continue
        blob = path.read_bytes()
        crlf = blob.count(b"\r\n")
        lf = blob.count(b"\n")
        if crlf and crlf != lf:
            mixed.append(f"{rel}: {crlf} CRLF rows, {lf - crlf} LF rows")
    assert not mixed, (
        "these files were appended to in text mode — the new rows are LF and "
        "the old ones CRLF:\n  " + "\n  ".join(mixed))
