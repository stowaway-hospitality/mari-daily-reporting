"""Stock IN coverage must not silently fall.

The first cut of the receive builder booked 12.5% of invoice lines, because it
demanded every one resolve to a `lightspeed:<id>` through product_map.csv. The
repo's own identity model already accepts a supplier code as a first-class key
(core.domain.purchasable_id), and honouring that took it to 73.5%.

A regression here is quiet and expensive: fewer receipts booked means stock that
looks like it lasts longer than it does, which is the flattering direction. So
the floor is asserted with the real measured number.
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from build_receive_movements import (collect_lines,           # noqa: E402
                                     derive_base_units)
from ledger import load_base_units                            # noqa: E402

LEDGER = ROOT / "data" / "ledger"

# Measured 2026-08-15: 2,572 of 3,501 lines, 712 distinct items. The floors sit
# a little under, so ordinary drift in the corpus doesn't cry wolf — but a
# structural regression (identity or units breaking) drops far below them.
MIN_BOOKED_PCT = 65.0
MIN_ITEMS = 600


def test_every_line_has_an_identity_or_a_stated_reason():
    """A supplier code is the natural key. Where one exists we must form an id
    from it — never from the description."""
    rows = collect_lines()
    assert rows, "no invoice stock lines found"
    for r in rows[:500]:
        if r["code"]:
            assert r["item"], (
                f"{r['supplier']} code {r['code']!r} produced no item id, but a "
                f"supplier code IS the natural key")
            assert ":" in r["item"]


def test_base_units_never_average_a_conflict():
    """An item delivered in two dimensions is refused, not resolved by majority.
    That is the CTN-6-read-as-one-tin failure wearing a new hat."""
    rows = collect_lines()
    units, conflicts = derive_base_units(rows, load_base_units())
    for item in conflicts:
        assert item not in units or item in load_base_units(), (
            f"{item} is delivered in {sorted(conflicts[item])} yet was still given a "
            f"base unit without a recipe deciding it")


def test_receive_coverage_has_not_regressed():
    files = sorted(LEDGER.glob("movements_*.csv"))
    if not files:
        pytest.skip("no ledger — run scripts/build_receive_movements.py --write")

    rows = []
    for path in files:
        with path.open() as f:
            rows.extend(csv.DictReader(f))
    receives = [r for r in rows if r["reason"] == "receive"]
    lines = collect_lines()

    pct = len(receives) / len(lines) * 100
    items = {r["item_id"] for r in receives}
    assert pct >= MIN_BOOKED_PCT, (
        f"only {pct:.1f}% of invoice stock lines book as receive movements "
        f"(floor {MIN_BOOKED_PCT}%). Fewer receipts booked = stock that looks "
        f"like it lasts longer than it does.")
    assert len(items) >= MIN_ITEMS, (
        f"only {len(items)} distinct items receivable (floor {MIN_ITEMS})")


def test_no_receive_row_is_zero_quantity():
    """A zero receipt is a parse that failed quietly."""
    files = sorted(LEDGER.glob("movements_*.csv"))
    if not files:
        pytest.skip("no ledger yet")
    for path in files:
        with path.open() as f:
            for r in csv.DictReader(f):
                if r["reason"] == "receive":
                    assert Decimal(r["qty_base"]) > 0, f"{r['item_id']} received 0 {r['base_unit']}"
