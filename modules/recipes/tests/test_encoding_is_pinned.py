"""Text I/O must name its encoding. New code may not add to the debt.

WHY THIS EXISTS
---------------
On 2026-08-07 two bugs of the same shape turned up an hour apart.

The loud one: data/costs.csv and data/cogs_list.csv were written with no
encoding=, so the encoding came from the machine's locale. Under LC_ALL=C the
write failed part way through and, not being atomic, left the fact table cut off
mid-row — 160,297 bytes instead of 372,729.

The quiet one, and the reason this guard exists rather than a one-line fix:
core/pack_overrides.py read the chef-confirmation log the same careless way and
swallowed the UnicodeDecodeError in a bare `except Exception: return {}`. No
crash, no log line, just 322 cost observations that stopped existing — every
chef-confirmed pack, gone, on a machine whose only sin was a different locale.

A sweep then found 258 more unpinned text I/O sites across tracked production
code, against 53 CSVs, 12 YAMLs (all three recipe books) and 6 JSONs that carry
non-ASCII today. Most are harmless — json.dumps escapes to ASCII by default, so
a JSON round-trip survives. Some are not, and telling the two apart by reading
is exactly the judgement that failed twice already.

So: the sites the rebuild chain actually exercises are fixed and proven by
running the chain under LC_ALL=C (see test_write_encoding_is_pinned.py). The
rest are recorded here as a frozen baseline. This test does not demand they all
be fixed. It demands the number never goes UP, and that no clean file becomes
dirty. Debt you can see and cannot add to is a different thing from debt you
have forgotten about.

To burn some down: fix the sites, run this file, paste the new baseline.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).with_name("encoding_debt_baseline.json")

# Text I/O whose encoding comes from the locale unless told otherwise.
NAMES = {"read_text", "write_text", "open"}
BINARY_MODES = {"rb", "wb", "ab", "xb", "r+b", "w+b", "rb+", "wb+"}

EXCLUDE_DIRS = ("_archive/", ".venv/", "node_modules/", "/tests/")


def _tracked_python_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                         capture_output=True, text=True)
    if out.returncode:
        return []
    files = []
    for rel in out.stdout.split("\n"):
        rel = rel.strip()
        if not rel or not rel.endswith(".py"):
            continue
        if any(x in f"/{rel}" for x in EXCLUDE_DIRS):
            continue
        if Path(rel).name.startswith("test_"):
            continue
        files.append(ROOT / rel)
    return files


def _offenders_in(path: Path) -> int:
    """Count calls that read or write text without saying in what encoding."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return 0

    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in NAMES:
            continue
        # An explicit encoding is the whole point — nothing more to say.
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        # Binary mode has no encoding to get wrong.
        mode = ""
        if node.args and len(node.args) >= (2 if name == "open" and not isinstance(fn, ast.Attribute) else 1):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value in BINARY_MODES or "b" in arg.value.replace("rb", "b"):
                        mode = arg.value
        if mode in BINARY_MODES:
            continue
        # Not our open(): fitz.open, zipfile.open, webbrowser.open, urlopen.
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            if fn.value.id in {"fitz", "zipfile", "webbrowser", "os", "gzip", "tarfile", "io"}:
                continue
        n += 1
    return n


def _current() -> dict[str, int]:
    out = {}
    for p in _tracked_python_files():
        c = _offenders_in(p)
        if c:
            out[str(p.relative_to(ROOT))] = c
    return dict(sorted(out.items()))


def test_no_new_unpinned_text_io():
    if not BASELINE.exists():
        import pytest
        pytest.skip("no baseline recorded")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = _current()

    grew = {f: (baseline.get(f, 0), n) for f, n in current.items()
            if n > baseline.get(f, 0)}

    assert not grew, (
        "Text I/O without an explicit encoding= was added.\n"
        + "\n".join(f"  {f}: {was} -> {now}" for f, (was, now) in sorted(grew.items()))
        + "\n\nThe encoding must come from the code, not from whatever locale the "
          "machine happens to have. Pass encoding='utf-8-sig' on reads and "
          "encoding='utf-8' on writes."
    )


def test_the_baseline_has_not_gone_stale():
    """If sites were fixed, the baseline should be re-frozen so it keeps biting."""
    if not BASELINE.exists():
        import pytest
        pytest.skip("no baseline recorded")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = _current()
    shrunk = {f: (baseline[f], current.get(f, 0)) for f in baseline
              if current.get(f, 0) < baseline[f]}
    assert not shrunk, (
        "Good news, and a chore: these files now have FEWER unpinned sites than "
        "the baseline records. Re-freeze it so it keeps its teeth —\n"
        + "\n".join(f"  {f}: {was} -> {now}" for f, (was, now) in sorted(shrunk.items()))
        + f"\n\n  python3 {Path(__file__).name} --freeze"
    )


if __name__ == "__main__":
    import sys
    if "--freeze" in sys.argv:
        BASELINE.write_text(json.dumps(_current(), indent=1) + "\n", encoding="utf-8")
        cur = _current()
        print(f"froze {sum(cur.values())} sites across {len(cur)} files -> {BASELINE.name}")
    else:
        cur = _current()
        print(f"{sum(cur.values())} unpinned sites across {len(cur)} files")
        for f, n in cur.items():
            print(f"  {n:3d}  {f}")
