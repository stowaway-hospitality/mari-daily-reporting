"""The costing path must name its encoding. Not one call site left to the locale.

WHY THIS EXISTS
---------------
On 2026-08-07 two bugs of the same shape turned up an hour apart.

The loud one: data/costs.csv and data/cogs_list.csv were written with no
encoding=, so the encoding came from the machine's locale. Under LC_ALL=C the
write failed part way through and, not being atomic, left the fact table cut off
mid-row — 160,297 bytes instead of 372,729.

The quiet one, and the reason this file exists rather than a one-line fix:
core/pack_overrides.py read the chef-confirmation log the same careless way and
swallowed the resulting UnicodeDecodeError in a bare `except Exception: return
{}`. No crash, no log line, just 322 cost observations that stopped existing —
every chef-confirmed pack, gone, on a machine whose only sin was a different
locale.

A sweep found the same shape in 253 places across tracked production code,
against 53 CSVs, 12 YAMLs (all three recipe books) and 6 JSONs that carry
non-ASCII today. Most are harmless: json.dumps escapes to ASCII by default, so a
JSON round-trip survives anything. Telling a harmless one from a harmful one by
reading is exactly the judgement that failed twice that day, so the files below
were not judged — the rebuild chain was run twice, once under LC_ALL=C, and the
diff decided.

WHAT THIS GUARDS, AND WHAT IT DOES NOT
--------------------------------------
It guards the costing path: every file the chain touches, at zero. Add an
unpinned read or write to one of them and this goes red.

It deliberately does NOT ratchet a whole-repo count. The first version did, and
it failed CI for an instructive reason: pull_request builds test the branch
MERGED INTO main, so a file untouched by the branch can arrive with more
unpinned calls than the branch's own copy, and a guard that fails on somebody
else's commit is a guard people learn to ignore. The remaining sites are
reported by `python3 <this file>` if anyone wants to burn them down.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Every file the rebuild chain reads or writes through, proven under LC_ALL=C.
# Order follows the chain: costs -> ingredients -> price compare -> recipes.
PINNED = [
    "core/pack_overrides.py",
    "modules/recipes/cost.py",
    "modules/recipes/labour.py",
    "modules/recipes/pipeline/build_costs.py",
    "modules/recipes/pipeline/build_ingredients.py",
    "modules/recipes/pipeline/build_recipe_feeds.py",
    "modules/invoices/build_cogs_list.py",
    "modules/invoices/build_price_compare.py",
    "scripts/convert_lightspeed_recipes.py",
    "scripts/audit_book.py",
    "scripts/cogs_blend.py",
    "scripts/clean_recipe_names.py",
    "scripts/merge_venue_scrape.py",
    "scripts/seed_recipe_costs.py",
    "scripts/seed_recipe_ingredient_costs.py",
    "scripts/build_recipe_bridge.py",
]

NAMES = {"read_text", "write_text", "open"}
BINARY_MODES = {"rb", "wb", "ab", "xb", "r+b", "w+b", "rb+", "wb+"}
NOT_OUR_OPEN = {"fitz", "zipfile", "webbrowser", "os", "gzip", "tarfile", "io", "urllib"}


def _offenders(path: Path) -> list[int]:
    """Line numbers of text reads/writes that never say in what encoding."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in NAMES:
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue                      # said so explicitly — the whole point
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            if fn.value.id in NOT_OUR_OPEN:
                continue                  # fitz.open, zipfile.open, webbrowser.open
        if any(isinstance(a, ast.Constant) and a.value in BINARY_MODES
               for a in node.args):
            continue                      # binary has no encoding to get wrong
        out.append(node.lineno)
    return sorted(out)


def test_the_costing_path_never_reads_or_writes_text_blind():
    dirty = {}
    for rel in PINNED:
        p = ROOT / rel
        if not p.exists():
            continue                      # moved or renamed: not this test's argument
        lines = _offenders(p)
        if lines:
            dirty[rel] = lines

    assert not dirty, (
        "These are on the costing path. Their encoding must come from the code, "
        "not from whatever locale the machine happens to have —\n"
        + "\n".join(f"  {f}: line(s) {', '.join(map(str, ls))}"
                    for f, ls in sorted(dirty.items()))
        + "\n\nPass encoding='utf-8-sig' on reads and encoding='utf-8' on writes. "
          "See this file's docstring for what happened the last time we didn't."
    )


def test_the_pinned_list_still_points_at_real_files():
    """A file renamed out from under this list would silence it quietly."""
    missing = [f for f in PINNED if not (ROOT / f).exists()]
    assert not missing, (
        "The pinned list names files that no longer exist, so it is guarding "
        "nothing for them: " + ", ".join(missing)
    )


if __name__ == "__main__":
    # Informational: everything else still on the locale's mercy.
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                         capture_output=True, text=True).stdout
    rest = {}
    for rel in (r.strip() for r in out.split("\n")):
        if not rel.endswith(".py") or rel in PINNED:
            continue
        if any(x in f"/{rel}" for x in ("_archive/", "/tests/")) or Path(rel).name.startswith("test_"):
            continue
        n = len(_offenders(ROOT / rel))
        if n:
            rest[rel] = n
    print(f"costing path: clean ({len(PINNED)} files)")
    print(f"elsewhere:    {sum(rest.values())} unpinned sites across {len(rest)} files")
    for f, n in sorted(rest.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {n:3d}  {f}")
