"""
A recipe line's unit must reach the cost book in the unit the book prices in.

THE GAP
-------
Our invoice-fed price is only used when its unit matches how the recipe uses the
line, and that check was a raw string compare. The Stowaway scrape writes "ml";
the Harry Gatos scrape writes "mL", "L", "kg" and "Units". So 299 lines — every
one of them at Harry Gatos — could never match no matter how good the invoice
data was, and 162 of them had a real cost sitting unread in data/costs.csv.

They failed silently, falling through to Lightspeed's scraped figure. Where that
figure is 0.00 the line cost $0 and the drink reported 100% GP: a $14.50 gin, a
$22 whisky, a $40 glass of rosé, all free. The flattering direction.

WHAT THIS GUARDS
----------------
- "mL" is "ml" and "Units" is "ea" — a label, not a different quantity
- "L"/"kg" convert WITH their quantity (0.27 L is 270 ml), never without
- a bulk label on a base-unit quantity is read as base units, and its cost with it
- ...but only above a bound no real line reaches, and never on an "ml"/"g" label
- an unrecognised label is left alone rather than guessed into a base unit
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from convert_lightspeed_recipes import (  # noqa: E402
    _MAX_REAL_BULK_LINE,
    _bulk_label_is_typo,
    normalise_line_units,
)


def _rec(qty, unit, cost="1.00", name="Thing", recipe="R"):
    return {recipe: {"ingredients": [{"name": name, "qty": qty, "unit": unit, "cost": cost}]}}


def _line(rec, recipe="R"):
    return rec[recipe]["ingredients"][0]


def test_capital_mL_is_the_same_unit_as_ml():
    """The whole Harry Gatos scrape writes mL. It is millilitres."""
    r = _rec("30", "mL")
    assert normalise_line_units(r) == 1
    assert _line(r)["unit"] == "ml"
    assert _line(r)["qty"] == "30"          # a relabel must not move the quantity


def test_units_is_each():
    r = _rec("2", "Units")
    normalise_line_units(r)
    assert _line(r)["unit"] == "ea"


def test_litres_convert_with_their_quantity():
    """0.27 L of soy is 270 ml of soy. Converting the label without the quantity
    would under-cost the line 1000x — the flattering direction."""
    r = _rec("0.27", "L")
    normalise_line_units(r)
    assert _line(r) == {"name": "Thing", "qty": 270.0, "unit": "ml", "cost": "1.00"}


def test_kilograms_convert_with_their_quantity():
    r = _rec("2.84", "kg")
    normalise_line_units(r)
    assert (_line(r)["qty"], _line(r)["unit"]) == (2840.0, "g")


def test_already_base_units_are_untouched():
    r = _rec("500", "g")
    assert normalise_line_units(r) == 0
    assert _line(r)["qty"] == "500"


def test_unknown_label_is_left_visible_not_guessed():
    """A unit we do not recognise stays as it is. Guessing it into a base unit is
    how "0.35 ml" of rosemary salt became 0.35 KG and the fries cost $0.0019."""
    r = _rec("1", "sachet")
    assert normalise_line_units(r) == 0
    assert _line(r)["unit"] == "sachet"


def test_bulk_label_on_a_base_unit_quantity_is_read_as_base_units():
    """Shiitake Tare's "1600 L" of soy is 1600 ml. A 2,800 litre batch of tare is
    not a large batch, it is a label typo."""
    r = _rec("1600", "L", cost="22400.00")
    normalise_line_units(r)
    assert (_line(r)["qty"], _line(r)["unit"]) == ("1600", "ml")


def test_a_bulk_typos_cost_is_scaled_only_when_produce_used_the_bulk_rate():
    """Produce priced "4000 L" of mirin at $26,666.67 (bulk) and "4000 L" of sake
    at $7.39 (base) inside the SAME recipe. Only the first is a dollar figure
    carrying the same 1000x error, and only it may be divided down."""
    bulk = _rec("4000", "L", cost="26666.67")
    normalise_line_units(bulk)
    assert abs(float(_line(bulk)["cost"]) - 26.66667) < 0.001

    already_base = _rec("4000", "L", cost="7.39")
    normalise_line_units(already_base)
    assert _line(already_base)["cost"] == "7.39"


def test_real_bulk_lines_survive():
    """5 kg of pork belly and 4.3 L of vodka are real lines and must not be read
    down to grams. The bound has to clear the biggest legitimate line in the book
    — Achiote Chicken's 15 kg — with room to spare."""
    assert not _bulk_label_is_typo(5, "kg")
    assert not _bulk_label_is_typo(4.3, "l")
    assert not _bulk_label_is_typo(15, "kg")
    assert _MAX_REAL_BULK_LINE > 15


def test_the_rule_never_fires_on_a_base_unit_label():
    """Reading a base-unit label as bulk is the dangerous direction and this rule
    cannot take it — 1600 g stays 1600 g however large it looks."""
    assert not _bulk_label_is_typo(1600, "g")
    assert not _bulk_label_is_typo(1600, "ml")


def test_the_twin_recipes_agree_after_normalisation():
    """The proof the rule rests on. Harry Gatos holds its chilli sauce twice:
    once typed 2.5 L / 0.25 kg and once typed 2500 L / 250 kg. Same sauce, same
    kitchen, exactly 1000x apart — so the big one is a label, not a quantity, and
    both must resolve to the same 2500 ml / 250 g."""
    typed_right = _rec("2.5", "L", recipe="HG's Soy Chilli Sauce")
    typed_wrong = _rec("2500", "L", recipe="HG Soy Chilli Sauce")
    normalise_line_units(typed_right)
    normalise_line_units(typed_wrong)
    a = _line(typed_right, "HG's Soy Chilli Sauce")
    b = _line(typed_wrong, "HG Soy Chilli Sauce")
    assert float(a["qty"]) == float(b["qty"]) == 2500.0
    assert a["unit"] == b["unit"] == "ml"
