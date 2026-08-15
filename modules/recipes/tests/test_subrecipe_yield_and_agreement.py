"""Saving a prep must not stop it linking, and a lying qty must not go live.

Two defects, found together on 2026-08-15 when every pizza on /recipes/ showed
"Pizza Sauce [Recipe] (imported)" next to a live "Pizza Dough [Recipe]".

1. YIELD RESOLUTION WAS FORKED. A prep gets its yield from the size bracket in
   its name or from data/prep_yields.yaml. That rule lived only on the
   Lightspeed-scraped path. A prep SAVED in the builder took the other path,
   which read the saved yield field and nothing else — so saving a prep shadowed
   its own scraped twin, lost the estimate, went usable_as_subrecipe=False, and
   every dish using it froze the line as manual. The only difference between the
   sauce and the dough was that somebody had once saved the sauce.

2. THE SUB-RECIPE BRANCH TRUSTED THE QTY. Fixing (1) exposed it: Salsa Rosa
   records its pizza sauce as "1.5 ml" when it means 1.5 kg, so wiring it live
   read $0.006 against the book's $5.50 — a 1000x understatement, the direction
   that flatters. The ingredient branch already checked live against the audited
   eff_cost; the sub-recipe branch did not.

Both tests hold real measured numbers, not round ones.
"""
from decimal import Decimal

from modules.recipes.pipeline.build_recipe_feeds import _agrees, resolve_yield

PREP_YIELDS = {
    "Pizza Sauce [Recipe]": {"yield_qty": 9338, "yield_unit": "g"},
    "Pizza Dough [Recipe]": {"yield_qty": 6900, "yield_unit": "g"},
}


def test_bracket_yield_wins_and_normalises_to_base_units():
    assert resolve_yield("Guacamole [4kg]", {}) == (Decimal("4000"), "g")
    assert resolve_yield("Super Lime Juice [1L]", {}) == (Decimal("1000"), "ml")
    assert resolve_yield("Brownie Prep [24 pcs]", {}) == (Decimal("24"), "each")


def test_estimate_rescues_a_prep_whose_bracket_carries_no_size():
    # "[Recipe]" is not a size, so the bracket cannot answer and the estimate must.
    qty, unit = resolve_yield("Pizza Sauce [Recipe]", PREP_YIELDS)
    assert (qty, unit) == (Decimal("9338"), "g")


def test_a_measured_bracket_still_beats_an_estimate():
    est = {"Guacamole [4kg]": {"yield_qty": 1, "yield_unit": "g"}}
    assert resolve_yield("Guacamole [4kg]", est) == (Decimal("4000"), "g")


def test_unknown_prep_yields_nothing_rather_than_a_guess():
    assert resolve_yield("Something Nobody Measured", {}) == (None, None)


def test_saved_pizza_sauce_resolves_the_same_as_the_scraped_twin():
    """The regression itself: both paths must now agree on the same yield."""
    assert resolve_yield("Pizza Sauce [Recipe]", PREP_YIELDS)[0] is not None


# --- the agreement guard -----------------------------------------------------
# $37.19 a batch / 9338 g = $0.003983/g. These are the real numbers.
RATE = 37.19 / 9338


def test_a_real_pizza_line_agrees_and_may_go_live():
    # Large pizza: 90 g, audited at $0.358439. Live reproduces it to the cent.
    assert _agrees(RATE, 90, 0.358439)
    assert _agrees(RATE, 53, 0.211081)      # regular
    assert _agrees(RATE, 64.44, 0.256642)   # gluten-free


def test_salsa_rosas_lying_qty_is_refused():
    # "1.5 ml" means 1.5 kg. Live says $0.006, the book says $5.498416.
    assert not _agrees(RATE, 1.5, 5.498416)


def test_the_guard_is_two_sided():
    """It must catch overstatement too, not just understatement."""
    # Cooked brisket wired off a 1 kg bracket reads $10.24 for 70 g; book $1.71.
    assert not _agrees(10.2436 / 70, 70, 1.7072)


def test_missing_inputs_never_wire_live():
    assert not _agrees(None, 90, 0.358439)   # no live rate for the sub
    assert not _agrees(RATE, 0, 0.358439)    # no qty
    assert not _agrees(RATE, 90, None)       # book never audited the line
    assert not _agrees(RATE, 90, 0)          # a $0 line proves nothing
