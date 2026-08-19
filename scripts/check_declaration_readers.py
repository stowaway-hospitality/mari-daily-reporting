#!/usr/bin/env python3
"""
Does everyone who SHOULD read a declaration, read it?

    python3 scripts/check_declaration_readers.py            # the matrix
    python3 scripts/check_declaration_readers.py --strict   # exit 1 on a NEW gap
    python3 scripts/check_declaration_readers.py --rebase   # pin today's count

THE QUESTION THIS ANSWERS, AND THE ONE IT DOES NOT
--------------------------------------------------
`check_declarations_bind.py` asks whether a declaration still describes a record
that exists. That catches a ruling whose target moved. It cannot catch the other
failure, which is more expensive because nothing about the file looks wrong:

    the declaration is perfect, the record is there, and the module that
    actually prices the menu has never opened the file.

Four of those in one session on 2026-08-19, each already solved and each silently
doing nothing. The pattern is always the same shape: one reader was wired, the
others were not, and there was no statement anywhere of who was supposed to be.

`core/declarations.py` is now that statement. This checks it.

WHAT COUNTS AS READING IT
-------------------------
A module consults a declaration if its own source names the file, or if it
imports from a module that does -- the shared-loader case, which is the shape we
WANT and must not be reported as a gap. `modules.recipes.units` owns the yield
relabels and `scripts/convert_lightspeed_recipes.py` gets them by importing
`apply_declared_yield_relabels`; that is a reader, and a good one.

WHY IT RATCHETS RATHER THAN FAILS
---------------------------------
Three gaps were open the day this was written and each needs a judgement, not a
wire-up -- see the `known_gaps` on each declaration for the worked reasoning.
Failing on them would have meant either landing three money-moving changes
unreviewed or shipping a red main, and a guard that ships red gets switched off.

So the pinned gaps are named, with evidence, in the registry; and this fails when
a NEW one appears. A new gap means somebody added a declaration and wired one
reader, which is exactly the habit that cost the days.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.declarations import Declaration, all_declarations  # noqa: E402

BASELINE = ROOT / "baselines" / "declaration_readers.json"


def _resolve(reader: str) -> Path:
    """A reader is a path ('scripts/x.py') or a dotted module ('core.domain')."""
    if reader.endswith(".py"):
        return ROOT / reader
    return ROOT / (reader.replace(".", "/") + ".py")


def _imported_modules(src: str) -> set[str]:
    """Every module this source imports, as dotted names."""
    out: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            # `from scripts.x import y` and bare `from x import y` for scripts
            # added to sys.path both appear here.
    return out


def _names_file(src: str, filename: str) -> bool:
    """Does this source CONSTRUCT the path, as opposed to mentioning it?

    Only non-docstring string literals count. A plain substring search says the
    live converter reads data/recipe_yields.yaml -- it does not; it names the
    file in a comment explaining why the yields it uses came from somewhere
    else. A detector that scores prose as wiring would have pronounced the very
    gap this script exists to find already closed.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return filename in src

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings and _is_path_literal(node.value,
                                                                   filename)):
            return True
    return False


def _is_path_literal(value: str, filename: str) -> bool:
    """Is this string the PATH, or a sentence that happens to mention it?

    `ROOT / "data" / "prep_yields.yaml"` is a reader. `print(f"... see
    data/measured_yields.yaml, which outranks both files")` is a message, and
    counting it made two new scripts look like they had hand-rolled a parse each
    when both go through the registry — the guard reporting its own explanatory
    output as a violation. A path literal is the path and nothing else.
    """
    v = value.strip()
    if any(c.isspace() for c in v):
        return False
    return v == filename or v.endswith("/" + filename)


#: The registry itself names every declaration file, by construction. Walking
#: INTO it would score every module that imports it as a reader of all twenty —
#: the guard would pass completely and mean nothing. So it is a wall: a reader
#: has to name the specific entry, in its own source, to count.
REGISTRY_MODULE = ROOT / "core" / "declarations.py"


def _mod_path(mod: str) -> Path | None:
    cand = ROOT / (mod.replace(".", "/") + ".py")
    if cand.exists():
        return cand
    cand = ROOT / "scripts" / (mod.split(".")[-1] + ".py")
    return cand if cand.exists() else None


def _touches(src: str, decl: Declaration) -> bool:
    """Names the file, or names the registry entry that owns the file.

    The second half matters as much as the first. Once a declaration is behind a
    loader the filename stops appearing anywhere but core/declarations.py — which
    is the POINT — so a check that only looked for filenames would score every
    properly-wired reader as a gap and push people back to spelling paths out by
    hand.
    """
    if _names_file(src, decl.path.name):
        return True
    # The registry symbol only counts when the registry is actually imported:
    # build_costs.py has its own module-level PACK_OVERRIDES path constant, and
    # a bare name match would have read that as wiring.
    return decl.const in src and "core.declarations" in src


# Three hops: reader -> shared loader -> the registry. Deeper than that and an
# "it reads it, indirectly" claim stops being one anybody can check by eye.
_MAX_HOPS = 3


def consults(reader: str, decl: Declaration) -> tuple[bool, str]:
    """Does this reader reach the declaration, directly or through a loader?"""
    p = _resolve(reader)
    if not p.exists():
        return False, "module does not exist"

    seen: set[Path] = set()
    frontier: list[tuple[Path, str]] = [(p, "direct")]
    for _hop in range(_MAX_HOPS):
        nxt: list[tuple[Path, str]] = []
        for path, how in frontier:
            if path in seen or path == REGISTRY_MODULE:
                continue
            seen.add(path)
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _touches(src, decl):
                return True, how
            for mod in _imported_modules(src):
                cand = _mod_path(mod)
                if cand and cand not in seen:
                    nxt.append((cand, how if how != "direct" else f"via {mod}"))
        frontier = nxt
        if not frontier:
            break
    return False, "never opens it"


def bypasses() -> list[dict]:
    """Modules that spell a declaration's path out instead of asking the registry.

    THE SECOND HALF OF THE PROBLEM. A reader that hand-rolls its own parse is
    reading the file TODAY, so the matrix above scores it green — and it is
    still how the disconnections happen. Six modules each spelled
    `ROOT / "data" / "pack_overrides.yaml"` out for themselves; every one is a
    place a seventh reader can be added without anyone noticing, and a place the
    encoding, the last-wins rule, or the shape of the document can quietly
    diverge. `core/pack_overrides.py` lost ~700 cost observations to exactly
    that: a per-reader `read_text()` under an ASCII locale.

    Ratcheted, not failed. Converting 40-odd call sites in one commit is how you
    ship a subtle regression to the P&L; the pin makes the number one that can
    only come down.
    """
    out: list[dict] = []
    for base in ("core", "modules", "scripts"):
        for p in sorted((ROOT / base).rglob("*.py")):
            if p == REGISTRY_MODULE or "/tests/" in str(p) or p.name.startswith("test_"):
                continue
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for d in all_declarations():
                if _names_file(src, d.path.name):
                    out.append({"module": str(p.relative_to(ROOT)),
                                "declaration": d.name})
    return out


def sweep() -> tuple[list[dict], list[dict]]:
    """(new gaps, pinned gaps) across the whole registry."""
    new: list[dict] = []
    pinned: list[dict] = []
    for d in all_declarations():
        if not d.readers:
            new.append({"declaration": d.name, "reader": "(none declared)",
                        "why": "no reader is claimed — a declaration nobody is "
                               "contractually bound to is already dead"})
            continue
        for r in d.readers:
            ok, how = consults(r, d)
            if ok:
                continue
            row = {"declaration": d.name, "reader": r, "why": how}
            (pinned if r in d.known_gaps else new).append(row)
    return new, pinned


def print_matrix() -> None:
    print(f"{'declaration':<26}{'reader':<52}{'status'}")
    print("-" * 96)
    for d in all_declarations():
        if not d.path.exists():
            print(f"{d.name:<26}{'-- file is missing from the tree --':<52}")
            continue
        for r in d.readers:
            ok, how = consults(r, d)
            if ok:
                mark = f"reads it ({how})"
            elif r in d.known_gaps:
                mark = "GAP (pinned)"
            else:
                mark = "GAP -- NEW"
            print(f"{d.name:<26}{r:<52}{mark}")
        if not d.readers:
            print(f"{d.name:<26}{'(nobody is required to read this)':<52}GAP -- NEW")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if a reader that should consult a declaration does not")
    ap.add_argument("--rebase", action="store_true", help="pin the current gap count")
    ap.add_argument("--quiet", action="store_true", help="findings only, no matrix")
    a = ap.parse_args()

    if not a.quiet:
        print_matrix()

    new, pinned = sweep()
    hand_rolled = bypasses()
    base = (json.loads(BASELINE.read_text()) if BASELINE.exists()
            else {"pinned_gaps": 0, "hand_rolled_parses": len(hand_rolled)})

    print(f"declared reader pairs that do not connect: "
          f"{len(new) + len(pinned)}  (pinned {len(pinned)}, new {len(new)})")
    print(f"modules parsing a declaration themselves rather than via the "
          f"registry: {len(hand_rolled)}  (pinned "
          f"{base.get('hand_rolled_parses', '?')})")

    for g in pinned:
        d = next(x for x in all_declarations() if x.name == g["declaration"])
        print(f"\n  PINNED  {g['declaration']} -> {g['reader']}")
        for line in d.known_gaps[g["reader"]].split(". "):
            if line.strip():
                print(f"          {line.strip().rstrip('.')}.")

    for g in new:
        print(f"\n  NEW     {g['declaration']} -> {g['reader']}   ({g['why']})")

    if a.rebase:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {"pinned_gaps": len(pinned),
             "hand_rolled_parses": len(hand_rolled),
             "note": "declaration -> reader pairs named in core/declarations.py "
                     "that do not connect. Pinned ones carry their reasoning in "
                     "the registry's known_gaps; this guard fails on a NEW one. "
                     "hand_rolled_parses counts modules that spell a declaration "
                     "path out instead of asking the registry — it may only fall."},
            indent=1) + "\n")
        print(f"\npinned at {len(pinned)} gap(s), {len(hand_rolled)} hand-rolled parse(s)")
        return 0

    rc = 0
    if a.strict and new:
        print(f"\n::error::{len(new)} declaration(s) name a reader that never "
              f"opens them. A ruling with evidence behind it is reaching one "
              f"module and not the one that prices the menu — wire it, or move "
              f"it to known_gaps with the reason it cannot be wired blind.")
        rc = 1
    if a.strict and len(hand_rolled) > base.get("hand_rolled_parses", len(hand_rolled)):
        print(f"\n::error::a new module parses a declaration file directly "
              f"({len(hand_rolled)} against a pin of {base['hand_rolled_parses']}). "
              f"Import it from core.declarations instead — a hand-rolled parse is "
              f"how a reader ends up disagreeing with every other reader about "
              f"the same file.")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
