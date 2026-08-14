#!/usr/bin/env python3
"""
Mutation testing: break it on purpose, and see whether anything notices.

    python3 scripts/mutation_test.py            # run every mutant
    python3 scripts/mutation_test.py --list     # just show them

WHY. A green suite tells you the tests pass. It does not tell you the tests would
FAIL if the code were wrong, and this repo has evidence that the two are not the
same thing. On 2026-08-10 three separate tests turned out to be asserting a
DEFECT rather than a fix — they would have stayed green forever once the shape of
the bug shifted — and the pack-agreement detector had a structural blind spot
that audit_book had been reporting the whole time.

So: re-introduce each real defect this repo has actually had, one at a time, and
record which gate catches it. A mutant that SURVIVES is a defect that could ship.

Every mutation below is a bug that was genuinely in this codebase, not an
invented one. That is the point — it measures the guards against history rather
than against imagination.

HOW IT WORKS. Patch one file, rebuild the derived feeds, run the gates, restore.
The restore is unconditional, and the rebuild runs again after it, so a crash
mid-run leaves the tree as it found it. It is slow (a rebuild per mutant) and is
not part of CI; run it after changing a guard, or when you want to know what the
suite is currently worth.

LAST RUN: 2026-08-10, 15/15 caught, 0 survived.
"""
import argparse
import pathlib
import subprocess
import sys
ROOT = pathlib.Path(__file__).resolve().parents[1]

# (file, find, replace, what it breaks) — each is a REAL defect this repo has had
MUTANTS = [
 ("modules/invoices/parsers/ilg.py", "return size * mult, base_unit",
  "return size * mult * per, base_unit", "ILG pack becomes the CASE again (the 6x under-cost)"),
 ("modules/invoices/parsers/ilg.py", "cases, singles = int(cell), 0",
  "cases, singles = 0, int(cell)", "a bare Qty read as loose singles, not cartons"),
 ("modules/invoices/build_cogs_list.py", 'HOLD_BAND = Decimal("0.99")',
  'HOLD_BAND = Decimal("0.0")', "the re-derive stops holding cheaper readings"),
 ("modules/invoices/build_cogs_list.py",
  'DERIVED = ("cost_per_unit_incl_gst", "pack_qty", "pack_unit", "cost_per_base_unit")',
  'DERIVED = ("cost_per_unit_incl_gst",)', "pack stops tracking its source"),
 ("modules/invoices/pack_size.py", "return next((r for r in runs if r in known), runs[0])",
  "return runs[0]", "UOM token falls back to the first alpha run (200g punnet -> 'g')"),
 ("modules/invoices/pack_size.py", "if _MULTI.search(raw_uom or \"\"):",
  "if False:", "a MULTI (6x700ML) is read as ONE unit — the case/bottle bug reborn"),
 ("scripts/check_pack_agreement.py", 'TOL = Decimal("0.01")',
  'TOL = Decimal("0.00000001")', "pack-agreement tolerance too tight to ever match"),
 ("scripts/check_pack_agreement.py", "if not (before and after):",
  "if False:", "regime changes reported as misreads"),
 ("scripts/check_pack_as_rate.py", "TOL = 0.15", "TOL = 0.0000001",
  "pack-as-rate can never match a pack size"),
 ("scripts/convert_lightspeed_recipes.py", '"Pizza Beef Brisket [Kg]": "Cooked Beef Brisket [1Kg]",',
  "", "the brisket duplicate is un-consolidated"),
 ("scripts/convert_lightspeed_recipes.py", "eff = so * (_q2 / _yf[0])",
  "eff = so * (ls / sl) if sl else so * (_q2 / _yf[0])",
  "recorded yields stop beating the LS line ratio"),
 ("modules/recipes/pipeline/build_costs.py", 'if _ctn and _ps in ("", "1"):',
  "if False:", "a CTN carton divided by one piece under an override"),
 ("modules/recipes/pipeline/build_ingredients.py", "suc = single_unit_content(note)",
  "suc = None", "the invoice UOM stops outranking the description"),
 ("data/prep_yields.yaml", "yield_qty: 6000", "yield_qty: 10500",
  "brisket cook yield back to the impossible 105%"),
 ("data/pack_overrides.yaml", "pack_qty: 3785", "pack_qty: 1",
  "Frank's gallon priced per litre again"),
]

def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True, text=True).returncode

GATES = [
 ("pytest",           "python3 -m pytest -p no:warnings -q"),
 ("pack-agreement",   "python3 scripts/check_pack_agreement.py --strict"),
 ("pack-as-rate",     "python3 scripts/check_pack_as_rate.py --strict"),
 ("arch_guard",       "python3 scripts/arch_guard.py"),
 ("schema_guard",     "python3 scripts/schema_guard.py"),
]
REBUILD = ("python3 modules/recipes/pipeline/build_costs.py >/dev/null 2>&1; "
           "python3 scripts/convert_lightspeed_recipes.py >/dev/null 2>&1; "
           "python3 scripts/build_cost_book_flags.py >/dev/null 2>&1")

if "--list" in sys.argv:
    for _p, _f, _r, _w in MUTANTS:
        print(f"  {_w}")
    raise SystemExit(0)

# THIS SCRIPT EDITS THE WORKING TREE IN PLACE, one file at a time, and a file is
# only correct again AFTER its mutation finishes. It takes ~25 minutes. On
# 2026-08-14 a `git commit -A` was run against the tree while it was mid-flight
# and committed a live mutation — build_cogs_list.py's DERIVED tuple shipped as
# ("cost_per_unit_incl_gst",), which is precisely the defect the mutation exists
# to prove we catch. It was found only because a later `git status` showed the
# working tree DISAGREEING with HEAD in the direction of correctness.
#
# A guard, not a comment, because a comment cannot stop a concurrent shell:
_LOCK = ROOT / ".mutation_test.lock"
if _LOCK.exists():
    print(f"REFUSING: {_LOCK} exists — a mutation run is already rewriting this "
          f"tree.\nIf you are sure none is, delete the lock and retry.")
    raise SystemExit(2)
if run("git diff --quiet && git diff --cached --quiet") != 0:
    print("REFUSING: the working tree is dirty. This script rewrites tracked "
          "files and restores them from memory, so an unrelated edit in flight "
          "can be lost or committed mid-mutation. Commit or stash first.")
    raise SystemExit(2)
_LOCK.write_text(f"pid {__import__('os').getpid()}\n", encoding="utf-8")
import atexit as _atexit
_atexit.register(lambda: _LOCK.exists() and _LOCK.unlink())

print("!! mutation run started — DO NOT commit from this tree until it prints a "
      "summary; tracked files are mutated in place for ~25 minutes.\n")
print(f"{'mutation':<58} {'caught by':<34} verdict")
survived = []
for path, find, repl, what in MUTANTS:
    f = ROOT / path
    orig = f.read_text(encoding="utf-8")
    if find not in orig:
        print(f"{what[:58]:<58} {'-- pattern not found --':<34} SKIP")
        continue
    f.write_text(orig.replace(find, repl, 1), encoding="utf-8")
    run(REBUILD)
    caught = [n for n, c in GATES if run(c) != 0]
    f.write_text(orig, encoding="utf-8")
    run(REBUILD)
    verdict = "caught" if caught else "*** SURVIVED ***"
    if not caught:
        survived.append(what)
    print(f"{what[:58]:<58} {(', '.join(caught) or 'nothing'):<34} {verdict}")

print(f"\n{len(MUTANTS)-len(survived)}/{len(MUTANTS)} caught. {len(survived)} survived:")
for s in survived:
    print(f"   - {s}")

raise SystemExit(1 if survived else 0)
