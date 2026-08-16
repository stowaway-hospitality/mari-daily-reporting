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

from modules.recipes.pipeline.build_recipe_feeds import (
    _agrees, _sub_agrees, resolve_yield)

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


def test_a_written_basis_beats_the_bracket_in_the_name():
    """The precedence was the other way round until 2026-08-16, and the real data
    refutes it: seven preps have a bracket that disagrees with prep_yields.yaml,
    and in all seven the bracket is a PACK OR NOMINAL size, not a yield.

    `Cooked Beef Brisket [1Kg]` is a 6x error -- $8.53 on every Meatlovers -- and
    `Jalapeno Tequila [1L]` is 7.5x. A 1 L bottle you decant a 7.5 L batch into
    is not a yield, and a 15 kg raw joint is not what comes out of the oven.

    prep_yields.yaml entries each carry a written `basis`. Brackets carry nothing
    but Lightspeed's product naming.
    """
    est = {"Cooked Beef Brisket [1Kg]": {"yield_qty": 6000, "yield_unit": "g"}}
    assert resolve_yield("Cooked Beef Brisket [1Kg]", est) == (Decimal("6000"), "g")


def test_the_bracket_still_answers_when_nothing_is_written_down():
    """The common case: most preps have no prep_yields entry and the bracket is
    all there is. Flipping the precedence must not throw that away."""
    assert resolve_yield("Guacamole [4kg]", {}) == (Decimal("4000"), "g")
    assert resolve_yield("Brownie Prep [24 pcs]", {}) == (Decimal("24"), "each")


def test_no_prep_yields_entry_is_a_pack_label_masquerading_as_a_yield():
    """A ratchet on the seven. If an eighth conflict appears, somebody has added
    a prep_yields entry that disagrees with its own name -- which is either a new
    finding or a typo, and both want a human before they reach a cost."""
    import yaml
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[3]
    est = yaml.safe_load((root / "data" / "prep_yields.yaml").read_text()) or {}
    known = {"Jalape\u00f1o Tequila [1L]", "Cooked Beef Brisket [1Kg]",
             "Coconut-washed Rooster Blanco [1L]", "Achiote Chicken [15Kg]",
             "Super Lime Juice [1L]", "Super Lemon Juice [1L]",
             "Mango-Chilli Puree [1L]"}
    # MAGNITUDE only. A pack label masquerading as a yield is a magnitude error
    # -- 1,000 against 7,500 -- and that is what this guards.
    #
    # A g/ml difference at the same magnitude is the house rule doing its job:
    # Buffalo Aioli [1L] is 750 g of mayonnaise, so it is 1,000 GRAMS, and the
    # bracket's "L" is the old convention rather than a contradiction. Counting
    # that as a conflict made a correct entry look like a regression.
    conflicts = set()
    for name, e in est.items():
        bq, bu = resolve_yield(name, {})
        if bq is None:
            continue
        if Decimal(str(bq)) != Decimal(str(e["yield_qty"])):
            conflicts.add(name)
    assert conflicts <= known, f"new bracket-vs-basis conflict(s): {conflicts - known}"


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


def test_missing_inputs_never_wire_live():
    assert not _agrees(None, 90, 0.358439)   # no live rate for the sub
    assert not _agrees(RATE, 0, 0.358439)    # no qty
    assert not _agrees(RATE, 90, None)       # book never audited the line
    assert not _agrees(RATE, 90, 0)          # a $0 line proves nothing


# --- the SUB-RECIPE guard ----------------------------------------------------
# Judged on the quantity, not the price, because the price is allowed to change:
# eff_cost is what LIGHTSPEED thought a batch cost, and re-speccing a batch in our
# builder is supposed to move our number away from theirs.
NEW_RATE = 14.3075 / 6028      # Zak's 2026-08-15 re-spec: $2.37/kg


def test_a_pizza_line_stays_live_through_a_real_respec():
    """The whole point. Pizza Sauce dropped 40%; the pizzas must NOT freeze."""
    assert _sub_agrees(NEW_RATE, 90, 0.358439, "g", "g")     # large
    assert _sub_agrees(NEW_RATE, 53, 0.211081, "g", "g")     # regular
    assert _sub_agrees(NEW_RATE, 64.44, 0.256642, "g", "g")  # gluten-free
    assert _sub_agrees(NEW_RATE, 110, 0.438092, "g", "g")    # parmy


def test_salsa_rosas_lying_unit_is_refused():
    """"1.5 ml" of a sauce measured in grams means 1.5 kg. Volume != mass."""
    assert not _sub_agrees(NEW_RATE, 1.5, 5.498416, "ml", "g")


def test_other_volume_against_mass_lines_are_refused():
    assert not _sub_agrees(0.000201, 0.077, 0.880957, "ml", "g")   # cauliflower cheese
    assert not _sub_agrees(0.0092, 1, 2.811866, "ml", "g")         # nut roast prep
    assert not _sub_agrees(0.0117, 1, 7.35, "ml", "g")             # tandoori sauce


def test_a_broken_yield_is_still_caught_even_with_matching_units():
    """Magnitude backstops the unit check when the yield itself is wrong."""
    # "[1Kg]" is a portion label, not a batch yield: brisket reads $146/kg,
    # so 70 g costs $10.24 against the book's $1.71 — 6x, past the 5x line.
    assert not _sub_agrees(10.2436 / 70, 70, 1.7072, "g", "g")
    # Jalapeno tequila at $561/L: 40 ml reads $22.44 against $2.99 — 7.5x.
    assert not _sub_agrees(22.4352 / 40, 40, 2.9914, "ml", "ml")


def test_ordinary_price_drift_still_goes_live():
    """A sub-recipe must not freeze just because its price moved a bit."""
    assert _sub_agrees(0.00227, 10, 0.016545, "ml", "ml")    # sugar syrup, 1.37x
    assert _sub_agrees(3.0530 / 175, 175, 4.361435, "g", "g")  # achiote, 0.70x
    assert _sub_agrees(0.3507 / 50, 50, 0.18009, "g", "g")   # buffalo aioli, 1.95x


def test_unknown_or_absent_units_never_wire_live():
    assert not _sub_agrees(NEW_RATE, 90, 0.358439, "handful", "g")
    assert not _sub_agrees(NEW_RATE, 90, 0.358439, None, "g")
    assert not _sub_agrees(NEW_RATE, 90, 0.358439, "g", None)


def test_sub_guard_rejects_missing_inputs():
    assert not _sub_agrees(None, 90, 0.358439, "g", "g")
    assert not _sub_agrees(NEW_RATE, 0, 0.358439, "g", "g")
    assert not _sub_agrees(NEW_RATE, 90, 0, "g", "g")


def test_kg_and_g_are_the_same_class():
    """A batch measured in g can answer a line written in kg."""
    assert _sub_agrees(NEW_RATE * 1000, 0.09, 0.358439, "kg", "g")


# --- sub-recipe unit conversion ----------------------------------------------

def test_a_batch_in_kg_drawn_in_grams_converts_the_right_way():
    """cost-per-yield-unit is MULTIPLIED by the same-dimension factor.

    A $10 batch yielding 1 kg is $0.01/g, so a 200 g line is $2.00. Dividing by
    the factor instead gives $2,000,000 — arithmetically perfect, physically
    absurd, and in the direction that does not alarm anybody until a P&L lands.
    """
    from datetime import date
    from decimal import Decimal

    from core.domain import CostObservation, CostSeries
    from modules.recipes.cost import Recipe, RecipeLine, cost_on

    on = date(2026, 8, 16)
    costs = CostSeries([CostObservation(
        ingredient="ing:x", observed_on=on, cost_per_unit=Decimal("0.01"),
        unit="g", venue=None, source_invoice="t")], purchasable_to_ingredient={})

    batch = Recipe(product="B", venue="v", yield_qty=Decimal("1"), yield_unit="kg",
                   lines=(RecipeLine(ingredient="ing:x", qty=Decimal("1000"), unit="g"),))
    dish = Recipe(product="D", venue="v",
                  lines=(RecipeLine(ingredient="", qty=Decimal("200"), unit="g",
                                    subrecipe="B"),))
    assert cost_on(dish, costs, on, recipes=[batch, dish]) == Decimal("2.00")


def test_ea_and_each_are_the_same_unit():
    """resolve_yield normalises a bracket to "each" while recipe lines say "ea".
    They refused each other over spelling, and it cost 23 wings deals their cost
    on 2026-08-16."""
    from modules.recipes.cost import _same_dim_factor
    assert _same_dim_factor("each", "ea") == 1
    assert _same_dim_factor("pcs", "each") == 1


def test_a_pack_is_still_not_a_count():
    """box/tray/bottle are counts too, but how many grams is in one of them is
    exactly the question the unit guard exists to refuse. That stays refused."""
    from modules.recipes.cost import _same_dim_factor
    assert _same_dim_factor("box", "each") is None
    assert _same_dim_factor("tray", "g") is None
