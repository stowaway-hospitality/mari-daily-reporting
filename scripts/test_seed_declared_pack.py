"""
A spirit sold by the millilitre must be COSTED by the millilitre.

THE GAP
-------
Lightspeed keeps a spirit twice: the pour a recipe names ("Four Pillars Olive
Leaf") and the stock bottle that holds the price ("Four Pillars Olive Leaf
[Bottle]", $82.58). seed_recipe_ingredient_costs already carried the price
across. What it could not carry was the SIZE — because it read the pack out of
the product's name, and there is no size in the word "Bottle".

So the seed priced a whole bottle as one countable "can". A 30 ml pour then
asked for 30 CANS of gin, the unit clash was refused, and three spirits selling
at $14.50-$19.00 costed $0.00 and reported 100% gross profit.

The size was in the export all along, in the Unit and DefaultSize columns.

WHAT THIS GUARDS
----------------
- the declared pack is read when the name has no size
- ...and NOT when it does: the two columns contradict the name, and the name wins
- a DefaultSize of 1 g/ml is an unconfigured product, not a 1-gram pack
- an existing whole-pack price may be re-seeded per ml; a per-ml one may not be
- the pack ends up in the DESCRIPTION, which is the only place build_costs reads
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from seed_recipe_ingredient_costs import _can_improve, _declared_pack, collect  # noqa: E402


def _row(unit, size):
    return {"Unit": unit, "DefaultSize": size}


def test_a_bottle_declares_its_millilitres():
    assert _declared_pack(_row("ml", "700")) == (700.0, "ml")
    assert _declared_pack(_row("ml", "750")) == (750.0, "ml")


def test_kilograms_and_litres_convert_to_base_units():
    assert _declared_pack(_row("kg", "5.4")) == (5400.0, "g")
    assert _declared_pack(_row("kg", "1")) == (1000.0, "g")


def test_a_default_size_of_one_base_unit_is_not_a_pack():
    """Potato Starch and dried shiitake both read Unit=g, DefaultSize=1. That is
    an unconfigured product, and taking it literally would price $25 of dried
    mushroom per GRAM and make it the dearest thing in the building."""
    assert _declared_pack(_row("g", "1")) is None
    assert _declared_pack(_row("ml", "1")) is None
    assert _declared_pack(_row("g", "0")) is None


def test_an_implausible_bulk_size_is_refused_rather_than_multiplied():
    """Sriracha reads Unit=l, DefaultSize=730 — but its name says 730 mL, and no
    single purchasable pack is 730 litres of chilli sauce. The columns disagree
    with each other across the catalogue, so a reading this size is dropped and
    the name is used instead."""
    assert _declared_pack(_row("l", "730")) is None
    assert _declared_pack(_row("l", "4000")) is None
    assert _declared_pack(_row("l", "4")) == (4000.0, "ml")


def test_non_measured_units_declare_nothing():
    assert _declared_pack(_row("unit", "1")) is None
    assert _declared_pack(_row("", "700")) is None


def test_a_whole_pack_price_may_be_replaced_by_a_per_ml_one():
    """Having a cost row is not the same as being costed: a per-'each' bottle
    price cannot be multiplied by a 30 ml pour."""
    assert _can_improve({"each"}, (700.0, "ml"))
    assert _can_improve({"can"}, (700.0, "g"))


def test_an_existing_per_ml_cost_is_never_overwritten():
    """That is an invoice or a better seed. A Back Office figure must not displace
    it, and a product with no declared pack cannot improve on anything."""
    assert not _can_improve({"ml"}, (700.0, "ml"))
    assert not _can_improve({"g"}, (1000.0, "g"))
    assert not _can_improve({"each"}, None)


def test_the_seeded_pack_reaches_the_description():
    """build_costs deliberately ignores the pack_qty/pack_unit columns — ILG
    records the CASE there while pricing some lines per bottle, and trusting them
    under-costs Patron 6x. It reads the DESCRIPTION. A row whose pack lives only
    in the columns is dropped as 'pack unreadable', which is how 63 seeded spirits
    reached cogs_list.csv and never appeared in the cost book."""
    seed, _skipped = collect()
    declared = [r for r in seed
                if r["pack_unit"] in ("ml", "g")
                and r["invoice_description"] != r["lightspeed_product"]]
    assert declared, "fixture sanity: some products carry no size in their name"
    for r in declared:
        assert r["invoice_description"].startswith(r["lightspeed_product"])
        assert r["invoice_description"].endswith(f"{float(r['pack_qty']):g}{r['pack_unit']}")


def test_no_seeded_rate_is_physically_absurd():
    """A per-ml or per-g rate above the ceiling means the pack was misread. No
    ingredient in this building costs more than 60c a millilitre."""
    seed, _skipped = collect()
    ceil = {"ml": 0.60, "g": 0.60}
    for r in seed:
        if r["pack_unit"] not in ceil:
            continue
        rate = float(r["cost_per_unit_incl_gst"]) / float(r["pack_qty"])
        assert rate <= ceil[r["pack_unit"]], f"{r['invoice_description']} = ${rate}/{r['pack_unit']}"
