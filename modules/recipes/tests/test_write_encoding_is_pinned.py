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
def test_builder_output_does_not_depend_on_the_locale(script, output):
    """Build it twice — once in UTF-8, once in ASCII — and compare the two runs.

    An earlier version compared the ASCII run against the file already on disk.
    That was wrong in a way worth recording: another test in the suite can
    legitimately rebuild costs.csv first, so the committed bytes are not a fixed
    point mid-run, and the test failed for a reason that had nothing to do with
    encodings. Locale-independence is a property of the two runs, not of what
    happens to be on disk when this test starts.
    """
    script_path = ROOT / script
    out_path = ROOT / output
    if not script_path.exists() or not out_path.exists():
        pytest.skip(f"{script} or {output} not present in this checkout")

    original = out_path.read_bytes()
    try:
        original.decode("ascii")
    except UnicodeDecodeError:
        pass
    else:
        pytest.skip(f"{output} holds no non-ASCII today — nothing for the locale to break")

    def build(extra_env):
        run = subprocess.run(["python3", str(script_path)], cwd=str(ROOT),
                             env={**os.environ, **extra_env},
                             capture_output=True, text=True, timeout=300)
        return run, out_path.read_bytes()

    try:
        utf8_run, utf8_bytes = build({"LC_ALL": "en_AU.UTF-8", "LANG": "en_AU.UTF-8"})
        ascii_run, ascii_bytes = build(ASCII_ENV)
    finally:
        # Restore before asserting: a failure here must not leave the repo
        # holding the truncated file this test exists to prevent.
        out_path.write_bytes(original)

    assert utf8_run.returncode == 0, f"{script} failed even in UTF-8\n{utf8_run.stderr[-1500:]}"
    assert ascii_run.returncode == 0, (
        f"{script} failed under LC_ALL=C — something in it is taking its encoding "
        f"from the locale.\n{ascii_run.stderr[-2000:]}"
    )
    assert utf8_bytes == ascii_bytes, (
        f"{output} differs between a UTF-8 and an ASCII locale "
        f"({len(utf8_bytes)} bytes -> {len(ascii_bytes)}). Same inputs, same code, "
        f"different machine — that is the bug."
    )
