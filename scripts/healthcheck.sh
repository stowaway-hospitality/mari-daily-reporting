#!/usr/bin/env bash
#
# One command that runs every gate CI runs, in the same order, against your
# working tree. Green here == green on push. Use it before a commit, or any
# time you want to know the whole system still holds.
#
#     bash scripts/healthcheck.sh
#
# Exit 0 = everything passed. Exit 1 = at least one gate failed (the summary
# at the bottom names which). It keeps going after a failure so one red gate
# doesn't hide another — you see the full picture in one run.
#
# This is a convenience mirror of .github/workflows/tests.yml, NOT a
# replacement: CI is still the source of truth. If you add a gate to CI, add
# it here too (and vice versa) — a divergence means "green locally, red on
# push", which is exactly the surprise this script exists to kill.
set -u
cd "$(dirname "$0")/.." || exit 2

# ---- pick the interpreter that can run the project ------------------------
# pyyaml is the project's hard runtime dep (recipes, venues, aggregator all
# import it), so drive the gauntlet with the first interpreter that has it —
# not whatever `python3` happens to resolve to. pytest is handled separately
# below, because on some machines it lives on a different interpreter.
PY=""
for cand in python3 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ] || continue
  if "$cand" -c "import yaml" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "!! no interpreter with pyyaml found. Install deps:"
  echo "     python3 -m pip install -r requirements.txt"
  exit 2
fi
echo "using interpreter: $PY ($("$PY" --version 2>&1))"
HAS_PYTEST=0 ; "$PY" -c "import pytest" >/dev/null 2>&1 && HAS_PYTEST=1

# The interpreter the live LaunchAgents actually run under (see
# ~/Library/LaunchAgents/com.stowaway.*.plist). The production scripts must
# IMPORT cleanly under this one, even if it differs from the test interpreter —
# a 3.9-only import crash would take out the 6am poller with a green CI.
LIVE_PY="/usr/bin/python3"

pass=0 ; fail=0 ; skip=0 ; failed_names=""

run () {          # run "Human name" command args...
  local name="$1" ; shift
  printf '\n=== %s ===\n' "$name"
  if "$@" ; then
    pass=$((pass+1))
  else
    fail=$((fail+1)) ; failed_names="${failed_names}\n  - ${name}"
  fi
}

# ---- the gauntlet, in CI order --------------------------------------------
run "Architecture guard"            "$PY" scripts/arch_guard.py
run "Schema guard (history CSVs)"   "$PY" scripts/schema_guard.py
if [ "$HAS_PYTEST" -eq 1 ]; then
  run "pytest"                      "$PY" -m pytest -q
else
  printf '\n=== pytest ===\n'
  echo "SKIPPED — pytest not installed for $PY."
  echo "  Install it into this interpreter to run the 289-test suite locally:"
  echo "     $PY -m pip install -r requirements.txt"
  echo "  (CI still runs pytest on every push, so this gate is covered there.)"
  skip=$((skip+1))
fi
run "Mari recovery (script-shaped)" "$PY" scripts/test_mari_recovery.py
run "Closed-week leave split"       "$PY" scripts/test_closed_week_leave.py
run "Wage calibration"              "$PY" scripts/test_wage_calibration.py
run "Wage open week"                "$PY" scripts/test_wage_open_week.py
run "Superannuation actuals"        "$PY" scripts/test_super_actuals.py
run "Corp payroll"                  "$PY" scripts/test_corp_payroll.py
run "Auth gate"                     "$PY" scripts/test_auth_gate.py
run "Invoice battletest (offline)"  "$PY" modules/invoices/tests/battletest_pipeline.py --offline
run "Wages reconcile to Xero"       "$PY" scripts/reconcile_wages.py
run "Dashboard P&L conservation"    node scripts/test_pnl_model.mjs
run "Dashboard render contract"     node scripts/test_dashboard_render.mjs
run "Dashboard unit discipline"     node scripts/test_dashboard_units.mjs
run "Site builds, refs resolve"     "$PY" scripts/build_site.py

# ---- live scripts must import under the LaunchAgent interpreter ------------
# Not in CI (CI has one Python). This is the gate that would have caught a
# core module going 3.10-only while /usr/bin/python3 runs the live poller.
printf '\n=== Live scripts import under %s ===\n' "$LIVE_PY"
if [ -x "$LIVE_PY" ]; then
  live_ok=1
  for m in modules.invoices.pull_mailbox modules.invoices.xero_process_approvals modules.invoices.run; do
    if "$LIVE_PY" -c "import importlib; importlib.import_module('$m')" >/dev/null 2>&1; then
      echo "  ok  $m"
    else
      echo "  FAIL  $m — will crash the live automation"; live_ok=0
    fi
  done
  if [ "$live_ok" -eq 1 ]; then pass=$((pass+1)); else
    fail=$((fail+1)) ; failed_names="${failed_names}\n  - Live scripts import under ${LIVE_PY}"
  fi
else
  echo "  ($LIVE_PY not present — skipping live-interpreter check)"
fi

# ---- costs.csv must reproduce from source (derived-file freshness) --------
printf '\n=== Deterministic feeds reproduce ===\n'
cp data/costs.csv /tmp/costs.before.csv 2>/dev/null && \
  "$PY" modules/recipes/pipeline/build_costs.py >/dev/null 2>&1 && \
  diff -q /tmp/costs.before.csv data/costs.csv >/dev/null 2>&1
if [ $? -eq 0 ]; then
  echo "costs.csv reproduces from source" ; pass=$((pass+1))
else
  echo "costs.csv is STALE — rerun build_costs.py and commit"
  fail=$((fail+1)) ; failed_names="${failed_names}\n  - Deterministic feeds reproduce"
fi

# ---- cost guard must stay AND, not OR -------------------------------------
# The pattern is assembled from fragments on purpose: if the contiguous string
# lived in this file, the grep (and CI's identical grep over scripts/) would
# match its own source and fail forever. "max(""DRIFT" is two adjacent shell
# literals — the bytes on disk are max("  "DRIFT, which the search never hits.
printf '\n=== Guard rails still guard ===\n'
guard_pat="max(""DRIFT_ABS\|max(""SUSPECT_ABS"
if grep -rn "$guard_pat" core/ modules/ scripts/ 2>/dev/null ; then
  echo "cost guard is using an OR where it must be AND."
  fail=$((fail+1)) ; failed_names="${failed_names}\n  - Guard rails still guard"
else
  echo "guards OK" ; pass=$((pass+1))
fi

# ---- summary ---------------------------------------------------------------
printf '\n============================================\n'
printf 'HEALTHCHECK: %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
if [ "$fail" -ne 0 ]; then
  printf 'Failed gates:%b\n' "$failed_names"
  exit 1
fi
if [ "$skip" -ne 0 ]; then
  printf 'All runnable gates green (%d skipped for missing local deps — see above).\n' "$skip"
else
  printf 'All gates green — safe to commit and push.\n'
fi
exit 0
