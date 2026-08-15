"""Produce's quantity dropdown only offers mL and g, so the kitchen types "kg".

Twenty lines across the prep book are written as qty N, unit "ml", with the real
unit appended to the ingredient NAME — "Chicken Thigh Flt S/Off [Kg] kg". Read
literally that is 15 millilitres of chicken thigh, and that is what our book
believed until 2026-08-15.

It is not a judgement call. Produce PRICED each line off the magnitude the
kitchen meant, so its own printed cost is an independent witness:

    15 x $16.30/kg = $244.50   <- exactly what Produce printed
    15 ml at the same rate     = $0.24

Measured over the whole class: 18 of the 20 lines had a live rate to check
against, and 18 of 18 reconciled at the NAME's unit, 0 at the recorded one.
So the rule proves itself before it fires, and declines when it cannot.
"""
import pytest

from scripts.convert_lightspeed_recipes import apply_unit_in_name


def _rec(name, qty, unit, cost):
    return {"Some Prep": {"ingredients": [
        {"name": name, "qty": str(qty), "unit": unit, "cost": str(cost)}]}}


def _line(rec):
    return rec["Some Prep"]["ingredients"][0]


def test_it_rescales_when_produces_cost_backs_the_name():
    # Chicken thigh at $16.30/kg = $0.0163/g. Produce printed $244.50 for "15 ml".
    rec = _rec("Chicken Thigh Flt S/Off [Kg] kg", 15, "ml", 244.50)
    assert apply_unit_in_name(rec, lambda i: 0.0163) == 1
    assert float(_line(rec)["qty"]) == 15000
    assert _line(rec)["unit"] == "g"


def test_litres_go_to_millilitres():
    # Milk $1.70/L = $0.0017/ml. Produce printed $3.40 for "2 ml".
    rec = _rec("Milk Fresh Full Cream 2L Norco L", 2, "ml", 3.40)
    assert apply_unit_in_name(rec, lambda i: 0.0017) == 1
    assert float(_line(rec)["qty"]) == 2000
    assert _line(rec)["unit"] == "ml"


def test_it_refuses_when_the_cost_does_not_back_the_name():
    """The whole safety property: no rescale without arithmetic agreement."""
    # Printed cost matches the line read AS WRITTEN, so leave it alone.
    rec = _rec("Something Odd kg", 15, "ml", 0.24)
    assert apply_unit_in_name(rec, lambda i: 0.0163) == 0
    assert float(_line(rec)["qty"]) == 15
    assert _line(rec)["unit"] == "ml"


def test_no_rate_means_no_change():
    """A sub-recipe reference has no per-unit rate here — hands off."""
    rec = _rec("Pizza Sauce [Recipe] kg", 1.5, "ml", 5.63)
    assert apply_unit_in_name(rec, lambda i: None) == 0
    assert _line(rec)["unit"] == "ml"


def test_a_name_without_a_unit_suffix_is_untouched():
    rec = _rec("Brown Onions [20kg]", 750, "g", 1.28)
    assert apply_unit_in_name(rec, lambda i: 0.0017) == 0
    assert float(_line(rec)["qty"]) == 750


def test_a_line_already_in_the_right_unit_is_untouched():
    """"... kg" with the unit already g means somebody fixed it. Don't re-apply."""
    rec = _rec("Peeled Garlic [1Kg] kg", 1000, "g", 4.40)
    assert apply_unit_in_name(rec, lambda i: 0.0044) == 0
    assert float(_line(rec)["qty"]) == 1000


@pytest.mark.parametrize("bad", [0, -5])
def test_junk_quantities_are_refused(bad):
    rec = _rec("Peeled Garlic [1Kg] kg", bad, "ml", 4.40)
    assert apply_unit_in_name(rec, lambda i: 0.0044) == 0


def test_a_zero_cost_line_proves_nothing():
    rec = _rec("Peeled Garlic [1Kg] kg", 1, "ml", 0)
    assert apply_unit_in_name(rec, lambda i: 0.0044) == 0
