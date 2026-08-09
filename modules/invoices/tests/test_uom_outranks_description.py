"""
The supplier's own UOM outranks a scavenged description.

THE DEFECT
----------
Fresh Fruit Team prints the selling unit in its own column — "200g punnet" — and
the money row lands BETWEEN the two rows that column wraps across, so the money
row's unit cell is empty. The line reached the cost book with no stated unit at
all, and pack_size fell back to scavenging a size out of the description.

On MKB500PUNN the description had wrapped too, to

    "Punnet) 8 x 100g packs supplied for"

— a fragment of "Mushroom King Brown (200G Punnet) ... 8 x 100g packs supplied
for same price". That "8 x 100g" is the supplier describing the WHOLE four-punnet
line (4 x 200 g = 800 g = 8 x 100 g), not one punnet. Read as one punnet it made
the pack 800 g, and King Brown mushrooms went into the book at

    $7.56/kg   against the $30.25/kg every other delivery of the same code states

Four times under, which flatters GP, on a line whose price ($6.05) was identical
to its neighbours the whole time.

THE RULE, which is what pack_size already claimed to do everywhere else: lead
with the UOM the supplier printed, and scavenge the description only when the
UOM is absent or uninformative. A description wraps, truncates and carries
substitution notes; a UOM column does not.

THE LIMIT, which matters more than the fix: this must NEVER be allowed to read a
MULTI ("6x700ML") or a bulk label ("CTN-6") as one unit. Those are exactly the
case/bottle question that needs a second source to answer, and answering it here
would put the 6x under-cost back in a new place.
"""

from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from modules.invoices.pack_size import (names_a_unit, parse_pack,
                                        single_unit_content)

MANGLED = "Punnet) 8 x 100g packs supplied for"
WHOLE = "Mushroom King Brown (200G Punnet)"


def test_the_uom_fixes_the_mangled_description():
    """The line the defect was found on. 200 g, not 800 g."""
    assert parse_pack(MANGLED, "200g punnet") == (D("0.2"), "kg")


def test_the_description_alone_still_reads_the_total_as_a_pack():
    """Kept as evidence: without the UOM there is nothing to correct it with."""
    assert parse_pack(MANGLED) == (D("0.8"), "kg")


def test_a_whole_description_agrees_with_the_uom():
    """Where both are readable they must say the same thing, or the fix is
    changing lines it has no business changing."""
    assert parse_pack(WHOLE, "200g punnet") == parse_pack(WHOLE) == (D("0.2"), "kg")


@pytest.mark.parametrize("uom,size", [("200g punnet", D("0.2")), ("500g punnet", D("0.5"))])
def test_uom_states_the_size_of_one_unit(uom, size):
    assert single_unit_content(uom) == (size, "kg")


@pytest.mark.parametrize("uom", ["6x700ML", "24x330ML", "12x1.25LT", "CTN-6", "CTN-24"])
def test_a_multi_or_a_carton_is_never_one_unit(uom):
    """THE REGRESSION THAT MATTERS. If this ever answers, ILG's "6x700ML" hands
    back a 4.2 L case against a price that may be for one 700 mL bottle, and the
    6x under-cost is back — this time bypassing the seed discriminator that
    exists to tell those apart."""
    assert single_unit_content(uom) is None


@pytest.mark.parametrize("text,ok", [
    ("200g punnet", True), ("500g punnet", True), ("Kilogram", True),
    ("CTN-6", True), ("Each", True), ("Dozen", True), ("Bunch", True),
    ("Cabbage 500g", False),      # description text bled into the unit column
    ("Red Shredded please", False),
    ("", False), (None, False),
])
def test_names_a_unit_separates_a_uom_from_description_text(text, ok):
    """A measure alone is not a unit — a description is full of measures. On
    INB00109089 the layout shifts and the neighbours' unit cells hold
    "Cabbage 500g"; taking that made a per-kilogram line a 500 g pack."""
    assert names_a_unit(text) is ok


@pytest.mark.parametrize("uom,tok_size", [
    ("200g punnet", D("0.2")),     # first alpha run is "g", the WORD is "punnet"
    ("500g punnet", D("0.5")),
])
def test_the_unit_word_is_found_past_a_glued_measure(uom, tok_size):
    """"200g punnet" -> the first alpha run is "g", which is in no vocabulary and
    used to send the whole thing to the description scavenger."""
    assert single_unit_content(uom) == (tok_size, "kg")


def test_unchanged_where_the_uom_says_nothing_useful():
    """Everything that worked before keeps working: the UOM leads only when it
    has something to say."""
    assert parse_pack("SOUR CREAM 2LT", "Each") == (D("2"), "L")
    assert parse_pack("Avocado Hass", "Tray") == (D("1"), "tray")
    assert parse_pack("EGG 700GM PACK", "doz") == (D("12"), "ea")
    assert parse_pack("Eggs 700 Grams", "Box") == (D("1"), "box")
    assert parse_pack("OLIVES 2KG", None) == (D("2"), "kg")
