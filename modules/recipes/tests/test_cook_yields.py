"""A weighed cook loss must reach the plate, and must not spread to what was not weighed.

Zak, 2026-08-15: "raw lamb 2.7kg was 2.3kg cooked for roast". 2.3 / 2.7 = 85.19%.

WHY THIS NEEDS A TEST AT ALL. data/cost_book_flags.yaml sizes the cook-loss
questions with `assumed_yield: 0.65` and says, in its own comment, that the
assumption "IS NEVER APPLIED TO A COST" and that when a real figure arrives it
"goes into the recipe and this whole block should be deleted rather than
corrected". So the measurement leaves the flags file entirely — and a number that
lives nowhere but a data file, applied by one function, is a number that can be
silently dropped by a refactor with nothing going red.

THE TWO HALVES, and the second matters as much as the first:

  1. the lamb plate charges the RAW joint (220 g plated -> 258.3 g raw), and
  2. pork and beef DO NOT MOVE. They are the same plate with the protein swapped
     and nobody has weighed them. The flags file is explicit that the three do not
     shrink at the same rate, which is why the question is asked once per protein.
     Generalising one weighing across all three would be inventing two.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"
SPEC = ROOT / "data" / "cook_yields.yaml"
pytestmark = pytest.mark.skipif(not BOOK.exists(), reason="costed book not built")


def _book():
    d = json.loads(BOOK.read_text(encoding="utf-8-sig"))
    return d.get("recipes") or d


def _line(recipe, needle):
    rec = _book().get(recipe) or {}
    return next((l for l in (rec.get("ingredients") or [])
                 if needle in str(l.get("name") or "")), None)


def test_the_measurement_is_recorded_with_its_weighing():
    """A yield with no measurement behind it is an assumption wearing a fact's
    clothes. Every entry has to say what was weighed and who weighed it."""
    specs = yaml.safe_load(SPEC.read_text(encoding="utf-8-sig")) or []
    assert specs, "data/cook_yields.yaml is empty"
    for s in specs:
        assert 0 < float(s["yield"]) <= 1, s          # cooking does not add weight
        assert s.get("measured"), s
        assert s.get("by"), s


def test_the_lamb_plate_charges_the_raw_joint():
    """220 g plated / 0.851852 = 258.26 g of raw leg."""
    ln = _line("Lamb Roast", "Lamb Leg")
    assert ln is not None, "Lamb Roast has no lamb line"
    assert float(ln["qty"]) == pytest.approx(220 / (2.3 / 2.7), rel=1e-4), ln
    # and the money follows the quantity, at the book's own per-gram rate
    assert float(ln["eff_cost"]) == pytest.approx(
        float(ln["qty"]) * float(ln["our_cost"]), rel=1e-4), ln


def test_the_yield_does_not_spread_to_the_unweighed_roasts():
    """The half of this that is easy to get wrong. Pork and beef are the same
    plate with the protein swapped, and neither has been weighed."""
    for recipe, needle in (("Pork Roast", "Pork Leg"), ("Beef Roast", "Beef Brisket")):
        ln = _line(recipe, needle)
        assert ln is not None, f"{recipe} has no protein line"
        assert float(ln["qty"]) == pytest.approx(220, rel=1e-6), (
            f"{recipe} moved off its 220 g plated portion — a yield has been "
            f"applied to a protein nobody has weighed")


def test_produce_s_own_stated_cost_is_left_alone():
    """We scale OUR quantity, not another system's figure. Produce says $4.29 for
    the plated portion and that remains what Produce says; rewriting it to agree
    with us would be the wrong way round, and _RAW_LINE_COST depends on it."""
    ln = _line("Lamb Roast", "Lamb Leg")
    assert float(ln["ls_cost"]) == pytest.approx(4.29, rel=1e-6), ln


def test_the_lamb_question_is_gone_and_the_others_remain():
    """A settled question must stop being asked; an unsettled one must not."""
    feed = ROOT / "data" / "cost_book_flags.json"
    if not feed.exists():
        pytest.skip("flags feed not built")
    ids = {f["id"] for f in json.loads(feed.read_text(encoding="utf-8-sig"))["flags"]}
    assert "yield-lamb-roast" not in ids
    for still_open in ("yield-pork-roast", "yield-beef-roast"):
        assert still_open in ids, still_open
