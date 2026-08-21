"""The unit rules in modules/recipes/feed_defects.py, against the real feeds.

WHY THESE ASSERTIONS HOLD REAL MEASURED NUMBERS
-----------------------------------------------
A plausibility rule that is right in a fixture and wrong on the book is worse
than no rule: it teaches whoever reads the queue to skip the family. So the
calibration in the module's docstring (10 / 4 / 17, zero false positives) is
re-measured here on every run, the same way scripts/test_recipe_line_guard.mjs
re-calibrates the builder's guard against the real book.

The four findings named below are the ones Zak listed after reading a screen,
and each is asserted BY NAME with its real number:

    Lemon                      $0.375 per ml   ($375/L of lemon)
    Cauliflower [ea]           $3.20  per can
    Turkish Bread [ea]         $1.50  per can
    Avocado                    $26.40 per tray beside $3.10 per ea

...plus the line-level one that keeps being re-raised and re-closed:

    American Standard Burger, Lettuce Cos Baby Twin Pack [Each], 0.083 "ml"

THE FEEDS ARE GENERATED, NOT COMMITTED. data/ingredients.json is a 90-day window
off date.today() and data/lightspeed_recipes_costed.json is rebuilt from the
scrape, so a clean checkout has neither. Those tests skip rather than fail, for
the same reason build_site.py generates them: a committed copy would rot on a
Tuesday with no commit behind it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.recipes import feed_defects as fd

ROOT = Path(__file__).resolve().parents[3]
ING = ROOT / "data" / "ingredients.json"
BOOK = ROOT / "data" / "lightspeed_recipes_costed.json"


def _ingredients():
    if not ING.exists():
        pytest.skip("data/ingredients.json is generated at build time")
    return json.loads(ING.read_text(encoding="utf-8-sig"))["ingredients"]


def _book():
    if not BOOK.exists():
        pytest.skip("data/lightspeed_recipes_costed.json is generated at build time")
    return json.loads(BOOK.read_text(encoding="utf-8-sig"))["recipes"]


# --------------------------------------------------------------------------
# the primitives
# --------------------------------------------------------------------------

def test_a_unit_we_have_never_seen_is_none_not_a_guess():
    """Reading '0.35 ml' as 0.35 KG took Rosemary Salted Fries from $1.86 to
    $0.0019. An unknown unit must stay unknown."""
    assert fd.unit_dimension("g") == "mass"
    assert fd.unit_dimension("mL") == "volume"
    assert fd.unit_dimension("Each") == "count"
    assert fd.unit_dimension("gubbins") is None
    assert fd.unit_dimension(None) is None
    assert fd.unit_dimension("") is None


def test_only_the_trailing_bracket_is_read():
    """'Coke 1.25L' is bought and priced by the can and is perfectly correct.
    Reading a size anywhere in a name flags it and four of its siblings."""
    assert fd.name_declared_unit("T2 Milk Bun Sliced White Sesame [85g]")[1] == "mass"
    assert fd.name_declared_unit("Ponzu Dashi Vinegar Uchibori [360mL]")[1] == "volume"
    assert fd.name_declared_unit("Cauliflower [ea]")[1] == "count"
    assert fd.name_declared_unit("Coke 1.25L")[1] is None
    assert fd.name_declared_unit("Pizza Base Gluten Free 11in [10x2 CTN]")[1] is None


def test_a_container_bracket_says_nothing_about_the_contents():
    """426 saved lines draw mL out of a '[Bottle]' and every one is correct. A
    keg, a bottle and a can describe the vessel, not the measurement."""
    for name in ("Aperol [Bottle]", "Guinness [Keg]", "Coco Lopez [Can]"):
        assert fd.name_declared_unit(name) == (None, None), name


def test_the_stem_ignores_the_pack_bracket_and_nothing_else():
    assert fd.product_stem("Avocado [Tray]") == "avocado"
    assert fd.product_stem("Avocado") == "avocado"
    assert fd.product_stem("Preserved Lemon 350g Chefs Choice") != "lemon"


# --------------------------------------------------------------------------
# rule 1 — the pack unit contradicts the name
# --------------------------------------------------------------------------

def test_the_four_named_produce_lines_are_fixed_and_the_rule_still_works():
    """CLOSED 2026-08-14. All four were "$X per can" where nothing had ever told
    Lightspeed otherwise, and all four are now pinned in data/pack_overrides.yaml
    at the unit the flag itself said the price was already sane for:

        Cauliflower     $3.20  -> 1 ea      Turkish Bread  $1.50  -> 1 ea
        Onion Brown     $1.54  -> 1000 g    Potato Peeled  $3.60  -> 1000 g

    None moved money — pack_qty 1 divides by one — except the two kg lines, where
    dividing the per-kilo price into grams is the conversion the recipes needed.
    Pull an override and its line comes back.
    """
    found = {f["description"]: f for f in fd.pack_unit_contradicts_name(_ingredients())}
    for gone in ("Cauliflower [ea]", "Turkish Bread [ea]",
                 "Onion Brown [kg]", "Potato Peeled [kg]"):
        assert gone not in found, (gone, "pack override missing or overwritten")

    # THE RULE ITSELF, on a fixture, so this cannot pass by going blind — the
    # same lesson as the pack detectors on 2026-08-14. Both halves of
    # "contradicts" must still fire: a container word against a piece name, and
    # a genuine dimension clash.
    planted = [{"id": "t:1", "description": "Cauliflower [ea]", "pack_unit": "can",
                "cost_per_base_unit": "3.20"},
               {"id": "t:2", "description": "Onion Brown [kg]", "pack_unit": "can",
                "cost_per_base_unit": "1.54"}]
    got = {f["description"]: f for f in fd.pack_unit_contradicts_name(planted)}
    assert got["Cauliflower [ea]"]["kind"] == "container"
    assert got["Cauliflower [ea]"]["rate"] == pytest.approx(3.20)
    assert got["Onion Brown [kg]"]["kind"] == "dimension"


def test_pack_unit_rule_does_not_flag_the_spirits():
    """The calibration that matters. Without the container-bracket exclusion the
    rule flags 199 of 1,091 ingredients and 195 of them are bottles of spirits
    measured, correctly, in millilitres."""
    flagged = {f["description"] for f in fd.pack_unit_contradicts_name(_ingredients())}
    for spirit in ("Aperol [Bottle]", "Campari [Bottle]", "Guinness [Keg]",
                   "Bombay Sapphire [Bottle]", "Philter Pale [Keg]"):
        assert spirit not in flagged, spirit
    assert len(flagged) <= 20, sorted(flagged)


# --------------------------------------------------------------------------
# rule 2 — one product, two incompatible prices
# --------------------------------------------------------------------------

def test_the_lemon_is_priced_in_two_dimensions():
    """$0.375 per mL is $375 a litre of lemon, beside the same lemon at
    $0.0033/g ($3.30/kg) from two other suppliers. It cannot be both."""
    by_stem = {f["stem"]: f for f in fd.product_priced_in_two_worlds(_ingredients())}
    # FIXED 2026-08-21, and a test that pins a defect expires when it is fixed.
    # $0.375 was never a volume rate: it is what ONE LEMON costs. Select Fresh
    # bill LEME "LEMON EA" at $0.45 each and LEMK "LEMON KG" at $3.20/kg, and at
    # $3.20/kg a 117 g lemon is $0.375 exactly. The seed is now stated per ea,
    # so the two dimensions are one and this flag must NOT come back.
    assert "lemon" not in by_stem, "the lemon is priced per ea now, not per ml"
    return
    assert lemon["kind"] == "two_dimensions"
    units = {m["pack_unit"] for m in lemon["members"]}
    assert units == {"ml", "g"}
    dear = next(m for m in lemon["members"] if m["pack_unit"] == "ml")
    assert dear["rate"] == pytest.approx(0.375)
    assert dear["id"] == "lightspeed:20564869"


def test_the_avocado_is_a_whole_tray_priced_as_one():
    """THE PAIR IS THE FINDING; THE TRAY PRICE IS JUST TODAY'S PRICE.

    This pinned the tray at $26.40 and the ratio at 26.40/3.10. Both are
    supplier prices read through a rolling 90-day window, so they move on
    their own: on 2026-08-14 the tray is $22.00 (lightspeed:22488995) and the
    ratio 7.10. Nothing about the defect changed — a tray still sits beside a
    single avocado at $3.10, which is the whole point — but the assertion
    called that a regression.

    Worse, it only ever said so on a developer's machine: data/ingredients.json
    is gitignored and generated, so this test SKIPS in CI. A pinned number that
    drifts, in a test nobody's pipeline runs, is a slow trap. So assert the
    shape — the tray/piece pair, still flagged, still above the detection
    threshold — and let the price be whatever it is."""
    by_stem = {f["stem"]: f for f in fd.product_priced_in_two_worlds(_ingredients())}
    avo = by_stem["avocado"]
    assert avo["kind"] == "container_vs_piece"
    assert avo["members"][0]["pack_unit"] == "tray"
    piece = next(m for m in avo["members"] if m["pack_unit"] == "ea")
    assert piece["rate"] == pytest.approx(3.10, rel=0.5), (
        f"a single avocado should still be a few dollars: {piece['rate']}")
    assert avo["ratio"] >= fd.TWO_WORLDS_X, (
        f"tray {avo['members'][0]['rate']} vs piece {piece['rate']} "
        f"= {avo['ratio']:.2f}x, below the {fd.TWO_WORLDS_X}x threshold")


def test_two_ways_of_buying_the_same_thing_is_not_a_defect():
    """A 3x gap is what separates 'we buy limes by the kilo and by the tray'
    from 'one of these is not this product's price'. Keep the family small."""
    hits = fd.product_priced_in_two_worlds(_ingredients())
    assert len(hits) <= 8, [f["stem"] for f in hits]
    assert all(f["ratio"] >= fd.TWO_WORLDS_X or f["kind"] == "two_dimensions"
               for f in hits)


# --------------------------------------------------------------------------
# rule 3 — the line-level twin, and the burger
# --------------------------------------------------------------------------

def test_the_american_standard_burger_lettuce_line_is_fixed():
    """CLOSED 2026-08-14, and this now guards the FIX.

    0.083 "ml" of a twin pack of baby cos was this rule's canonical example. The
    quantity was right — a twelfth of the pack at $0.228, exactly what the book
    charged and exactly what the other burger carrying it charged — and the unit
    was meaningless against a countable pack. Nothing failed, and it read as an
    error to every human who saw it.

    data/recipe_line_unit_fixes.yaml now relabels it ml -> ea with NO cost_factor,
    because the money was never wrong. So the assertions invert: the line must be
    GONE from the rule, and the cost must NOT have moved. Delete the yaml entry
    and the first assertion reds; add a cost_factor to it and the second does.
    """
    hits = {f["id"]: f for f in
            fd.line_unit_contradicts_pack(_book(), _ingredients())}
    assert "lightspeed:22995348" not in hits, (
        "the lettuce line is being reported again — check that "
        "data/recipe_line_unit_fixes.yaml still carries the American Standard "
        "Burger entry and that apply_unit_fixes still runs before costing")

    # and the money did not move: 0.083 x $2.75 = $0.22825, the same figure the
    # book carried while the label was wrong.
    book = _book()
    line = next(l for l in (book.get("American Standard Burger") or {}).get("ingredients", [])
                if l.get("ref") == "lightspeed:22995348")
    assert str(line["qty"]) == "0.083", line
    assert line["unit"] == "ea", ("the relabel did not reach the costed feed", line)
    assert float(line["eff_cost"]) == pytest.approx(0.083 * 2.75, rel=1e-4)


def test_the_line_rule_leaves_the_delivery_cans_alone():
    """'Corona D, 1 ml' is 45 more lines of the same typo where 1 means one can,
    the cost is right, and the fix is in Lightspeed's export rather than in this
    book. Including them triples the family and adds nothing actionable."""
    hits = fd.line_unit_contradicts_pack(_book(), _ingredients())
    assert all(f["pack_unit"] in fd.PIECEWISE_PACK for f in hits)
    assert not any("Corona" in f["description"] for f in hits)
    assert len(hits) <= 25, [f["description"] for f in hits]


def test_the_line_rule_ranks_by_the_money_riding_on_it():
    hits = fd.line_unit_contradicts_pack(_book(), _ingredients())
    totals = [sum(l["eff_cost"] for l in f["lines"]) for f in hits]
    assert totals == sorted(totals, reverse=True)


def test_every_rule_returns_json_safe_plain_data():
    """The flags builder serialises these straight into a feed a browser reads.
    A Decimal or a set in here is a build that fails at json.dumps, at 6am."""
    ings, book = _ingredients(), _book()
    payload = {
        "a": fd.pack_unit_contradicts_name(ings),
        "b": fd.product_priced_in_two_worlds(ings),
        "c": fd.line_unit_contradicts_pack(book, ings),
    }
    json.dumps(payload)


def test_the_rules_survive_an_empty_world():
    """build_cost_book_flags runs before every deploy, including one where the
    ingredient feed has not been generated. Half a feed is worse than none."""
    assert fd.pack_unit_contradicts_name([]) == []
    assert fd.product_priced_in_two_worlds([]) == []
    assert fd.line_unit_contradicts_pack({}, []) == []
    assert fd.line_unit_contradicts_pack(None, None) == []
