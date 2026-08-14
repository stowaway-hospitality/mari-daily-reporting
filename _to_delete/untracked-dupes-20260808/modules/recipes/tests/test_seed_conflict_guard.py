"""
A ls-recipe-seed that wildly disagrees with the BO seed is a unit misread.

THE BUG
-------
Two seeds describe the same ProductID:

    bo-seed        2026-01-01  Massenez Elderflower 5000ML  $253.00 / 5000ml = $0.0506/ml
    ls-recipe-seed 2026-01-02  Massenez Elderflower [5L]    "$4.8333 per_L"  = $0.004833/ml

The BO row is a STATED product cost from the Back Office export. The LS row is
Lightspeed's own computed recipe-line cost — the number this whole project exists
to escape — and here it is 10.5x low ($24.17 for 5L of elderflower liqueur, less
than sesame oil; every Massenez sibling sits at $0.05-0.07/ml).

Because the LS row is dated ONE DAY LATER it wins the as-of lookup forever, so
Hugo Spritz reported 92.9% GP. Same class, opposite direction: Bittermen's Tiki
Bitters, where the LS seed reads 6.45x HIGH.

THE RULE
--------
When the two seeds for one ProductID disagree by more than SEED_CONFLICT_X, trust
the BO export and drop the LS row. Inside the band, leave both alone — a real
price move is not a 3x move, but an ordinary drift must still flow through.

This is deliberately narrow: it only fires when a second, independent, stated
figure contradicts the derived one. It never invents a number.
"""

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_costs import SEED_CONFLICT_X, ls_seed_is_misread  # noqa: E402


def test_massenez_elderflower_ls_seed_is_refused():
    """10.5x LOW vs the BO export — the real bug that made Hugo Spritz 92.9% GP."""
    assert ls_seed_is_misread(Decimal("0.004833"), Decimal("0.0506")) is True


def test_tiki_bitters_ls_seed_is_refused():
    """6.45x HIGH — the same class in the over-costing direction."""
    assert ls_seed_is_misread(Decimal("0.240000"), Decimal("0.037186")) is True


def test_ordinary_price_drift_is_kept():
    """A seed that merely differs a little is a real price, not a misread. These
    must keep flowing or the guard becomes a silent data-loss bug."""
    assert ls_seed_is_misread(Decimal("0.0675"), Decimal("0.0718")) is False   # Massenez Triple Sec
    assert ls_seed_is_misread(Decimal("0.058"), Decimal("0.050782")) is False  # Massenez Lychee
    assert ls_seed_is_misread(Decimal("0.070633"), Decimal("0.073943")) is False  # Bombay Dry


def test_boundary_is_not_hair_triggered():
    """Just inside the band is kept; well outside is refused."""
    assert ls_seed_is_misread(Decimal("2.9"), Decimal("1.0")) is False
    assert ls_seed_is_misread(Decimal("1.0"), Decimal("2.9")) is False
    assert ls_seed_is_misread(Decimal(str(SEED_CONFLICT_X + 1)), Decimal("1.0")) is True


def test_missing_or_zero_reference_never_fires():
    """No BO seed to compare against -> we have no second opinion -> keep the LS
    row. The guard must never fire on absence."""
    assert ls_seed_is_misread(Decimal("0.004833"), None) is False
    assert ls_seed_is_misread(Decimal("0.004833"), Decimal("0")) is False
    assert ls_seed_is_misread(None, Decimal("0.05")) is False
    assert ls_seed_is_misread(Decimal("0"), Decimal("0.05")) is False
