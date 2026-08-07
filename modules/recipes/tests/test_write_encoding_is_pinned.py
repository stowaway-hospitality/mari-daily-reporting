"""The two fact-table writes must name their encoding, not inherit the locale's.

WHY THIS EXISTS
---------------
`data/costs.csv` and `data/cogs_list.csv` are the two files this system derives
everything else from. Both were written with `newline=""` and no `encoding=`, so
the encoding came from `locale.getpreferredencoding()` — the machine's, not the
code's. Every *read* in the codebase already passed `encoding="utf-8-sig"`; only
the two writes were left to chance.

Both files carry UTF-8: Dom Pérignon, Don Julio 1942 Añejo, Flor De Caña,
Whispering Angel Rosé, Germano Caetano Cachaça — 35 such lines in costs.csv, 117
in cogs_list.csv. Under an ASCII locale the write does not merely fail. It fails
*part way through*, and the write is not atomic, so what is left on disk is the
fact table cut off mid-row. Measured on 2026-08-07 before the fix: costs.csv came
back 160,297 bytes instead of 372,729. Everything below the cut then reads as a
missing cost or falls back to a stale `as_of` — silent under-costing, on the file
the whole book stands on.

It has never bitten because this Mac and CI are both UTF-8. That is the entire
reason it survived: not that it was safe, but that nobody had run it anywhere
else. A cron that loses its environment, a container, a CI image that changes
base — any of those is the first bad day.

So this runs the two builders for real under `LC_ALL=C` with PEP 538 coercion
and UTF-8 mode both switched off, and asserts the output is byte-identical to
what a UTF-8 locale produces. Without the pinned encoding it raises
UnicodeEncodeError and leaves a truncated file; with it, the locale cannot be
felt at all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# builder script -> the fact table it writes
BUILDERS = [
    ("modules/recipes/pipeline/build_costs.py", "data/costs.csv"),
    ("modules/invoices/build_cogs_list.py", "data/cogs_list.csv"),
]

# The locale a naive write would inherit on a machine that is not this one.
# PYTHONCOERCECLOCALE=0 stops PEP 538 quietly upgrading C to C.UTF-8 and
# PYTHONUTF8=0 stops PEP 540 doing the same, which is the whole point: we are
# asking what happens when nothing rescues us.
ASCII_ENV = {"LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}


@pytest.mark.parametrize("script,output", BUILDERS, ids=lambda v: Path(v).name)
def test_builder_output_is_byte_identical_under_an_ascii_locale(script, output):
    script_path = ROOT / script
    out_path = ROOT / output
    if not script_path.exists() or not out_path.exists():
        pytest.skip(f"{script} or {output} not present in this checkout")

    before = out_path.read_bytes()

    # If the file were pure ASCII this test would pass for the wrong reason and
    # keep passing after a regression. Refuse to be vacuous.
    try:
        before.decode("ascii")
    except UnicodeDecodeError:
        pass
    else:
        pytest.skip(f"{output} holds no non-ASCII today — nothing for the locale to break")

    env = {**os.environ, **ASCII_ENV}
    try:
        run = subprocess.run(
            ["python3", str(script_path)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
        )
        after = out_path.read_bytes()
    finally:
        # Restore before asserting: a failure here must not leave the repo
        # holding the truncated file this test exists to prevent.
        out_path.write_bytes(before)

    assert run.returncode == 0, (
        f"{script} failed under LC_ALL=C — the write is taking its encoding from "
        f"the locale again.\n{run.stderr[-2000:]}"
    )
    assert after == before, (
        f"{output} differs when built under an ASCII locale "
        f"({len(before)} bytes -> {len(after)}). The write is locale-dependent."
    )
