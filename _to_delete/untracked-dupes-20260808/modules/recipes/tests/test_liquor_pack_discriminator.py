"""
Liquor pack discriminator — the regression the 2026-08-05 handoff demanded first.

THE TRAP
--------
A liquor invoice line is "BOMBAY DRY GIN" / "PATRON SILVER TEQUILA R" with no
size in the description, so resolve_pack refuses it and build_costs SKIPS it —
$1,583 of gin since June never reached the cost book, and 13 cocktails price off
a January seed.

The tempting fix (divide the line by its explicit pack_qty/pack_unit) is WRONG:
ILG records pack_qty as the CASE (4.2 L = 6x700 ML) but prices SOME lines per
BOTTLE. Bombay's $296.60 is a case (÷4200 mL is right); Patron's $76.52 is one
bottle (÷4200 mL under-costs it 6x, $2.94 -> $0.61 a pour).

THE DISCRIMINATOR
-----------------
Test BOTH readings against the product's own seed rate and take the one that
agrees. If neither agrees, return None (skip — under-costing spirits is the
flattering, dangerous direction; a line that matches nothing stays out).

THE REGRESSION THAT MATTERS: Patron Silver must NOT move to the 6x-cheap
case-misread. It reads as a bottle, landing next to its seed.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from modules.recipes.pipeline.build_costs import seed_matched_liquor_cost  # noqa: E402

# Real numbers from data/cogs_list.csv (invoice 03726400 / 03670302) and the
# seeds (seed_price for the bridged ProductID).
PATRON_SEED = Decimal("0.0979")      # lightspeed:20445825, from bo-seed-ilgpb 68.53/700
BOMBAY_SEED = Decimal("0.070633")    # lightspeed:20445811, ls-recipe-seed
ROOSTER_SEED = Decimal("0.0738024")  # lightspeed:20483410


def test_patron_reads_as_a_bottle_not_a_case():
    """$76.52 is ONE bottle. ÷700 mL = $0.1093/mL (near seed), NOT ÷4200 = $0.0182."""
    per, unit, label = seed_matched_liquor_cost(
        Decimal("76.52"), Decimal("4.2"), "L", "6x700ML", PATRON_SEED)
    assert unit == "ml"
    # the bottle reading, ~= seed. The whole point of the regression:
    assert per == pytest.approx(Decimal("0.109314"), abs=Decimal("0.0001"))
    # and it did NOT collapse to the case-misread that made a pour $0.61:
    assert per > Decimal("0.05")
    assert "single" in label or "bottle" in label


def test_patron_does_not_move_to_the_case_misread():
    """Belt-and-braces: whatever the machinery does, the $0.0182 value never wins."""
    per, _, _ = seed_matched_liquor_cost(
        Decimal("76.52"), Decimal("4.2"), "L", "6x700ML", PATRON_SEED)
    assert per != pytest.approx(Decimal("0.018219"), abs=Decimal("0.0005"))


def test_bombay_reads_as_a_case():
    """$296.60 IS a case. ÷4200 mL = $0.0706/mL, matching the seed. Bottle reading
    ($0.42/mL) is 6x the seed and must lose."""
    per, unit, label = seed_matched_liquor_cost(
        Decimal("296.60"), Decimal("4.2"), "L", "6x700ML", BOMBAY_SEED)
    assert unit == "ml"
    assert per == pytest.approx(Decimal("0.070619"), abs=Decimal("0.0001"))
    assert "case" in label


def test_rooster_reads_as_a_case():
    per, unit, _ = seed_matched_liquor_cost(
        Decimal("309.97"), Decimal("4.2"), "L", "6x700ML", ROOSTER_SEED)
    assert unit == "ml"
    assert per == pytest.approx(Decimal("0.073802"), abs=Decimal("0.0001"))


def test_no_seed_returns_none_never_guess():
    """No bridged seed to test against -> we cannot tell case from bottle -> skip."""
    assert seed_matched_liquor_cost(
        Decimal("76.52"), Decimal("4.2"), "L", "6x700ML", None) is None


def test_neither_reading_agrees_returns_none():
    """If the seed matches neither reading (bad bridge / genuine anomaly), refuse
    rather than book a number that is plausibly 6x wrong."""
    assert seed_matched_liquor_cost(
        Decimal("76.52"), Decimal("4.2"), "L", "6x700ML", Decimal("0.5")) is None


def test_non_mass_volume_unit_returns_none():
    """A keg priced 'per EA' has no ml/g pack to divide — not this function's job."""
    assert seed_matched_liquor_cost(
        Decimal("184.94"), Decimal("1"), "EA", "", Decimal("0.0043")) is None


def test_750ml_rose_bottle_size_from_note():
    """Whispering Angel is 6x750ML, not 700. The bottle size comes from the note,
    so the single reading divides by 750, not a hardcoded 700."""
    # a hypothetical bottle-priced rosé line at $46 vs a $0.061/mL seed
    per, unit, label = seed_matched_liquor_cost(
        Decimal("46.00"), Decimal("4.5"), "L", "6x750ML", Decimal("0.061"))
    assert unit == "ml"
    assert per == pytest.approx(Decimal("0.061333"), abs=Decimal("0.0001"))  # 46/750
    assert "single" in label or "bottle" in label
