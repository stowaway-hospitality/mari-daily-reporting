"""
A recipe with a free ingredient must not claim to be fully costed.

Several paths in convert_lightspeed_recipes can leave an ingredient line at $0
while the recipe still reports `fully_our_book: True`:

  * the line's Lightspeed cost is 0.00 and our book does not price it either —
    Oregano Leaves Rubbed, White Miso, Pickled Ginger, Togarashi;
  * or the line was CAPPED. _LS_LINE_CAP throws away any non-prep line over $40
    on the grounds that it is probably a bad datum, and throwing it away means
    replacing it with nothing. Frozen Marg's Dehydrated Lime Garnish arrived at
    $274.40 and was published free; Miso Tare's Potato Starch at $250.00.

Capping a suspicious cost to zero moves it in the flattering direction, and a
flattering number is the one nobody questions. audit_book already names each
line, but the recipe went on advertising `fully_our_book: True`, and cogs_blend
reads `our_cost` off these recipes for the P&L with no way to tell.

This does NOT touch resolved_pct. "We know what this ingredient is" and "we know
what it costs" are two different claims and the book should keep making them
separately — a $0 line is fully resolved and not costed at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

BOOK = Path(__file__).resolve().parents[3] / "data" / "lightspeed_recipes_costed.json"


def _qty(line) -> float:
    try:
        return float(line.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


@pytest.fixture(scope="module")
def recipes():
    if not BOOK.exists():
        pytest.skip("costed book not built")
    return json.loads(BOOK.read_text(encoding="utf-8"))["recipes"]


def test_no_recipe_claims_fully_costed_while_carrying_a_free_ingredient(recipes):
    liars = []
    for name, r in recipes.items():
        free = [ln.get("name") or ln.get("id") for ln in r["ingredients"]
                if (ln.get("eff_cost") or 0) == 0 and _qty(ln) > 0]
        if free and r.get("fully_our_book"):
            liars.append((name, free))
    assert not liars, (
        "these recipes publish fully_our_book: True with an ingredient costed at "
        f"$0 against a real quantity: {liars[:5]}")


def test_a_free_line_is_named_so_the_flag_can_be_explained(recipes):
    """fully_our_book: False on its own only says something is wrong. The reader
    needs to know WHICH line, or the flag is just a shrug."""
    for name, r in recipes.items():
        free = [ln.get("name") or ln.get("id") for ln in r["ingredients"]
                if (ln.get("eff_cost") or 0) == 0 and _qty(ln) > 0]
        if free:
            assert r.get("zero_cost_lines"), f"{name} has a $0 line but names none"
            assert set(r["zero_cost_lines"]) == set(free), name


def test_zero_cost_lines_is_absent_when_everything_is_costed(recipes):
    """The key must mean something. If it were emitted empty on every recipe, a
    reader scanning for it would learn nothing from finding it."""
    clean = [n for n, r in recipes.items()
             if not any((ln.get("eff_cost") or 0) == 0 and _qty(ln) > 0
                        for ln in r["ingredients"])]
    assert clean, "expected at least one fully-costed recipe in the book"
    for n in clean:
        assert "zero_cost_lines" not in recipes[n], n
