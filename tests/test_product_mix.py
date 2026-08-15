"""The full daily product mix must stay full, and must tie to the day it came from.

WHY: the stock ledger (INVENTORY_ARCHITECTURE.md) deducts inventory from this
file. The daily record's `top_products` is the top 20 by revenue — deducting
from THAT would under-deduct silently and forever: on-hand drifts down slower
than reality and every variance comes out wrong in the flattering direction,
which is the class of error this repo treats as dangerous.

Two failures are therefore asserted here, both with real measured numbers:

  1. TRUNCATION. Stowaway rang 207 product lines on 2026-08-14. If a mix file
     ever comes back at 20, someone has pointed the ledger at the dashboard
     panel — the whole reason this file exists.
  2. DRIFT. A mix that doesn't sum to its day's revenue is a mix that has lost
     or gained lines. It must be flagged `reconciled: false`, never published
     as if it were whole.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIX_DIR = ROOT / "data" / "product_mix"
DAILY_CSV = ROOT / "data" / "products_daily.csv"
MIX_FILES = sorted(MIX_DIR.glob("*.json"))

# Measured 2026-08-15 off data/insights_stow_2026-08-14.csv (a Friday).
# 207 product lines, $9,793.95 ex-GST, 825.7 units. The daily record's
# top_products holds 20 of those 207.
REFERENCE = {
    "file": "stow_2026-08-14.json",
    "row_count": 207,
    "rev_ex": 9793.95,
    "qty": 825.7,
}
TOP_PRODUCTS_CAP = 20


def test_mix_files_exist():
    assert MIX_FILES, (
        "no data/product_mix/*.json — the stock ledger has nothing to deduct from. "
        "Run scripts/backfill_product_mix.py.")


def test_reference_day_is_not_truncated():
    """The exact day, the exact numbers. A regression here is the bug."""
    path = MIX_DIR / REFERENCE["file"]
    assert path.exists(), f"{REFERENCE['file']} missing — that day is the measured baseline"
    d = json.loads(path.read_text())

    assert d["truncated"] is False
    assert d["row_count"] == REFERENCE["row_count"], (
        f"Stowaway rang {REFERENCE['row_count']} product lines on 2026-08-14; this file "
        f"has {d['row_count']}. If it is {TOP_PRODUCTS_CAP}, the mix has been wired to "
        f"top_products and every stock deduction from it under-counts.")
    assert len(d["products"]) == d["row_count"], "row_count disagrees with the rows"
    assert d["totals"]["rev_ex"] == pytest.approx(REFERENCE["rev_ex"], abs=0.01)
    assert d["totals"]["qty"] == pytest.approx(REFERENCE["qty"], abs=0.01)


@pytest.mark.parametrize("path", MIX_FILES, ids=lambda p: p.name)
def test_every_mix_ties_to_its_day(path):
    d = json.loads(path.read_text())
    r = d["reconciliation"]
    total = sum(p["rev_ex"] for p in d["products"])

    assert total == pytest.approx(d["totals"]["rev_ex"], abs=0.01), (
        f"{path.name}: the rows do not sum to the stated total")
    assert d["reconciled"] is (abs(r["gap_ex"]) <= 0.01 and abs(r["gap_inc"]) <= 0.01), (
        f"{path.name}: reconciled flag disagrees with its own gaps — a mix that "
        f"doesn't tie must say so, because a consumer trusts the flag not the maths")
    assert d["reconciled"], (
        f"{path.name}: mix sums to ${d['totals']['rev_ex']:,.2f} ex, the day says "
        f"${r['expected_rev_ex']:,.2f} (gap ${r['gap_ex']:+,.2f}). Lines have been "
        f"lost or gained. Do not deduct stock from this day.")


@pytest.mark.parametrize("path", MIX_FILES, ids=lambda p: p.name)
def test_mix_carries_the_fields_a_deduction_needs(path):
    d = json.loads(path.read_text())
    assert d["schema"] == "product_mix/1"
    for p in d["products"][:50]:
        assert {"name", "qty", "rev_ex", "rev_inc", "dept", "till"} <= set(p), (
            f"{path.name}: a mix line needs name/qty/rev_ex/rev_inc/dept/till; got {sorted(p)}")
        assert isinstance(p["qty"], (int, float)), (
            f"{path.name}: {p['name']} qty is {type(p['qty'])} — a string qty "
            f"concatenates instead of summing")
        assert isinstance(p["rev_ex"], (int, float))


def test_mix_is_wider_than_the_dashboard_panel():
    """Any busy day must carry more lines than top_products holds.

    This is the cheap, general form of the reference-day test: it keeps
    working when 2026-08-14 eventually ages out of the repo.
    """
    busiest = max(MIX_FILES, key=lambda p: json.loads(p.read_text())["row_count"])
    n = json.loads(busiest.read_text())["row_count"]
    assert n > TOP_PRODUCTS_CAP, (
        f"the widest mix in data/ is {busiest.name} at {n} lines. Real trading days "
        f"ring hundreds of products; {TOP_PRODUCTS_CAP} is the dashboard cap, and a "
        f"mix capped at it is the truncation this file exists to prevent.")


def test_daily_rollup_covers_every_mix_file():
    """products_daily.csv is derived — if it lags the facts it is a fossil."""
    if not DAILY_CSV.exists():
        pytest.skip("data/products_daily.csv not built yet")

    with DAILY_CSV.open() as f:
        rows = list(csv.DictReader(f))
    in_csv = {(r["date"], r["prefix"]) for r in rows}
    on_disk = {(json.loads(p.read_text())["date"], json.loads(p.read_text())["prefix"])
               for p in MIX_FILES}

    missing = on_disk - in_csv
    assert not missing, (
        f"{len(missing)} venue-day(s) have a mix file but no rows in products_daily.csv "
        f"(e.g. {sorted(missing)[:3]}). Re-run scripts/build_products_daily.py — a stale "
        f"rollup silently deducts nothing for those days.")
