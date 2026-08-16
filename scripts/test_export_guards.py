"""The export guards, against the real files that made them necessary.

Fixtures are the committed exports, not invented ones, because the whole point
is what actually arrives in the mailbox:

  insights_hg_2026-08-15.csv  a normal trading Saturday, 66 product rows
  insights_hg_2026-08-16.csv  header only — HG was CLOSED that Sunday

The duplicate cases use synthetic files in a temp dir. They used to point at
insights_hg_2026-08-03/10.csv, which were byte-identical Mondays — but 10 Aug
was repaired on 2026-08-17 (it held 3 Aug's trade; 3 Aug was always genuine),
so pinning the test to real files meant it would fail the moment the data got
better. What is asserted about the committed pair now is that they are NOT
duplicates any more.

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
# Synthetic fixtures, not committed ones. The original test pinned itself to
# insights_hg_2026-08-03/10.csv being byte-identical; when those days were
# repaired on 2026-08-17 the test would have started failing for the happiest
# possible reason. A guard's test should not depend on the bug still being in
# the data.
import shutil          # noqa: E402
import tempfile        # noqa: E402

HEADER = ("Product Name,Product Quantity,$ Sales,Total Tax,Cost,"
          "% of Quantity,% of Sale Amount,Gross Profit %")
LINES = [
    "Unlimited Dumplings,29,$684.00,$62.12,$203.30,25%,42%,70%",
    "BBQ Pork Buns,3,$45.00,$4.08,$10.50,3%,3%,77%",
    "Tonkotsu,3,$58.00,$5.28,$12.49,3%,4%,78%",
    "Edamame,2,$22.00,$2.00,$0.00,2%,1%,0%",
]
FOOTER = ',37,"$809.00",$71.48,$226.29,100%,100%,72%'


def _write(d: Path, day: str, lines) -> Path:
    p = d / f"insights_hg_{day}.csv"
    p.write_text("\n".join([HEADER, *lines, FOOTER]) + "\n")
    return p


tmp = Path(tempfile.mkdtemp())
try:
    a = _write(tmp, "2026-08-03", LINES)
    b = _write(tmp, "2026-08-10", LINES)                 # exact re-send
    raised, msg = False, ""
    try:
        assert_not_a_copy(b, "hg", data_dir=tmp)
    except StaleExport as e:
        raised, msg = True, str(e)
    check("an exact re-send is refused", raised)
    if raised:
        check("the message names the other file", "2026-08-03" in msg, msg[:90])

    # THE CASE THE BYTE HASH MISSED. Lightspeed does not promise a stable order
    # for rows tying on the sort column — re-pulling HG 3 Aug on 2026-08-17
    # returned the same cents with six equal-quantity pairs swapped. A re-send
    # that came back re-sorted used to pass straight through.
    shuffled = [LINES[0], LINES[2], LINES[1], LINES[3]]  # Tonkotsu <-> BBQ Pork Buns
    c = _write(tmp, "2026-08-17", shuffled)
    check("the re-sorted copy is NOT byte-identical",
          c.read_bytes() != a.read_bytes())
    raised = False
    try:
        assert_not_a_copy(c, "hg", data_dir=tmp)
    except StaleExport:
        raised = True
    check("a RE-SORTED re-send is refused too", raised,
          "byte-hash regression: row order disguised the duplicate")

    # And a day that genuinely traded differently must still pass.
    d = _write(tmp, "2026-08-24", [LINES[0].replace(",29,", ",61,"), *LINES[1:]])
    ok = True
    try:
        assert_not_a_copy(d, "hg", data_dir=tmp)
    except StaleExport:
        ok = False
    check("a genuinely different day still passes", ok)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if mon_a.exists() and mon_b.exists():
    check("the committed 3 + 10 Aug Mondays are no longer duplicates",
          mon_a.read_bytes() != mon_b.read_bytes(),
          "these were repaired on 2026-08-17 — if this fails, one has regressed")

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
