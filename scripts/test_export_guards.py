"""The export guards, against the real files that made them necessary.

Fixtures are the committed exports, not invented ones, because the whole point
is what actually arrives in the mailbox:

  insights_hg_2026-08-15.csv  a normal trading Saturday, 66 product rows
  insights_hg_2026-08-16.csv  header only — HG was CLOSED that Sunday
  insights_hg_2026-08-03.csv  \\ byte-identical 51-row Mondays. One is a re-send.
  insights_hg_2026-08-10.csv  /  Discovered 17 Aug 2026.

Run: python3 scripts/test_export_guards.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_guards import (StaleExport, assert_not_a_copy,  # noqa: E402
                           is_closed_day, is_product_level, read_rows)

DATA = Path(__file__).resolve().parent.parent / "data"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


trading = DATA / "insights_hg_2026-08-15.csv"
closed = DATA / "insights_hg_2026-08-16.csv"
mon_a = DATA / "insights_hg_2026-08-03.csv"
mon_b = DATA / "insights_hg_2026-08-10.csv"

print("-- a normal trading day --")
if trading.exists():
    rows, _ = read_rows(trading)
    check("has product rows", len(rows) > 10, str(len(rows)))
    check("is product-level", is_product_level(trading))
    check("is not treated as closed", not is_closed_day(trading))
else:
    check("fixture present: 15 Aug", False, "missing")

print("\n-- a CLOSED day (header only) --")
if closed.exists():
    check("recognised as closed", is_closed_day(closed))
    check("closed is NOT an error — no exception", (assert_not_a_copy(closed, "hg") is None))
    # This is the distinction that was missing: closed and never-arrived looked
    # identical on the dashboard, and one of them is a fact.
    check("a closed day carries no product rows", read_rows(closed)[0] == [])
else:
    check("fixture present: 16 Aug", False, "missing")

print("\n-- a RE-SENT report (the dangerous one) --")
if mon_a.exists() and mon_b.exists():
    same = mon_a.read_bytes() == mon_b.read_bytes()
    check("the two Mondays really are byte-identical", same,
          "fixtures diverged — re-point this test")
    raised = False
    try:
        assert_not_a_copy(mon_b, "hg")
    except StaleExport as e:
        raised = True
        msg = str(e)
    check("refused rather than aggregated", raised)
    if raised:
        check("the message names the other file", "2026-08-03" in msg, msg[:80])
else:
    check("fixtures present: 3 + 10 Aug", False, "missing")

print("\n-- a unique day passes --")
if trading.exists():
    ok = True
    try:
        assert_not_a_copy(trading, "hg")
    except StaleExport as e:
        ok = False
        print("     ", e)
    check("15 Aug is accepted", ok)

print("\n-- the alternate export shape (Position/Product Number) --")
alt = DATA / "insights_stow_2026-08-10.csv"
if alt.exists():
    from export_guards import is_schema_b
    check("recognised as the alternate shape", is_schema_b(alt))
    rows, fields = read_rows(alt)
    check("normalised to the standard field names",
          "Product Name" in fields and "$ Sales" in fields, str(fields[:4]))
    check("and its rows are readable", len(rows) > 10, str(len(rows)))

print("\n-- reconciliation against the till --")
from export_guards import reconcile_against_till
good_p, good_h = DATA / "insights_stow_2026-08-15.csv", DATA / "stow_hourly_2026-08-15.csv"
bad_p,  bad_h  = DATA / "insights_stow_2026-08-11.csv", DATA / "stow_hourly_2026-08-11.csv"
if good_p.exists() and good_h.exists():
    ok, msg = reconcile_against_till(good_p, good_h)
    check("a normal day reconciles", ok, msg)
if bad_p.exists() and bad_h.exists():
    ok, msg = reconcile_against_till(bad_p, bad_h)
    # 11 Aug 2026: $3,807 of a $9,438 day. The break this exists for.
    check("11 Aug is caught (60% short of the till)", not ok, msg)
missing = reconcile_against_till(good_p, DATA / "stow_hourly_1999-01-01.csv")
check("no hourly export = no false alarm", missing[0] is True, missing[1])

print("\n" + "=" * 58)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)
